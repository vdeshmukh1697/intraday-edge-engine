"""Real NSE cash-equity universe provider (PLAN §3.3/§4.0) — free, no broker feed.

The canonical list of NSE-listed equities is published by the exchange as a plain CSV
(``EQUITY_L.csv``) — ~2,000 ``SERIES == EQ`` names, no auth. We load that for the symbol
set, then enrich each name with a cheap **real** liquidity snapshot (last price +
average daily turnover) from one batched daily-bar sweep (``yahoo_batch``). That feeds
the static liquidity screen so the scan stays "scan wide, rank narrow" on real data —
without pulling intraday history for all ~2,000 names.

Network access (the NSE CSV and the daily-bar fetch) is injectable, so tests run offline.
"""

from __future__ import annotations

import csv
import io
from typing import Callable, Dict, List, Optional

from signal_engine.obs.logging_setup import get_logger
from signal_engine.universe.base import UniverseProvider
from signal_engine.universe.models import InstrumentMeta

log = get_logger(__name__)

# NSE's official equity master (no auth; needs a browser-like User-Agent).
NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

CsvFetch = Callable[[], str]
MetricsFetch = Callable[[List[str]], Dict[str, Dict[str, float]]]


def _default_csv_fetch() -> str:
    import urllib.request

    req = urllib.request.Request(
        NSE_EQUITY_LIST_URL, headers={"User-Agent": "Mozilla/5.0 (intraday-signal-engine)"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def load_nse_equity_symbols(csv_fetch: Optional[CsvFetch] = None) -> List[str]:
    """Return all ``SERIES == EQ`` NSE symbols from the official equity master."""
    text = (csv_fetch or _default_csv_fetch)()
    syms: List[str] = []
    for row in csv.DictReader(io.StringIO(text)):
        # Header names carry leading spaces in NSE's CSV (e.g. ' SERIES').
        series = (row.get(" SERIES") or row.get("SERIES") or "").strip().upper()
        sym = (row.get("SYMBOL") or "").strip().upper()
        if sym and series == "EQ":
            syms.append(sym)
    return syms


def _estimate_spread_pct(turnover_cr: float) -> float:
    """Spread proxy: liquid names trade tight. Decreasing in turnover, clamped."""
    if turnover_cr <= 0:
        return 5.0  # unknown / no data -> treat as wide (fails the screen)
    return max(0.01, min(2.0, 0.02 + 1.5 / turnover_cr))


class NSEUniverseProvider(UniverseProvider):
    """Full NSE cash-equity universe with real per-symbol liquidity metadata.

    Symbols with no liquidity data (fetch miss) get zeroed metrics so the static
    screen rejects them — best-effort coverage, never a crash.
    """

    def __init__(self, symbols: List[str], metrics: Dict[str, Dict[str, float]],
                 sector: str = "NSE") -> None:
        self._symbols = [s.upper() for s in symbols]
        self._metrics = metrics
        self._sector = sector

    def instruments(self) -> List[InstrumentMeta]:
        metas: List[InstrumentMeta] = []
        for sym in self._symbols:
            m = self._metrics.get(sym, {})
            turnover = float(m.get("avg_daily_turnover_cr", 0.0))
            metas.append(InstrumentMeta(
                symbol=sym,
                sector=self._sector,
                avg_daily_turnover_cr=turnover,
                last_price=float(m.get("last_price", 0.0)),
                est_spread_pct=_estimate_spread_pct(turnover),
            ))
        return metas

    @classmethod
    def build(
        cls,
        limit: Optional[int] = None,
        csv_fetch: Optional[CsvFetch] = None,
        metrics_fetch: Optional[MetricsFetch] = None,
    ) -> "NSEUniverseProvider":
        """Fetch the live NSE list + real liquidity snapshot. ``limit`` caps the count."""
        symbols = load_nse_equity_symbols(csv_fetch)
        if limit is not None:
            symbols = symbols[:limit]
        log.info("NSE universe: %d EQ symbols", len(symbols))
        if metrics_fetch is None:
            from signal_engine.data.yahoo_batch import fetch_daily_metrics
            metrics_fetch = fetch_daily_metrics
        metrics = metrics_fetch(symbols)
        log.info("NSE universe: liquidity snapshot for %d/%d symbols", len(metrics), len(symbols))
        return cls(symbols, metrics)
