"""Scanner — the scan lane (PLAN §4.0): full universe -> filter -> strategy -> risk -> rank.

Snapshot model: given each symbol's bar history up to an as-of time, compute features,
apply the liquidity+cost filter, run the strategy, gate through risk, and rank survivors
into a Top-N leaderboard. The SAME indicator/strategy/risk core as the live engine and the
backtester — only the driver differs.

Structured so the per-symbol inner loop can later be swapped for a vectorized (Polars)
batch without changing the contract (deferred perf work; see PROGRESS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from signal_engine.domain.enums import MarketState
from signal_engine.indicators import compute_features
from signal_engine.risk.costs import CostModel
from signal_engine.risk.manager import RiskManager
from signal_engine.scan.filter import LiquidityCostFilter
from signal_engine.scan.ranking import LeaderboardEntry, rank_plans
from signal_engine.state.store import StateStore
from signal_engine.strategies.base import Strategy, StrategyContext
from signal_engine.universe.models import InstrumentMeta

_MIN_BARS = 35  # need enough history for ADX(14)/EMA(21)/RVOL(20) to be non-NaN


@dataclass
class ScanResult:
    leaderboard: List[LeaderboardEntry] = field(default_factory=list)
    universe_size: int = 0
    deep_scanned: int = 0      # symbols with enough history that we evaluated
    filtered_out: int = 0      # failed liquidity/cost filter
    no_signal: int = 0         # strategy produced nothing
    vetoed: int = 0            # risk layer rejected (R:R / edge-after-cost)
    news_vetoed: int = 0       # news overlay vetoed / event-guarded a signal
    candidates: int = 0        # surfaced trade plans (pre-Top-N)
    ml_confidence: Dict[str, float] = field(default_factory=dict)  # shadow ML conf per symbol


class Scanner:
    def __init__(
        self,
        params: Dict[str, float],
        strategy: Strategy,
        cost_model: CostModel,
        risk_manager: RiskManager,
        liquidity_filter: LiquidityCostFilter,
        state_store: Optional[StateStore] = None,
        news_overlay=None,
        ml_scorer=None,
    ):
        self.params = params
        self.strategy = strategy
        self.cost_model = cost_model
        self.risk_manager = risk_manager
        self.filter = liquidity_filter
        self.state = state_store
        self.news_overlay = news_overlay
        self.ml_scorer = ml_scorer  # SHADOW only — never changes ranking decisions

    def scan(
        self,
        metas: List[InstrumentMeta],
        histories: Dict[str, pd.DataFrame],
        top_n: int = 20,
        news_features: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> ScanResult:
        result = ScanResult(universe_size=len(metas))
        candidates = []  # list[(plan, meta)]

        for meta in metas:
            df = histories.get(meta.symbol)
            if df is None or len(df) < _MIN_BARS:
                continue
            result.deep_scanned += 1

            features = compute_features(df, self.params)
            # Merge in this symbol's news features (point-in-time) so they're visible to
            # the strategy/overlay and stored alongside technical features.
            nf = (news_features or {}).get(meta.symbol, {})
            if nf:
                features = {**features, **nf}
            if self.state is not None:
                self.state.set_features(meta.symbol, features)

            fr = self.filter.evaluate(meta, features)
            if not fr.tradeable:
                result.filtered_out += 1
                continue

            ts = df.index[-1]
            ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            ctx = StrategyContext(
                symbol=meta.symbol, ts=ts, features=features, bars=df,
                session_state=MarketState.OPEN, params=self.params,
            )
            signal = self.strategy.on_bar(ctx)
            if signal is None:
                result.no_signal += 1
                continue

            # News overlay: gate / boost / veto using the symbol's news features.
            if self.news_overlay is not None and nf:
                signal = self.news_overlay.apply(signal, nf)
                if signal is None:
                    result.news_vetoed += 1
                    continue

            plan = self.risk_manager.build_trade_plan(signal, features, self.cost_model)
            if plan is None:
                result.vetoed += 1
                continue

            # SHADOW ML: record an ML confidence alongside rules — does NOT change ranking.
            if self.ml_scorer is not None:
                from signal_engine.ml.features import vectorize

                result.ml_confidence[meta.symbol] = self.ml_scorer.score_one(vectorize(features))

            candidates.append((plan, meta))

        result.candidates = len(candidates)
        result.leaderboard = rank_plans(candidates, top_n)
        if self.state is not None:
            self.state.set_leaderboard(result.leaderboard)
        return result
