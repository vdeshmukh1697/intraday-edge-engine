"""Hand-verified tests for pre-market gap/bias scoring (PLAN §4.8, Phase 5)."""

from __future__ import annotations

from signal_engine.domain.enums import Direction
from signal_engine.premarket.models import GapBias, GlobalCues, RiskTone
from signal_engine.premarket.scoring import index_outlook, stock_bias

TOL = 1e-9


# --------------------------------------------------------------------------- #
# index_outlook
# --------------------------------------------------------------------------- #
def test_index_outlook_gap_up():
    # round(0.6*0.5 + 0.25*0.8 + 0.15*0.4, 3) = round(0.3 + 0.2 + 0.06, 3) = 0.56
    cues = GlobalCues(gift_nifty_pct=0.5, us_pct=0.8, asia_pct=0.4)
    out = index_outlook(cues)
    assert abs(out.expected_gap_pct - 0.56) < TOL
    assert out.gap_bias is GapBias.GAP_UP
    assert out.risk_tone is RiskTone.RISK_ON
    assert out.drivers == ["GIFT +0.50%", "US +0.80%", "Asia +0.40%"]


def test_index_outlook_flat():
    cues = GlobalCues(gift_nifty_pct=0.01, us_pct=0.0, asia_pct=-0.01)
    out = index_outlook(cues)
    # round(0.6*0.01 + 0 + 0.15*(-0.01), 3) = round(0.006 - 0.0015, 3) = 0.005
    assert abs(out.expected_gap_pct - 0.005) < TOL
    assert out.gap_bias is GapBias.FLAT
    assert out.risk_tone is RiskTone.NEUTRAL


def test_index_outlook_gap_down():
    cues = GlobalCues(gift_nifty_pct=-0.6, us_pct=-0.4, asia_pct=-0.2)
    out = index_outlook(cues)
    # round(0.6*-0.6 + 0.25*-0.4 + 0.15*-0.2, 3) = round(-0.36 - 0.1 - 0.03, 3) = -0.49
    assert abs(out.expected_gap_pct - (-0.49)) < TOL
    assert out.gap_bias is GapBias.GAP_DOWN
    assert out.risk_tone is RiskTone.RISK_OFF


def test_index_outlook_threshold_boundary():
    # expected exactly at +threshold -> not strictly greater -> FLAT/NEUTRAL
    cues = GlobalCues(gift_nifty_pct=0.5)  # 0.6*0.5 = 0.30
    out = index_outlook(cues)
    assert abs(out.expected_gap_pct - 0.3) < TOL
    assert out.gap_bias is GapBias.FLAT
    assert out.risk_tone is RiskTone.NEUTRAL


# --------------------------------------------------------------------------- #
# stock_bias
# --------------------------------------------------------------------------- #
def test_stock_bias_strong_long():
    pick = stock_bias(
        "RELIANCE",
        news_sentiment_avg=0.8,
        adr_move_pct=1.0,
        index_gap_pct=0.5,
        prev_return_pct=2.0,
    )
    assert pick is not None
    # s=0.8, adr_n=0.5, idx_n=0.5, mom_n=2/3
    # score = 0.45*0.8 + 0.25*0.5 + 0.15*0.5 + 0.15*(2/3)
    #       = 0.36 + 0.125 + 0.075 + 0.1 = 0.66
    assert abs(pick.score - 0.66) < TOL
    assert pick.bias is Direction.LONG
    # confidence = round(min(100, 0.66*120), 1) = round(79.2, 1) = 79.2
    assert abs(pick.confidence - 79.2) < TOL
    # setup: index_gap 0.5 >= 0.3 and LONG with positive gap -> gap-up momentum
    assert pick.setup == "gap-up momentum"
    # expected_gap_pct = round(0.5*0.5 + 0.4*1.0 + 0.6*0.8, 2)
    #                  = round(0.25 + 0.4 + 0.48, 2) = round(1.13, 2) = 1.13
    assert abs(pick.expected_gap_pct - 1.13) < TOL


def test_stock_bias_reversal():
    pick = stock_bias(
        "INFY",
        news_sentiment_avg=-0.7,
        news_event_type="EARNINGS",
        prev_return_pct=2.0,
    )
    assert pick is not None
    # score = 0.45*(-0.7) + 0.15*(2/3) = -0.315 + 0.1 = -0.215
    assert abs(pick.score - (-0.215)) < TOL
    assert pick.bias is Direction.SHORT
    # news -ve (sign -1) vs prior +ve (sign +1), high-impact EARNINGS -> reversal
    assert pick.setup == "reversal"


def test_stock_bias_deadzone_returns_none():
    pick = stock_bias(
        "TCS",
        news_sentiment_avg=0.05,
        adr_move_pct=0.05,
        index_gap_pct=0.05,
        prev_return_pct=0.05,
    )
    assert pick is None


def test_stock_bias_catalyst_news():
    pick = stock_bias(
        "BHEL",
        news_sentiment_avg=0.8,
        news_event_type="ORDER_WIN",
    )
    assert pick is not None
    assert "ORDER_WIN" in pick.catalyst
    assert "+ve news" in pick.catalyst


def test_stock_bias_catalyst_adr_clears_deadzone():
    pick = stock_bias("WIT", adr_move_pct=2.0)
    assert pick is not None
    # score = 0.25 * 1.0 = 0.25, LONG; no news event, |ADR| >= 0.5
    assert pick.catalyst == "ADR +2.0%"
    assert pick.bias is Direction.LONG


def test_stock_bias_catalyst_global_cues():
    # Index gap alone clears the dead zone, no news, ADR below 0.5.
    pick = stock_bias("HDFCBANK", index_gap_pct=1.0)
    assert pick is not None
    # score = 0.15 * 1.0 = 0.15, not < deadzone (0.15) -> actionable
    assert abs(pick.score - 0.15) < TOL
    assert pick.catalyst == "global cues"


def test_stock_bias_drivers_format():
    pick = stock_bias(
        "SBIN",
        news_sentiment_avg=0.5,
        adr_move_pct=1.2,
        index_gap_pct=0.4,
        prev_return_pct=-0.6,
    )
    assert pick is not None
    assert pick.drivers == [
        "news +0.50",
        "ADR +1.2%",
        "index +0.40%",
        "prevday -0.60%",
    ]
