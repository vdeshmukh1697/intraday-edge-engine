"""Tests for the plain-English reason module (portfolio-manager contract §5/§10).

The strategy reason-code list is DERIVED, not hand-copied: we drive the real
``VwapEmaAdxStrategy`` over feature sets that exercise every reasons-emitting branch
(long/short × fresh-cross/aligned) and collect ``Signal.reasons``. If the strategy
grows a new code, the coverage test fails and ``alerts/plain.py`` must learn it.
"""

from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest
import pytz

from signal_engine.alerts import plain
from signal_engine.strategies.base import StrategyContext
from signal_engine.strategies.vwap_ema_adx import VwapEmaAdxStrategy

IST = pytz.timezone("Asia/Kolkata")
_TS = IST.localize(datetime(2025, 6, 23, 10, 0))


def _ctx(features):
    return StrategyContext(symbol="X", ts=_TS, features=features, bars=pd.DataFrame())


_LONG_FRESH = {
    "close": 105.0, "vwap": 100.0,
    "ema_fast": 104.0, "ema_slow": 102.0,
    "ema_fast_prev": 101.0, "ema_slow_prev": 102.0,   # fresh cross up
    "rsi": 55.0, "adx": 30.0, "atr": 1.0, "atr_pct": 0.95, "rvol": 2.0,
    "bar_count": 60,
}
_LONG_ALIGNED = {**_LONG_FRESH, "ema_fast_prev": 103.0}   # aligned, no fresh cross
_SHORT_FRESH = {
    "close": 95.0, "vwap": 100.0,
    "ema_fast": 96.0, "ema_slow": 98.0,
    "ema_fast_prev": 99.0, "ema_slow_prev": 98.0,     # fresh cross down
    "rsi": 45.0, "adx": 28.0, "atr": 1.0, "atr_pct": 0.95, "rvol": 1.8,
    "bar_count": 60,
}
_SHORT_ALIGNED = {**_SHORT_FRESH, "ema_fast_prev": 97.0}  # aligned, no fresh cross


def _collect_strategy_codes():
    strategy = VwapEmaAdxStrategy()
    codes = []
    for features in (_LONG_FRESH, _LONG_ALIGNED, _SHORT_FRESH, _SHORT_ALIGNED):
        signal = strategy.on_bar(_ctx(features))
        assert signal is not None and signal.reasons
        for code in signal.reasons:
            if code not in codes:
                codes.append(code)
    return codes


STRATEGY_CODES = _collect_strategy_codes()


# --------------------------------------------------------------------------- #
# translate_reasons
# --------------------------------------------------------------------------- #
def test_collected_codes_cover_the_strategy_vocabulary():
    # The literal codes in strategies/vwap_ema_adx.py::_score — if this fails the
    # derivation above no longer exercises every branch (or the strategy changed).
    assert {"above VWAP", "below VWAP", "EMA cross up", "EMA cross down",
            "EMA fast>slow", "EMA fast<slow"} <= set(STRATEGY_CODES)
    for family in ("ADX ", "RVOL ", "RSI "):
        assert any(code.startswith(family) for code in STRATEGY_CODES)


@pytest.mark.parametrize("code", STRATEGY_CODES)
def test_every_strategy_code_translates(code):
    out = plain.translate_reasons([code])
    assert isinstance(out, str) and out.strip()
    assert code not in out                      # raw codes are never echoed
    # Indicator names stay out of the prose. ("VWAP" alone is allowed as the one
    # teaching parenthetical in the contract example: "day-average (VWAP)".)
    for jargon in ("ADX", "RSI", "RVOL", "EMA"):
        assert jargon not in out


def test_full_reason_list_reads_as_one_clause():
    out = plain.translate_reasons(STRATEGY_CODES)
    assert out.strip() and not out.endswith(".")   # embeddable clause, not a sentence
    for code in STRATEGY_CODES:
        assert code not in out


def test_unknown_code_falls_back_to_readable_generic():
    out = plain.translate_reasons(["ema9>ema21"])
    assert out.strip()
    assert "ema9" not in out and ">" not in out    # never echo raw codes
    mixed = plain.translate_reasons(["above VWAP", "brand_new_code_42"])
    assert "brand_new_code_42" not in mixed
    assert "day-average" in mixed                  # known code still translated


def test_empty_reasons_yield_generic_setup_sentence():
    for empty in (None, [], ["", None]):
        out = plain.translate_reasons(empty)
        assert out.strip() and "setup" in out


def test_translate_reasons_never_raises():
    for bad in (None, [], [None, 42, ""], "not-a-list", object(), [object()]):
        out = plain.translate_reasons(bad)
        assert isinstance(out, str) and out.strip()


def test_premarket_and_scan_driver_codes_translate():
    # Vocabulary emitted by premarket/scoring.py (drivers, setups, catalysts).
    drivers = ["GIFT +0.50%", "US +0.20%", "Asia -0.10%", "news +0.50",
               "ADR +1.2%", "index +0.35%", "prevday -2.10%",
               "reversal", "momentum", "gap-up momentum", "gap-down momentum",
               "EARNINGS (+ve news)", "DOWNGRADE (-ve news)", "global cues"]
    for code in drivers:
        out = plain.translate_reasons([code])
        assert out.strip() and code not in out, code


# --------------------------------------------------------------------------- #
# ₹ formatting (en-IN grouping)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value,expected", [
    (1000, "₹1,000"),
    (100000, "₹1,00,000"),
    (1234567.89, "₹12,34,568"),   # last 3 then pairs; no decimals above ₹100
])
def test_inr_indian_grouping(value, expected):
    assert plain._inr(value) == expected


def test_inr_edge_cases():
    assert plain._inr(0) == "₹0"
    assert plain._inr(99.5) == "₹99.5"        # paise kept below ₹100
    assert plain._inr(-1000) == "-₹1,000"
    assert plain._inr(None) == "₹0"           # degenerate inputs never raise
    assert plain._inr(float("nan")) == "₹0"


# --------------------------------------------------------------------------- #
# explain_entry
# --------------------------------------------------------------------------- #
def _plan(**overrides):
    base = dict(
        symbol="RELIANCE", direction="LONG", entry=2850.0, stop_loss=2827.0,
        targets=[2893.0], reasons=["above VWAP", "EMA fast>slow", "ADX 30"],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_explain_entry_matches_contract_example_shape():
    out = plain.explain_entry(_plan(), 17, 48450.0, 100000.0)
    for fragment in ("RELIANCE", "trending up", "day-average (VWAP)",
                     "17 shares", "~₹48,450", "48% of the portfolio",
                     "risking about ₹391",           # 17 × ₹23 stop distance
                     "exit at ₹2,827 if it drops", "book profit near ₹2,893"):
        assert fragment in out
    assert "Why:" not in out                   # the call site adds the prefix
    assert "ADX" not in out and "EMA" not in out


def test_explain_entry_qty_zero_describes_plan_only():
    out = plain.explain_entry(_plan(), 0, None, None)
    assert out.strip() and "0 share" not in out
    assert "would" in out                      # plan framing, never a promise
    assert "Buying" not in out


def test_explain_entry_short_direction_wording():
    out = plain.explain_entry(
        _plan(direction="SHORT", reasons=["below VWAP"]), 5, 14250.0, 100000.0)
    assert "trending down" in out and "5 shares short" in out and "if it rises" in out


def test_explain_entry_accepts_enum_like_direction():
    out = plain.explain_entry(
        _plan(direction=SimpleNamespace(value="LONG")), 1, 2850.0, 100000.0)
    assert "trending up" in out and "1 share" in out and "1 shares" not in out


# --------------------------------------------------------------------------- #
# explain_exit
# --------------------------------------------------------------------------- #
def test_explain_exit_matches_contract_example_shape():
    pos = SimpleNamespace(symbol="NTPC", direction="LONG", exit_reason="TARGET")
    money = {"qty": 120, "notional": 43000.0, "charges_inr": 43.0,
             "pnl_inr": 612.0, "equity_after": 100569.0, "cash_after": 100569.0}
    out = plain.explain_exit(pos, money)
    assert out.startswith("Sold NTPC's 120 shares at the profit target.")
    for fragment in ("Made ₹612", "after ₹43 charges",
                     "portfolio is now ₹1,00,569", "(cash ₹1,00,569)"):
        assert fragment in out


def test_explain_exit_short_stop_loss_wording():
    pos = SimpleNamespace(symbol="TCS", direction="SHORT", exit_reason="STOP")
    out = plain.explain_exit(pos, {"qty": 10, "pnl_inr": -520.0, "charges_inr": 41.0})
    assert out.startswith("Bought back TCS's 10 shares")
    assert "Lost ₹520" in out and "after ₹41 charges" in out


def test_explain_exit_accepts_enum_like_fields_and_none_money():
    pos = SimpleNamespace(symbol="INFY",
                          direction=SimpleNamespace(value="LONG"),
                          exit_reason=SimpleNamespace(value="TIME_STOP"))
    out = plain.explain_exit(pos, None)
    assert "INFY" in out and out.strip().endswith(".")
    assert "TIME_STOP" not in out              # enum codes never leak into prose


# --------------------------------------------------------------------------- #
# explain_skip / explain_halt
# --------------------------------------------------------------------------- #
def test_explain_skip_matches_contract_example_exactly():
    out = plain.explain_skip(SimpleNamespace(symbol="MRF"), 122340.0, 51550.0)
    assert out == ("Found a setup on MRF but one share costs ₹1,22,340 and only "
                   "₹51,550 cash is free, so the paper portfolio sits this one out.")


def test_explain_halt_translates_breaker_reasons():
    # Exact shapes emitted by engine/runner.py::_LossBreaker.
    drawdown = "daily drawdown limit hit (drawdown 4.10% from peak +3.00% >= 4.00%)"
    out = plain.explain_halt(drawdown, -1.2)
    assert "4.10%" in out and "4.00%" in out and "no new trades" in out.lower()
    assert "Today's paper result so far: -1.20%." in out
    assert "daily drawdown limit hit" not in out   # raw reason never echoed

    out2 = plain.explain_halt("3 consecutive losses >= 3", None)
    assert "3 losing trades in a row" in out2
    assert "consecutive" not in out2


# --------------------------------------------------------------------------- #
# explain_premarket / explain_scan_pick
# --------------------------------------------------------------------------- #
def test_explain_premarket_matches_contract_example_shape():
    outlook = SimpleNamespace(expected_gap_pct=0.45, gap_bias="GAP_UP",
                              risk_tone="RISK_ON", drivers=["GIFT +0.50%"])
    pick = SimpleNamespace(symbol="INFY", bias="LONG",
                           notional=22000.0, portfolio_equity=100000.0)
    out = plain.explain_premarket(outlook, pick)
    assert "Market looks slightly positive at open (global cues up)." in out
    assert ("Best idea: INFY long — if taken, the plan would put about ₹22,000 of "
            "the ₹1,00,000 book on it.") in out
    assert "money moves only when the live engine actually enters" in out


def test_explain_premarket_flat_and_pickless_morning():
    outlook = SimpleNamespace(expected_gap_pct=0.05, gap_bias="FLAT",
                              risk_tone="NEUTRAL", drivers=[])
    out = plain.explain_premarket(outlook, None)
    assert "flat" in out and "global cues mixed" in out
    assert "live engine" in out


def test_explain_scan_pick_risk_reward_phrasing():
    pick = SimpleNamespace(symbol="TCS", direction="SHORT", entry=4000.0,
                           stop_loss=4040.0, targets=[3920.0],
                           reasons=["below VWAP", "EMA cross down", "ADX 28"])
    out = plain.explain_scan_pick(pick)
    assert "TCS" in out and "short" in out
    assert "risk about ₹40 a share to make about ₹80" in out
    assert "Suggestion only" in out and "live engine" in out


# --------------------------------------------------------------------------- #
# Degenerate inputs + honesty framing
# --------------------------------------------------------------------------- #
def test_every_explain_function_survives_degenerate_inputs():
    blank = SimpleNamespace()   # object with no fields at all
    calls = [
        (plain.explain_entry, (None, None, None, None)),
        (plain.explain_entry, (blank, 0, None, None)),
        (plain.explain_exit, (None, None)),
        (plain.explain_exit, (blank, {})),
        (plain.explain_skip, (None, None, None)),
        (plain.explain_halt, (None, None)),
        (plain.explain_halt, ("", 0.0)),
        (plain.explain_premarket, (None, None)),
        (plain.explain_premarket, (blank, blank)),
        (plain.explain_scan_pick, (None,)),
        (plain.explain_scan_pick, (blank,)),
        (plain.translate_reasons, (None,)),
    ]
    for fn, args in calls:
        out = fn(*args)
        assert isinstance(out, str) and out.strip(), fn.__name__


def test_prose_never_uses_win_rate_or_promises():
    outputs = [
        plain.explain_entry(_plan(), 17, 48450.0, 100000.0),
        plain.explain_exit(SimpleNamespace(symbol="NTPC", direction="LONG",
                                           exit_reason="TARGET"),
                           {"qty": 120, "pnl_inr": 612.0, "charges_inr": 43.0}),
        plain.explain_premarket(None, None),
        plain.explain_scan_pick(_plan()),
        plain.explain_halt("3 consecutive losses >= 3", -2.0),
    ]
    for out in outputs:
        low = out.lower()
        assert "win-rate" not in low and "win rate" not in low
        assert "guaranteed" not in low and "sure profit" not in low
