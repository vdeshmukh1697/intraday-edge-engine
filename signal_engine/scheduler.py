"""Daily orchestration (PLAN §7, §8) — APScheduler jobs for the always-on engine box.

Jobs (IST), all skipped automatically on non-trading days:
  ~08:30  pre-market briefing      -> alert
  ~15:45  full-NSE universe scan   -> alert top picks (real Yahoo bars, EOD)
  ~16:10  nightly archive          -> persist the day's real bars (the corpus you
                                       cannot backfill later)

Why once-daily and not a 5-minute intraday loop: the free data source (Yahoo Finance)
is REST and ~15-min delayed — fine for an end-of-day full-universe scan + archive over
all ~2,000 NSE names, but it cannot be polled live for the whole universe every few
minutes (PLAN §3.3 — that needs the paid Dhan sharded websocket). For actionable
*intraday* signals on a small liquid watchlist, use ``signal-engine replay`` live, or
wire the Dhan feed when subscribed.

Jobs are thin wrappers around existing functions, each guarded so one failure never kills
the scheduler. Live order placement is never scheduled (decision-support only).
"""

from __future__ import annotations

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

    Schedule (IST): pre-market briefing 08:30, full-NSE scan 15:45 (after close, on
    ~15-min-delayed Yahoo data), real-bar archive 16:10. Once-daily by design — see the
    module docstring for why the free Yahoo feed can't drive a live intraday loop.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone=IST)
    sched.add_job(premarket_job, CronTrigger(hour=8, minute=30, timezone=IST),
                  args=[cfg], id="premarket", replace_existing=True)
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
