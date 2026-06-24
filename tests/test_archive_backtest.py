"""Tests for the real-archive backtest harness (offline via a tmp Parquet store)."""

from __future__ import annotations

import datetime

from signal_engine.backtest.archive import (
    run_archive_backtest,
    run_archive_walkforward,
    variant_cfg,
)
from signal_engine.config import load_config
from signal_engine.data.synthetic import generate_session
from signal_engine.storage.bars import ParquetBarStore


def _seed_store(tmp_path):
    store = ParquetBarStore(str(tmp_path))
    for yr, day in [(2025, datetime.date(2025, 6, 23)), (2026, datetime.date(2026, 6, 23))]:
        for sym in ["RELIANCE", "TCS"]:
            df = generate_session(sym, day, start_price=2000.0, seed=hash((sym, yr)) % 999,
                                  regime="trend_up")
            store.save_symbol_year(sym, yr, df)
    return store


def _seed_many_days(tmp_path, n_days=12):
    """Seed a multi-day corpus (consecutive sessions) so walk-forward windows form.

    ``save_symbol_year`` overwrites the year file, so accumulate all sessions per symbol
    into one frame and save once.
    """
    import pandas as pd

    store = ParquetBarStore(str(tmp_path))
    days = []
    day = datetime.date(2025, 1, 6)  # a Monday
    while len(days) < n_days:
        if day.weekday() < 5:
            days.append(day)
        day += datetime.timedelta(days=1)
    for sym in ["RELIANCE", "TCS"]:
        frames = [generate_session(sym, d, start_price=2000.0,
                                   seed=(i * 7 + hash(sym)) % 9991, regime="trend_up")
                  for i, d in enumerate(days)]
        store.save_symbol_year(sym, 2025, pd.concat(frames).sort_index())
    return store


def test_variant_cfg_overrides_without_mutating_original():
    cfg = load_config()
    base = cfg.risk.risk.atr_stop_multiple
    v = variant_cfg(cfg, atr_stop_multiple=2.5, target_rr=2.0)
    assert v.risk.risk.atr_stop_multiple == 2.5
    assert v.risk.risk.target_rr == 2.0
    assert cfg.risk.risk.atr_stop_multiple == base  # original untouched


def test_run_archive_backtest_returns_metrics(tmp_path):
    cfg = load_config()
    store = _seed_store(tmp_path)
    metrics, ledger = run_archive_backtest(cfg, store, ["RELIANCE", "TCS"], max_sessions=5)
    # trending sessions should produce some trades and standard metric fields
    assert metrics.trades == len(ledger)
    assert hasattr(metrics, "profit_factor") and hasattr(metrics, "win_rate")
    assert metrics.trades >= 0


def test_run_archive_backtest_respects_risk_variant(tmp_path):
    cfg = load_config()
    store = _seed_store(tmp_path)
    # A very strict edge-cost gate + high R:R floor should not error and trades <= baseline.
    strict = variant_cfg(cfg, edge_cost_multiple=20.0, rr_floor=5.0, target_rr=5.0)
    m_strict, _ = run_archive_backtest(strict, store, ["RELIANCE", "TCS"], max_sessions=5)
    m_base, _ = run_archive_backtest(cfg, store, ["RELIANCE", "TCS"], max_sessions=5)
    assert m_strict.trades <= m_base.trades  # stricter gates never trade MORE


def test_only_days_restricts_replay(tmp_path):
    cfg = load_config()
    store = _seed_many_days(tmp_path, n_days=10)
    # Replaying a single known session date must touch only that date's trades.
    only = {datetime.date(2025, 1, 6)}
    m_one, ledger_one = run_archive_backtest(cfg, store, ["RELIANCE", "TCS"],
                                             min_bars=40, only_days=only)
    for p in ledger_one:
        assert p.exit_ts.date() in only


# ----------------------------------------------------- walk-forward (V1)


def test_walkforward_returns_per_window_pfs(tmp_path):
    cfg = load_config()
    store = _seed_many_days(tmp_path, n_days=12)
    wf = run_archive_walkforward(cfg, store, ["RELIANCE", "TCS"],
                                 train_size=4, test_size=2, min_bars=40)
    # 12 days, train 4 + test 2, step=test=2 -> starts 0,2,4,6 -> 4 windows.
    assert len(wf.windows) == 4
    assert len(wf.window_pfs) == len(wf.windows)
    for train_days, test_days, _m in wf.windows:
        assert len(train_days) == 4
        assert len(test_days) == 2
        assert max(train_days) < min(test_days)  # out-of-sample is sacred


def test_walkforward_aggregates_median_and_pct(tmp_path):
    cfg = load_config()
    store = _seed_many_days(tmp_path, n_days=12)
    wf = run_archive_walkforward(cfg, store, ["RELIANCE", "TCS"],
                                 train_size=4, test_size=2, min_bars=40)
    assert 0.0 <= wf.pct_windows_pf_gt_1 <= 1.0
    assert wf.median_pf >= 0.0
    # median_pf must equal the statistical median of the per-window PFs.
    pfs = sorted(wf.window_pfs)
    mid = len(pfs) // 2
    expected = pfs[mid] if len(pfs) % 2 else (pfs[mid - 1] + pfs[mid]) / 2.0
    assert wf.median_pf == expected


def test_walkforward_empty_when_insufficient_days(tmp_path):
    cfg = load_config()
    store = _seed_many_days(tmp_path, n_days=3)
    wf = run_archive_walkforward(cfg, store, ["RELIANCE", "TCS"],
                                 train_size=4, test_size=2, min_bars=40)
    assert wf.windows == []
    assert wf.median_pf == 0.0
    assert wf.pct_windows_pf_gt_1 == 0.0
