"""Live paper-trader (PLAN §6.5).

Simulates fills, exits and per-trade metrics on a stream of bars. Capital-agnostic:
everything is expressed in price and percent (matching ``TradePlan``). No I/O.

Fill / exit conventions
-----------------------
* Entry fills on the FIRST bar with ``bar.ts > plan.ts`` (the *next* bar), at that
  bar's open adjusted adversely for slippage.
* Exits are evaluated on the bar's high/low/close. A bar that triggers entry may
  also hit a stop/target within the same bar.
* If BOTH stop and target are inside one bar, the STOP wins (pessimistic).
* Slippage is always applied in the adverse direction for the side being executed.
"""

from __future__ import annotations

from typing import Any, List

from signal_engine.domain.enums import Direction, ExitReason, PositionStatus
from signal_engine.domain.models import Bar, PaperPosition, TradePlan


class PaperTrader:
    """Tracks paper positions across a bar stream.

    Parameters
    ----------
    cost_model:
        Any object exposing ``breakeven_pct(price) -> float`` — the round-trip
        cost as a percentage. The real ``CostModel`` is intentionally NOT imported
        here so this module stays decoupled and testable with a stub.
    slippage_pct:
        Per-leg slippage in percent, applied adversely on both entry and exit.
    max_hold_minutes:
        Maximum holding time before a time-stop exit at the bar close.
    """

    def __init__(self, cost_model: Any, slippage_pct: float = 0.03,
                 max_hold_minutes: int = 90) -> None:
        self.cost_model = cost_model
        self.slippage_pct = slippage_pct
        self.max_hold_minutes = max_hold_minutes
        self._active: List[PaperPosition] = []  # PENDING or OPEN
        self._counter = 0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @property
    def open_positions(self) -> List[PaperPosition]:
        """Currently PENDING or OPEN positions."""
        return list(self._active)

    def open_from_plan(self, plan: TradePlan) -> PaperPosition:
        """Create a PENDING position from a plan and track it."""
        pos_id = "{}-{}-{}".format(plan.symbol, plan.ts.isoformat(), self._counter)
        self._counter += 1
        pos = PaperPosition(id=pos_id, plan=plan, status=PositionStatus.PENDING)
        self._active.append(pos)
        return pos

    def on_bar(self, bar: Bar) -> List[PaperPosition]:
        """Process all active positions for this bar's symbol.

        Returns the positions that became CLOSED on THIS bar.
        """
        closed: List[PaperPosition] = []
        for pos in list(self._active):
            if pos.symbol != bar.symbol:
                continue

            # PENDING -> maybe fill entry on the next bar.
            if pos.status == PositionStatus.PENDING:
                if bar.ts > pos.plan.ts:
                    self._fill_entry(pos, bar)
                else:
                    continue  # entry trigger not reached yet

            # OPEN -> evaluate exits (an entered bar can also exit within itself).
            if pos.status == PositionStatus.OPEN:
                if self._evaluate_exit(pos, bar):
                    self._active.remove(pos)
                    closed.append(pos)
        return closed

    def force_square_off(self, bar: Bar) -> List[PaperPosition]:
        """Close all OPEN positions and cancel never-filled PENDING ones.

        OPEN positions exit at ``bar.close`` (with exit slippage) with reason
        SQUARE_OFF. PENDING positions become CANCELLED. Returns all affected.
        """
        affected: List[PaperPosition] = []
        for pos in list(self._active):
            if pos.symbol != bar.symbol:
                continue
            if pos.status == PositionStatus.OPEN:
                exit_fill = self._apply_exit_slippage(pos.direction, bar.close)
                self._close(pos, exit_fill, bar.ts, ExitReason.SQUARE_OFF)
                self._active.remove(pos)
                affected.append(pos)
            elif pos.status == PositionStatus.PENDING:
                pos.status = PositionStatus.CANCELLED
                self._active.remove(pos)
                affected.append(pos)
        return affected

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _fill_entry(self, pos: PaperPosition, bar: Bar) -> None:
        direction = pos.direction
        if direction == Direction.LONG:
            fill = bar.open * (1 + self.slippage_pct / 100)
        else:  # SHORT
            fill = bar.open * (1 - self.slippage_pct / 100)
        pos.status = PositionStatus.OPEN
        pos.entry_fill = fill
        pos.entry_ts = bar.ts

    def _evaluate_exit(self, pos: PaperPosition, bar: Bar) -> bool:
        """Check stop/target/time-stop for an OPEN position. Returns True if closed."""
        plan = pos.plan
        direction = pos.direction

        if direction == Direction.LONG:
            stop_hit = bar.low <= plan.stop_loss
            target_hit = bar.high >= plan.t1
        else:  # SHORT
            stop_hit = bar.high >= plan.stop_loss
            target_hit = bar.low <= plan.t1

        # Pessimistic: if both are inside the bar, assume the stop triggered first.
        if stop_hit:
            exit_fill = self._apply_exit_slippage(direction, plan.stop_loss)
            self._close(pos, exit_fill, bar.ts, ExitReason.STOP)
            return True
        if target_hit:
            exit_fill = self._apply_exit_slippage(direction, plan.t1)
            self._close(pos, exit_fill, bar.ts, ExitReason.TARGET)
            return True

        # Time stop.
        held_minutes = (bar.ts - pos.entry_ts).total_seconds() / 60.0
        if held_minutes >= self.max_hold_minutes:
            exit_fill = self._apply_exit_slippage(direction, bar.close)
            self._close(pos, exit_fill, bar.ts, ExitReason.TIME_STOP)
            return True

        return False

    def _apply_exit_slippage(self, direction: Direction, trigger: float) -> float:
        """Adverse exit slippage: exiting a LONG sells lower, exiting a SHORT buys higher."""
        if direction == Direction.LONG:
            return trigger * (1 - self.slippage_pct / 100)
        return trigger * (1 + self.slippage_pct / 100)

    def _close(self, pos: PaperPosition, exit_fill: float, exit_ts: Any,
               exit_reason: ExitReason) -> None:
        pos.exit_fill = exit_fill
        pos.exit_ts = exit_ts
        pos.exit_reason = exit_reason
        pos.status = PositionStatus.CLOSED

        sign = pos.direction.sign
        gross_pct = sign * (exit_fill - pos.entry_fill) / pos.entry_fill * 100
        pos.pnl_pct_net = gross_pct - self.cost_model.breakeven_pct(pos.entry_fill)
        pos.r_multiple = pos.pnl_pct_net / pos.plan.stop_pct
        pos.won = (exit_reason == ExitReason.TARGET)
        pos.hold_minutes = (exit_ts - pos.entry_ts).total_seconds() / 60.0
