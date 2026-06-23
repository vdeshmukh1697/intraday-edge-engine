"""Dhan instrument-master loader (PLAN §3.2/§3.5).

Dhan's API addresses instruments by numeric ``security_id`` + ``exchange_segment``, NOT by
ticker symbol — so to request data for "RELIANCE" we must map it via Dhan's scrip-master CSV
(published free, no auth). This loads that CSV into a symbol -> InstrumentRef map.

The CSV's exact headers vary by Dhan's "detailed" vs "compact" master; column names are
configurable with sensible defaults. Parsing is defensive — unknown rows are skipped.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Dict, Optional

# Dhan publishes the compact scrip master here (no auth). Verify the URL/format against
# current Dhan docs before relying on it in production.
DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Default column names (Dhan compact master). Override via load_* kwargs if the CSV differs.
_COL_SYMBOL = "SEM_TRADING_SYMBOL"
_COL_SECURITY_ID = "SEM_SMST_SECURITY_ID"
_COL_EXCH = "SEM_EXM_EXCH_ID"
_COL_SEGMENT = "SEM_SEGMENT"


@dataclass(frozen=True)
class InstrumentRef:
    symbol: str
    security_id: str
    exchange_segment: str   # e.g. "NSE_EQ"


class DhanInstrumentMaster:
    """symbol -> InstrumentRef lookup for NSE equity (extendable to other segments)."""

    def __init__(self, refs: Dict[str, InstrumentRef]):
        self._refs = refs

    def __len__(self) -> int:
        return len(self._refs)

    def ref(self, symbol: str) -> Optional[InstrumentRef]:
        return self._refs.get(symbol.upper())

    def security_id(self, symbol: str) -> Optional[str]:
        r = self.ref(symbol)
        return r.security_id if r else None

    def symbols(self):
        return list(self._refs.keys())

    # --- constructors ------------------------------------------------------
    @classmethod
    def from_csv_text(
        cls,
        text: str,
        exchange: str = "NSE",
        segment: str = "E",  # 'E' = equity in Dhan's compact master
        col_symbol: str = _COL_SYMBOL,
        col_security_id: str = _COL_SECURITY_ID,
        col_exch: str = _COL_EXCH,
        col_segment: str = _COL_SEGMENT,
    ) -> "DhanInstrumentMaster":
        refs: Dict[str, InstrumentRef] = {}
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            try:
                if col_exch in row and row.get(col_exch) and row[col_exch].strip().upper() != exchange:
                    continue
                if col_segment in row and segment and row.get(col_segment, "").strip().upper() not in (segment, "EQUITY", "E"):
                    continue
                sym = (row.get(col_symbol) or "").strip().upper()
                sid = (row.get(col_security_id) or "").strip()
                if not sym or not sid:
                    continue
                refs[sym] = InstrumentRef(symbol=sym, security_id=sid,
                                          exchange_segment=f"{exchange}_EQ")
            except Exception:  # noqa: BLE001 - skip malformed rows, never crash
                continue
        return cls(refs)

    @classmethod
    def from_file(cls, path: str, **kw) -> "DhanInstrumentMaster":
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return cls.from_csv_text(fh.read(), **kw)

    @classmethod
    def fetch(cls, url: str = DHAN_SCRIP_MASTER_URL, timeout: float = 30.0, **kw) -> "DhanInstrumentMaster":
        """Download the public scrip master (network). No auth required."""
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "intraday-signal-engine"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return cls.from_csv_text(resp.read().decode("utf-8", errors="replace"), **kw)
