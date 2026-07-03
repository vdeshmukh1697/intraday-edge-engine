"""PortfolioLedger — the ONE persistent ₹1,00,000 paper book (PORTFOLIO §3).

ALL money here is PAPER. The ledger makes results legible in rupees (the strategy has no
proven edge — see docs/PAPER_TRADING_ANALYSIS_2026-06.md); it never implies profitability
and never touches a real order.

Accounting model
----------------
* One book, anchored at ``starting_capital``. Entries **block** the fill notional
  (``qty * entry_fill``) as cash for LONG and SHORT alike — a margin model with **no
  leverage**: you can never deploy more than the cash you have.
* Exits credit the blocked notional back plus realized ₹ P&L net of the *real* modeled
  round-trip charges (``CostModel.charges(entry_fill, exit_fill, qty).total``). Slippage
  is already inside the persisted fills, so it is not double-counted here.
* Equity == cash + Σ over open positions of (notional + unrealized ₹), where unrealized
  ₹ = ``direction.sign * (last - entry_fill) * qty``. After every close,
  equity == starting_capital + Σ pnl_inr over all closed sized trades (the invariant the
  tests assert).
* Internal math is unrounded; money is rounded to 2dp only at the persistence boundary
  (the repository helpers do the rounding).

Single-writer rule: only the live engine mutates the book (constructs a ledger and calls
``on_entry``/``on_exit``/``snapshot``). The API and scheduler read ``portfolio_state`` /
``portfolio_equity`` directly and never write. SQLite WAL makes that split safe.

``rebuild()`` re-derives cash deterministically from the tables (closed trades + currently
open positions), so a crash or restart can never double-spend: the runner calls it right
after warm-start replay, before live bars.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from signal_engine.storage.repository import SignalRepository, _now_iso


def _direction_sign(direction: Any) -> int:
    """+1 LONG / -1 SHORT for either a Direction enum or the TEXT stored in the DB."""
    sign = getattr(direction, "sign", None)
    if sign is not None:
        return sign
    return -1 if str(direction).upper() == "SHORT" else 1


class PortfolioLedger:
    """Cash/equity book over ``SignalRepository`` tables (PORTFOLIO §3).

    ``starting_capital`` (from ``cfg.risk.portfolio``) is the anchor: ``rebuild()`` always
    re-derives cash from it plus the recorded trades, so editing the config re-anchors the
    book at the next rebuild instead of silently forking from persisted state.
    """

    def __init__(self, repo: SignalRepository, starting_capital: float = 100000.0):
        self.repo = repo
        self.starting_capital = float(starting_capital)
        state = repo.fetch_portfolio_state()
        if state is None:
            # Fresh book (or a DB that predates the ledger): derive + persist portfolio_state.
            self.rebuild()
        else:
            self._cash = float(state["cash"] if state["cash"] is not None
                               else self.starting_capital)
            self._realized_total = float(state["realized_pnl_total"] or 0.0)

    # ------------------------------------------------------------------ #
    # Read side (all ₹)
    # ------------------------------------------------------------------ #
    @property
    def cash(self) -> float:
        """Free (unblocked) paper cash."""
        return self._cash

    @property
    def realized_pnl_total(self) -> float:
        """Lifetime realized ₹ P&L net of modeled charges (sized trades only)."""
        return self._realized_total

    @property
    def invested(self) -> float:
        """Σ blocked notional over currently open sized positions."""
        return sum(float(r["notional"] or 0.0) for r in self._sized_open_rows())

    def equity(self, marks: Optional[Dict[str, float]] = None) -> float:
        """cash + Σ over open positions of (notional + unrealized ₹).

        ``marks`` (symbol -> last price) overrides the stored ``last_price`` per row; a
        position with no mark at all values at its entry fill (zero unrealized).
        """
        total = self._cash
        for row in self._sized_open_rows():
            notional, unreal = self._row_value(row, marks)
            total += notional + unreal
        return total

    # ------------------------------------------------------------------ #
    # Write side — live engine only (single-writer rule)
    # ------------------------------------------------------------------ #
    def rebuild(self) -> None:
        """Re-derive cash from the tables and persist ``portfolio_state``. Idempotent.

        cash = starting_capital
               + Σ pnl_inr over ALL closed paper_trades WHERE pnl_inr IS NOT NULL
               - Σ notional of rows currently in open_positions with qty (re-block open money).

        Legacy rows (pnl_inr NULL / qty 0) predate the book and are ignored. Called at
        construction when ``portfolio_state`` is missing and at live() start right after
        warm-start replay, so a restart can never double-spend.
        """
        realized = self.repo.sum_closed_pnl_inr()
        blocked = sum(float(r["notional"] or 0.0) for r in self._sized_open_rows())
        self._cash = self.starting_capital + realized - blocked
        self._realized_total = realized
        self._persist_state()

    def affordable_qty(self, plan: Any, risk_qty: int) -> int:
        """min(risk_qty, floor(cash / plan.entry)); never negative (PORTFOLIO §3).

        The risk sizer proposes ``risk_qty``; this caps it to what the book's free cash can
        actually block (no leverage). 0 means "sit this one out" (the skip path)."""
        entry = float(getattr(plan, "entry", 0.0) or 0.0)
        if entry <= 0 or risk_qty <= 0 or self._cash <= 0:
            return 0
        return max(0, min(int(risk_qty), int(self._cash // entry)))

    def on_entry(self, pos: Any, qty: int) -> None:
        """Block the fill notional for a just-filled position and persist.

        notional = ``qty * pos.entry_fill`` (the FILL, not the plan price — slippage is in
        the fill). LONG and SHORT both block notional (margin model, no leverage). Persists
        portfolio_state + the open_positions row's qty/notional. Idempotent: if the open row
        already carries a qty the debit already happened (belt to the runner's own guard).
        """
        qty = int(qty)
        if qty <= 0 or pos.entry_fill is None:
            return  # unsized/unfilled — the skip path never opens, nothing to block
        row = self.repo.conn.execute(
            "SELECT qty FROM open_positions WHERE id = ?", (pos.id,)).fetchone()
        if row is not None and (row["qty"] or 0) > 0:
            return  # already debited for this position (e.g. a repeated sync call)
        notional = qty * float(pos.entry_fill)
        pos.qty = qty
        pos.notional = notional
        self._cash -= notional
        self.repo.save_open_position(pos)  # upsert with qty/notional (marks follow per bar)
        self._persist_state()

    def on_exit(self, pos: Any, qty: int, cost_model: Any) -> dict:
        """Settle a closed position: credit notional + realized ₹ net of modeled charges.

        charges = ``cost_model.charges(entry_fill, exit_fill, qty).total`` (₹, round-trip)
        pnl_inr = ``direction.sign * (exit_fill - entry_fill) * qty - charges``
        cash += notional + pnl_inr. Persists the closed trade row (with qty/notional/
        charges/pnl), removes the open_positions row and updates portfolio_state, then
        returns ``{qty, notional, charges_inr, pnl_inr, equity_after, cash_after}``.
        """
        qty = int(qty)
        if qty <= 0 or pos.entry_fill is None or pos.exit_fill is None:
            # Unsized (legacy/no-ledger) or never-filled position: no money ever moved.
            return {"qty": 0, "notional": 0.0, "charges_inr": 0.0, "pnl_inr": 0.0,
                    "equity_after": self.equity(), "cash_after": self._cash}
        entry, exit_ = float(pos.entry_fill), float(pos.exit_fill)
        charges = float(cost_model.charges(entry, exit_, qty).total)
        pnl_inr = _direction_sign(pos.direction) * (exit_ - entry) * qty - charges
        notional = float(getattr(pos, "notional", None) or qty * entry)
        pos.qty = qty
        pos.notional = notional
        pos.charges_inr = charges
        pos.pnl_inr = pnl_inr
        self._cash += notional + pnl_inr
        self._realized_total += pnl_inr
        self.repo.save_position(pos)              # closed trade row with the ₹ fields
        self.repo.remove_open_position(pos.id)    # its money is back in cash now
        self._persist_state()
        return {"qty": qty, "notional": notional, "charges_inr": charges, "pnl_inr": pnl_inr,
                "equity_after": self.equity(), "cash_after": self._cash}

    def snapshot(self, kind: str, marks: Optional[Dict[str, float]] = None) -> None:
        """Append one ``portfolio_equity`` point (kind: 'mark' intraday | 'eod')."""
        invested = 0.0
        unreal = 0.0
        rows = self._sized_open_rows()
        for row in rows:
            notional, u = self._row_value(row, marks)
            invested += notional
            unreal += u
        self.repo.insert_equity_snapshot(
            kind=kind, equity=self._cash + invested + unreal, cash=self._cash,
            invested=invested, unrealized_pnl=unreal, open_count=len(rows), ts=_now_iso(),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _sized_open_rows(self) -> List[dict]:
        """open_positions rows the book actually holds money against (qty >= 1)."""
        return [r for r in self.repo.fetch_open_positions() if (r.get("qty") or 0) > 0]

    @staticmethod
    def _row_value(row: dict, marks: Optional[Dict[str, float]]) -> tuple:
        """(notional, unrealized ₹) for one sized open row.

        Mark preference: caller-supplied ``marks[symbol]`` > stored ``last_price`` > entry
        fill (=> zero unrealized). Unrealized ₹ = sign * (last - entry_fill) * qty.
        """
        qty = int(row.get("qty") or 0)
        entry = float(row.get("entry_fill") or 0.0)
        notional = float(row.get("notional") or 0.0)
        last = None
        if marks:
            last = marks.get(row.get("symbol"))
        if last is None:
            last = row.get("last_price")
        if last is None:
            last = entry
        unreal = _direction_sign(row.get("direction")) * (float(last) - entry) * qty
        return notional, unreal

    def _persist_state(self) -> None:
        self.repo.save_portfolio_state(
            starting_capital=self.starting_capital, cash=self._cash,
            realized_pnl_total=self._realized_total,
        )
