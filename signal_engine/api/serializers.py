"""Dataclass -> JSON-able dict helpers for the API (keeps app.py thin)."""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from signal_engine.indicators import core as ind


def leaderboard_to_json(result, day) -> dict:
    entries = []
    for e in result.leaderboard:
        p = e.plan
        entries.append({
            "rank": e.rank, "symbol": p.symbol, "direction": p.direction.value,
            "score": e.score, "entry": p.entry, "stop_pct": p.stop_pct,
            "t1_pct": p.target_pcts[0] if p.target_pcts else None,
            "risk_reward": p.risk_reward, "confidence": p.confidence,
            "ml_confidence": result.ml_confidence.get(p.symbol),
            "expected_move_pct": p.expected_move_pct,
            "cost_to_break_even_pct": p.cost_to_break_even_pct,
            "sector": e.sector, "turnover_cr": e.turnover_cr, "reasons": list(p.reasons),
        })
    return {
        "day": day.isoformat(),
        "stats": {
            "universe": result.universe_size, "deep_scanned": result.deep_scanned,
            "filtered_out": result.filtered_out, "no_signal": result.no_signal,
            "vetoed": result.vetoed, "news_vetoed": result.news_vetoed,
            "candidates": result.candidates,
        },
        "entries": entries,
    }


def premarket_to_json(b) -> dict:
    o = b.index_outlook
    return {
        "day": b.day.isoformat(),
        "outlook": {
            "gap_bias": o.gap_bias.value, "expected_gap_pct": o.expected_gap_pct,
            "risk_tone": o.risk_tone.value, "drivers": list(o.drivers),
        },
        "picks": [
            {"symbol": p.symbol, "bias": p.bias.value, "setup": p.setup,
             "expected_gap_pct": p.expected_gap_pct, "confidence": p.confidence,
             "catalyst": p.catalyst, "drivers": list(p.drivers)}
            for p in b.picks
        ],
    }


def backtest_to_json(res) -> dict:
    m, h = res.metrics, res.health
    pf = None if m.profit_factor == float("inf") else m.profit_factor
    return {
        "days": [d.isoformat() for d in res.days],
        "picks": res.picks,
        "metrics": {
            "trades": m.trades, "win_rate": m.win_rate, "profit_factor": pf,
            "expectancy_pct": m.expectancy_pct, "total_net_pct": m.total_net_pct,
            "max_drawdown_pct": m.max_drawdown_pct, "sharpe": m.sharpe,
            "sortino": m.sortino, "avg_hold_minutes": m.avg_hold_minutes,
        },
        "equity_curve": list(m.equity_curve),
        "daily_returns": [{"date": d.isoformat(), "pct": v} for d, v in m.daily_returns],
        "health": {
            "overall": h.overall, "status": h.status, "hit_rate": h.hit_rate,
            "profit_factor": (None if h.profit_factor == float("inf") else h.profit_factor),
            "expectancy_pct": h.expectancy_pct, "calibration_error": h.calibration_error,
            "max_drawdown_pct": h.max_drawdown_pct, "components": h.components,
            "window_trades": h.window_trades,
        },
    }


def _epoch(ts) -> int:
    return int(pd.Timestamp(ts).timestamp())


def chart_to_json(symbol: str, df: pd.DataFrame, params: Dict) -> dict:
    """OHLC candles + VWAP/EMA overlays for TradingView Lightweight Charts."""
    close = df["close"]
    vwap = ind.vwap(df)
    ema_fast = ind.ema(close, int(params.get("ema_fast", 9)))
    ema_slow = ind.ema(close, int(params.get("ema_slow", 21)))
    candles: List[dict] = []
    vwap_line: List[dict] = []
    ema_fast_line: List[dict] = []
    ema_slow_line: List[dict] = []
    for i, (ts, row) in enumerate(df.iterrows()):
        t = _epoch(ts)
        candle = {"time": t, "open": float(row["open"]), "high": float(row["high"]),
                  "low": float(row["low"]), "close": float(row["close"])}
        if "volume" in row:
            candle["volume"] = int(row["volume"])
        candles.append(candle)
        if vwap.iloc[i] == vwap.iloc[i]:
            vwap_line.append({"time": t, "value": round(float(vwap.iloc[i]), 2)})
        if ema_fast.iloc[i] == ema_fast.iloc[i]:
            ema_fast_line.append({"time": t, "value": round(float(ema_fast.iloc[i]), 2)})
        if ema_slow.iloc[i] == ema_slow.iloc[i]:
            ema_slow_line.append({"time": t, "value": round(float(ema_slow.iloc[i]), 2)})
    return {
        "symbol": symbol,
        "candles": candles,
        "overlays": {"vwap": vwap_line, "ema_fast": ema_fast_line, "ema_slow": ema_slow_line},
    }
