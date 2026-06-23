"""Leaderboard ranking (PLAN §4.9).

Composite score = confidence × R:R × liquidity × catalyst, penalized when the expected
move barely clears round-trip cost. Catalyst is neutral (1.0) until news lands (Phase 4).
All factors are normalized to [0, 1] and the score is scaled to ~[0, 100].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from signal_engine.domain.models import TradePlan
from signal_engine.universe.models import InstrumentMeta

# Normalization caps (tunable).
_RR_CAP = 3.0
_LIQ_CAP_CR = 200.0
_EDGE_GATE = 3.0       # edge-after-cost gate multiple (matches RiskManager default)
_EDGE_FULL = 10.0      # edge ratio at which the cost factor saturates to 1.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def score_plan(plan: TradePlan, turnover_cr: float, catalyst: float = 1.0) -> float:
    conf_norm = _clamp(plan.confidence / 100.0, 0.0, 1.0)
    rr_norm = _clamp(plan.risk_reward / _RR_CAP, 0.0, 1.0)
    liq_norm = _clamp(turnover_cr / _LIQ_CAP_CR, 0.0, 1.0)

    breakeven = max(plan.cost_to_break_even_pct, 1e-9)
    edge_ratio = plan.expected_move_pct / breakeven
    # 0.5 at the gate boundary -> 1.0 once edge is comfortably large.
    cost_factor = 0.5 + 0.5 * _clamp(
        (edge_ratio - _EDGE_GATE) / (_EDGE_FULL - _EDGE_GATE), 0.0, 1.0
    )
    return round(100.0 * conf_norm * rr_norm * liq_norm * catalyst * cost_factor, 3)


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int
    plan: TradePlan
    score: float
    sector: str
    turnover_cr: float

    @property
    def symbol(self) -> str:
        return self.plan.symbol


def rank_plans(
    items: List[Tuple[TradePlan, InstrumentMeta]], top_n: int = 20
) -> List[LeaderboardEntry]:
    """Rank (plan, meta) pairs into a Top-N leaderboard, highest score first.

    Ties break by confidence, then R:R, then symbol — so ordering is deterministic.
    """
    scored = [
        (score_plan(plan, meta.avg_daily_turnover_cr), plan, meta)
        for plan, meta in items
    ]
    scored.sort(
        key=lambda t: (t[0], t[1].confidence, t[1].risk_reward, t[1].symbol),
        reverse=True,
    )
    out: List[LeaderboardEntry] = []
    for rank, (score, plan, meta) in enumerate(scored[:top_n], start=1):
        out.append(
            LeaderboardEntry(
                rank=rank, plan=plan, score=score,
                sector=meta.sector, turnover_cr=meta.avg_daily_turnover_cr,
            )
        )
    return out
