"""Tests for the movers research sleeve (signal_engine/movers/).

Hand-verified on synthetic daily data — no network, no real archive. The calibration is a
tiny hand-built lookup so every probability the predictor quotes is checkable by eye.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from signal_engine.movers import predictor
from signal_engine.movers.sleeve import format_alert, next_trading_day, resolve_predictions
from signal_engine.storage.repository import SignalRepository

CAL = {
    "vol_terciles": [1.0, 2.5],
    "base": {"p_big5": 0.05, "p_big10": 0.005},
    "buckets": {
        # UP-spike streak in a high-vol name — the headline bucket (LONG continuation lean)
        "up10+|high|streak": {"n": 1000, "p_big5": 0.37, "p_big10": 0.08,
                              "p_up_given_big5": 0.58, "p_next_up_big": 0.20,
                              "p_next_down_big": 0.15, "n_big5": 400,
                              "median_bigmove_pct": 8.0, "mean_next_ret_pct": 0.1},
        # UP-spike, single big day (no streak) — lower p_big5, still a LONG lean
        "up10+|high|nostreak": {"n": 1000, "p_big5": 0.25, "p_big10": 0.06,
                                "p_up_given_big5": 0.60, "p_next_up_big": 0.15,
                                "p_next_down_big": 0.10, "n_big5": 300,
                                "median_bigmove_pct": 7.5, "mean_next_ret_pct": 0.2},
        # DOWN-crash streak — SIGN-CONDITIONED and DISTINCT from the up bucket. This synthetic
        # bucket is a coin-flip on purpose, so a down-crash name shows coin-flip / NOT a blind
        # LONG (real dn10+ is a weak dead-cat bounce — the point is it uses ITS OWN measured split).
        "dn10+|high|streak": {"n": 900, "p_big5": 0.41, "p_big10": 0.11,
                              "p_up_given_big5": 0.50, "p_next_up_big": 0.25,
                              "p_next_down_big": 0.16, "n_big5": 360,
                              "median_bigmove_pct": 8.5, "mean_next_ret_pct": -0.1},
        # quiet name — below-base bucket (unsigned small-move band)
        "0-3|low|nostreak": {"n": 5000, "p_big5": 0.018, "p_big10": 0.001,
                             "p_up_given_big5": 0.69, "p_next_up_big": 0.012,
                             "p_next_down_big": 0.006, "n_big5": 90,
                             "median_bigmove_pct": 6.2, "mean_next_ret_pct": 0.02},
    },
}


def _daily(symbol: str, closes, turnover_cr=50.0) -> pd.DataFrame:
    days = [f"2026-07-{d:02d}" for d in range(1, len(closes) + 1)]
    return pd.DataFrame({"symbol": symbol, "day": days, "close": closes,
                         "turnover_cr": turnover_cr})


def test_predictor_streak_bucket_and_labels():
    # WILD: two consecutive ~+12% UP days (streak, |prev|>=10, high vol) -> up10+|high|streak.
    wild = _daily("WILD", [100, 100.5, 99.8, 100.2, 99.7, 100.1, 112, 125.4])
    # QUIET: barely moves -> 0-3|low|nostreak (vol < 1.0 tercile).
    quiet = _daily("QUIET", [100, 100.1, 100.05, 100.12, 100.03, 100.1, 100.06, 100.11])
    daily = pd.concat([wild, quiet], ignore_index=True)

    preds = predictor.build_predictions(daily, CAL, date(2026, 7, 9), top_n=5)
    by_sym = {p["symbol"]: p for p in preds}

    w = by_sym["WILD"]
    assert w["basis"] == "up10+|high|streak"        # SIGNED bucket — up-spike
    assert w["qual_dir"] == "up"
    assert w["p_big5"] == pytest.approx(0.37)
    assert w["lift"] == pytest.approx(0.37 / 0.05)
    assert w["pred_dir"] == "LONG" and w["p_dir"] == pytest.approx(0.58)
    # Direction is an explicit tail split, not just a blended P(up).
    assert w["p_next_up_big"] == pytest.approx(0.20)
    assert w["p_next_down_big"] == pytest.approx(0.15)
    assert "circuit" in w["warn"]          # +12% day -> may be locked
    assert w["rank"] == 1                   # highest probability ranks first (both fillable)

    q = by_sym["QUIET"]
    assert q["basis"] == "0-3|low|nostreak"
    assert q["pred_dir"] == "LONG" and q["p_dir"] == pytest.approx(0.69)
    assert q["warn"] == ""                  # liquid, far from bands


def test_down_streak_is_not_a_blind_long():
    """A down-crash streak must map to a SIGNED dn* bucket and take THAT bucket's direction —
    never the up-pooled 'lean LONG'. Same |move| magnitude, opposite sign, different call."""
    # WILD: two +12% UP days -> up10+|high|streak (LONG 58%).
    wild = _daily("WILD", [100, 100.5, 99.8, 100.2, 99.7, 100.1, 112, 125.4])
    # CRASH: two ~-12/-19% DOWN days -> dn10+|high|streak (coin-flip in this CAL).
    crash = _daily("CRASH", [100, 99.5, 100.2, 99.8, 100.1, 99.7, 88, 71.0])
    preds = predictor.build_predictions(pd.concat([wild, crash], ignore_index=True),
                                        CAL, date(2026, 7, 9), top_n=5)
    by_sym = {p["symbol"]: p for p in preds}

    c = by_sym["CRASH"]
    assert c["basis"] == "dn10+|high|streak"   # SIGN-conditioned onto the DOWN bucket
    assert c["qual_dir"] == "down"
    assert c["pred_dir"] != "LONG"             # <-- the fix: NOT a blind LONG
    assert c["pred_dir"] == "NONE"             # this synthetic dn bucket is a coin-flip
    assert c["p_next_up_big"] == pytest.approx(0.25)   # its OWN measured tail split
    assert c["p_next_down_big"] == pytest.approx(0.16)
    # The up name, same magnitude/streak, still leans LONG — proving it's the SIGN that split them.
    assert by_sym["WILD"]["pred_dir"] == "LONG"


def test_fillable_outranks_higher_prob_unfillable():
    """Fillability is the PRIMARY sort: a tradeable name ranks above a higher-p_big5 un-fillable
    one (un-fillable BNALTD-style circuit-lockers can't be bought and must not rank #1)."""
    # FILL: one +12% UP day -> up10+|high|nostreak (p_big5 0.25), liquid (₹50cr ADV).
    fill = _daily("FILL", [100, 100.5, 99.8, 100.2, 99.7, 100.1, 100.4, 112.0], turnover_cr=50.0)
    # THIN: two +12% UP days -> up10+|high|streak (p_big5 0.37, HIGHER) but un-fillable (₹0.5cr).
    thin = _daily("THIN", [100, 100.5, 99.8, 100.2, 99.7, 100.1, 112, 125.4], turnover_cr=0.5)
    preds = predictor.build_predictions(pd.concat([fill, thin], ignore_index=True),
                                        CAL, date(2026, 7, 9), top_n=5)
    by_sym = {p["symbol"]: p for p in preds}

    assert by_sym["FILL"]["fillable"] == 1 and by_sym["THIN"]["fillable"] == 0
    assert by_sym["THIN"]["p_big5"] > by_sym["FILL"]["p_big5"]   # THIN is the higher probability
    assert by_sym["FILL"]["rank"] == 1                           # ...yet FILL (tradeable) ranks first
    assert by_sym["THIN"]["rank"] == 2


def test_predictor_never_quotes_tiny_buckets():
    # One big UP day then quiet history -> up10+|high|nostreak; here that bucket has n=6 -> skipped.
    tiny_cal = {**CAL, "buckets": {**CAL["buckets"],
                "up10+|high|nostreak": {"n": 6, "p_big5": 0.5, "p_big10": 0.1,
                                        "p_up_given_big5": None, "p_next_up_big": 0.2,
                                        "p_next_down_big": 0.1, "n_big5": 3,
                                        "median_bigmove_pct": 6.7, "mean_next_ret_pct": 0.0}}}
    sym = _daily("TINY", [100, 101.4, 99.6, 101.2, 99.4, 101.0, 99.5, 111.0])
    preds = predictor.build_predictions(sym, tiny_cal, date(2026, 7, 9), top_n=5)
    assert preds == []  # bucket n<100 -> no quote, never a made-up number


def test_predictor_no_lookahead():
    """Rows on/after for_day must not leak into features."""
    wild = _daily("WILD", [100, 100.5, 99.8, 100.2, 99.7, 100.1, 112, 125.4])
    # Add a huge move ON the predicted day — must not change the prediction basis.
    with_leak = pd.concat([wild, pd.DataFrame([{"symbol": "WILD", "day": "2026-07-09",
                                                "close": 250.0, "turnover_cr": 50.0}])],
                          ignore_index=True)
    a = predictor.build_predictions(wild, CAL, date(2026, 7, 9), top_n=5)
    b = predictor.build_predictions(with_leak, CAL, date(2026, 7, 9), top_n=5)
    assert a[0]["basis"] == b[0]["basis"] and a[0]["prev_close"] == b[0]["prev_close"]


class _FakeStore:
    """Session store stub: one day's OHLC per symbol."""

    def __init__(self, sessions):
        self._s = sessions  # {(symbol, iso_day): (open, close)}

    def load_session(self, symbol, day):
        key = (symbol, day.isoformat())
        if key not in self._s:
            return None
        o, c = self._s[key]
        return pd.DataFrame({"open": [o, c], "close": [o, c], "volume": [1, 1]})


def test_resolver_hand_verified(tmp_path):
    repo = SignalRepository(f"sqlite:///{tmp_path/'m.sqlite3'}")
    day = date(2026, 7, 9)
    # Fillable LONG call, prev_close 100: open 104, close 108 -> realized +8% (HIT),
    # dir correct, sleeve pnl = (108-104)/104*100 - 0.15 cost = 3.6962 - 0.15
    repo.save_mover_prediction({
        "for_day": day.isoformat(), "symbol": "WILD", "p_big5": 0.37, "p_big10": 0.08,
        "lift": 7.4, "pred_dir": "LONG", "p_dir": 0.58, "exp_move_pct": 8.0,
        "basis": "up10+|high|streak", "n_bucket": 1000, "rank": 1, "fillable": 1,
        "warn": "", "prev_close": 100.0,
        "p_next_up_big": 0.20, "p_next_down_big": 0.15,   # tail split persists round-trip
    })
    # Un-fillable name: outcome resolves but NO sleeve P&L.
    repo.save_mover_prediction({
        "for_day": day.isoformat(), "symbol": "LOCKED", "p_big5": 0.37, "p_big10": 0.08,
        "lift": 7.4, "pred_dir": "LONG", "p_dir": 0.58, "exp_move_pct": 8.0,
        "basis": "up10+|high|streak", "n_bucket": 1000, "rank": 2, "fillable": 0,
        "warn": "near circuit band", "prev_close": 50.0,
    })
    store = _FakeStore({("WILD", "2026-07-09"): (104.0, 108.0),
                        ("LOCKED", "2026-07-09"): (55.0, 51.0)})
    stats = resolve_predictions(repo, store, day, cost_pct=0.15)
    assert stats == {"resolved": 2, "skipped": 0, "total": 2}

    rows = {r["symbol"]: r for r in repo.fetch_mover_predictions(for_day=day.isoformat())}
    w = rows["WILD"]
    assert w["realized_move_pct"] == pytest.approx(8.0)
    assert w["hit_big5"] == 1 and w["dir_correct"] == 1
    assert w["sleeve_pnl_pct"] == pytest.approx((108 - 104) / 104 * 100 - 0.15)
    assert w["p_next_up_big"] == pytest.approx(0.20)      # new columns survive save/fetch
    assert w["p_next_down_big"] == pytest.approx(0.15)
    lk = rows["LOCKED"]
    assert lk["realized_move_pct"] == pytest.approx(2.0)  # 51 vs prev_close 50
    assert lk["hit_big5"] == 0 and lk["sleeve_pnl_pct"] is None
    # Resolution is idempotent-safe: second run finds nothing unresolved.
    assert resolve_predictions(repo, store, day, 0.15)["total"] == 0
    repo.close()


def test_next_trading_day_skips_weekend():
    class _Cal:
        def is_trading_day(self, d):
            return d.weekday() < 5

    assert next_trading_day(_Cal(), date(2026, 7, 10)) == date(2026, 7, 13)  # Fri -> Mon


def test_alert_format_is_honest():
    # A fillable down-crash name with a LONG lean (must read as a BOUNCE, never a blind LONG)
    # plus an un-fillable circuit-locker that belongs in the research-only section.
    preds = [
        {"for_day": "2026-07-16", "symbol": "HARDWYN", "rank": 1, "p_big5": 0.41,
         "lift": 8.3, "exp_move_pct": 8.5, "pred_dir": "LONG", "p_dir": 0.61,
         "p_next_up_big": 0.25, "p_next_down_big": 0.16, "qual_dir": "down",
         "basis": "dn10+|high|streak", "fillable": 1,
         "warn": "near circuit band — may be un-buyable/un-sellable at open"},
        {"for_day": "2026-07-16", "symbol": "BNALTD", "rank": 2, "p_big5": 0.37,
         "lift": 7.6, "exp_move_pct": 8.0, "pred_dir": "NONE", "p_dir": None,
         "p_next_up_big": 0.20, "p_next_down_big": 0.15, "qual_dir": "up",
         "basis": "up10+|high|nostreak", "fillable": 0,
         "warn": "thin liquidity (ADV ₹0.1cr < ₹5cr)"},
    ]
    msg = format_alert(preds)
    assert "Tradeable" in msg and "HARDWYN" in msg       # fillable leads
    assert "Research only" in msg and "BNALTD" in msg    # un-fillable sectioned off
    assert "bounce" in msg                 # down-day up-lean framed as a bounce, not blind LONG
    assert "coin-flip" in msg              # NONE direction never dressed up
    assert "% up / " in msg and "down" in msg  # explicit tail split surfaced
    assert "un-buyable" in msg             # fillability warning travels
    assert "survivor" in msg               # calibration caveat always present
    assert "Paper only" in msg
