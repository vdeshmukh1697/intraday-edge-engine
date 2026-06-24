"""Training harness (PLAN §4.7): build labeled data -> GLOBAL DATE split -> train -> compare.

The split is GLOBAL by calendar date across ALL symbols (V0): every sample carries its
entry-bar timestamp ``ts`` and its label-window end ``ts_exit``. Samples with their whole
label window before the cutoff date train the model; samples whose entry is on/after
``cutoff + embargo`` are judged out-of-sample. Any sample whose label window STRADDLES the
cutoff is purged (V2 — interval embargo, López de Prado *Advances in Financial ML*), which
replaces the old "drop 1% of samples by count" heuristic and the contaminated per-index
split that mixed late-list symbols' old bars into the test set. The rules confidence is the
baseline: ML must beat it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from signal_engine.backtest.engine import trading_days
from signal_engine.config import AppConfig
from signal_engine.market.calendar import NSECalendar
from signal_engine.ml.base import FEATURE_COLUMNS, MLModel
from signal_engine.ml.dataset import Dataset, build_dataset, build_dataset_from_archive
from signal_engine.ml.evaluate import compare
from signal_engine.ml.model import default_model

DEFAULT_MODEL_PATH = "data/models/signal_model.json"

# Default embargo gap between train and test, in CALENDAR DAYS. The intraday label window
# is bounded by max_hold_minutes (< 1 trading day), so a 1-day embargo on top of the
# interval purge is comfortably conservative.
DEFAULT_EMBARGO_DAYS = 1


def date_split_indices(
    ts: np.ndarray,
    ts_exit: np.ndarray,
    test_frac: float = 0.3,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
) -> Tuple[np.ndarray, np.ndarray, "np.datetime64"]:
    """Global calendar-date train/test split with interval-based embargo (V0 + V2).

    The cutoff is the unique-date quantile at ``1 - test_frac`` (computed over the set of
    distinct calendar dates so a few high-firing days can't skew the boundary). Then:

      train = {i : ts_exit[i]  <  cutoff}                 (whole label window before cutoff)
      test  = {i : ts[i]       >= cutoff + embargo_days}  (entry on/after the embargoed cutoff)

    A sample whose label interval ``[ts[i], ts_exit[i]]`` straddles the cutoff lands in
    NEITHER set — that is the interval embargo (V2): it shares outcome information with both
    sides, so it is purged rather than leaked. Returns ``(train_idx, test_idx, cutoff)`` as
    integer index arrays plus the cutoff as a ``datetime64[D]``.
    """
    ts = np.asarray(ts, dtype="datetime64[ns]")
    ts_exit = np.asarray(ts_exit, dtype="datetime64[ns]")
    n = ts.shape[0]
    if n == 0:
        empty = np.array([], dtype=int)
        return empty, empty, np.datetime64("NaT")

    days = ts.astype("datetime64[D]")
    unique_days = np.unique(days)
    # Index into the sorted unique-day axis; clamp so both sides stay non-empty when possible.
    k = int(np.floor(len(unique_days) * (1.0 - test_frac)))
    k = min(max(k, 1), max(len(unique_days) - 1, 1))
    cutoff = unique_days[k] if k < len(unique_days) else unique_days[-1]

    embargo = np.timedelta64(int(embargo_days), "D")
    train_idx = np.nonzero(ts_exit.astype("datetime64[D]") < cutoff)[0]
    test_idx = np.nonzero(days >= cutoff + embargo)[0]
    return train_idx, test_idx, cutoff


def temporal_integrity_spearman(idx_order: np.ndarray, ts: np.ndarray) -> float:
    """Spearman rank correlation between sample POSITION and timestamp (V0 post-fix check).

    After a correct global date split there is no single monotone index->time ordering being
    relied on, but within each split the samples should still be near-randomly ordered in
    time relative to their array position (no smuggled chronological structure). Returns the
    Spearman coefficient; ``abs(corr) ~ 0`` is the healthy signal. NaN-safe: returns 0.0 for
    fewer than 2 samples or zero-variance input.
    """
    ts = np.asarray(ts, dtype="datetime64[ns]").astype("int64").astype(float)
    pos = np.asarray(idx_order, dtype=float)
    n = ts.shape[0]
    if n < 2:
        return 0.0
    rank_pos = _rankdata(pos)
    rank_ts = _rankdata(ts)
    sp = np.std(rank_pos)
    st = np.std(rank_ts)
    if sp == 0.0 or st == 0.0:
        return 0.0
    return float(np.corrcoef(rank_pos, rank_ts)[0, 1])


def _rankdata(a: np.ndarray) -> np.ndarray:
    """Average-rank of each element (ties share the mean rank). Small, dependency-free."""
    a = np.asarray(a, dtype=float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1, dtype=float)
    # Average ties.
    sorted_a = a[order]
    i = 0
    n = len(a)
    while i < n:
        j = i
        while j + 1 < n and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        if j > i:
            avg = (i + j) / 2.0 + 1.0
            ranks[order[i:j + 1]] = avg
        i = j + 1
    return ranks


def cv_auc_label_shuffle(
    X: np.ndarray, y: np.ndarray, seed: int = 0
) -> float:
    """Train on a permuted-``y`` control and return the resulting AUC (V0 label-shuffle).

    With labels randomly permuted there is no learnable signal, so a leakage-free pipeline
    must collapse to chance AUC ~[0.48, 0.52]. A value materially above that range is a
    red flag for temporal/feature leakage. Uses the same default model and a simple
    in-order split; ``seed`` controls the permutation for determinism.
    """
    n = len(y)
    if n < 4:
        return 0.5
    rng = np.random.default_rng(seed)
    y_perm = y.copy()
    rng.shuffle(y_perm)
    cut = max(1, int(n * 0.7))
    model = default_model()
    model.fit(X[:cut], y_perm[:cut])
    probs = model.predict_proba(X[cut:])
    from signal_engine.ml.evaluate import evaluate
    return float(evaluate(y_perm[cut:], probs)["auc"])


@dataclass
class TrainReport:
    n_samples: int
    n_train: int
    n_test: int
    base_rate: float
    ml: Dict[str, float] = field(default_factory=dict)
    rules: Dict[str, float] = field(default_factory=dict)
    auc_gain: float = 0.0
    brier_gain: float = 0.0
    importances: Dict[str, float] = field(default_factory=dict)
    model_path: Optional[str] = None
    # V0/V2 diagnostics (additive).
    n_purged: int = 0                       # samples dropped by the interval embargo
    cutoff: Optional[str] = None            # global date-split cutoff (ISO date) or None
    shuffle_auc: Optional[float] = None     # label-shuffle control AUC (must ~0.5)
    spearman_index_ts: Optional[float] = None  # temporal-integrity check (must ~0.0)


def train_model(
    cfg: AppConfig,
    symbols: List[str],
    start: date,
    n_days: int,
    seed: int = 42,
    test_frac: float = 0.3,
    model_path: Optional[str] = DEFAULT_MODEL_PATH,
    min_samples: int = 50,
) -> Tuple[Optional[MLModel], TrainReport]:
    days = trading_days(start, n_days, NSECalendar())
    ds = build_dataset(cfg, symbols, days, seed=seed)
    return _train_on_dataset(ds, test_frac, model_path, min_samples)


def _train_on_dataset(
    ds: Dataset,
    test_frac: float = 0.3,
    model_path: Optional[str] = DEFAULT_MODEL_PATH,
    min_samples: int = 50,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
    embargo_frac: float = 0.01,  # retained for backward-compat (legacy index-split fallback only)
) -> Tuple[Optional[MLModel], TrainReport]:
    """GLOBAL calendar-date out-of-sample split -> fit -> compare vs rules -> save (V0/V2).

    When the dataset carries per-sample timestamps (``ds.ts`` / ``ds.ts_exit``), the split is
    done GLOBALLY by calendar date across all symbols with an interval-based embargo: a sample
    trains only if its whole label window precedes the cutoff, is tested only if its entry is
    on/after ``cutoff + embargo_days``, and is PURGED if its label window straddles the cutoff.
    This replaces the contaminated per-index split (which mixed late-list symbols' old bars
    into the test set) and the count-based ``embargo_frac`` heuristic.

    For backward-compatibility, datasets WITHOUT timestamps (e.g. a hand-built ``Dataset``)
    fall back to the legacy chronological index split + ``embargo_frac`` purge.

    Also runs two leakage controls (reported, non-fatal here so callers can assert/inspect):
    a label-shuffle AUC (should collapse to ~0.5) and the Spearman(index, ts) integrity check
    (should be ~0.0 within each split).
    """
    n = len(ds)
    if n < min_samples:
        return None, TrainReport(n_samples=n, n_train=0, n_test=0, base_rate=0.0)

    has_ts = getattr(ds, "ts", None) is not None and ds.ts.shape[0] == n and n > 0

    if has_ts:
        train_idx, test_idx, cutoff = date_split_indices(
            ds.ts, ds.ts_exit, test_frac=test_frac, embargo_days=embargo_days)
        n_purged = n - (len(train_idx) + len(test_idx))
        if len(train_idx) < 1 or len(test_idx) < 1:
            return None, TrainReport(n_samples=n, n_train=len(train_idx), n_test=len(test_idx),
                                     base_rate=round(float(ds.y.mean()), 4),
                                     n_purged=n_purged, cutoff=str(cutoff))
        Xtr, ytr = ds.X[train_idx], ds.y[train_idx]
        Xte, yte = ds.X[test_idx], ds.y[test_idx]
        rules_te = ds.rules_conf[test_idx]
        n_train = len(train_idx)
        cutoff_str = str(cutoff)
        # V0 post-fix integrity: position-vs-time correlation within the TEST set ~ 0.
        spearman = round(temporal_integrity_spearman(np.arange(len(test_idx)), ds.ts[test_idx]), 4)
    else:
        # Legacy fallback (no per-sample timestamps): chronological index split.
        n_train = max(1, int(n * (1.0 - test_frac)))
        embargo = int(n * embargo_frac)
        tr_end = max(1, n_train - embargo)
        Xtr, ytr = ds.X[:tr_end], ds.y[:tr_end]
        Xte, yte = ds.X[n_train:], ds.y[n_train:]
        rules_te = ds.rules_conf[n_train:]
        n_purged = n_train - tr_end
        cutoff_str = None
        spearman = None

    model = default_model()
    model.fit(Xtr, ytr)
    probs = model.predict_proba(Xte)
    comp = compare(yte, probs, rules_te)

    importances = dict(zip(FEATURE_COLUMNS, model.feature_importance()))

    # Label-shuffle control over the train split — must collapse to chance.
    shuffle_auc = round(cv_auc_label_shuffle(Xtr, ytr), 4)

    if model_path:
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        model.save(model_path)

    return model, TrainReport(
        n_samples=n, n_train=n_train, n_test=len(yte),
        base_rate=round(float(ds.y.mean()), 4),
        ml=comp["ml"], rules=comp["baseline"],
        auc_gain=round(comp["auc_gain"], 4), brier_gain=round(comp["brier_gain"], 4),
        importances={k: round(v, 4) for k, v in importances.items()},
        model_path=model_path,
        n_purged=n_purged, cutoff=cutoff_str,
        shuffle_auc=shuffle_auc, spearman_index_ts=spearman,
    )


def train_model_from_archive(
    cfg: AppConfig,
    store,
    symbols: List[str],
    stride: int = 2,
    max_samples: Optional[int] = 200_000,
    max_per_symbol: Optional[int] = 2_000,
    test_frac: float = 0.3,
    model_path: Optional[str] = DEFAULT_MODEL_PATH,
    min_samples: int = 200,
    log=None,
) -> Tuple[Optional[MLModel], TrainReport]:
    """Train on REAL backfilled bars (the 5-year corpus) instead of synthetic sessions.

    ``max_per_symbol`` keeps the dataset diverse across the whole universe instead of being
    dominated by a handful of high-firing names.
    """
    ds = build_dataset_from_archive(cfg, store, symbols, stride=stride, max_samples=max_samples,
                                    max_per_symbol=max_per_symbol, log=log)
    return _train_on_dataset(ds, test_frac, model_path, min_samples)
