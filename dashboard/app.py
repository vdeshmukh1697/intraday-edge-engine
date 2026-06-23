"""Minimal Streamlit cockpit for the MVP (PLAN §11, Phase-1 local shortcut).

Run:  streamlit run dashboard/app.py
This is the throwaway local dashboard; the polished Next.js/Vercel UI is Phase 6.
It runs a synthetic replay and shows the leaderboard, paper trades, and a per-stock
chart with VWAP/EMA overlays + signal markers.
"""

from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st

from signal_engine.alerts.console import ConsoleAlerter
from signal_engine.brokers.mock import MockBroker
from signal_engine.config import load_config
from signal_engine.engine.runner import EngineRunner
from signal_engine.indicators import core as ind
from signal_engine.market.calendar import NSECalendar
from signal_engine.market.session import MarketSession
from signal_engine.strategies.base import create_strategy

st.set_page_config(page_title="Intraday Signal Engine", layout="wide")
st.title("📈 Intraday Signal Engine — paper cockpit")
st.caption(
    "Decision-support only. Not investment advice. No live orders are placed. "
    "Intraday trading carries substantial risk of loss. (PLAN §9)"
)

cfg = load_config()

with st.sidebar:
    mode = st.radio(
        "Mode",
        ["Scan (leaderboard)", "Replay (paper book)", "Backtest + Health", "Pre-market"],
    )
    st.divider()
    day = st.date_input("Trading day", value=date(2025, 6, 23))
    seed = st.number_input("Seed", value=7, step=1)
    run, run_scan_btn, run_bt_btn, run_pm_btn, demo, symbols = False, False, False, False, False, []
    if mode.startswith("Scan"):
        st.header("Scan controls")
        universe_n = st.number_input("Universe size", value=2000, step=100)
        top_n = st.number_input("Top N", value=20, step=5)
        run_scan_btn = st.button("Run scan", type="primary")
    elif mode.startswith("Replay"):
        st.header("Replay controls")
        demo = st.checkbox("Demo regimes (force setups)", value=True)
        symbols = st.multiselect("Symbols", cfg.settings.watchlist, default=cfg.settings.watchlist)
        run = st.button("Run replay", type="primary")
    elif mode.startswith("Backtest"):
        st.header("Backtest controls")
        bt_start = st.date_input("Start date", value=date(2025, 6, 2))
        bt_days = st.number_input("Trading days", value=10, step=1)
        run_bt_btn = st.button("Run backtest", type="primary")
    else:
        st.header("Pre-market controls")
        symbols = st.multiselect("Symbols", cfg.settings.watchlist, default=cfg.settings.watchlist)
        run_pm_btn = st.button("Build briefing", type="primary")


@st.cache_data(show_spinner=True)
def _run(day_iso: str, seed: int, demo: bool, symbols: tuple):
    d = date.fromisoformat(day_iso)
    regime_map = {}
    if demo:
        regimes = ["trend_up", "trend_up", "trend_down", "choppy", "choppy"]
        regime_map = {s: regimes[i % len(regimes)] for i, s in enumerate(symbols)}
    broker = MockBroker(day=d, seed=int(seed), regime_map=regime_map)
    strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
    session = MarketSession(cfg.settings.market, NSECalendar())
    runner = EngineRunner(cfg, broker, strategy, session, ConsoleAlerter(io.StringIO()))
    summary = runner.replay(list(symbols))
    # capture per-symbol sessions for charting
    sessions = {s: broker._sessions[s].copy() for s in symbols}
    picks = [
        {
            "symbol": p.symbol, "dir": p.direction.value, "time": p.ts.strftime("%H:%M"),
            "entry": p.entry, "stop%": -p.stop_pct, "T1%": p.target_pcts[0],
            "R:R": p.risk_reward, "conf": p.confidence, "why": ", ".join(p.reasons),
        }
        for p in summary.picks
    ]
    trades = [
        {
            "symbol": t.symbol, "dir": t.direction.value, "exit": t.exit_reason.value,
            "net%": t.pnl_pct_net, "R": t.r_multiple, "won": t.won,
        }
        for t in summary.closed if t.entry_fill is not None
    ]
    stats = {"bars": summary.bars_processed, "picks": len(summary.picks),
             "trades": len(trades), "win_rate": summary.win_rate, "net_pct": summary.net_pnl_pct}
    return picks, trades, stats, sessions


if run and symbols:
    picks, trades, stats, sessions = _run(day.isoformat(), int(seed), demo, tuple(symbols))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Picks surfaced", stats["picks"])
    c2.metric("Paper trades", stats["trades"])
    c3.metric("Win rate", f"{stats['win_rate']}%")
    c4.metric("Net P&L (Σ%)", f"{stats['net_pct']:+.2f}%")

    st.subheader("🏆 Best intraday setups (leaderboard)")
    if picks:
        st.dataframe(
            pd.DataFrame(picks).sort_values("conf", ascending=False),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No picks surfaced for these settings.")

    st.subheader("📒 Paper trades (net of costs)")
    if trades:
        st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)

    st.subheader("🔎 Per-stock chart")
    sym = st.selectbox("Symbol", symbols)
    df = sessions[sym]
    p = cfg.settings.strategy.params
    chart = pd.DataFrame(
        {
            "close": df["close"].values,
            "vwap": ind.vwap(df).values,
            "ema_fast": ind.ema(df["close"], int(p.get("ema_fast", 9))).values,
            "ema_slow": ind.ema(df["close"], int(p.get("ema_slow", 21))).values,
        },
        index=df.index,
    )
    st.line_chart(chart)


@st.cache_data(show_spinner=True)
def _scan(day_iso: str, seed: int, universe_n: int, top_n: int):
    from datetime import time as _time

    from signal_engine.scan.harness import run_scan
    from signal_engine.universe.mock import MockUniverseProvider

    uni = MockUniverseProvider(n=int(universe_n), seed=int(seed))
    res = run_scan(cfg, uni, date.fromisoformat(day_iso), as_of=_time(11, 0),
                   seed=int(seed), top_n=int(top_n))
    rows = [
        {
            "rank": e.rank, "symbol": e.symbol, "dir": e.plan.direction.value,
            "score": e.score, "entry": e.plan.entry, "stop%": -e.plan.stop_pct,
            "T1%": e.plan.target_pcts[0], "R:R": e.plan.risk_reward,
            "conf": e.plan.confidence, "sector": e.sector, "turnover_cr": e.turnover_cr,
            "why": ", ".join(e.plan.reasons),
        }
        for e in res.leaderboard
    ]
    stats = {"universe": res.universe_size, "deep_scanned": res.deep_scanned,
             "candidates": res.candidates, "vetoed": res.vetoed}
    return rows, stats


if run_scan_btn:
    rows, stats = _scan(day.isoformat(), int(seed), int(universe_n), int(top_n))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Universe", stats["universe"])
    c2.metric("Deep-scanned", stats["deep_scanned"])
    c3.metric("Candidates", stats["candidates"])
    c4.metric("Risk-vetoed", stats["vetoed"])
    st.subheader("🏆 Best intraday stocks — leaderboard")
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No setups passed the filters/gates. Try another seed.")

@st.cache_data(show_spinner=True)
def _backtest(start_iso: str, days: int, seed: int):
    from signal_engine.backtest.engine import run_backtest

    res = run_backtest(cfg, cfg.settings.watchlist, date.fromisoformat(start_iso),
                       int(days), seed=int(seed))
    m, h = res.metrics, res.health
    metrics = {
        "trades": m.trades, "win_rate": m.win_rate,
        "profit_factor": (None if m.profit_factor == float("inf") else round(m.profit_factor, 2)),
        "expectancy_pct": m.expectancy_pct, "total_net_pct": m.total_net_pct,
        "max_dd_pct": m.max_drawdown_pct, "sharpe": round(m.sharpe, 2),
    }
    equity = list(m.equity_curve)
    health = {"overall": h.overall, "status": h.status, "hit_rate": h.hit_rate,
              "calibration_error": h.calibration_error, "components": h.components}
    return metrics, equity, health


if run_bt_btn:
    metrics, equity, health = _backtest(bt_start.isoformat(), int(bt_days), int(seed))
    badge = {"green": "🟢", "amber": "🟡", "red": "🔴"}.get(health["status"], "⚪")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trades", metrics["trades"])
    c2.metric("Win rate", f"{metrics['win_rate']:.0f}%")
    c3.metric("Net P&L (Σ%)", f"{metrics['total_net_pct']:+.2f}%")
    c4.metric("Strategy Health", f"{badge} {health['overall']:.0f}/100")

    st.subheader("📈 Equity curve (cumulative daily %, net of costs)")
    if equity:
        st.line_chart(pd.DataFrame({"equity_%": equity}))

    st.subheader("📊 Backtest metrics")
    st.dataframe(pd.DataFrame([metrics]), use_container_width=True, hide_index=True)

    st.subheader(f"🩺 Strategy Health — {badge} {health['status'].upper()}")
    st.caption(f"hit rate {health['hit_rate']:.0f}% · calibration (Brier) "
               f"{health['calibration_error']:.3f} (lower=better)")
    st.dataframe(pd.DataFrame([health["components"]]), use_container_width=True, hide_index=True)

@st.cache_data(show_spinner=True)
def _premarket(day_iso: str, seed: int, symbols: tuple):
    from signal_engine.premarket.briefing import build_briefing

    b = build_briefing(cfg, symbols=list(symbols), day=date.fromisoformat(day_iso), seed=int(seed))
    o = b.index_outlook
    outlook = {"gap_bias": o.gap_bias.value, "expected_gap_pct": o.expected_gap_pct,
               "risk_tone": o.risk_tone.value, "drivers": ", ".join(o.drivers)}
    picks = [
        {"symbol": p.symbol, "bias": p.bias.value, "setup": p.setup,
         "exp_gap_%": p.expected_gap_pct, "conf": p.confidence, "catalyst": p.catalyst}
        for p in b.picks
    ]
    return outlook, picks


if run_pm_btn and symbols:
    outlook, picks = _premarket(day.isoformat(), int(seed), tuple(symbols))
    tone_badge = {"RISK_ON": "🟢", "RISK_OFF": "🔴"}.get(outlook["risk_tone"], "⚪")
    c1, c2, c3 = st.columns(3)
    c1.metric("Index bias", outlook["gap_bias"])
    c2.metric("Expected gap", f"{outlook['expected_gap_pct']:+.2f}%")
    c3.metric("Risk tone", f"{tone_badge} {outlook['risk_tone']}")
    st.caption("Drivers: " + outlook["drivers"])
    st.subheader("🌅 Pre-open watchlist")
    if picks:
        st.dataframe(pd.DataFrame(picks), use_container_width=True, hide_index=True)
    else:
        st.info("No actionable pre-open bias for these settings.")

if not (run or run_scan_btn or run_bt_btn or run_pm_btn):
    st.info("Pick a **Mode** in the sidebar, set controls, and click the run button.")
