"""Scan harness — wires a universe + synthetic data into a Scanner run (no live feed).

Pipeline:
  universe -> cheap STATIC liquidity screen (no history needed)
           -> generate intraday history only for survivors, truncated to as-of
           -> Scanner.scan -> ranked leaderboard

The static pre-screen means we generate sessions only for the liquid handful, not all
~2,000 names — fast and faithful ("scan wide, rank narrow"). Deterministic given a seed.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Dict, List, Optional

import numpy as np
import pytz

from signal_engine.config import AppConfig
from signal_engine.data.synthetic import generate_session
from signal_engine.risk.costs import CostModel
from signal_engine.risk.manager import RiskManager
from signal_engine.scan.filter import LiquidityCostFilter
from signal_engine.scan.scanner import Scanner, ScanResult
from signal_engine.state.store import InMemoryStateStore
from signal_engine.strategies.base import create_strategy
from signal_engine.universe.base import UniverseProvider
from signal_engine.universe.models import InstrumentMeta

IST = pytz.timezone("Asia/Kolkata")
_REGIMES = ["trend_up", "trend_down", "choppy"]


def run_scan(
    cfg: AppConfig,
    universe: UniverseProvider,
    day: date,
    as_of: time = time(11, 0),
    seed: int = 42,
    top_n: int = 20,
    with_news: bool = True,
    with_ml: bool = False,
    model_path: Optional[str] = None,
) -> ScanResult:
    cost_model = CostModel(cfg.risk.costs)
    liquidity_filter = LiquidityCostFilter(cfg.risk.liquidity, cost_model)

    metas = universe.instruments()

    # 1) Cheap static screen (no history needed) -> survivors worth deep-scanning.
    survivors: List[InstrumentMeta] = [
        m for m in metas if liquidity_filter.evaluate(m, features=None).tradeable
    ]

    # 2) Generate intraday history for survivors, truncated to the as-of time.
    rng = np.random.default_rng(seed)
    cutoff = IST.localize(datetime.combine(day, as_of))
    histories: Dict[str, "object"] = {}
    for i, meta in enumerate(survivors):
        regime = _REGIMES[int(rng.integers(0, len(_REGIMES)))]
        df = generate_session(
            meta.symbol, day, start_price=meta.last_price, seed=seed + i, regime=regime
        )
        histories[meta.symbol] = df[df.index <= cutoff]

    # 3) News (Phase 4): synthetic headlines -> point-in-time per-symbol features + overlay.
    news_features = None
    news_overlay = None
    if with_news:
        from signal_engine.news.features import compute_news_features
        from signal_engine.news.overlay import NewsOverlay
        from signal_engine.news.provider import MockNewsProvider

        survivor_syms = [m.symbol for m in survivors]
        provider = MockNewsProvider(survivor_syms, day, seed=seed)
        items = provider.fetch(as_of=cutoff)  # point-in-time
        news_features = {
            sym: compute_news_features(items, sym, cutoff) for sym in survivor_syms
        }
        news_overlay = NewsOverlay()

    # 3b) Optional SHADOW ML scorer (loads a trained model if present; never changes ranking).
    ml_scorer = None
    if with_ml:
        from pathlib import Path

        from signal_engine.ml.scorer import MLScorer
        from signal_engine.ml.train import DEFAULT_MODEL_PATH

        path = model_path or DEFAULT_MODEL_PATH
        if Path(path).exists():
            from signal_engine.ml.base import FEATURE_COLUMNS
            from signal_engine.ml.model import LogisticModel

            model = LogisticModel.load(path)
            # Guard against a stale model trained on a different feature schema: if its
            # width doesn't match the current FEATURE_COLUMNS, treat it as "no usable
            # model" (shadow scores stay null) rather than crashing the scan with a
            # broadcast error. Retrain to re-enable shadow ML on the new features.
            if getattr(model, "n_features", len(FEATURE_COLUMNS)) == len(FEATURE_COLUMNS):
                ml_scorer = MLScorer(model)

    # 4) Scan + rank.
    strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
    scanner = Scanner(
        params=dict(cfg.settings.strategy.params),
        strategy=strategy,
        cost_model=cost_model,
        risk_manager=RiskManager(cfg.risk.risk),
        liquidity_filter=liquidity_filter,
        state_store=InMemoryStateStore(),
        news_overlay=news_overlay,
        ml_scorer=ml_scorer,
    )
    result = scanner.scan(survivors, histories, top_n=top_n, news_features=news_features)
    result.universe_size = len(metas)  # report against the FULL universe, not just survivors
    return result


def regime_for(symbol: str, seed: int = 42) -> Optional[str]:
    """Deterministic helper (mainly for tests/inspection)."""
    rng = np.random.default_rng(abs(hash((symbol, seed))) % (2**32))
    return _REGIMES[int(rng.integers(0, len(_REGIMES)))]
