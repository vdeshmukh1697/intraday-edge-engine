"""Daily orchestration (PLAN §7, §8) — APScheduler jobs for the always-on engine box.

Jobs (IST), all skipped automatically on non-trading days:
  ~06:00  Dhan token renewal       -> roll the 24h token before market (Dhan source only)
  ~08:30  pre-market briefing      -> alert
  ~09:15  LIVE intraday feed       -> stream Dhan ticks through the pipeline to close
                                       (Dhan source only; paper signals + alerts)
  ~15:45  full-NSE universe scan   -> alert top picks (real Yahoo bars, EOD)
  ~16:10  nightly archive          -> persist the day's real bars

With the paid Dhan feed subscribed, the engine runs a genuine live intraday loop
(``live_job``) on the watchlist during market hours. The EOD Yahoo scan/archive remain as
a free, token-independent safety net over the whole ~2,000-name universe. The token renewal
job keeps the Dhan source alive unattended (Dhan tokens expire every 24h — see
``brokers/dhan_auth``).

Jobs are thin wrappers around existing functions, each guarded so one failure never kills
the scheduler. Live order placement is never scheduled (decision-support only).
"""

from __future__ import annotations

import os
import time as _time
from datetime import date
from typing import Optional

import pytz

from signal_engine.alerts import send_alert
from signal_engine.config import AppConfig, load_config, refresh_runtime_env, resolve_live_watchlist
from signal_engine.market.calendar import NSECalendar
from signal_engine.obs.logging_setup import get_logger
from signal_engine.universe.nse import NSEUniverseProvider

IST = pytz.timezone("Asia/Kolkata")
_log = get_logger("scheduler")

# Cache the universe (symbol list + real liquidity snapshot) for one process run so the
# scan and archive jobs don't each re-fetch the ~2,000-symbol daily snapshot.
_UNIVERSE_CACHE: dict = {}


def _today() -> date:
    from datetime import datetime

    return datetime.now(IST).date()


def _nse_universe(cfg: AppConfig, limit: Optional[int] = None) -> NSEUniverseProvider:
    """Build (and cache for the day) the real full-NSE universe with liquidity metadata."""
    key = (_today(), limit)
    cached = _UNIVERSE_CACHE.get("entry")
    if cached and cached[0] == key:
        return cached[1]
    uni = NSEUniverseProvider.build(limit=limit)
    _UNIVERSE_CACHE["entry"] = (key, uni)
    return uni


def renew_token_job(cfg: Optional[AppConfig] = None) -> None:
    """Roll the Dhan 24h access token before market open (Dhan source only).

    Persists the fresh token to .env (with backup) AND to this process's environment so the
    same-day live/scan jobs pick it up without a restart. No-op for non-Dhan sources.
    """
    refresh_runtime_env()  # operate on the freshest token if .env was updated manually overnight
    cfg = load_config()
    if cfg.env.data_source != "dhan":
        return
    try:
        from signal_engine.brokers.dhan_auth import (
            generate_token_via_totp, renew_token, update_env_token,
        )

        totp_secret, pin = os.getenv("DHAN_TOTP_SECRET"), os.getenv("DHAN_PIN")
        if totp_secret and pin and cfg.env.dhan_client_id:
            # PERMANENT path: mint a brand-new token via TOTP (no browser, no manual OTP). Works
            # even from a fully-expired state, so the engine self-heals after any downtime.
            # Dhan intermittently rejects a code with "Invalid TOTP" (window-edge/reuse — seen
            # 2/2 on scheduler startups 2026-07-02 while a mint minutes later succeeded), so on
            # that specific rejection wait out the 30s TOTP window and try the next code once.
            try:
                new = generate_token_via_totp(cfg.env.dhan_client_id, pin, totp_secret)
            except Exception as first_exc:  # noqa: BLE001
                if "Invalid TOTP" not in str(first_exc):
                    raise
                _log.warning("TOTP mint rejected (%s) — retrying with the next TOTP window",
                             first_exc)
                _time.sleep(35)
                new = generate_token_via_totp(cfg.env.dhan_client_id, pin, totp_secret)
            via = "TOTP auto-login"
        else:
            # Fallback: RenewToken (extends an ACTIVE token; works only for Dhan-Web tokens).
            new = renew_token(cfg.env.dhan_client_id, cfg.env.dhan_access_token)
            via = "RenewToken"
        update_env_token(new)
        os.environ["DHAN_ACCESS_TOKEN"] = new  # so load_config() in later jobs sees it
        _log.info("Dhan token refreshed via %s + persisted (.env + env)", via)
    except Exception as exc:  # noqa: BLE001
        _log.error("renew_token_job failed (set DHAN_TOTP_SECRET + DHAN_PIN for auto-login, or "
                   "regenerate the token if expired): %s", exc)


def live_job(cfg: Optional[AppConfig] = None) -> None:
    """Stream the live Dhan feed through the pipeline until market close (Dhan source only).

    Blocks for the trading session (one scheduler worker). Paper signals + alerts only;
    never places orders. No-op for non-Dhan sources or non-trading days.
    """
    # Re-read the daily-regenerated Dhan token from .env and OVERRIDE the (possibly stale) value
    # this long-running process loaded at startup. Without this, a token refreshed in .env after
    # the scheduler started is never seen — _load_dotenv runs once and never overrides — so
    # live_job dies at 09:15 with "token expired" even though .env holds a valid one.
    refreshed = refresh_runtime_env()
    if refreshed:
        _log.info("live_job: refreshed %s from .env", ", ".join(sorted(refreshed)))
    cfg = load_config()  # now reflects the freshest token
    if cfg.env.data_source != "dhan":
        _log.info("live_job skipped: SE_DATA_SOURCE is %r, not 'dhan'", cfg.env.data_source)
        return
    cal = NSECalendar()
    if not cal.is_trading_day(_today()):
        return
    repo = None
    try:
        from signal_engine.engine.runner import EngineRunner
        from signal_engine.factory import build_alerter, build_broker
        from signal_engine.market.session import MarketSession
        from signal_engine.storage.repository import SignalRepository
        from signal_engine.strategies.base import create_strategy

        broker = build_broker(cfg, day=_today())
        strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
        session = MarketSession(cfg.settings.market, cal)
        # Persist every live paper trade so the Paper-Trading tracker accumulates real history.
        repo = SignalRepository(cfg.env.db_url)
        runner = EngineRunner(cfg, broker, strategy, session, build_alerter(cfg), repo=repo)
        symbols = resolve_live_watchlist(cfg)  # optional large-cap/liquid gate (default: full list)
        if len(symbols) != len(cfg.settings.watchlist):
            _log.info("live_job: universe gate ON — trading %d of %d watchlist names",
                      len(symbols), len(cfg.settings.watchlist))
        _log.info("live_job: streaming Dhan feed for %d symbols until close", len(symbols))
        summary = runner.live(symbols)
        _log.info("live_job done: %d bars, %d picks, %d paper trades (persisted)",
                  summary.bars_processed, len(summary.picks), len(summary.closed))
    except Exception as exc:  # noqa: BLE001
        _log.error("live_job failed: %s", exc)
    finally:
        if repo is not None:
            repo.close()


def premarket_job(cfg: AppConfig) -> None:
    cal = NSECalendar()
    if not cal.is_trading_day(_today()):
        return
    try:
        from signal_engine.factory import build_alerter, build_cues_provider
        from signal_engine.premarket.briefing import build_briefing

        b = build_briefing(cfg, day=_today(), cues_provider=build_cues_provider(cfg))
        o = b.index_outlook
        top = b.picks[0] if b.picks else None
        msg = (f"Pre-market {b.day}: {o.gap_bias.value} ({o.expected_gap_pct:+.2f}%), "
               f"{o.risk_tone.value}. Top: "
               + (f"{top.symbol} {top.bias.value} ({top.setup}, conf {top.confidence:.0f})"
                  if top else "none"))
        meta = {"kind": "premarket", "gap_bias": o.gap_bias.value,
                "expected_gap_pct": o.expected_gap_pct, "risk_tone": o.risk_tone.value}
        if top:
            meta.update({"symbol": top.symbol, "direction": top.bias.value,
                         "strategy": top.setup, "confidence": top.confidence})
        send_alert(build_alerter(cfg), msg, level="signal", meta=meta)
        _log.info("premarket briefing sent: %s", msg)
    except Exception as exc:  # noqa: BLE001
        _log.error("premarket_job failed: %s", exc)


def healthcheck_job(cfg: Optional[AppConfig] = None) -> None:
    """Pre-open self-check (~08:45 IST): confirm the Dhan token + live feed are healthy BEFORE the
    09:15 session, self-heal the token via TOTP if needed, and alert the morning status. Turns a
    silent broken setup into an actionable Telegram ping so the day isn't lost unnoticed."""
    from datetime import datetime

    cal = NSECalendar()
    if not cal.is_trading_day(_today()):
        return
    refresh_runtime_env()
    cfg = load_config()
    from signal_engine.factory import build_alerter

    alerter = build_alerter(cfg)
    try:
        if cfg.env.data_source != "dhan":
            send_alert(alerter, f"⚠️ Pre-open {_today()}: SE_DATA_SOURCE is "
                       f"{cfg.env.data_source!r}, not 'dhan' — live session will be skipped.",
                       level="signal", meta={"kind": "health"})
            return
        from signal_engine.brokers.dhan import token_expiry
        from signal_engine.factory import build_broker

        exp = token_expiry(cfg.env.dhan_access_token or "")
        if not (exp and exp > datetime.utcnow()):
            # Self-heal: try a fresh TOTP mint right now, then re-read.
            try:
                renew_token_job(cfg)
                refresh_runtime_env()
                cfg = load_config()
                exp = token_expiry(cfg.env.dhan_access_token or "")
            except Exception:  # noqa: BLE001
                pass
        if not (exp and exp > datetime.utcnow()):
            send_alert(alerter, f"⚠️ Pre-open {_today()}: Dhan token invalid and TOTP re-mint "
                       f"FAILED — fix before 09:15 (check DHAN_TOTP_SECRET/PIN).",
                       level="signal", meta={"kind": "health"})
            return
        broker = build_broker(cfg, day=_today())
        q = broker.quote(cfg.settings.watchlist[:3])
        send_alert(alerter, f"✅ Pre-open OK {_today()}: token valid (exp "
                   f"{exp.strftime('%H:%M UTC')}), live feed returning {len(q)}/3 quotes. "
                   f"Live session fires 09:15.", level="signal", meta={"kind": "health"})
        _log.info("healthcheck_job: token valid, feed %d/3 quotes", len(q))
    except Exception as exc:  # noqa: BLE001
        send_alert(alerter, f"⚠️ Pre-open {_today()}: health check FAILED — {str(exc)[:160]}",
                   level="signal", meta={"kind": "health"})
        _log.error("healthcheck_job failed: %s", exc)


def scan_job(cfg: AppConfig, top_n: int = 10, limit: Optional[int] = None) -> None:
    """Full-NSE-universe scan on real Yahoo bars (EOD) -> alert top picks."""
    cal = NSECalendar()
    if not cal.is_trading_day(_today()):
        return
    try:
        from signal_engine.factory import build_alerter
        from signal_engine.scan.real_harness import run_real_scan

        uni = _nse_universe(cfg, limit=limit)
        res = run_real_scan(cfg, uni, _today(), top_n=top_n)
        alerter = build_alerter(cfg)
        if not res.leaderboard:
            send_alert(alerter, f"Scan {_today()}: no setups passed filters today.",
                       level="info", meta={"kind": "scan"})
        else:
            send_alert(alerter, f"📊 Best intraday setups {_today()} "
                       f"(scanned {res.universe_size} NSE names):", level="signal",
                       meta={"kind": "scan", "universe_size": res.universe_size})
            for e in res.leaderboard[:top_n]:
                p = e.plan
                send_alert(alerter,
                           f"{p.symbol} {p.direction.value} entry~{p.entry:.2f} "
                           f"SL -{p.stop_pct:.2f}% T1 +{p.target_pcts[0]:.2f}% "
                           f"R:R {p.risk_reward:.1f} conf {p.confidence:.0f}",
                           level="signal",
                           meta={"kind": "scan", "symbol": p.symbol,
                                 "direction": p.direction.value, "strategy": p.strategy,
                                 "entry": p.entry, "stop_loss": p.stop_loss,
                                 "stop_pct": p.stop_pct,
                                 "target": p.t1 if p.targets else None,
                                 "target_pct": p.target_pcts[0] if p.target_pcts else None,
                                 "risk_reward": p.risk_reward, "confidence": p.confidence,
                                 "reasons": p.reasons})
        _log.info("scan job surfaced %d picks from %d names", len(res.leaderboard), res.universe_size)
    except Exception as exc:  # noqa: BLE001
        _log.error("scan_job failed: %s", exc)


def archive_job(cfg: AppConfig, limit: Optional[int] = None) -> None:
    """Persist the day's REAL bars for the full NSE universe (the corpus you can't backfill).

    Fetches real intraday bars for every NSE equity in batched, throttled sweeps and writes
    each to the Parquet store. Best-effort: symbols that don't return data are skipped and
    the count is logged — coverage is never silently truncated.
    """
    cal = NSECalendar()
    if not cal.is_trading_day(_today()):
        return
    try:
        from signal_engine.data.yahoo_batch import fetch_intraday
        from signal_engine.storage.bars import ParquetBarStore

        uni = _nse_universe(cfg, limit=limit)
        symbols = uni.symbols()
        store = ParquetBarStore(cfg.env.parquet_dir)
        frames = fetch_intraday(symbols, interval="1m", period="1d")
        saved = 0
        for sym, df in frames.items():
            try:
                store.save_session(sym, _today(), df)
                saved += 1
            except Exception as exc:  # noqa: BLE001
                _log.warning("archive save failed for %s: %s", sym, exc)
        _log.info("archived %d/%d NSE symbols (real bars) for %s", saved, len(symbols), _today())
    except Exception as exc:  # noqa: BLE001
        _log.error("archive_job failed: %s", exc)


def build_scheduler(cfg: AppConfig):
    """Build (but do not start) the scheduler with all jobs registered.

    Schedule (IST): token renew 06:00, pre-market briefing 08:30, LIVE Dhan feed 09:15→close,
    full-NSE Yahoo scan 15:45, real-bar archive 16:10. The live feed runs only when
    SE_DATA_SOURCE=dhan; the renew/live jobs no-op otherwise, so the same scheduler works on
    the free Yahoo tier too.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone=IST)
    # Token renewal MULTIPLE times/day (every ~8h, incl. weekends) so the chain is effectively
    # PERMANENT: each RenewToken mints a fresh ~24h token, so as long as ANY one of these runs in
    # a 24h window the token never lapses and no manual OTP is ever needed. Three times/day means
    # the laptop would have to be off for >~24h continuously to break the chain. Misfire grace +
    # coalesce so a slightly-late wake still renews. (RenewToken only works on a CONSENT-minted
    # token — the dashboard "Reconnect Dhan" OTP — NOT a token pasted from the Dhan web portal.)
    for _h in (6, 14, 22):
        sched.add_job(renew_token_job, CronTrigger(hour=_h, minute=0, timezone=IST),
                      id=f"renew_token_{_h}", replace_existing=True,
                      misfire_grace_time=3600, coalesce=True)
    # Morning data gather: pull the prior session's bars for the whole NSE universe so the
    # leaderboard/ML use yesterday's complete data before the 08:30 briefing (and today's open).
    sched.add_job(archive_job, CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=IST),
                  args=[cfg], id="archive_morning", replace_existing=True,
                  misfire_grace_time=3600, coalesce=True)
    # Generous misfire grace + coalesce so a brief sleep/wake delay around 08:30 still fires the
    # briefing once (rather than silently skipping the morning prediction).
    sched.add_job(premarket_job, CronTrigger(hour=8, minute=30, timezone=IST),
                  args=[cfg], id="premarket", replace_existing=True,
                  misfire_grace_time=1800, coalesce=True)
    # Pre-open self-check + Telegram alert (token/feed health, self-heals token) before the 09:15 open.
    sched.add_job(healthcheck_job, CronTrigger(day_of_week="mon-fri", hour=8, minute=45, timezone=IST),
                  id="healthcheck", replace_existing=True, misfire_grace_time=1800, coalesce=True)
    # Live intraday feed: blocks one worker for the whole session. Generous misfire grace +
    # coalesce so a slightly late start (e.g. scheduler restart) still launches the session.
    sched.add_job(live_job, CronTrigger(day_of_week="mon-fri", hour=9, minute=15, timezone=IST),
                  id="live", replace_existing=True, coalesce=True,
                  misfire_grace_time=3600, max_instances=1)
    sched.add_job(scan_job, CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=IST),
                  args=[cfg], id="scan", replace_existing=True)
    sched.add_job(archive_job, CronTrigger(day_of_week="mon-fri", hour=16, minute=10, timezone=IST),
                  args=[cfg], id="archive", replace_existing=True)
    return sched


def start(cfg: AppConfig = None) -> None:  # pragma: no cover - blocking loop
    cfg = cfg or load_config()
    # Renew immediately on startup so a (re)start refreshes the token chain right away — the
    # engine is never left running on a token that's about to lapse just because it booted between
    # the scheduled renew times. Best-effort: a failure here never blocks the scheduler.
    try:
        renew_token_job(cfg)
    except Exception as exc:  # noqa: BLE001
        _log.error("startup token renew failed (non-fatal): %s", exc)
    sched = build_scheduler(cfg)
    _log.info("scheduler starting: jobs=%s", [j.id for j in sched.get_jobs()])
    sched.start()
