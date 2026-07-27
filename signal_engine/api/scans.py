"""The dashboard's expensive scans, isolated so they can run OUTSIDE the API process.

``signal_engine/api/app.py`` hands each of these to a dedicated scan subprocess (see
``_submit_scan`` there). Two consequences shape this module:

* **No import side effects.** The child imports this module by name to unpickle the target
  function, so importing it must not build a FastAPI app, open a broker or touch the network.
  (That is why these live here and not in ``app.py``, whose import builds the app.)
* **Plain arguments and plain results.** Everything crossing the process boundary is pickled,
  so the entry points take scalars/dates and return the same JSON-ready dicts the serializers
  already produce. Config is re-read here rather than shipped in, which also means a
  ``config/`` edit takes effect on the next refresh without an API restart.

Module-level caches below are per-child; the scan worker is long-lived, so they survive
across tasks the same way they used to survive across requests.
"""

from __future__ import annotations

from datetime import date

from signal_engine.api.serializers import backtest_to_json, leaderboard_to_json, premarket_to_json
from signal_engine.config import load_config


def exit_with_parent() -> None:
    """Scan-worker initializer: leave when the API process does.

    The supervisor escalates to SIGKILL on a worker that won't stop, and a killed parent takes
    no children with it. Without this, a heal in the middle of a 10-minute backtest would strand
    a pinned core with nobody left to hand the result to — the same orphan problem this whole
    change exists to fix, one level down. macOS has no PDEATHSIG, so watch for reparenting."""
    import os
    import threading
    import time

    def _watch() -> None:
        while os.getppid() != 1:
            time.sleep(2.0)
        os._exit(0)

    threading.Thread(target=_watch, name="parent-watchdog", daemon=True).start()


_REAL_SCAN_CAP = 600   # scan at most the N most-liquid archived names (keeps it responsive)
_MAX_LEADERBOARD = 100  # compute the ranking once at this size; requests slice [:top] from it
                        # (must be >= the leaderboard endpoint's `top` ceiling)


def archive_leaderboard(news: bool):
    """Build the leaderboard from the REAL backfilled Parquet corpus (no network).

    Reads each archived symbol's latest session, derives a real liquidity snapshot, keeps the
    most-liquid names, and runs the same Scanner used everywhere else. Returns None if the
    archive is empty (caller falls back to synthetic). Ranked at _MAX_LEADERBOARD; the endpoint
    slices [:top] from it, so changing the dashboard's stock count never triggers a rescan."""
    from signal_engine.scan.real_harness import run_real_scan
    from signal_engine.storage.bars import ParquetBarStore
    from signal_engine.universe.nse import NSEUniverseProvider

    cfg = load_config()
    store = ParquetBarStore(cfg.env.parquet_dir)
    sessions, metrics = {}, {}
    day = None
    for sym in store.list_symbols():
        df = store.load_latest_session(sym)
        if df is None or df.empty:
            continue
        sessions[sym] = df
        metrics[sym] = {"last_price": float(df["close"].iloc[-1]),
                        "avg_daily_turnover_cr": float((df["close"] * df["volume"]).sum()) / 1e7}
        day = df.index.max().date()
    if not sessions:
        return None
    # Keep the most-liquid N so the deep scan stays responsive.
    top_syms = sorted(sessions, key=lambda s: metrics[s]["avg_daily_turnover_cr"],
                      reverse=True)[:_REAL_SCAN_CAP]
    uni = NSEUniverseProvider([s for s in top_syms], {s: metrics[s] for s in top_syms})
    res = run_real_scan(cfg, uni, day, top_n=_MAX_LEADERBOARD, with_news=news,
                        intraday_fetch=lambda syms: {s: sessions[s] for s in syms if s in sessions})
    return leaderboard_to_json(res, day)


_LIQUID_UNIVERSE_CACHE: dict = {}


def _liquid_universe_data(cfg, limit: int):
    """Return (symbols, sessions) for the N most-liquid archived NSE names, ranked by the latest
    session's rupee turnover. `sessions[sym]` is that latest session's bars — reused for real
    prior-day momentum in the pre-market briefing (for today's briefing the prior session IS the
    latest archived one). Cached per (corpus size, newest date, limit); None if the archive is
    empty (caller falls back to the watchlist)."""
    from signal_engine.storage.bars import ParquetBarStore

    store = ParquetBarStore(cfg.env.parquet_dir)
    all_syms = store.list_symbols()
    if not all_syms:
        return None
    latest = None
    for s in all_syms[:5] + all_syms[-5:]:
        d = store.load_latest_session(s)
        if d is not None and not d.empty:
            dt = d.index.max().date()
            latest = dt if latest is None or dt > latest else latest
    ckey = (len(all_syms), str(latest), int(limit))
    if ckey in _LIQUID_UNIVERSE_CACHE:
        return _LIQUID_UNIVERSE_CACHE[ckey]

    sessions, turnover = {}, {}
    for sym in all_syms:
        df = store.load_latest_session(sym)
        if df is None or df.empty:
            continue
        sessions[sym] = df
        turnover[sym] = float((df["close"] * df["volume"]).sum()) / 1e7  # ₹cr
    if not sessions:
        return None
    ranked = sorted(sessions, key=lambda s: turnover.get(s, 0.0), reverse=True)[:int(limit)]
    result = (ranked, {s: sessions[s] for s in ranked})
    _LIQUID_UNIVERSE_CACHE.clear()
    _LIQUID_UNIVERSE_CACHE[ckey] = result
    return result


def _prior_state_from_sessions(sessions: dict):
    """Build a prior_state_fn(sym, prior_day) closure that reads real prior-session momentum from
    already-loaded archive bars (% return + where it closed in its range). Neutral if missing."""
    def _fn(sym: str, _prior_day) -> dict:
        df = sessions.get(sym)
        if df is None or df.empty:
            return {"prev_return_pct": 0.0, "close_position": 0.5}
        o = float(df["open"].iloc[0])
        c = float(df["close"].iloc[-1])
        hi = float(df["high"].max())
        lo = float(df["low"].min())
        return {
            "prev_return_pct": (c / o - 1.0) * 100.0 if o else 0.0,
            "close_position": (c - lo) / (hi - lo) if hi > lo else 0.5,
        }
    return _fn


def warm_liquid_universe(limit: int) -> int:
    """Load the liquid-universe archive slice into this worker's cache and report its size.

    The ~2k-session Parquet load is the slow part the pre-market briefing shares with the
    leaderboard, so the API warms it at startup. Deliberately does NOT build the briefing:
    that would fetch Yahoo cues and RSS news, which a warm-up has no business doing."""
    cfg = load_config()
    data = _liquid_universe_data(cfg, limit)
    return len(data[0]) if data else 0


def premarket_briefing(day: date, seed: int, top: int, universe: int) -> dict:
    """Pre-open briefing over the `universe` most-liquid archived NSE names (not just the trading
    watchlist), showing the top `top` by conviction. With a real data source it uses real Yahoo
    global cues + real RSS news + real prior-session momentum from the archive; falls back to the
    synthetic path (and the watchlist) when the archive is empty or SE_DATA_SOURCE=mock."""
    from signal_engine.factory import build_cues_provider, build_news_provider
    from signal_engine.premarket.briefing import build_briefing

    cfg = load_config()
    symbols = None
    cues_provider = None
    news_provider = None
    prior_state_fn = None
    meta = {"universe_source": "watchlist (synthetic)", "data": "synthetic"}
    if cfg.env.data_source != "mock":
        liquid = _liquid_universe_data(cfg, universe)
        if liquid is not None:
            symbols, sessions = liquid
            prior_state_fn = _prior_state_from_sessions(sessions)
            cues_provider = build_cues_provider(cfg)   # real Yahoo (None if not configured)
            news_provider = build_news_provider(cfg)   # real RSS (None if not configured)
            meta = {
                "universe_source": f"{len(symbols)} most-liquid NSE names (archive)",
                "data": "real" if (cues_provider or news_provider) else "archive+synthetic",
                "cues": "yahoo" if cues_provider else "synthetic",
                "news": "rss" if news_provider else "synthetic",
            }
    briefing = build_briefing(cfg, symbols=symbols, day=day, seed=seed, top_n=top,
                              cues_provider=cues_provider, news_provider=news_provider,
                              prior_state_fn=prior_state_fn)
    payload = premarket_to_json(briefing)
    payload["meta"] = {**meta, "scored": len(symbols) if symbols else len(cfg.settings.watchlist),
                       "shown": len(payload["picks"])}
    return payload


def backtest(start: date, days: int, seed: int) -> dict:
    """Replay the configured watchlist over `days` sessions from `start`. Measured 2026-07-26 at
    ~62 s per session on a 40-name watchlist (623 s for the default 10 days), which is why this
    never runs on the request thread."""
    from signal_engine.backtest.engine import run_backtest

    cfg = load_config()
    return backtest_to_json(run_backtest(cfg, cfg.settings.watchlist, start, days, seed=seed))
