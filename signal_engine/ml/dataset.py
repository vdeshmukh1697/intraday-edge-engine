"""Labeled-dataset builder for the ML scorer (PLAN §4.7).

For each historical signal the rules strategy would fire, record:
  - the feature dict AT the signal bar (point-in-time: only past bars), and
  - the binary label "good trade" = did price reach T1 before the stop (forward outcome),
    computed by first-touch over subsequent bars using the SAME pessimistic stop-first rule
    as the paper-trader, and
  - the rules confidence (so training can be compared against the rules baseline).

Features use only past bars; the label uses future bars — that's the correct supervised
setup (lookahead only matters for *features*, never the label).

Indicators are computed vectorized once per session for speed, then sliced per bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import numpy as np

from signal_engine.config import AppConfig
from signal_engine.data.synthetic import generate_session
from signal_engine.domain.enums import Direction
from signal_engine.indicators import core as ind
from signal_engine.ml.features import build_matrix
from signal_engine.risk.costs import CostModel
from signal_engine.risk.manager import RiskManager
from signal_engine.strategies.base import StrategyContext, create_strategy

_REGIMES = ["trend_up", "trend_down", "choppy"]


@dataclass
class Dataset:
    X: np.ndarray                       # (n, n_features)
    y: np.ndarray                       # (n,) binary
    rules_conf: np.ndarray              # (n,) rules confidence / 100 (baseline prob)
    raws: List[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.y)


def _session_feature_frame(df, params) -> Dict[str, np.ndarray]:
    """Vectorize all indicators for the session once (arrays aligned to df rows)."""
    close = df["close"]
    ef = int(params.get("ema_fast", 9))
    es = int(params.get("ema_slow", 21))
    return {
        "close": close.to_numpy(),
        "vwap": ind.vwap(df).to_numpy(),
        "ema_fast": ind.ema(close, ef).to_numpy(),
        "ema_slow": ind.ema(close, es).to_numpy(),
        "rsi": ind.rsi(close, int(params.get("rsi_period", 14))).to_numpy(),
        "adx": ind.adx(df, int(params.get("adx_period", 14))).to_numpy(),
        "atr": ind.atr(df, int(params.get("atr_period", 14))).to_numpy(),
        "rvol": ind.rvol(df["volume"], int(params.get("rvol_lookback", 20))).to_numpy(),
    }


def _raw_at(fr: Dict[str, np.ndarray], t: int) -> dict:
    close = fr["close"][t]
    atr = fr["atr"][t]
    return {
        "close": close,
        "vwap": fr["vwap"][t],
        "ema_fast": fr["ema_fast"][t],
        "ema_slow": fr["ema_slow"][t],
        "ema_fast_prev": fr["ema_fast"][t - 1] if t > 0 else float("nan"),
        "ema_slow_prev": fr["ema_slow"][t - 1] if t > 0 else float("nan"),
        "rsi": fr["rsi"][t],
        "adx": fr["adx"][t],
        "atr": atr,
        "atr_pct": (atr / close * 100.0) if close else float("nan"),
        "rvol": fr["rvol"][t],
        "bar_count": t + 1,
    }


def _label(df, entry_idx: int, direction: Direction, stop: float, target: float,
           max_hold: int) -> Optional[int]:
    """First-touch label from the NEXT bar onward. Pessimistic: stop before target
    within the same bar. 1 if T1 reached before stop, else 0. None if no future bars."""
    n = len(df)
    if entry_idx + 1 >= n:
        return None
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    end = min(n, entry_idx + 1 + max_hold)
    for k in range(entry_idx + 1, end):
        if direction == Direction.LONG:
            if lows[k] <= stop:
                return 0
            if highs[k] >= target:
                return 1
        else:
            if highs[k] >= stop:
                return 0
            if lows[k] <= target:
                return 1
    return 0  # never hit target within the window -> not a "good trade"


def build_dataset(
    cfg: AppConfig,
    symbols: List[str],
    days: List[date],
    seed: int = 42,
    stride: int = 1,
) -> Dataset:
    params = dict(cfg.settings.strategy.params)
    strategy = create_strategy(cfg.settings.strategy.active, params)
    risk = RiskManager(cfg.risk.risk)
    cost = CostModel(cfg.risk.costs)
    max_hold = int(cfg.risk.risk.max_hold_minutes)
    min_bars = 35

    raws: List[dict] = []
    labels: List[int] = []
    confs: List[float] = []

    for di, day in enumerate(days):
        for si, sym in enumerate(symbols):
            regime = _REGIMES[(di + si) % len(_REGIMES)]
            df = generate_session(sym, day, start_price=1000.0 + 50 * si,
                                  seed=seed + di * 100 + si, regime=regime)
            fr = _session_feature_frame(df, params)
            ts_index = df.index
            # leave room for at least one forward bar for labelling
            for t in range(min_bars, len(df) - 1, stride):
                raw = _raw_at(fr, t)
                if raw["atr"] != raw["atr"] or raw["adx"] != raw["adx"]:  # NaN guard
                    continue
                ctx = StrategyContext(symbol=sym, ts=ts_index[t].to_pydatetime(),
                                      features=raw, bars=df.iloc[: t + 1], params=params)
                signal = strategy.on_bar(ctx)
                if signal is None:
                    continue
                plan = risk.build_trade_plan(signal, raw, cost)
                if plan is None:
                    continue
                label = _label(df, t, plan.direction, plan.stop_loss, plan.t1, max_hold)
                if label is None:
                    continue
                raws.append(raw)
                labels.append(label)
                confs.append(signal.confidence / 100.0)

    X = build_matrix(raws)
    return Dataset(X=X, y=np.array(labels, dtype=int),
                   rules_conf=np.array(confs, dtype=float), raws=raws)
