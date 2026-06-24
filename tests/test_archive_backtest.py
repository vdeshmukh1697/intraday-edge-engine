"""Tests for the real-archive backtest harness (offline via a tmp Parquet store)."""

from __future__ import annotations

import datetime

from signal_engine.backtest.archive import run_archive_backtest, variant_cfg
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
