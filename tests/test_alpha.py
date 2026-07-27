"""Hand-verified tests for alpha-vs-NIFTY resolution (P3.2, STRATEGY_IMPROVEMENT_PLAN_2026-07).

No network: index closes are synthetic pandas Series; the DB is the repo's real schema
in a temp file so the UPDATE path is exercised end-to-end.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytz

from signal_engine.analytics.alpha import (
    alpha_pct, index_return_pct, resolve_all, resolve_day,
)
from signal_engine.domain.enums import Direction, ExitReason, PositionStatus
from signal_engine.domain.models import PaperPosition, TradePlan
from signal_engine.storage.repository import SignalRepository

IST = pytz.timezone("Asia/Kolkata")
TOL = 1e-9
DAY = date(2026, 7, 6)


def _ts(hour, minute):
    return IST.localize(datetime(2026, 7, 6, hour, minute))


def _closes():
    # 09:30 -> 100.0, 09:45 -> 101.0, 10:00 -> 99.0 : +1% then -2% legs, hand-computable.
    idx = pd.DatetimeIndex([_ts(9, 30), _ts(9, 45), _ts(10, 0)])
    return pd.Series([100.0, 101.0, 99.0], index=idx)


def test_index_return_asof_semantics():
    closes = _closes()
    # entry 09:40 anchors on the 09:30 bar (100), exit 09:50 on the 09:45 bar (101) -> +1%.
    assert abs(index_return_pct(closes, _ts(9, 40), _ts(9, 50)) - 1.0) < TOL
    # entry before the first bar -> no anchor -> None.
    assert index_return_pct(closes, _ts(9, 0), _ts(9, 50)) is None
    # empty series -> None.
    assert index_return_pct(pd.Series(dtype=float), _ts(9, 40), _ts(9, 50)) is None


def test_alpha_direction_sign():
    # LONG riding a +1% tape with +1.5% gross: alpha = 0.5 (tide subtracted).
    assert abs(alpha_pct(1.5, "LONG", 1.0) - 0.5) < TOL
    # SHORT gaining +1.5% gross while the index FELL 1%: tide gave it +1, alpha = 0.5.
    assert abs(alpha_pct(1.5, "SHORT", -1.0) - 0.5) < TOL
    # SHORT fighting a rising tape: gross +0.5 against +1% index -> alpha = +1.5.
    assert abs(alpha_pct(0.5, "SHORT", 1.0) - 1.5) < TOL


def _position(pos_id, direction, entry_fill, exit_fill, gross=None):
    plan = TradePlan(
        symbol="ACME", ts=_ts(9, 35), direction=direction, strategy="t",
        entry=entry_fill, stop_loss=entry_fill * 0.99, stop_pct=1.0,
        targets=[entry_fill * 1.02], target_pcts=[2.0], expected_move_pct=2.0,
        risk_reward=2.0, cost_to_break_even_pct=0.1, confidence=80.0,
        reasons=[], time_validity=None,
    )
    pos = PaperPosition(
        id=pos_id, plan=plan, status=PositionStatus.CLOSED,
        entry_fill=entry_fill, entry_ts=_ts(9, 40), exit_fill=exit_fill, exit_ts=_ts(9, 50),
        exit_reason=ExitReason.TARGET, pnl_pct_net=1.0, r_multiple=1.0,
        hold_minutes=10.0, won=True,
    )
    pos.pnl_pct_gross = gross
    pos.cost_pct = None if gross is None else 0.1
    return pos


def test_resolve_day_updates_alpha_and_recovers_legacy_gross(tmp_path):
    db = str(tmp_path / "t.sqlite3")
    repo = SignalRepository(f"sqlite:///{db}")
    # LONG with persisted gross +2.0; index +1% over the window -> alpha 1.0.
    repo.save_position(_position("p1", Direction.LONG, 100.0, 102.0, gross=2.0))
    # Legacy SHORT with NULL gross: fills 100 -> 99 => gross = +1.0; alpha = 1.0 - (-1)*1.0 = 2.0.
    repo.save_position(_position("p2", Direction.SHORT, 100.0, 99.0, gross=None))
    repo.close()

    import sqlite3

    conn = sqlite3.connect(db)
    updated, skipped = resolve_day(conn, DAY, _closes())
    assert (updated, skipped) == (2, 0)
    rows = {r[0]: r for r in conn.execute(
        "SELECT id, nifty_ret_pct, alpha_pct FROM paper_trades")}
    conn.close()
    assert abs(rows["p1"][1] - 1.0) < TOL
    assert abs(rows["p1"][2] - 1.0) < TOL
    assert abs(rows["p2"][1] - 1.0) < TOL
    assert abs(rows["p2"][2] - 2.0) < TOL


def test_resolve_all_is_idempotent_and_self_healing(tmp_path):
    db = str(tmp_path / "t.sqlite3")
    repo = SignalRepository(f"sqlite:///{db}")
    repo.save_position(_position("p1", Direction.LONG, 100.0, 102.0, gross=2.0))
    repo.close()

    calls = []

    def fake_fetch(day):
        calls.append(day)
        return _closes()

    out = resolve_all(db, fetch=fake_fetch)
    assert out == {DAY.isoformat(): (1, 0)}
    # Second run: nothing unresolved -> no fetch at all.
    out2 = resolve_all(db, fetch=fake_fetch)
    assert out2 == {}
    assert calls == [DAY]

    # Failed fetch leaves the row NULL for the next run (self-healing), reported as (0, -1).
    repo = SignalRepository(f"sqlite:///{db}")
    repo.save_position(_position("p3", Direction.LONG, 100.0, 101.0, gross=1.0))
    repo.close()

    def boom(day):
        raise RuntimeError("yahoo down")

    assert resolve_all(db, fetch=boom) == {DAY.isoformat(): (0, -1)}
    assert resolve_all(db, fetch=fake_fetch) == {DAY.isoformat(): (1, 0)}
