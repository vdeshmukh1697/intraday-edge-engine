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
from signal_engine.indicators import (
    _FRAC_DIFF_D,
    _REGIME_WINDOW,
    _RET_WINDOW,
    bar_shape,
    realized_vol_pct,
    regime_trend,
    short_window_return_pct,
)
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
    # frac_diff (López de Prado expanding window) is causal — frac_diff[t] uses only
    # close[0..t] — so it is safe to vectorize once per session; price-scale it to %.
    close_np = close.to_numpy(dtype=float)
    fd = ind.frac_diff(close, _FRAC_DIFF_D).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        fd_pct = np.where(close_np != 0.0, fd / close_np * 100.0, np.nan)
    return {
        "open": df["open"].to_numpy(dtype=float),
        "high": df["high"].to_numpy(dtype=float),
        "low": df["low"].to_numpy(dtype=float),
        "close": close_np,
        "vwap": ind.vwap(df).to_numpy(),
        "ema_fast": ind.ema(close, ef).to_numpy(),
        "ema_slow": ind.ema(close, es).to_numpy(),
        "rsi": ind.rsi(close, int(params.get("rsi_period", 14))).to_numpy(),
        "adx": ind.adx(df, int(params.get("adx_period", 14))).to_numpy(),
        "atr": ind.atr(df, int(params.get("atr_period", 14))).to_numpy(),
        "rvol": ind.rvol(df["volume"], int(params.get("rvol_lookback", 20))).to_numpy(),
        "frac_diff_close_pct": fd_pct,
    }


def _raw_at(fr: Dict[str, np.ndarray], t: int) -> dict:
    close = fr["close"][t]
    atr = fr["atr"][t]
    adx = fr["adx"][t]
    raw = {
        "close": close,
        "vwap": fr["vwap"][t],
        "ema_fast": fr["ema_fast"][t],
        "ema_slow": fr["ema_slow"][t],
        "ema_fast_prev": fr["ema_fast"][t - 1] if t > 0 else float("nan"),
        "ema_slow_prev": fr["ema_slow"][t - 1] if t > 0 else float("nan"),
        "rsi": fr["rsi"][t],
        "adx": adx,
        "atr": atr,
        "atr_pct": (atr / close * 100.0) if close else float("nan"),
        "rvol": fr["rvol"][t],
        "bar_count": t + 1,
    }
    # Task 1B — point-in-time (only data up to row t): single-bar shape at t, and the
    # trailing window close[: t + 1]. Identical math to the live compute_features path.
    raw.update(bar_shape(fr["open"][t], fr["high"][t], fr["low"][t], close))
    window_close = fr["close"][: t + 1]
    raw["ret_5_pct"] = short_window_return_pct(window_close, _RET_WINDOW)
    raw["rv_5_pct"] = realized_vol_pct(window_close, _RET_WINDOW)
    raw["regime_trend"] = regime_trend(window_close, adx, _REGIME_WINDOW)
    raw["frac_diff_close_pct"] = fr["frac_diff_close_pct"][t]
    return raw


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


def _session_samples(df, sym, strategy, risk, cost, params, max_hold, min_bars, stride,
                     raws: List[dict], labels: List[int], confs: List[float]) -> None:
    """Extract labeled signal samples from ONE session df, appending in place.

    Shared by the synthetic and real-archive builders so both label trades with the exact
    same point-in-time features + pessimistic first-touch rule the paper-trader uses.
    """
    if df is None or len(df) < min_bars + 2:
        return
    fr = _session_feature_frame(df, params)
    ts_index = df.index
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
            _session_samples(df, sym, strategy, risk, cost, params, max_hold, min_bars,
                             stride, raws, labels, confs)

    X = build_matrix(raws)
    return Dataset(X=X, y=np.array(labels, dtype=int),
                   rules_conf=np.array(confs, dtype=float), raws=raws)


def build_dataset_from_archive(
    cfg: AppConfig,
    store,
    symbols: List[str],
    stride: int = 2,
    max_samples: Optional[int] = None,
    max_per_symbol: Optional[int] = None,
    progress_every: int = 100,
    log=None,
) -> Dataset:
    """Build the labeled dataset from the REAL backfilled Parquet corpus (PLAN §4.7).

    Splits each symbol's multi-year history into per-day sessions and runs the same
    signal->plan->first-touch labeling as the synthetic builder. Sessions are processed
    oldest-first so the chronological train/test split stays out-of-sample.

    ``max_per_symbol`` caps each symbol's contribution so a few high-firing names don't
    saturate the total budget — without it the dataset is dominated by ~11 symbols and the
    model never sees the rest of the universe. ``max_samples`` bounds the overall run.
    """
    params = dict(cfg.settings.strategy.params)
    strategy = create_strategy(cfg.settings.strategy.active, params)
    risk = RiskManager(cfg.risk.risk)
    cost = CostModel(cfg.risk.costs)
    max_hold = int(cfg.risk.risk.max_hold_minutes)
    min_bars = 35

    raws: List[dict] = []
    labels: List[int] = []
    confs: List[float] = []

    for i, sym in enumerate(symbols, 1):
        hist = store.load_symbol_history(sym)
        if hist is not None and not hist.empty:
            # Collect this symbol's samples separately so we can cap its contribution evenly.
            s_raw: List[dict] = []
            s_lab: List[int] = []
            s_conf: List[float] = []
            for _day, df in hist.groupby(hist.index.normalize()):  # one session/day, oldest first
                _session_samples(df, sym, strategy, risk, cost, params, max_hold, min_bars,
                                 stride, s_raw, s_lab, s_conf)
                if max_per_symbol and len(s_lab) >= max_per_symbol:
                    break
            if max_per_symbol and len(s_lab) > max_per_symbol:
                s_raw, s_lab, s_conf = (s_raw[:max_per_symbol], s_lab[:max_per_symbol],
                                        s_conf[:max_per_symbol])
            raws.extend(s_raw)
            labels.extend(s_lab)
            confs.extend(s_conf)
        if log and (i % progress_every == 0 or i == len(symbols)):
            log.info("ml dataset: %d/%d symbols, %d labeled signals", i, len(symbols), len(labels))
        if max_samples and len(labels) >= max_samples:
            if log:
                log.info("ml dataset: hit max_samples=%d at symbol %d/%d", max_samples, i, len(symbols))
            break

    X = build_matrix(raws)
    return Dataset(X=X, y=np.array(labels, dtype=int),
                   rules_conf=np.array(confs, dtype=float), raws=raws)
