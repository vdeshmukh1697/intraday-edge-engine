"""Tests for Angel One SmartAPI adapter (DI — no live creds or SDK calls)."""

import pytest

from signal_engine.brokers.angelone import (
    AngelOneBroker,
    AngelOneInstrumentMaster,
    _df_from_candles,
)

_SCRIP_JSON = """[
  {"exch_seg": "NSE", "symbol": "RELIANCE-EQ", "token": "2885", "lotsize": "1"},
  {"exch_seg": "NSE", "symbol": "INFY-EQ", "token": "1594", "lotsize": "1"},
  {"exch_seg": "BSE", "symbol": "RELIANCE-EQ", "token": "500325", "lotsize": "1"},
  {"exch_seg": "NSE", "symbol": "NIFTY-FUT", "token": "99999", "lotsize": "50"},
  {"exch_seg": "NSE", "symbol": "-EQ", "token": "1", "lotsize": "1"}
]"""


# --- instrument master -------------------------------------------------------

def test_instrument_master_parses_nse_eq():
    m = AngelOneInstrumentMaster.from_json(_SCRIP_JSON)
    assert m.token("RELIANCE") == "2885"
    assert m.token("INFY") == "1594"


def test_instrument_master_excludes_bse_and_non_eq():
    m = AngelOneInstrumentMaster.from_json(_SCRIP_JSON)
    # BSE RELIANCE gets a different token; we only keep NSE
    assert m.token("NIFTY-FUT") is None  # not -EQ
    assert len(m) == 2  # RELIANCE-EQ + INFY-EQ only


def test_instrument_master_case_insensitive():
    m = AngelOneInstrumentMaster.from_json(_SCRIP_JSON)
    assert m.token("reliance") == "2885"


def test_instrument_master_unknown():
    m = AngelOneInstrumentMaster.from_json(_SCRIP_JSON)
    assert m.token("TATASTEEL") is None


# --- candle normalization ----------------------------------------------------

_RAW = [
    ["2026-06-23T09:15:00+05:30", 1300.0, 1305.0, 1299.0, 1303.0, 10000],
    ["2026-06-23T09:16:00+05:30", 1303.0, 1310.0, 1302.0, 1308.0, 12000],
]


def test_df_from_candles_basic():
    bars = _df_from_candles("RELIANCE", _RAW, "1m")
    assert len(bars) == 2
    assert bars[0].symbol == "RELIANCE"
    assert bars[0].open == 1300.0
    assert bars[0].close == 1303.0
    assert bars[0].volume == 10000
    assert bars[0].timeframe == "1m"
    assert str(bars[0].ts.tzinfo) == "Asia/Kolkata"


def test_df_from_candles_empty():
    assert _df_from_candles("RELIANCE", [], "1m") == []


def test_df_from_candles_bad_row_skipped():
    raw = [_RAW[0], ["not-a-date", None, None, None, None, None]]
    bars = _df_from_candles("RELIANCE", raw, "1m")
    assert len(bars) == 1  # bad row silently dropped


# --- broker with DI ----------------------------------------------------------

def _make_instruments():
    return AngelOneInstrumentMaster.from_json(_SCRIP_JSON)


class FakeSmartConnect:
    """Minimal SmartConnect stub for unit tests."""

    def getCandleData(self, params):
        assert params["symboltoken"] == "2885"
        assert params["exchange"] == "NSE"
        return {"status": True, "message": "SUCCESS", "data": _RAW}

    def ltpData(self, exchange, symbol, token):
        return {"status": True, "data": {"ltp": 1303.0, "tradedQty": 5000}}

    def terminateSession(self, client_id):
        pass


def _make_broker() -> AngelOneBroker:
    sc = FakeSmartConnect()
    return AngelOneBroker(
        api_key="key",
        client_id="A123",
        password="pw",
        totp_secret="JBSWY3DPEHPK3PXP",
        instruments=_make_instruments(),
        smart_api_factory=lambda: sc,
    )


def test_broker_connect_requires_all_fields():
    b = AngelOneBroker()
    with pytest.raises(RuntimeError, match="ANGELONE_API_KEY"):
        b.connect()


def test_broker_supports_no_live_orders():
    assert AngelOneBroker.supports_live_orders is False


def test_historical_returns_bars():
    b = _make_broker()
    bars = b.historical("RELIANCE", "1m", None, None)
    assert len(bars) == 2
    assert bars[0].close == 1303.0
    assert bars[1].volume == 12000


def test_historical_unknown_symbol_raises():
    b = _make_broker()
    with pytest.raises(KeyError):
        b.historical("TATASTEEL", "1m", None, None)


def test_quote_returns_tick():
    b = _make_broker()
    ticks = b.quote(["RELIANCE"])
    assert "RELIANCE" in ticks
    assert ticks["RELIANCE"].ltp == pytest.approx(1303.0)
    assert ticks["RELIANCE"].volume == 5000


def test_quote_skips_failed_symbol():
    class ErrorSC(FakeSmartConnect):
        def ltpData(self, *a, **kw):
            raise RuntimeError("network error")

    b = AngelOneBroker(
        api_key="k", client_id="c", password="p", totp_secret="JBSWY3DPEHPK3PXP",
        instruments=_make_instruments(), smart_api_factory=lambda: ErrorSC(),
    )
    ticks = b.quote(["RELIANCE"])
    assert ticks == {}  # failed gracefully
