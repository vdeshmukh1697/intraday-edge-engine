"""Portfolio-book integration through EngineRunner (PORTFOLIO §4/§10).

Drives the entry -> fill -> exit money path with a repo-backed runner (so the ledger is live)
and asserts the ₹ book moves cash exactly, records real charges, preserves the no-leverage /
equity invariants, skips unaffordable setups, and re-derives cash idempotently on rebuild.

The strategy is bypassed on purpose: plans are constructed directly and bars are pushed
through ``PaperTrader``/``_on_position_closed`` so the assertions are deterministic and about
the ledger wiring, not about whatever the synthetic strategy happens to surface.
"""

import io
from datetime import date, datetime, timedelta

import pytz

from signal_engine.alerts.console import ConsoleAlerter
from signal_engine.brokers.mock import MockBroker
from signal_engine.config import load_config
from signal_engine.domain.enums import Direction
from signal_engine.domain.models import Bar, TradePlan
from signal_engine.engine.runner import EngineRunner
from signal_engine.market.calendar import NSECalendar
from signal_engine.market.session import MarketSession
from signal_engine.storage.repository import SignalRepository
from signal_engine.strategies.base import create_strategy

_IST = pytz.timezone("Asia/Kolkata")


class _CapturingAlerter:
    """Records (level, message, meta) so we can assert on the skip alert's structured meta."""

    def __init__(self):
        self.sent = []

    def send(self, message, level="info", meta=None):
        self.sent.append((level, message, meta))


def _runner(repo, alerter=None):
    cfg = load_config()
    broker = MockBroker(day=date(2025, 6, 23), seed=1)
    strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
    session = MarketSession(cfg.settings.market, NSECalendar())
    alerter = alerter or ConsoleAlerter(stream=io.StringIO())
    return EngineRunner(cfg, broker, strategy, session, alerter, repo=repo)


def _plan(symbol="ZED", entry=100.0, stop_pct=1.0, t1_pct=2.0, minute=10):
    ts = _IST.localize(datetime(2025, 6, 23, 10, minute))
    sl = entry * (1 - stop_pct / 100)
    t1 = entry * (1 + t1_pct / 100)
    return TradePlan(symbol=symbol, ts=ts, direction=Direction.LONG, strategy="s", entry=entry,
                     stop_loss=sl, stop_pct=stop_pct, targets=[t1, t1],
                     target_pcts=[t1_pct, t1_pct], expected_move_pct=t1_pct,
                     risk_reward=t1_pct / stop_pct, cost_to_break_even_pct=0.1, confidence=70.0)


def _bar(symbol, ts, o, h, l, c):
    return Bar(symbol=symbol, ts=ts, open=o, high=h, low=l, close=c, volume=10000)


def test_ledger_starts_at_one_lakh():
    repo = SignalRepository("sqlite:///:memory:")
    r = _runner(repo)
    assert r.ledger is not None
    assert abs(r.ledger.equity() - 100000.0) < 1e-6
    assert abs(r.ledger.cash - 100000.0) < 1e-6


def test_entry_fill_debits_notional_then_target_exit_credits_exact_pnl():
    repo = SignalRepository("sqlite:///:memory:")
    r = _runner(repo)
    plan = _plan(entry=100.0, stop_pct=1.0, t1_pct=2.0)  # sl=99 (dist 1), t1=102

    r._surface(plan)                                   # sizes qty, opens PENDING, reserves cash
    pos = r.paper.open_positions[0]
    # capital 100000 * 0.5% risk / stop-dist 1 = 500 shares (notional 50000 <= cap; affordable 1000).
    assert pos.qty == 500
    assert r.ledger.cash == 100000.0                   # nothing blocked until it actually fills

    # Next bar fills the entry at open (adverse slippage) -> ledger blocks the fill notional.
    fill_bar = _bar("ZED", plan.ts + timedelta(minutes=1), 100.0, 100.5, 99.8, 100.2)
    r.paper.on_bar(fill_bar)
    entry_fill = pos.entry_fill
    assert entry_fill > 100.0                          # long entry slips up
    assert abs(r.ledger.cash - (100000.0 - 500 * entry_fill)) < 1e-6
    assert abs(r.ledger.equity() - 100000.0) < 1e-2    # equity ~ unchanged at the mark (minus slippage)

    # A later bar tags the target -> exit, ledger credits notional + realized ₹ net of charges.
    tgt_bar = _bar("ZED", plan.ts + timedelta(minutes=5), 101.0, 103.0, 100.9, 102.5)
    closed = r.paper.on_bar(tgt_bar)
    assert len(closed) == 1
    for p in closed:
        r._on_position_closed(p, tgt_bar)

    exit_fill = pos.exit_fill
    charges = r.cost_model.charges(entry_fill, exit_fill, 500).total
    expected_pnl = (exit_fill - entry_fill) * 500 - charges
    # Book invariant: equity == starting + realized ₹ (only closed trade), cash fully back.
    assert abs(r.ledger.equity() - (100000.0 + expected_pnl)) < 1e-6
    assert abs(r.ledger.cash - (100000.0 + expected_pnl)) < 1e-6

    # The closed trade row carries the real ₹ fields the portfolio API/page read.
    trades = repo.fetch_trades()
    assert len(trades) == 1
    row = trades[0]
    assert row["qty"] == 500
    assert abs(row["pnl_inr"] - round(expected_pnl, 2)) < 0.01
    assert abs(row["charges_inr"] - round(charges, 2)) < 0.01
    assert repo.fetch_open_positions() == []           # open mirror dropped on close
    state = repo.fetch_portfolio_state()
    assert abs(state["cash"] - round(100000.0 + expected_pnl, 2)) < 0.01


def test_unaffordable_setup_is_skipped_not_opened():
    repo = SignalRepository("sqlite:///:memory:")
    alerter = _CapturingAlerter()
    r = _runner(repo, alerter=alerter)
    # One share costs more than the whole ₹1,00,000 book -> affordable qty 0 -> skip.
    r._surface(_plan(symbol="MRF", entry=125000.0, stop_pct=1.0, t1_pct=2.0))
    assert r.paper.open_positions == []                # nothing opened
    assert r._daily_trades == 0                         # skip burns no daily-trade slot
    assert r.ledger.cash == 100000.0                    # no money moved
    skips = [m for m in alerter.sent if m[2] and m[2].get("kind") == "skip"]
    assert len(skips) == 1
    assert skips[0][2].get("reason_plain")              # carries a plain-English why
    assert "MRF" in skips[0][1]


def test_no_leverage_across_concurrent_positions():
    """Two positions filling in one book can never block more cash than exists."""
    repo = SignalRepository("sqlite:///:memory:")
    r = _runner(repo)
    # Two ~₹50k names surfaced same minute: the reservation guard keeps total <= cash.
    r._surface(_plan(symbol="AAA", entry=100.0, minute=10))
    r._surface(_plan(symbol="BBB", entry=100.0, minute=10))
    # A third same-minute ₹50k name would exceed cash -> reserved-aware affordability trims it.
    r._surface(_plan(symbol="CCC", entry=100.0, minute=10))
    total_reserved = sum(r._reserved.values())
    assert total_reserved <= 100000.0 + 1e-6

    # Fill everything that did get sized; cash must never go negative.
    for i, sym in enumerate(["AAA", "BBB", "CCC"]):
        pos = next((p for p in r.paper.open_positions if p.symbol == sym), None)
        if pos is None or pos.qty <= 0:
            continue
        fb = _bar(sym, pos.plan.ts + timedelta(minutes=1), 100.0, 100.4, 99.7, 100.1)
        r.paper.on_bar(fb)
    assert r.ledger.cash >= -1e-6                        # no implicit leverage, ever


def test_rebuild_is_idempotent_no_double_spend():
    repo = SignalRepository("sqlite:///:memory:")
    r = _runner(repo)
    plan = _plan(entry=100.0)
    r._surface(plan)
    pos = r.paper.open_positions[0]
    r.paper.on_bar(_bar("ZED", plan.ts + timedelta(minutes=1), 100.0, 100.5, 99.9, 100.2))
    cash_after_entry = r.ledger.cash
    # Re-deriving from the tables (what a live restart does after warm-start) must not move cash:
    # the open position's notional is re-blocked exactly once.
    r.ledger.rebuild()
    assert abs(r.ledger.cash - cash_after_entry) < 1e-6
    r.ledger.rebuild()
    assert abs(r.ledger.cash - cash_after_entry) < 1e-6
