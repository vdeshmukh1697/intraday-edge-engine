"""SWING signal-evaluation helper (additive; panel-only).

Mirror of :mod:`signal_engine.research.probe` but for the DAILY/swing dataset and DELIVERY
costs. Every swing scan reports through :func:`evaluate_swing_signal` so verdicts are
comparable: same NET-OF-DELIVERY-COST accounting (round-trip delivery charges + optional
slippage from :mod:`signal_engine.research.delivery_costs`), same metrics, and the same
:func:`signal_engine.ml.evaluate.edge_verdict` gate. The OOS number is the headline; the IS
number is reported alongside (never IS alone).

LONG-ONLY vs LONG-SHORT
-----------------------
``evaluate_swing_signal`` evaluates a single directional leg (``direction=+1`` long, or
``-1`` short) on a per-(symbol,day) entry mask — long-only is directly implementable in cash.
``evaluate_long_short`` builds a CROSS-SECTIONAL daily long-short from a ranking score (top
quantile long, bottom quantile short), nets BOTH legs of delivery cost, and ALSO returns the
LONG-ONLY leg standalone. Because India forbids carrying a short cash-equity position
overnight, the short leg is FLAGGED as requiring a single-stock / index FUTURES (or options)
short, and its cost uses :func:`delivery_costs.futures_short_leg_pct` for that leg. The
long-only standalone result is the honest implementable headline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from signal_engine.ml.evaluate import EdgeVerdict, edge_verdict, estimate_pbo
from signal_engine.research.delivery_costs import (
    DEFAULT_REFERENCE_NOTIONAL,
    delivery_breakeven_pct,
    futures_short_leg_pct,
)

# Forward-return label column per supported horizon (see research.swing_dataset).
_FWD_COL = {1: "fwd_ret_1", 2: "fwd_ret_2", 5: "fwd_ret_5", 10: "fwd_ret_10", 20: "fwd_ret_20"}


def default_delivery_cost(reference_notional: float = DEFAULT_REFERENCE_NOTIONAL,
                          slippage_pct_per_side: float = 0.03) -> float:
    """Round-trip DELIVERY break-even as a FRACTION of notional (statutory + slippage).

    Defaults include 3 bps/side slippage (an honest implementable number for liquid daily
    swing fills). Pass ``slippage_pct_per_side=0.0`` for the pure statutory contract-note %.
    """
    return delivery_breakeven_pct(reference_notional=reference_notional,
                                  slippage_pct_per_side=slippage_pct_per_side)


@dataclass
class SwingStats:
    n: int
    win_rate: float
    mean_fwd_ret: float          # NET of delivery costs, per-trade fraction
    median_fwd_ret: float        # NET
    gross_mean_fwd_ret: float    # before costs (diagnostic)
    profit_factor: float         # NET
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
                        cost: float, pbo: float) -> SwingStats:
    n = int(net.shape[0])
    if n == 0:
        return SwingStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, direction, horizon, cost,
                          verdict=edge_verdict(0, 0.0, 0.0, pbo))
    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    pf = float(wins / losses) if losses > 0 else (np.inf if wins > 0 else 0.0)
    win_rate = float(np.mean(net > 0))
    sd = float(np.std(net))
    sharpe = float(np.mean(net) / sd) if sd > 0 else 0.0
    return SwingStats(
        n=n, win_rate=win_rate,
        mean_fwd_ret=float(np.mean(net)), median_fwd_ret=float(np.median(net)),
        gross_mean_fwd_ret=float(np.mean(gross)),
        profit_factor=pf, sharpe=sharpe, direction=direction, horizon=horizon,
        cost_per_trade=cost,
        verdict=edge_verdict(n, win_rate, pf if np.isfinite(pf) else 99.0, pbo),
    )


def evaluate_swing_signal(
    df: pd.DataFrame,
    entry_mask: pd.Series,
    direction: int = 1,
    horizon: int = 5,
    cost_per_trade: Optional[float] = None,
    oos_only: bool = True,
    compute_pbo: bool = True,
) -> SwingStats:
    """Net-of-DELIVERY-cost evaluation of a single-leg swing signal, gated by edge_verdict.

    Parameters
    ----------
    df : the swing research table (carries ``_FWD_COL[horizon]``, ``is_oos``, ``is_purged``,
         ``session_date``).
    entry_mask : boolean Series aligned to ``df`` — True where a position is opened at day d.
    direction : +1 long, -1 short (short = NOT cash-implementable overnight; caller must flag
         and use a futures cost via ``cost_per_trade``).
    horizon : forward-return horizon in TRADING DAYS in {1,2,5,10,20}.
    cost_per_trade : round-trip cost as a FRACTION of notional, subtracted ONCE per trade.
         Defaults to the DELIVERY break-even (statutory + 3 bps/side slippage).
    oos_only : headline on the OOS slice. When False, evaluates all rows (use for the IS line).
    compute_pbo : monthly walk-forward PBO (IS vs OOS PF) for the overfit gate.
    """
    col = _FWD_COL[horizon]
    if cost_per_trade is None:
        cost_per_trade = default_delivery_cost()

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
    """PBO across monthly windows (IS vs OOS profit factor) via ml.evaluate.estimate_pbo."""
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
    return estimate_pbo(pfs[:-1], pfs[1:])


@dataclass
class LongShortResult:
    """Cross-sectional long-short evaluation. The long-only leg is the implementable headline;
    the short leg and the combined L/S are FLAGGED as requiring a futures/options short."""

    long_only: SwingStats
    short_only: SwingStats
    long_short: SwingStats
    short_implementable: bool = False  # always False in India cash; needs futures/options
    note: str = ("Short leg NOT implementable in cash overnight in India; requires single-"
                 "stock/index FUTURES or OPTIONS (own cost+roll). Long-only is the headline.")

    def as_dict(self) -> Dict[str, object]:
        return {
            "long_only": self.long_only.as_dict(),
            "short_only": self.short_only.as_dict(),
            "long_short_combined": self.long_short.as_dict(),
            "short_implementable": self.short_implementable,
            "note": self.note,
        }


def evaluate_long_short(
    df: pd.DataFrame,
    score: pd.Series,
    horizon: int = 5,
    top_q: float = 0.2,
    bottom_q: float = 0.2,
    oos_only: bool = True,
    long_cost: Optional[float] = None,
    short_cost: Optional[float] = None,
    compute_pbo: bool = True,
) -> LongShortResult:
    """Cross-sectional daily LONG-SHORT from a ranking ``score`` (higher == more bullish).

    Each session date, rank the eligible (non-purged, finite-label) names by ``score``; go LONG
    the top ``top_q`` quantile and SHORT the bottom ``bottom_q`` quantile. Each leg is evaluated
    net of its own round-trip cost: the long leg uses DELIVERY cost; the SHORT leg uses the
    FUTURES proxy (``futures_short_leg_pct``) because cash shorts can't be held overnight.

    Returns a :class:`LongShortResult` with the long-only leg (implementable headline), the
    short-only leg (futures-cost, flagged), and the combined long-short.
    """
    col = _FWD_COL[horizon]
    if long_cost is None:
        long_cost = default_delivery_cost()
    if short_cost is None:
        short_cost = futures_short_leg_pct()

    purged = df["is_purged"].to_numpy() if "is_purged" in df else np.zeros(len(df), bool)
    valid = np.isfinite(df[col].to_numpy()) & np.isfinite(score.to_numpy()) & ~purged
    if oos_only:
        valid = valid & df["is_oos"].to_numpy()

    sub = df.loc[valid, ["session_date", col]].copy()
    sub["score"] = score.to_numpy()[valid]

    long_mask = pd.Series(False, index=df.index)
    short_mask = pd.Series(False, index=df.index)
    for _, g in sub.groupby("session_date"):
        if len(g) < 5:  # need enough cross-section to form quantiles
            continue
        hi = g["score"].quantile(1.0 - top_q)
        lo = g["score"].quantile(bottom_q)
        long_idx = g.index[g["score"] >= hi]
        short_idx = g.index[g["score"] <= lo]
        long_mask.loc[long_idx] = True
        short_mask.loc[short_idx] = True

    long_only = evaluate_swing_signal(df, long_mask, direction=1, horizon=horizon,
                                      cost_per_trade=long_cost, oos_only=oos_only,
                                      compute_pbo=compute_pbo)
    short_only = evaluate_swing_signal(df, short_mask, direction=-1, horizon=horizon,
                                       cost_per_trade=short_cost, oos_only=oos_only,
                                       compute_pbo=compute_pbo)

    # Combined: stack the two legs' net returns into one distribution.
    gross_long = df.loc[long_mask.to_numpy() & valid, col].to_numpy()
    gross_short = -df.loc[short_mask.to_numpy() & valid, col].to_numpy()
    net_long = gross_long - long_cost
    net_short = gross_short - short_cost
    net = np.concatenate([net_long, net_short])
    gross = np.concatenate([gross_long, gross_short])
    ls = _stats_from_returns(net, gross, direction=0, horizon=horizon,
                             cost=(long_cost + short_cost) / 2.0, pbo=long_only.verdict.pbo)

    return LongShortResult(long_only=long_only, short_only=short_only, long_short=ls)


# Signature reference (panel docs):
#   evaluate_swing_signal(df, entry_mask, direction=1, horizon=5, cost_per_trade=None,
#                         oos_only=True, compute_pbo=True) -> SwingStats
#   evaluate_long_short(df, score, horizon=5, top_q=0.2, bottom_q=0.2, oos_only=True,
#                       long_cost=None, short_cost=None, compute_pbo=True) -> LongShortResult
