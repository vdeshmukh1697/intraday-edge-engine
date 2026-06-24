"""Reusable, consistent signal-evaluation helper for the alpha panel (additive).

Every scan agent reports through :func:`evaluate_signal` so the panel's verdicts are
comparable: same NET-OF-COST accounting (statutory round-trip charges + 2x slippage from
:class:`signal_engine.risk.costs.CostModel`), same metrics, and the same
:func:`signal_engine.ml.evaluate.edge_verdict` gate. The harness reports the OOS number as
the headline and the IS number alongside (never IS alone).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from signal_engine.config import load_config
from signal_engine.ml.evaluate import EdgeVerdict, edge_verdict, estimate_pbo
from signal_engine.risk.costs import CostModel

# Forward-return label column name for each supported horizon (see research.dataset).
_FWD_COL = {5: "fwd_ret_5", 15: "fwd_ret_15", 30: "fwd_ret_30", 60: "fwd_ret_60"}


def _default_breakeven_pct() -> float:
    """Round-trip break-even as a FRACTION of notional move (statutory + 2x slippage),
    using the production cost model at the configured reference notional/price."""
    cfg = load_config()
    cm = CostModel(cfg.risk.costs, cfg.risk.slippage)
    # breakeven_pct returns a percent (e.g. 0.14 == 0.14%); convert to a fraction.
    ref_price = 1000.0  # representative liquid-name price; cost is ~scale-invariant in %
    return cm.breakeven_pct(ref_price) / 100.0


@dataclass
class SignalStats:
    n: int
    win_rate: float
    mean_fwd_ret: float          # NET of costs, per-trade fraction
    median_fwd_ret: float        # NET of costs
    gross_mean_fwd_ret: float    # before costs (for diagnostics)
    profit_factor: float         # NET of costs
    sharpe: float                # per-trade NET Sharpe (mean/std), not annualized
    direction: int
    horizon: int
    cost_per_trade: float
    verdict: Optional[EdgeVerdict] = None
    extra: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        d = {
            "n": self.n, "win_rate": round(self.win_rate, 4),
            "mean_fwd_ret_net": round(self.mean_fwd_ret, 6),
            "median_fwd_ret_net": round(self.median_fwd_ret, 6),
            "gross_mean_fwd_ret": round(self.gross_mean_fwd_ret, 6),
            "profit_factor_net": round(self.profit_factor, 4),
            "sharpe_net": round(self.sharpe, 4),
            "direction": self.direction, "horizon": self.horizon,
            "cost_per_trade": round(self.cost_per_trade, 6),
        }
        d.update(self.extra)
        if self.verdict is not None:
            d["edge_passed"] = self.verdict.passed
            d["pbo"] = round(self.verdict.pbo, 4)
        return d


def _stats_from_returns(net: np.ndarray, gross: np.ndarray, direction: int, horizon: int,
                        cost: float, pbo: float) -> SignalStats:
    n = int(net.shape[0])
    if n == 0:
        return SignalStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, direction, horizon, cost,
                           verdict=edge_verdict(0, 0.0, 0.0, pbo))
    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    pf = float(wins / losses) if losses > 0 else (np.inf if wins > 0 else 0.0)
    win_rate = float(np.mean(net > 0))
    sd = float(np.std(net))
    sharpe = float(np.mean(net) / sd) if sd > 0 else 0.0
    return SignalStats(
        n=n, win_rate=win_rate,
        mean_fwd_ret=float(np.mean(net)), median_fwd_ret=float(np.median(net)),
        gross_mean_fwd_ret=float(np.mean(gross)),
        profit_factor=pf, sharpe=sharpe, direction=direction, horizon=horizon,
        cost_per_trade=cost,
        verdict=edge_verdict(n, win_rate, pf if np.isfinite(pf) else 99.0, pbo),
    )


def evaluate_signal(
    df: pd.DataFrame,
    entry_mask: pd.Series,
    direction: int = 1,
    horizon: int = 15,
    cost_per_trade: Optional[float] = None,
    oos_only: bool = True,
    compute_pbo: bool = True,
) -> SignalStats:
    """Net-of-cost evaluation of a candidate signal, gated by :func:`edge_verdict`.

    Parameters
    ----------
    df : the research table (must carry ``_FWD_COL[horizon]``, ``is_oos``, ``is_purged``,
         ``session_date``).
    entry_mask : boolean Series aligned to ``df`` — True where a trade is entered at bar t.
    direction : +1 long, -1 short. The forward return is multiplied by ``direction``.
    horizon : forward-return horizon in {5,15,30,60} minutes.
    cost_per_trade : round-trip cost as a FRACTION of notional move. Defaults to the
         production cost model's break-even (statutory + 2x slippage), applied ONCE per
         round-trip trade (subtracted from the directional gross return).
    oos_only : headline on the OOS slice (the honest number). When False, evaluates all rows.
    compute_pbo : estimate PBO across monthly walk-forward windows (IS vs OOS PF) so the
         verdict's overfit gate is meaningful; otherwise PBO=0.

    Returns a :class:`SignalStats` whose ``verdict`` is the edge gate. Caller should report
    BOTH the OOS stats (headline) and, separately, the IS stats for honesty.
    """
    col = _FWD_COL[horizon]
    if cost_per_trade is None:
        cost_per_trade = _default_breakeven_pct()

    purged = df["is_purged"].to_numpy() if "is_purged" in df else np.zeros(len(df), bool)
    sel = entry_mask.to_numpy() & np.isfinite(df[col].to_numpy()) & ~purged
    if oos_only:
        sel = sel & df["is_oos"].to_numpy()

    gross = direction * df[col].to_numpy()[sel]
    net = gross - cost_per_trade

    pbo = 0.0
    if compute_pbo:
        pbo = _walk_forward_pbo(df, entry_mask, direction, col, cost_per_trade)

    return _stats_from_returns(net, gross, direction, horizon, cost_per_trade, pbo)


def _walk_forward_pbo(df: pd.DataFrame, entry_mask: pd.Series, direction: int,
                      col: str, cost: float) -> float:
    """PBO across monthly windows (IS vs OOS profit factor) via ml.evaluate.estimate_pbo.

    Splits the full sample into calendar-month windows; for each month computes the signal's
    profit factor. Then pairs each window's PF as IS against the NEXT window's PF as OOS
    (walk-forward), and feeds the paired PFs to the canonical PBO estimator. With < 2 paired
    windows it returns 0.0 (not estimable).
    """
    purged = df["is_purged"].to_numpy() if "is_purged" in df else np.zeros(len(df), bool)
    sel = entry_mask.to_numpy() & np.isfinite(df[col].to_numpy()) & ~purged
    if sel.sum() == 0:
        return 0.0
    sub = df.loc[sel, ["session_date"]].copy()
    sub["net"] = direction * df.loc[sel, col].to_numpy() - cost
    month = sub["session_date"].dt.to_period("M")

    pfs: List[float] = []
    for _, g in sub.groupby(month):
        r = g["net"].to_numpy()
        gains = r[r > 0].sum()
        losses = -r[r < 0].sum()
        pfs.append(float(gains / losses) if losses > 0 else (2.0 if gains > 0 else 0.0))
    if len(pfs) < 3:
        return 0.0
    is_pfs = pfs[:-1]
    oos_pfs = pfs[1:]
    return estimate_pbo(is_pfs, oos_pfs)


# Signature reference (printed by --help style introspection / panel docs):
#   evaluate_signal(df, entry_mask, direction=1, horizon=15, cost_per_trade=None,
#                   oos_only=True, compute_pbo=True) -> SignalStats
