"""Core data models (frozen contracts). Dataclasses — no I/O, no heavy deps.

Conventions
-----------
* All timestamps are timezone-aware IST (``Asia/Kolkata``).
* Prices are rupees (float). Percentages are expressed as **percent** (e.g. 0.5 == 0.5%),
  NOT as fractions, unless a field name ends in ``_frac``.
* These objects flow: Tick -> Bar -> Signal -> TradePlan -> PaperPosition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from signal_engine.domain.enums import Direction, ExitReason, PositionStatus


@dataclass(frozen=True)
class Tick:
    """A single market data update for a symbol."""

    symbol: str
    ts: datetime          # tz-aware IST
    ltp: float            # last traded price
    volume: int           # cumulative traded volume for the day
    bid: Optional[float] = None
    ask: Optional[float] = None


@dataclass(frozen=True)
class Bar:
    """An OHLCV bar for one timeframe.

    ``ts`` is the bar's OPEN time aligned to the timeframe boundary (e.g. 09:15:00
    for the 09:15 one-minute bar). ``is_provisional`` is True for the still-forming
    current bar — the signal engine MUST ignore provisional bars (PLAN §3.3).
    """

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    timeframe: str = "1m"        # "1m", "5m", "15m"
    is_provisional: bool = False


@dataclass(frozen=True)
class Signal:
    """Raw output of a Strategy on a closed bar. The risk layer turns this into a TradePlan."""

    symbol: str
    ts: datetime
    direction: Direction
    confidence: float            # 0..100
    strategy_name: str
    entry_hint: float            # reference price (usually signal bar close)
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class TradePlan:
    """The capital-agnostic, surfaced recommendation (PLAN §4.9).

    Everything is in price + percent. No rupee position sizing — the user decides
    the money. ``stop_pct`` / ``target_pcts`` / ``expected_move_pct`` are percentages.
    """

    symbol: str
    ts: datetime
    direction: Direction
    strategy: str
    entry: float
    stop_loss: float
    stop_pct: float                          # |entry-stop| / entry * 100
    targets: List[float]                     # absolute prices, T1 first
    target_pcts: List[float]                 # % from entry, aligned with targets
    expected_move_pct: float                 # realistic move estimate (== distance to T1)
    risk_reward: float                       # R:R to T1
    cost_to_break_even_pct: float            # % move needed to clear round-trip charges
    confidence: float                        # 0..100
    reasons: List[str] = field(default_factory=list)
    time_validity: Optional[datetime] = None  # plan expires at/after this time

    @property
    def t1(self) -> float:
        return self.targets[0]


@dataclass(frozen=True)
class CostBreakdown:
    """Itemized round-trip charges for one paper/real trade (PLAN §5.4)."""

    brokerage: float
    stt: float
    exchange_txn: float
    gst: float
    sebi: float
    stamp: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange_txn + self.gst + self.sebi + self.stamp


@dataclass
class PaperPosition:
    """A position tracked by the live paper-trader (PLAN §6.5). Mutable: it evolves
    PENDING -> OPEN -> CLOSED as bars arrive."""

    id: str
    plan: TradePlan
    status: PositionStatus = PositionStatus.PENDING
    entry_fill: Optional[float] = None
    entry_ts: Optional[datetime] = None
    exit_fill: Optional[float] = None
    exit_ts: Optional[datetime] = None
    exit_reason: ExitReason = ExitReason.OPEN
    pnl_pct_net: Optional[float] = None      # net-of-cost % return on the move
    r_multiple: Optional[float] = None       # realized R (pnl / risk)
    hold_minutes: Optional[float] = None
    won: Optional[bool] = None               # reached T1 before stop?

    @property
    def symbol(self) -> str:
        return self.plan.symbol

    @property
    def direction(self) -> Direction:
        return self.plan.direction

    @property
    def is_closed(self) -> bool:
        return self.status in (PositionStatus.CLOSED, PositionStatus.CANCELLED)
