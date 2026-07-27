"""₹-denominated cost meter for LLM calls (the desk's optional reasoning layer).

The engine's whole philosophy is edge-after-cost, and that has to apply to compute too: if the
desk cannot justify its own API bill in decision quality, that is a finding, not an overhead.
TradingAgents' documented failure mode was the opposite — 10-13 LLM calls per ticker per day
whose value was measured nowhere in their repo (docs/TRADINGAGENTS_ANALYSIS_2026-07.md §6).

Prices are USD per million tokens, verified against the Claude API model table on 2026-07-26.
They are a POINT-IN-TIME snapshot: if a price changes, the meter reports a stale number, so
`PRICES_AS_OF` is printed alongside every estimate rather than hidden.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PRICES_AS_OF = "2026-07-26"

# USD per 1M tokens: (input, output).
PRICE_USD_PER_MTOK: Dict[str, tuple] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Only used to render a ₹ figure in the report. Override with SE_USD_INR when it drifts.
DEFAULT_USD_INR = 88.0


@dataclass
class CallCost:
    model: str
    input_tokens: int
    output_tokens: int
    usd: float
    inr: float


@dataclass
class CostMeter:
    """Accumulates the cost of a review's LLM calls. Zero calls => zero cost, reported as such."""

    usd_inr: float = field(default_factory=lambda: float(os.getenv("SE_USD_INR",
                                                                   DEFAULT_USD_INR)))
    calls: List[CallCost] = field(default_factory=list)

    def record(self, model: str, input_tokens: int, output_tokens: int) -> CallCost:
        pin, pout = PRICE_USD_PER_MTOK.get(model, (0.0, 0.0))
        usd = (input_tokens / 1e6) * pin + (output_tokens / 1e6) * pout
        cost = CallCost(model=model, input_tokens=int(input_tokens),
                        output_tokens=int(output_tokens), usd=usd, inr=usd * self.usd_inr)
        self.calls.append(cost)
        return cost

    @property
    def total_usd(self) -> float:
        return sum(c.usd for c in self.calls)

    @property
    def total_inr(self) -> float:
        return sum(c.inr for c in self.calls)

    def summary(self) -> str:
        if not self.calls:
            return "LLM cost: ₹0.00 (0 calls — deterministic path only)."
        per = ", ".join("{} {}in/{}out".format(c.model, c.input_tokens, c.output_tokens)
                        for c in self.calls)
        return ("LLM cost: ₹{:.2f} (${:.4f}) over {} call(s) [{}] at prices as of {} and "
                "USD/INR {:.1f}.".format(self.total_inr, self.total_usd, len(self.calls), per,
                                         PRICES_AS_OF, self.usd_inr))

    def unaffordable_note(self, daily_book_inr: float) -> Optional[str]:
        """If the reasoning layer costs a material fraction of a 1% day, say so out loud."""
        if not self.calls or daily_book_inr <= 0:
            return None
        share = self.total_inr / daily_book_inr
        if share < 0.02:
            return None
        return ("The desk's own API bill (₹{:.2f}) is {:.1f}% of a 1%-of-book day (₹{:,.0f}). "
                "At that ratio the reasoning layer has to earn its keep or be switched off "
                "(SE_DESK_LLM=0).".format(self.total_inr, share * 100.0, daily_book_inr))
