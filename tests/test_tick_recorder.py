"""Tests for the dependency-light TickRecorder (PLAN B-tick) — fully offline.

Ticks are hand-built; we record, flush, and read the files back. Parquet is exercised when
pandas+a parquet engine is importable; CSV is forced otherwise so the suite always covers a
backend.
"""

from __future__ import annotations

import csv
from datetime import datetime

import pytz

from signal_engine.domain.models import Tick
from signal_engine.ingestion.tick_recorder import COLUMNS, TickRecorder

IST = pytz.timezone("Asia/Kolkata")


def _tick(symbol, h, m, ltp, vol, bid=None, ask=None):
    ts = IST.localize(datetime(2026, 6, 24, h, m, 0))
    return Tick(symbol=symbol, ts=ts, ltp=ltp, volume=vol, bid=bid, ask=ask)


def _read(path):
    """Read a written file back into a list of dict rows, regardless of backend."""
    if path.suffix == ".parquet":
        import pandas as pd

        return pd.read_parquet(path).to_dict("records")
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def test_records_and_flushes_one_file_per_symbol_day(tmp_path):
    rec = TickRecorder(root=str(tmp_path / "ticks"), flush_every=0)
    rec.record(_tick("RELIANCE", 9, 16, 2900.0, 100, bid=2899.5, ask=2900.5), last_qty=10)
    rec.record(_tick("TCS", 9, 16, 3800.0, 200))
    written = rec.flush()
    assert len(written) == 2
    for p in written:
        assert p.exists() and p.name.startswith("ticks.")
    # Partition layout mirrors the bar archive.
    assert any("symbol=RELIANCE" in str(p) and "date=2026-06-24" in str(p) for p in written)


def test_round_trip_values_and_columns(tmp_path):
    rec = TickRecorder(root=str(tmp_path / "ticks"), flush_every=0)
    rec.record(_tick("RELIANCE", 9, 16, 2900.0, 100, bid=2899.5, ask=2900.5), last_qty=42)
    (path,) = rec.flush()
    rows = _read(path)
    assert len(rows) == 1
    row = rows[0]
    assert set(COLUMNS).issubset(set(row.keys()))
    assert str(row["symbol"]) == "RELIANCE"
    assert float(row["ltp"]) == 2900.0
    assert int(float(row["volume"])) == 100
    assert float(row["bid"]) == 2899.5 and float(row["ask"]) == 2900.5
    assert int(float(row["last_qty"])) == 42


def test_append_across_flushes_accumulates_rows(tmp_path):
    rec = TickRecorder(root=str(tmp_path / "ticks"), flush_every=0)
    rec.record(_tick("RELIANCE", 9, 16, 2900.0, 100))
    rec.flush()
    rec.record(_tick("RELIANCE", 9, 17, 2901.0, 150))
    (path,) = rec.flush()
    rows = _read(path)
    assert len(rows) == 2
    assert [float(r["ltp"]) for r in rows] == [2900.0, 2901.0]


def test_auto_flush_on_threshold(tmp_path):
    rec = TickRecorder(root=str(tmp_path / "ticks"), flush_every=2)
    rec.record(_tick("RELIANCE", 9, 16, 2900.0, 100))
    assert rec._buf  # still buffered after 1
    rec.record(_tick("RELIANCE", 9, 17, 2901.0, 150))  # hits threshold -> auto flush
    assert not rec._buf
    (path,) = list((tmp_path / "ticks").rglob("ticks.*"))
    assert len(_read(path)) == 2


def test_context_manager_flushes_on_exit(tmp_path):
    root = tmp_path / "ticks"
    with TickRecorder(root=str(root), flush_every=0) as rec:
        rec.record(_tick("TCS", 9, 16, 3800.0, 200))
    files = list(root.rglob("ticks.*"))
    assert len(files) == 1 and len(_read(files[0])) == 1


def test_csv_backend_forced(tmp_path):
    rec = TickRecorder(root=str(tmp_path / "ticks"), fmt="csv", flush_every=0)
    assert rec.ext == "csv"
    rec.record(_tick("RELIANCE", 9, 16, 2900.0, 100, bid=2899.0, ask=2901.0), last_qty=7)
    (path,) = rec.flush()
    assert path.suffix == ".csv"
    rows = _read(path)
    assert len(rows) == 1 and rows[0]["symbol"] == "RELIANCE"


def test_missing_bid_ask_qty_recorded_as_empty(tmp_path):
    rec = TickRecorder(root=str(tmp_path / "ticks"), fmt="csv", flush_every=0)
    rec.record(_tick("TCS", 9, 16, 3800.0, 200))  # no bid/ask/last_qty
    (path,) = rec.flush()
    row = _read(path)[0]
    assert row["bid"] == "" and row["ask"] == "" and row["last_qty"] == ""
