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
from datetime import date
from typing import Optional

import pytz

from signal_engine.config import AppConfig, load_config
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
    cfg = load_config()
    if cfg.env.data_source != "dhan":
        return
    try:
        from signal_engine.brokers.dhan_auth import renew_token, update_env_token

        new = renew_token(cfg.env.dhan_client_id, cfg.env.dhan_access_token)
        update_env_token(new)
        os.environ["DHAN_ACCESS_TOKEN"] = new  # so load_config() in later jobs sees it
        _log.info("Dhan token renewed + persisted (.env + env)")
    except Exception as exc:  # noqa: BLE001
        _log.error("renew_token_job failed (regenerate token in the Dhan portal if expired): %s",
                   exc)


def live_job(cfg: Optional[AppConfig] = None) -> None:
    """Stream the live Dhan feed through the pipeline until market close (Dhan source only).

    Blocks for the trading session (one scheduler worker). Paper signals + alerts only;
    never places orders. No-op for non-Dhan sources or non-trading days.
    """
    cfg = load_config()  # fresh: picks up a token renewed earlier today
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
        symbols = cfg.settings.watchlist
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
        build_alerter(cfg).send(msg, level="signal")
        _log.info("premarket briefing sent: %s", msg)
    except Exception as exc:  # noqa: BLE001
        _log.error("premarket_job failed: %s", exc)


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
            alerter.send(f"Scan {_today()}: no setups passed filters today.", level="info")
        else:
            alerter.send(f"📊 Best intraday setups {_today()} "
                         f"(scanned {res.universe_size} NSE names):", level="signal")
            for e in res.leaderboard[:top_n]:
                p = e.plan
                alerter.send(f"{p.symbol} {p.direction.value} entry~{p.entry:.2f} "
                             f"SL -{p.stop_pct:.2f}% T1 +{p.target_pcts[0]:.2f}% "
                             f"R:R {p.risk_reward:.1f} conf {p.confidence:.0f}",
                             level="signal")
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
    # Token renewal first thing (before any Dhan job needs it). Daily incl. weekends so the
    # token never lapses over a long weekend.
    sched.add_job(renew_token_job, CronTrigger(hour=6, minute=0, timezone=IST),
                  id="renew_token", replace_existing=True)
    # Morning data gather: pull the prior session's bars for the whole NSE universe so the
    # leaderboard/ML use yesterday's complete data before the 08:30 briefing (and today's open).
    sched.add_job(archive_job, CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=IST),
                  args=[cfg], id="archive_morning", replace_existing=True,
                  misfire_grace_time=3600, coalesce=True)
    sched.add_job(premarket_job, CronTrigger(hour=8, minute=30, timezone=IST),
                  args=[cfg], id="premarket", replace_existing=True)
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
    sched = build_scheduler(cfg)
    _log.info("scheduler starting: jobs=%s", [j.id for j in sched.get_jobs()])
    sched.start()
