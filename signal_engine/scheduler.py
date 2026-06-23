"""Daily orchestration (PLAN §7, §8) — APScheduler jobs for the always-on engine box.

Jobs (IST), all skipped automatically on non-trading days:
  ~08:30  pre-market briefing  -> alert
  09:20–15:00 (every N min)    market scan -> alert top picks
  ~16:00  nightly archive      -> persist the day's bars (builds the proprietary corpus)

Jobs are thin wrappers around existing functions, each guarded so one failure never kills
the scheduler. Live order placement is never scheduled (decision-support only).
"""

from __future__ import annotations

from datetime import date

import pytz

from signal_engine.config import AppConfig, load_config
from signal_engine.market.calendar import NSECalendar
from signal_engine.obs.logging_setup import get_logger

IST = pytz.timezone("Asia/Kolkata")
_log = get_logger("scheduler")


def _today() -> date:
    from datetime import datetime

    return datetime.now(IST).date()


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


def scan_job(cfg: AppConfig, top_n: int = 5) -> None:
    cal = NSECalendar()
    if not cal.is_trading_day(_today()):
        return
    try:
        from signal_engine.factory import build_alerter
        from signal_engine.scan.harness import run_scan
        from signal_engine.universe.mock import MockUniverseProvider

        # NOTE: uses the synthetic universe until the live Dhan feed is wired (KYC pending).
        uni = MockUniverseProvider(n=500)
        res = run_scan(cfg, uni, _today(), top_n=top_n)
        alerter = build_alerter(cfg)
        for e in res.leaderboard[:top_n]:
            p = e.plan
            alerter.send(f"{p.symbol} {p.direction.value} entry~{p.entry:.2f} "
                         f"SL -{p.stop_pct:.2f}% T1 +{p.target_pcts[0]:.2f}% conf {p.confidence:.0f}",
                         level="signal")
        _log.info("scan job surfaced %d picks", len(res.leaderboard))
    except Exception as exc:  # noqa: BLE001
        _log.error("scan_job failed: %s", exc)


def archive_job(cfg: AppConfig) -> None:
    """Persist the day's bars to the Parquet archive (the corpus you can't backfill later)."""
    cal = NSECalendar()
    if not cal.is_trading_day(_today()):
        return
    try:
        from signal_engine.data.synthetic import generate_session
        from signal_engine.storage.bars import ParquetBarStore

        store = ParquetBarStore(cfg.env.parquet_dir)
        # NOTE: in live mode this archives bars fetched from Dhan; with the mock feed it
        # archives the generated sessions so the pipeline + storage are exercised.
        for sym in cfg.settings.watchlist:
            df = generate_session(sym, _today())
            store.save_session(sym, _today(), df)
        _log.info("archived %d symbols for %s", len(cfg.settings.watchlist), _today())
    except Exception as exc:  # noqa: BLE001
        _log.error("archive_job failed: %s", exc)


def build_scheduler(cfg: AppConfig, scan_interval_min: int = 5):
    """Build (but do not start) the scheduler with all jobs registered."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone=IST)
    sched.add_job(premarket_job, CronTrigger(hour=8, minute=30, timezone=IST),
                  args=[cfg], id="premarket", replace_existing=True)
    sched.add_job(scan_job, CronTrigger(day_of_week="mon-fri", hour="9-14",
                                        minute=f"*/{scan_interval_min}", timezone=IST),
                  args=[cfg], id="scan", replace_existing=True)
    sched.add_job(archive_job, CronTrigger(hour=16, minute=0, timezone=IST),
                  args=[cfg], id="archive", replace_existing=True)
    return sched


def start(cfg: AppConfig = None) -> None:  # pragma: no cover - blocking loop
    cfg = cfg or load_config()
    sched = build_scheduler(cfg)
    _log.info("scheduler starting: jobs=%s", [j.id for j in sched.get_jobs()])
    sched.start()
