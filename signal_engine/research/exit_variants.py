"""P4 exit-variant replays over the REAL archived 1-min bars (research only, never live).

Replays the book's ACTUAL recorded entries (symbol, entry_ts, entry_fill, stop, direction)
through alternative EXIT rules on the archived bars, so entry selection is held constant
and only exit geometry varies:

  baseline   — current rules (hard target / stop / 90m time-stop): harness validation;
               must approximately reproduce the recorded outcomes on covered trades.
  trail      — chandelier trail at the plan's stop distance off the high-water mark;
               NO hard target (winners run until the trail or square-off catches them).
  hold90     — NO target: exit at original stop or the 90-minute mark, whichever first.

Fill conventions mirror paper/trader.py exactly: adverse slippage both legs, stop-first
pessimism inside a bar, net = gross - statutory charges (charges-only cost model — the
fills already carry slippage). Costs use the CURRENT cost config; recorded cost_pct
averaged 0.0824% so drift is negligible.

Motivation (STRATEGY_IMPROVEMENT_PLAN_2026-07 §P4): at the realized 29% win rate the book
needs ~2.4:1 payoff to break even but the hard-target structure pays 1.62:1, and TIME_STOP
exits drift +0.23R — hypothesis: hard targets truncate winners. This replay measures that.

Run:  .venv/bin/python -m signal_engine.research.exit_variants
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

from signal_engine.config import load_config
from signal_engine.risk.costs import CostModel
from signal_engine.storage.bars import ParquetBarStore

SLIP = 0.03  # % per side, adverse — matches config/risk.yaml slippage.pct_per_side
SQUARE_OFF = "15:15"  # intraday square-off checkpoint used for open-ended variants


@dataclass
class Sim:
    exit_fill: float
    exit_ts: object
    exit_reason: str


def _slip(direction: str, price: float, leg: str) -> float:
    """Adverse slippage: LONG buys higher/sells lower; SHORT the reverse."""
    sign = 1.0 if ((direction == "LONG") == (leg == "entry")) else -1.0
    return price * (1 + sign * SLIP / 100.0)


def simulate(bars: pd.DataFrame, direction: str, entry_ts, entry_fill: float,
             stop: float, target: Optional[float], variant: str,
             max_hold_min: int = 90) -> Optional[Sim]:
    """Walk bars after entry_ts applying the variant's exit rule. None if no bars."""
    fwd = bars[bars.index > entry_ts]
    if len(fwd) == 0:
        return None
    long = direction == "LONG"
    stop_dist = abs(entry_fill - stop)
    hw = entry_fill  # high-water (LONG) / low-water (SHORT) for the trail
    trail = stop
    deadline = entry_ts + timedelta(minutes=max_hold_min)
    last = None
    for ts, b in fwd.iterrows():
        last = (ts, b)
        eff_stop = trail if variant == "trail" else stop
        stop_hit = (b.low <= eff_stop) if long else (b.high >= eff_stop)
        tgt_hit = (target is not None and variant == "baseline"
                   and ((b.high >= target) if long else (b.low <= target)))
        if stop_hit:  # pessimistic: stop wins any bar it shares with a target
            return Sim(_slip(direction, eff_stop, "exit"), ts,
                       "TRAIL" if variant == "trail" else "STOP")
        if tgt_hit:
            return Sim(_slip(direction, target, "exit"), ts, "TARGET")
        if variant in ("baseline", "hold90") and ts >= deadline:
            return Sim(_slip(direction, b.close, "exit"), ts, "TIME_STOP")
        if variant == "trail":
            hw = max(hw, b.high) if long else min(hw, b.low)
            new_trail = hw - stop_dist if long else hw + stop_dist
            trail = max(trail, new_trail) if long else min(trail, new_trail)
        if ts.strftime("%H:%M") >= SQUARE_OFF:
            return Sim(_slip(direction, b.close, "exit"), ts, "SQUARE_OFF")
    ts, b = last
    return Sim(_slip(direction, b.close, "exit"), ts, "SQUARE_OFF")


def run(db_path: str = "data/signal_engine.sqlite3") -> pd.DataFrame:
    cfg = load_config()
    cost_model = CostModel(cfg.risk.costs)  # charges only; slippage lives in the fills
    store = ParquetBarStore(cfg.env.parquet_dir)

    con = sqlite3.connect(db_path)
    trades = pd.read_sql(
        """SELECT id, symbol, direction, entry_ts, entry_fill, stop_loss, target,
                  exit_reason, pnl_pct_net, r_multiple, stop_pct
           FROM (SELECT *, ABS(entry_fill - stop_loss)/entry_fill*100 AS stop_pct
                 FROM paper_trades) ORDER BY entry_ts""",
        con, parse_dates=["entry_ts"])
    con.close()

    rows: List[Dict] = []
    skipped = 0
    bar_cache: Dict[tuple, Optional[pd.DataFrame]] = {}
    for _, t in trades.iterrows():
        day = t.entry_ts.date()
        key = (t.symbol, day)
        if key not in bar_cache:
            try:
                bar_cache[key] = store.load_session(t.symbol, day)
            except Exception:  # noqa: BLE001
                bar_cache[key] = None
        bars = bar_cache[key]
        if bars is None or len(bars) == 0:
            skipped += 1
            continue
        cost = cost_model.breakeven_pct(t.entry_fill)
        sign = 1.0 if t.direction == "LONG" else -1.0
        for variant in ("baseline", "trail", "hold90"):
            sim = simulate(bars, t.direction, t.entry_ts, t.entry_fill,
                           t.stop_loss, t.target, variant)
            if sim is None:
                continue
            gross = sign * (sim.exit_fill - t.entry_fill) / t.entry_fill * 100.0
            net = gross - cost
            rows.append({
                "id": t.id, "symbol": t.symbol, "day": str(day), "variant": variant,
                "exit_reason": sim.exit_reason, "net_pct": net,
                "r": net / t.stop_pct if t.stop_pct else None,
                "recorded_net": t.pnl_pct_net, "recorded_reason": t.exit_reason,
                "stop_pct": t.stop_pct,
            })
    out = pd.DataFrame(rows)
    out.attrs["skipped_trades"] = skipped
    return out


def report(df: pd.DataFrame) -> str:
    lines = []
    n_trades = df.id.nunique()
    lines.append(f"P4 exit-variant replay — {n_trades} of 136 recorded entries covered by the "
                 f"archive ({df.attrs.get('skipped_trades', '?')} skipped: 2026-06-29 archive gap "
                 f"+ symbols missing bars). Entries held constant; only exits vary.")
    base = df[df.variant == "baseline"]
    fidelity = (base.net_pct - base.recorded_net).abs()
    lines.append(f"harness validation: baseline vs recorded net, median |diff| = "
                 f"{fidelity.median():.3f}pp, p90 = {fidelity.quantile(0.9):.3f}pp "
                 f"(same bars, same rules — small residuals = live tick-vs-bar timing).")
    for v in ("baseline", "trail", "hold90"):
        m = df[df.variant == v]
        wr = (m.net_pct > 0).mean() * 100.0
        lines.append(f"  {v:9s} n={len(m):3d}  sum net = {m.net_pct.sum():+7.2f}%  "
                     f"avgR = {m.r.mean():+.3f}  net-win% = {wr:4.1f}  "
                     f"exits: {m.exit_reason.value_counts().to_dict()}")
    for v in ("trail", "hold90"):
        m = df[df.variant == v].set_index("id")
        b = df[df.variant == "baseline"].set_index("id")
        common = m.index.intersection(b.index)
        d = (m.loc[common, "net_pct"] - b.loc[common, "net_pct"])
        lines.append(f"  {v} minus baseline (paired, n={len(common)}): "
                     f"{d.sum():+.2f}pp total, mean {d.mean():+.3f}pp/trade")
    # The P0-gated cohort under each variant (stop >= 0.5%): the forward-relevant slice.
    wide = df[df.stop_pct >= 0.5]
    lines.append("  P0-gated cohort only (stop >= 0.5%):")
    for v in ("baseline", "trail", "hold90"):
        m = wide[wide.variant == v]
        if len(m):
            lines.append(f"    {v:9s} n={len(m):3d}  sum net = {m.net_pct.sum():+7.2f}%  "
                         f"avgR = {m.r.mean():+.3f}")
    return "\n".join(lines)


if __name__ == "__main__":
    result = run()
    print(report(result))
    out_path = "data/research/exit_variants_2026-07.parquet"
    try:
        result.to_parquet(out_path)
        print(f"\nfull per-trade results -> {out_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"(parquet save failed: {exc})")
