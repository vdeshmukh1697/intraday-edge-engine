"""News -> symbol mapping (PLAN §4.6).

Maps free-text headlines to the NSE symbols they mention, using pure dictionary /
string matching (no network, no NLP model). A symbol is mentioned when its ticker
appears as a whole word, or any of its configured aliases appears as a whole,
contiguous phrase. Full NER is deferred.

Matching rules (all case-insensitive):
  - Ticker: the exact symbol token, on word boundaries. "TCS" matches "TCS results"
    but NOT "TCSX" or "matcstcs".
  - Alias: the alias phrase, on word boundaries; multi-word aliases ("HDFC Bank")
    must appear contiguously. Aliases are regex-escaped, so special characters
    (e.g. "M&M") are matched literally.

Result ordering: symbols are returned sorted by the position of their *first* match
in the headline, ties broken alphabetically by symbol. De-duplicated.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple


def default_symbol_aliases() -> Dict[str, List[str]]:
    """Aliases for the project watchlist plus a few common NSE names (PLAN §4.6)."""
    return {
        "RELIANCE": ["Reliance", "Reliance Industries", "RIL"],
        "HDFCBANK": ["HDFC Bank", "HDFCBANK"],
        "INFY": ["Infosys", "Infy"],
        "TCS": ["TCS", "Tata Consultancy"],
        "ICICIBANK": ["ICICI Bank", "ICICI"],
        "SBIN": ["SBI", "State Bank"],
        "TATAMOTORS": ["Tata Motors"],
        "MM": ["M&M", "Mahindra"],
    }


class SymbolMapper:
    """Maps headlines to mentioned symbols via ticker + alias dictionary matching."""

    def __init__(self, symbol_aliases: Dict[str, List[str]]) -> None:
        self._symbol_aliases = symbol_aliases
        # Per symbol, build one combined case-insensitive pattern covering the
        # ticker and every alias, each anchored on word boundaries.
        self._patterns: Dict[str, "re.Pattern[str]"] = {}
        for symbol, aliases in symbol_aliases.items():
            terms = [symbol] + list(aliases)
            # De-dup terms (case-insensitive) while preserving order.
            seen = set()
            unique_terms = []
            for term in terms:
                key = term.lower()
                if key not in seen:
                    seen.add(key)
                    unique_terms.append(term)
            alternation = "|".join(re.escape(t) for t in unique_terms)
            # \b...\b gives whole-word/phrase matching. For terms whose edges are
            # non-word chars (e.g. "M&M"), \b still anchors correctly at the
            # word/non-word transition of the alphanumeric edges.
            self._patterns[symbol] = re.compile(
                r"\b(?:" + alternation + r")\b", re.IGNORECASE
            )

    def map(self, headline: str) -> List[str]:
        """Return symbols mentioned in ``headline``, ordered by first-match position."""
        if not headline:
            return []
        hits: List[Tuple[int, str]] = []
        for symbol, pattern in self._patterns.items():
            m = pattern.search(headline)
            if m is not None:
                hits.append((m.start(), symbol))
        hits.sort(key=lambda pair: (pair[0], pair[1]))
        return [symbol for _, symbol in hits]

    def map_many(self, headlines: List[str]) -> List[List[str]]:
        """Map each headline; returns a list aligned 1:1 with ``headlines``."""
        return [self.map(h) for h in headlines]
