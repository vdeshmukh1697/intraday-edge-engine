"""Real global-cues provider backed by Yahoo Finance (yfinance).

This is the live counterpart to ``MockGlobalCuesProvider``: it sources real
overnight / global quotes instead of synthetic values. It implements the
:class:`~signal_engine.premarket.cues.GlobalCuesProvider` interface.

Design notes
------------
* Quotes are fetched through a *dependency-injected* ``quote_fn`` callable so
  the class can be unit-tested fully offline. The default implementation uses
  yfinance and computes the most-recent close-to-close percent change.
* Because yfinance returns the latest available daily bars, the resulting
  percentages are a **daily-resolution proxy** (most-recent close-to-close
  move), NOT an exact pre-open snapshot at a specific instant. The ``day``
  argument to :meth:`get_cues` is accepted for interface compatibility but does
  not pin the quote to that calendar day.
* The default ``quote_fn`` never raises: any error (network, missing data,
  malformed frame) is swallowed and reported as ``0.0`` so the engine degrades
  gracefully rather than crashing pre-open.
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Dict, List, Optional

from signal_engine.premarket.cues import GlobalCuesProvider
from signal_engine.premarket.models import GlobalCues

# --- Yahoo ticker map (module constants) ------------------------------------
US_TICKER = "^GSPC"        # S&P 500 (alt: "ES=F" S&P 500 futures)
ASIA_TICKER = "^N225"      # Nikkei 225

# NOTE: There is no reliable *free* Yahoo Finance ticker for GIFT Nifty
# (the SGX/NSE-IX overnight Nifty future). As a documented stand-in we use the
# Nifty 50 spot index ("^NSEI"). This is only an approximation of the GIFT
# Nifty pre-open signal and SHOULD be replaced with a proper GIFT Nifty feed
# (e.g. NSE-IX data) when one becomes available.
GIFT_NIFTY_TICKER = "^NSEI"  # stand-in: Nifty 50 spot (see note above)

USDINR_TICKER = "INR=X"    # USD/INR FX
BRENT_TICKER = "BZ=F"      # Brent crude futures
GOLD_TICKER = "GC=F"       # Gold futures

# NSE symbol -> Yahoo ADR ticker (only names with a US-listed ADR).
ADR_TICKER_MAP: Dict[str, str] = {
    "INFY": "INFY",       # Infosys
    "ICICIBANK": "IBN",   # ICICI Bank
    "HDFCBANK": "HDB",    # HDFC Bank
    "WIPRO": "WIT",       # Wipro
}


def _yfinance_quote(ticker: str) -> float:
    """Default ``quote_fn``: latest daily % change for ``ticker`` via yfinance.

    Computes ``(close[-1] / close[-2] - 1) * 100`` from a 2-day history. Returns
    ``0.0`` if fewer than 2 rows are available, or on *any* error (never raises).
    """
    try:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(period="2d")
        closes = hist["Close"]
        if len(closes) < 2:
            return 0.0
        prev = float(closes.iloc[-2])
        last = float(closes.iloc[-1])
        if prev == 0.0:
            return 0.0
        return (last / prev - 1.0) * 100.0
    except Exception:
        return 0.0


def _safe_quote(quote_fn: Callable[[str], float], ticker: str) -> float:
    """Call ``quote_fn`` and round to 2dp; any error -> ``0.0`` (resilience)."""
    try:
        value = quote_fn(ticker)
        return round(float(value), 2)
    except Exception:
        return 0.0


class YahooCuesProvider(GlobalCuesProvider):
    """Live global cues sourced from Yahoo Finance.

    Parameters
    ----------
    adr_symbols:
        NSE symbols to fetch ADR moves for. Defaults to every key in
        :data:`ADR_TICKER_MAP`. Symbols without a known ADR ticker are skipped.
    quote_fn:
        Callable ``(ticker) -> float`` returning the latest daily % change for a
        Yahoo ticker. Defaults to a yfinance-backed implementation. This is the
        dependency-injection seam that lets tests run fully offline.
    """

    def __init__(
        self,
        adr_symbols: Optional[List[str]] = None,
        quote_fn: Optional[Callable[[str], float]] = None,
    ) -> None:
        self.adr_symbols: List[str] = (
            list(adr_symbols)
            if adr_symbols is not None
            else list(ADR_TICKER_MAP.keys())
        )
        self.quote_fn: Callable[[str], float] = quote_fn or _yfinance_quote

    def get_cues(self, day: date) -> GlobalCues:
        """Return :class:`GlobalCues` from the latest available quotes.

        ``day`` is accepted for interface compatibility; yfinance returns the
        latest available data, so the result is the most-recent close-to-close
        move (a daily-resolution proxy), not an exact pre-open snapshot.
        """
        adr_moves: Dict[str, float] = {}
        for symbol in self.adr_symbols:
            adr_ticker = ADR_TICKER_MAP.get(symbol)
            if adr_ticker is None:
                continue
            adr_moves[symbol] = _safe_quote(self.quote_fn, adr_ticker)

        return GlobalCues(
            gift_nifty_pct=_safe_quote(self.quote_fn, GIFT_NIFTY_TICKER),
            us_pct=_safe_quote(self.quote_fn, US_TICKER),
            asia_pct=_safe_quote(self.quote_fn, ASIA_TICKER),
            usdinr_pct=_safe_quote(self.quote_fn, USDINR_TICKER),
            brent_pct=_safe_quote(self.quote_fn, BRENT_TICKER),
            gold_pct=_safe_quote(self.quote_fn, GOLD_TICKER),
            adr_moves=adr_moves,
        )
