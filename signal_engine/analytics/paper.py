"""Paper-trading performance analytics — pure, deterministic, heavily tested (PLAN §6.5/§8).

Two layers, kept separate so the money-math is testable in isolation:

  * ``enrich_trade`` turns a stored trade row (entry/exit fills, direction) into absolute
    figures using a fixed per-trade notional + the project cost model: quantity, gross P&L,
    modeled round-trip charges, and net P&L (₹ and %). The tool is capital-agnostic, so a
    constant notional (``reference_trade_value``, default ₹1,00,000) is assumed per trade.
  * the metric functions (``summary``, ``equity_curve``, ``max_drawdown``, breakdowns) operate
    on the enriched ``net_pnl`` values only — so they can be unit-tested against hand-checked
    numbers without any cost model or DB.

A "win" is net-of-cost P&L > 0 (the financial definition), not merely "target hit".
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

# Intraday time-of-day buckets (IST). Open is the volatile first 45 min.
_TOD_BUCKETS = [
    ("Open (09:15-10:00)", (9, 15), (10, 0)),
    ("Late-morning (10:00-12:00)", (10, 0), (12, 0)),
    ("Midday (12:00-14:00)", (12, 0), (14, 0)),
    ("Close (14:00-15:30)", (14, 0), (15, 30)),
]


def _tod_bucket(entry_ts: Optional[str]) -> str:
    if not entry_ts:
        return "Unknown"
    try:
        t = datetime.fromisoformat(entry_ts).time()
    except ValueError:
        return "Unknown"
    mins = t.hour * 60 + t.minute
    for label, (h1, m1), (h2, m2) in _TOD_BUCKETS:
        if h1 * 60 + m1 <= mins < h2 * 60 + m2:
            return label
    return "Off-hours"


def enrich_trade(trade: dict, notional: float, cost_model) -> dict:
    """Add quantity + absolute gross/net P&L + modeled charges to a stored trade row.

    Slippage is already baked into the persisted entry/exit fills (the paper trader applies
    it); ``costs_abs`` is the modeled round-trip brokerage+statutory charges on top.
    """
    out = dict(trade)
    entry = trade.get("entry_fill")
    exit_ = trade.get("exit_fill")
    if entry is None or exit_ is None or entry <= 0:
        out.update(qty=0, gross_pnl_abs=0.0, costs_abs=0.0, net_pnl_abs=0.0,
                   net_pnl_pct=0.0, tod=_tod_bucket(trade.get("entry_ts")))
        return out
    qty = max(1, round(notional / entry))
    sign = 1.0 if str(trade.get("direction")).upper() == "LONG" else -1.0
    gross = qty * (exit_ - entry) * sign
    costs = cost_model.charges(entry, exit_, qty).total
    net = gross - costs
    out.update(
        qty=qty,
        gross_pnl_abs=round(gross, 2),
        costs_abs=round(costs, 2),
        net_pnl_abs=round(net, 2),
        net_pnl_pct=round(net / notional * 100.0, 4),
        tod=_tod_bucket(trade.get("entry_ts")),
    )
    return out


def enrich_all(trades: List[dict], notional: float, cost_model) -> List[dict]:
    return [enrich_trade(t, notional, cost_model) for t in trades]


# --- metric primitives (operate on enriched trades' net_pnl_abs) -----------

def max_drawdown(equity: List[float]) -> float:
    """Largest peak-to-trough drop on a cumulative-P&L series (absolute, >= 0)."""
    peak = float("-inf")
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    return round(mdd, 2)


def profit_factor(pnls: List[float]) -> Optional[float]:
    """Gross profit / gross loss. None if there are no losers (undefined)."""
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses == 0:
        return None  # no losing trades -> profit factor is undefined/infinite
    return round(gains / losses, 4)


def summary(trades: List[dict]) -> dict:
    """Headline metrics from enriched trades (each must have ``net_pnl_abs``)."""
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "total_pnl_abs": 0.0, "total_pnl_pct": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "profit_factor": None, "max_drawdown": 0.0, "expectancy": 0.0,
                "best_trade": None, "worst_trade": None}
    pnls = [t["net_pnl_abs"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    cum, eq = 0.0, []
    for p in pnls:
        cum += p
        eq.append(cum)
    best = max(trades, key=lambda t: t["net_pnl_abs"])
    worst = min(trades, key=lambda t: t["net_pnl_abs"])
    return {
        "n_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100.0, 2),
        "total_pnl_abs": round(sum(pnls), 2),
        "total_pnl_pct": round(sum(t.get("net_pnl_pct", 0.0) for t in trades), 4),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": profit_factor(pnls),
        "max_drawdown": max_drawdown(eq),
        "expectancy": round(sum(pnls) / n, 2),
        "best_trade": {"symbol": best.get("symbol"), "net_pnl_abs": best["net_pnl_abs"]},
        "worst_trade": {"symbol": worst.get("symbol"), "net_pnl_abs": worst["net_pnl_abs"]},
    }


def equity_curve(trades: List[dict]) -> List[dict]:
    """Cumulative net P&L over time, ordered by exit (fallback entry) timestamp."""
    ordered = sorted(trades, key=lambda t: t.get("exit_ts") or t.get("entry_ts") or "")
    cum, out = 0.0, []
    for t in ordered:
        cum += t["net_pnl_abs"]
        out.append({"ts": t.get("exit_ts") or t.get("entry_ts"),
                    "cum_pnl": round(cum, 2), "pnl": t["net_pnl_abs"], "symbol": t.get("symbol")})
    return out


def drawdown_series(curve: List[dict]) -> List[dict]:
    """Running drawdown (<= 0) beneath the equity curve."""
    peak = float("-inf")
    out = []
    for pt in curve:
        peak = max(peak, pt["cum_pnl"])
        out.append({"ts": pt["ts"], "drawdown": round(pt["cum_pnl"] - peak, 2)})
    return out


def pnl_histogram(trades: List[dict], bins: int = 11) -> List[dict]:
    """Per-trade net-P&L distribution as histogram buckets (symmetric around 0)."""
    if not trades:
        return []
    pnls = [t["net_pnl_abs"] for t in trades]
    lo, hi = min(pnls), max(pnls)
    span = max(abs(lo), abs(hi)) or 1.0
    lo, hi = -span, span
    width = (hi - lo) / bins
    counts = [0] * bins
    for p in pnls:
        idx = min(bins - 1, int((p - lo) / width)) if width else 0
        counts[max(0, idx)] += 1
    return [{"lo": round(lo + i * width, 2), "hi": round(lo + (i + 1) * width, 2),
             "count": counts[i]} for i in range(bins)]


def _group(trades: List[dict], key: str) -> List[dict]:
    groups: Dict[str, List[dict]] = {}
    for t in trades:
        groups.setdefault(str(t.get(key)), []).append(t)
    rows = []
    for name, ts in groups.items():
        s = summary(ts)
        rows.append({key: name, "n_trades": s["n_trades"], "win_rate": s["win_rate"],
                     "total_pnl_abs": s["total_pnl_abs"], "profit_factor": s["profit_factor"]})
    return sorted(rows, key=lambda r: r["total_pnl_abs"], reverse=True)


def by_strategy(trades: List[dict]) -> List[dict]:
    return _group(trades, "strategy")


def by_symbol(trades: List[dict]) -> List[dict]:
    return _group(trades, "symbol")


def by_time_of_day(trades: List[dict]) -> List[dict]:
    rows = _group(trades, "tod")
    order = {label: i for i, (label, _a, _b) in enumerate(_TOD_BUCKETS)}
    return sorted(rows, key=lambda r: order.get(r["tod"], 99))


def auto_summary(trades: List[dict]) -> List[str]:
    """Factual takeaways derived strictly from the computed numbers (no invented insight)."""
    out: List[str] = []
    s = summary(trades)
    if s["n_trades"] == 0:
        return ["No paper trades recorded yet."]

    pf = s["profit_factor"]
    if pf is not None and pf < 1.0:
        out.append(f"Overall profit factor {pf:.2f} (<1) — the recorded system is net-losing; "
                   f"losers outweigh winners despite a {s['win_rate']:.0f}% win rate.")
    elif pf is not None:
        out.append(f"Overall profit factor {pf:.2f} with a {s['win_rate']:.0f}% win rate.")

    # Strategy that wins often but loses money (large-loser pattern).
    for r in by_strategy(trades):
        if r["n_trades"] >= 5 and r["win_rate"] >= 55 and r["profit_factor"] is not None \
                and r["profit_factor"] < 1.0:
            out.append(f"Strategy '{r['strategy']}' wins {r['win_rate']:.0f}% of the time but its "
                       f"profit factor is {r['profit_factor']:.2f} — a few large losers erase many "
                       f"small wins.")

    # Time-of-day concentration of losses.
    tod = by_time_of_day(trades)
    losers = [r for r in tod if r["total_pnl_abs"] < 0]
    if losers:
        worst = min(losers, key=lambda r: r["total_pnl_abs"])
        if worst["n_trades"] >= 3:
            out.append(f"Most losses concentrate in '{worst['tod']}' "
                       f"(net {worst['total_pnl_abs']:.0f} over {worst['n_trades']} trades).")

    # Best / worst symbol when there's enough data.
    syms = by_symbol(trades)
    if len(syms) >= 2:
        out.append(f"Best symbol: {syms[0]['symbol']} ({syms[0]['total_pnl_abs']:+.0f}); "
                   f"worst: {syms[-1]['symbol']} ({syms[-1]['total_pnl_abs']:+.0f}).")

    if s["max_drawdown"] > 0:
        out.append(f"Max drawdown on the equity curve: {s['max_drawdown']:.0f}.")
    return out


def full_report(enriched: List[dict]) -> dict:
    """Assemble everything the dashboard needs from already-enriched trades."""
    curve = equity_curve(enriched)
    return {
        "summary": summary(enriched),
        "equity_curve": curve,
        "drawdown": drawdown_series(curve),
        "histogram": pnl_histogram(enriched),
        "by_strategy": by_strategy(enriched),
        "by_symbol": by_symbol(enriched),
        "by_time_of_day": by_time_of_day(enriched),
        "auto_summary": auto_summary(enriched),
    }
