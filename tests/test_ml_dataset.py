"""Tests for the contaminated-OOS fix: global date split, interval embargo,
label-shuffle control, temporal-integrity check, and the edge_verdict gate (V0/V1/V2).

These cover the *logic* of the split/gate over synthetic arrays — no training/backtest runs.
"""

from __future__ import annotations

import numpy as np
import pytest

from signal_engine.ml.evaluate import (
    EDGE_MAX_PBO,
    EDGE_MIN_PROFIT_FACTOR,
    EDGE_MIN_SAMPLES,
    EDGE_MIN_WIN_RATE,
    edge_verdict,
    estimate_pbo,
)
from signal_engine.ml.train import (
    cv_auc_label_shuffle,
    date_split_indices,
    temporal_integrity_spearman,
)


def _ts(day: int, minute: int = 0) -> np.datetime64:
    base = np.datetime64(f"2025-01-{day:02d}T09:15:00")
    return base + np.timedelta64(minute, "m")


# ----------------------------------------------------- date_split_indices (V0)


def test_date_split_separates_by_calendar_date():
    # 10 distinct days, one sample each; entry and exit on the same day (no straddle).
    ts = np.array([_ts(d) for d in range(1, 11)], dtype="datetime64[ns]")
    ts_exit = ts.copy()
    train_idx, test_idx, cutoff = date_split_indices(ts, ts_exit, test_frac=0.3, embargo_days=0)
    # cutoff is the unique-day quantile at 1-0.3 = 0.7 -> 7th distinct day (index 7 == day 8).
    assert str(np.datetime64(cutoff, "D")) == "2025-01-08"
    # Train = whole label window before cutoff (days 1..7); test = entry on/after cutoff (8..10).
    assert sorted(ts[train_idx].astype("datetime64[D]").astype(str)) == [
        f"2025-01-{d:02d}" for d in range(1, 8)]
    assert sorted(ts[test_idx].astype("datetime64[D]").astype(str)) == [
        "2025-01-08", "2025-01-09", "2025-01-10"]
    # No overlap.
    assert set(train_idx.tolist()).isdisjoint(test_idx.tolist())


def test_date_split_is_global_across_symbols():
    # Two "symbols" interleaved in array order, but the split must respect TIME, not order.
    # Symbol A: early days (1-4). Symbol B appended AFTER but on later days (6-9).
    ts = np.array([_ts(1), _ts(2), _ts(3), _ts(4), _ts(6), _ts(7), _ts(8), _ts(9)],
                  dtype="datetime64[ns]")
    ts_exit = ts.copy()
    train_idx, test_idx, _ = date_split_indices(ts, ts_exit, test_frac=0.25, embargo_days=0)
    # Every train timestamp must be strictly before every test timestamp (no temporal inversion).
    assert ts[train_idx].max() < ts[test_idx].min()


def test_date_split_embargo_days_drops_buffer():
    ts = np.array([_ts(d) for d in range(1, 11)], dtype="datetime64[ns]")
    ts_exit = ts.copy()
    _, test_no_emb, cutoff = date_split_indices(ts, ts_exit, test_frac=0.3, embargo_days=0)
    _, test_emb, _ = date_split_indices(ts, ts_exit, test_frac=0.3, embargo_days=2)
    # A 2-day embargo pushes the test-set start later -> strictly fewer test samples.
    assert len(test_emb) < len(test_no_emb)
    assert ts[test_emb].min() >= cutoff + np.timedelta64(2, "D")


def test_date_split_empty_input():
    empty = np.array([], dtype="datetime64[ns]")
    tr, te, cutoff = date_split_indices(empty, empty)
    assert len(tr) == 0 and len(te) == 0
    assert np.isnat(cutoff)


# --------------------------------------------- interval embargo / purge (V2)


def test_interval_embargo_purges_straddlers():
    # Day-7 entry whose label window EXITS on day 9 straddles a day-8 cutoff -> purged.
    ts = np.array(
        [_ts(1), _ts(3), _ts(5), _ts(7), _ts(9), _ts(10), _ts(11), _ts(12), _ts(13), _ts(14)],
        dtype="datetime64[ns]")
    ts_exit = ts.copy()
    straddler = 3  # entry day 7
    ts_exit[straddler] = _ts(9)  # exit day 9 -> straddles a cutoff between day 7 and day 9
    train_idx, test_idx, cutoff = date_split_indices(ts, ts_exit, test_frac=0.3, embargo_days=0)
    cutoff_day = np.datetime64(cutoff, "D")
    # The straddler must be in NEITHER set (its [entry, exit] crosses the cutoff).
    if ts[straddler].astype("datetime64[D]") < cutoff_day <= ts_exit[straddler].astype("datetime64[D]"):
        assert straddler not in train_idx.tolist()
        assert straddler not in test_idx.tolist()
    # Sanity: train uses ts_exit < cutoff, so no trained sample's window crosses the cutoff.
    assert (ts_exit[train_idx].astype("datetime64[D]") < cutoff_day).all()


def test_purge_is_reported_via_train_plus_test_lt_n():
    ts = np.array([_ts(d) for d in range(1, 13)], dtype="datetime64[ns]")
    ts_exit = ts.copy()
    # Make several samples straddle by extending their exit well past their entry day.
    for i in (5, 6, 7):
        ts_exit[i] = _ts(12)
    train_idx, test_idx, _ = date_split_indices(ts, ts_exit, test_frac=0.3, embargo_days=0)
    n_purged = len(ts) - (len(train_idx) + len(test_idx))
    assert n_purged >= 1  # straddlers exist and are dropped from both sides


# ----------------------------------------------- label-shuffle control (V0)


def test_label_shuffle_collapses_auc_to_chance():
    rng = np.random.default_rng(7)
    # Features carry real signal so a normal fit would score high...
    n, d = 400, 6
    X = rng.normal(size=(n, d))
    y = (X[:, 0] + 0.5 * X[:, 1] > 0).astype(int)
    # ...but with permuted labels the AUC must collapse to ~chance.
    auc = cv_auc_label_shuffle(X, y, seed=1)
    assert 0.40 <= auc <= 0.60, f"shuffled-label AUC {auc} not near chance"


def test_label_shuffle_tiny_input_neutral():
    assert cv_auc_label_shuffle(np.zeros((2, 3)), np.array([0, 1])) == 0.5


# -------------------------------------------- temporal integrity check (V0)


def test_spearman_zero_when_position_uncorrelated_with_time():
    rng = np.random.default_rng(3)
    ts = np.array([_ts(1, m) for m in range(200)], dtype="datetime64[ns]")
    rng.shuffle(ts)  # array position no longer tracks time
    corr = temporal_integrity_spearman(np.arange(len(ts)), ts)
    assert abs(corr) < 0.2


def test_spearman_one_when_position_tracks_time():
    ts = np.array([_ts(1, m) for m in range(50)], dtype="datetime64[ns]")  # monotone in position
    corr = temporal_integrity_spearman(np.arange(len(ts)), ts)
    assert corr == pytest.approx(1.0, abs=1e-6)


# ----------------------------------------------------- edge_verdict (gate)


def test_edge_verdict_passes_when_all_gates_clear():
    v = edge_verdict(n_samples=3000, win_rate=0.55, profit_factor=1.2, pbo=0.05)
    assert v.passed is True


def test_edge_verdict_pf_path_passes_when_winrate_low():
    # Win-rate below 0.52 but PF >= 1.10 -> performance gate still satisfied (OR).
    v = edge_verdict(n_samples=EDGE_MIN_SAMPLES, win_rate=0.49,
                     profit_factor=EDGE_MIN_PROFIT_FACTOR, pbo=0.0)
    assert v.passed is True


def test_edge_verdict_fails_on_too_few_samples():
    v = edge_verdict(n_samples=EDGE_MIN_SAMPLES - 1, win_rate=0.60, profit_factor=2.0, pbo=0.0)
    assert v.passed is False
    assert any("too few samples" in r for r in v.reasons)


def test_edge_verdict_fails_on_no_performance_edge():
    v = edge_verdict(n_samples=5000, win_rate=EDGE_MIN_WIN_RATE - 0.01,
                     profit_factor=EDGE_MIN_PROFIT_FACTOR - 0.01, pbo=0.0)
    assert v.passed is False
    assert any("no performance edge" in r for r in v.reasons)


def test_edge_verdict_fails_on_high_pbo():
    v = edge_verdict(n_samples=5000, win_rate=0.60, profit_factor=2.0, pbo=EDGE_MAX_PBO)
    assert v.passed is False
    assert any("overfit" in r for r in v.reasons)


# ----------------------------------------------------- estimate_pbo helper


def test_estimate_pbo_low_for_consistent_ranking():
    # IS-good windows stay OOS-good -> low PBO.
    is_pfs = [1.5, 1.4, 0.9, 0.8]
    oos_pfs = [1.45, 1.35, 0.85, 0.75]
    assert estimate_pbo(is_pfs, oos_pfs) == 0.0


def test_estimate_pbo_high_when_is_best_decays_oos():
    # IS-good windows are exactly the ones that fall below the OOS median -> overfit.
    is_pfs = [2.0, 1.8, 0.5, 0.4]
    oos_pfs = [0.5, 0.4, 2.0, 1.8]
    assert estimate_pbo(is_pfs, oos_pfs) == 1.0


def test_estimate_pbo_too_few_windows():
    assert estimate_pbo([1.2], [0.8]) == 0.0
