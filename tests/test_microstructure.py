"""Hand-verified tests for the microstructure shadow signal (signal_engine/microstructure/).

Every number below is computed by hand in the comments. No network. The signal is PURE
observability — these tests also assert it never claims to gate a trade.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from signal_engine.domain.models import Bar, Tick
from signal_engine.microstructure.collector import MicrostructureCollector
from signal_engine.microstructure.features import compute_bar_signal
from signal_engine.microstructure.scorer import score_day
from signal_engine.microstructure.store import MicrostructureStore

IST = pytz.timezone("Asia/Kolkata")


def _t(sec, ltp, vol, bidq=None, askq=None, bid=None, ask=None):
    return Tick(symbol="ACME", ts=IST.localize(datetime(2026, 7, 16, 9, 15, sec)),
                ltp=ltp, volume=vol, bid=bid, ask=ask, bid_qty=bidq, ask_qty=askq)


def test_volume_delta_tick_rule_quote_mode():
    # QUOTE mode (no depth). Cumulative volume + tick rule:
    #  t0 100 vol0   (first, sign 0, no increment)
    #  t1 101 vol100 uptick(+) inc=100 -> +100
    #  t2 100 vol150 downtick(-) inc=50 -> -50
    #  t3 100 vol250 unchanged -> carry (-) inc=100 -> -100
    #  CVD = 100-50-100 = -50 ; total vol = 250 ; frac = -50/250 = -0.2
    ticks = [_t(0, 100, 0), _t(1, 101, 100), _t(2, 100, 150), _t(3, 100, 250)]
    sig = compute_bar_signal("ACME", ticks[0].ts, ticks)
    assert sig.cvd == 100 - 50 - 100
    assert abs(sig.signed_vol_frac - (-0.2)) < 1e-9
    assert sig.ob_imbalance is None            # no depth in quote mode
    assert abs(sig.dir_score - (-0.2)) < 1e-9  # blend = the only component
    # bar return = (100-100)/100 = 0
    assert sig.bar_ret_pct == 0.0


def test_order_book_imbalance_full_mode():
    # FULL mode adds bid/ask qty. Imbalance per tick = (bidQ-askQ)/(bidQ+askQ):
    #  t1: (900-100)/1000 = +0.8 ; t2: (400-600)/1000 = -0.2 ; mean = +0.3
    # volume-delta: t1 uptick inc100 +100 ; t2 uptick inc100 +100 ; CVD 200/200 = +1.0
    # dir_score = mean(+1.0, +0.3) = +0.65
    ticks = [
        _t(0, 100, 0, bidq=500, askq=500, bid=99.9, ask=100.1),
        _t(1, 101, 100, bidq=900, askq=100, bid=100.9, ask=101.1),
        _t(2, 102, 200, bidq=400, askq=600, bid=101.9, ask=102.1),
    ]
    sig = compute_bar_signal("ACME", ticks[0].ts, ticks)
    # imbalance mean over the 3 ticks: (0 + 0.8 + (-0.2))/3 = 0.2
    assert abs(sig.ob_imbalance - 0.2) < 1e-9
    assert abs(sig.signed_vol_frac - 1.0) < 1e-9
    assert abs(sig.dir_score - (1.0 + 0.2) / 2) < 1e-9
    assert sig.spread_bps is not None and sig.spread_bps > 0


def test_empty_bar_returns_none():
    assert compute_bar_signal("ACME", _t(0, 100, 0).ts, []) is None


def _bar(ts, o, c):
    return Bar(symbol="ACME", ts=ts, open=o, high=max(o, c), low=min(o, c), close=c,
               volume=100, timeframe="1m")


def test_collector_flushes_on_bar_close(tmp_path):
    store = MicrostructureStore(f"sqlite:///{tmp_path/'m.sqlite3'}", run_id="RUN1")
    col = MicrostructureCollector(store)
    m0 = IST.localize(datetime(2026, 7, 16, 9, 15))
    # minute 09:15 ticks (uptrend, net buying)
    for s, ltp, vol in [(0, 100, 0), (10, 101, 100), (30, 102, 200)]:
        col.on_tick(Tick("ACME", m0 + timedelta(seconds=s), ltp, vol))
    # a tick in the NEXT minute rolls the bar; the runner then calls on_bar_close(09:15 bar)
    col.on_tick(Tick("ACME", m0 + timedelta(seconds=61), 103, 250))
    col.on_bar_close(_bar(m0, 100, 102))
    rows = store.fetch_day("2026-07-16")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ACME" and rows[0]["dir_score"] > 0   # net buying -> +
    assert rows[0]["run_id"] == "RUN1"
    # the 09:16 tick is still buffered (its bar hasn't closed) — not double-counted
    assert ("ACME", m0 + timedelta(minutes=1)) in col._buf
    store.close()


def test_scorer_grades_next_bar_direction(tmp_path):
    store = MicrostructureStore(f"sqlite:///{tmp_path/'s.sqlite3'}", run_id="R")
    col = MicrostructureCollector(store)
    base = IST.localize(datetime(2026, 7, 16, 9, 15))
    # bar 1: net BUYING (dir_score +), bar 2: price went UP (+1%) -> signal was RIGHT
    for s, ltp, vol in [(0, 100, 0), (20, 101, 100), (40, 102, 200)]:
        col.on_tick(Tick("ACME", base + timedelta(seconds=s), ltp, vol))
    col.on_bar_close(_bar(base, 100, 102))
    b2 = base + timedelta(minutes=1)
    for s, ltp, vol in [(0, 102, 200), (20, 103, 260), (40, 103, 300)]:
        col.on_tick(Tick("ACME", b2 + timedelta(seconds=s), ltp, vol))
    col.on_bar_close(_bar(b2, 102, 103))   # +0.98% next bar

    res = score_day(store, "2026-07-16")
    assert res["graded"] == 1               # only bar 1 has a "next bar" to grade
    assert res["directional_calls"] == 1
    assert res["hit_rate"] == 1.0           # buy signal -> up next bar -> correct
    rows = {r["bar_ts"]: r for r in store.fetch_day("2026-07-16")}
    assert rows[base.isoformat()]["dir_correct"] == 1
    store.close()


def test_shadow_never_touches_trading_path():
    """The collector exposes no method that returns a trade/plan/score to the engine —
    on_tick/on_bar_close both return None (pure side effects)."""
    col = MicrostructureCollector(store=None)
    assert col.on_tick(_t(0, 100, 0)) is None
    # with store=None the bar-close is a guarded no-op (never raises)
    assert col.on_bar_close(_bar(_t(0, 100, 0).ts, 100, 101)) is None
