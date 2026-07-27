"""EngineRunner — the live/replay orchestrator (PLAN §2 "Compute core").

Pipeline per CLOSED 1-minute bar:
    bar -> append history -> paper-trader.on_bar (manage open positions)
        -> (if square-off time) force flat
        -> (if can_enter) compute features -> strategy -> guards -> risk -> TradePlan
        -> surface (record + alert + open paper position)

The SAME pipeline runs for synthetic replay (now) and a live feed (later) — only the
broker changes. Guardrails (§5.3) are applied as guidance on the paper book so the
system mirrors disciplined manual trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import pytz

from signal_engine.alerts import send_alert
from signal_engine.alerts.base import Alerter
from signal_engine.alerts.plain import (  # plain-English "why" for every alert (PORTFOLIO §5)
    _inr,
    explain_entry,
    explain_exit,
    explain_halt,
    explain_skip,
)
from signal_engine.brokers.base import BrokerAdapter
from signal_engine.config import AppConfig
from signal_engine.domain.enums import Direction, MarketState, PositionStatus
from signal_engine.domain.models import Bar, PaperPosition, TradePlan
from signal_engine.indicators import compute_features
from signal_engine.ingestion.aggregator import BarAggregator
from signal_engine.market.session import MarketSession
from signal_engine.paper.trader import PaperTrader
from signal_engine.risk.costs import CostModel
from signal_engine.risk.manager import RiskManager
from signal_engine.risk.sizing import size_plan
from signal_engine.strategies.base import Strategy, StrategyContext

_MAX_HISTORY = 300  # bars kept per symbol for feature computation


@dataclass
class _LossBreaker:
    """Session capital-preservation circuit breaker (PLAN §5.3 M1, paper-only).

    Tracks cumulative realized PnL% for the session and a run of consecutive losing
    trades. Halts NEW entries once the daily loss limit is breached OR after N straight
    losses; it never touches open positions and never places/cancels real orders — it only
    gates the runner's entry path. State is per-session (a fresh runner == a fresh session).

    ``daily_max_loss_pct`` is a positive number (e.g. 2.0 == halt at -2.0% on the book).
    """

    daily_max_loss_pct: float
    max_consecutive_losses: int
    realized_pnl_pct: float = 0.0
    peak_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    _halted: bool = False
    halt_reason: str = ""

    def record(self, pnl_pct_net: Optional[float]) -> None:
        """Fold one CLOSED trade's net PnL% into the session tally and update the run.

        The daily cap trips on DRAWDOWN-FROM-PEAK, not cumulative realized PnL: a book that
        ran to +3% then bled to -1% has drawn down 4% and should halt, even though the
        cumulative figure (-1%) is well within a -4% cumulative cap. The old cumulative test
        let a -7.8% intra-session trough pass because earlier wins netted it up (the 2026-06-25
        failure). Drawdown-from-peak binds on the actual capital give-back.
        """
        pnl = float(pnl_pct_net or 0.0)
        self.realized_pnl_pct += pnl
        self.peak_pnl_pct = max(self.peak_pnl_pct, self.realized_pnl_pct)
        drawdown = self.peak_pnl_pct - self.realized_pnl_pct
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        if not self._halted:
            if self.daily_max_loss_pct > 0 and drawdown >= abs(self.daily_max_loss_pct):
                self._halted = True
                self.halt_reason = (f"daily drawdown limit hit "
                                    f"(drawdown {drawdown:.2f}% from peak {self.peak_pnl_pct:+.2f}% "
                                    f">= {abs(self.daily_max_loss_pct):.2f}%)")
            elif (self.max_consecutive_losses > 0
                  and self.consecutive_losses >= self.max_consecutive_losses):
                self._halted = True
                self.halt_reason = (f"{self.consecutive_losses} consecutive losses "
                                    f">= {self.max_consecutive_losses}")

    @property
    def halted(self) -> bool:
        return self._halted


@dataclass
class RunSummary:
    picks: List[TradePlan] = field(default_factory=list)
    closed: List[PaperPosition] = field(default_factory=list)
    bars_processed: int = 0

    @property
    def net_pnl_pct(self) -> float:
        return round(sum(p.pnl_pct_net or 0.0 for p in self.closed), 4)

    @property
    def wins(self) -> int:
        return sum(1 for p in self.closed if p.won)

    @property
    def win_rate(self) -> float:
        return round(100.0 * self.wins / len(self.closed), 1) if self.closed else 0.0


class EngineRunner:
    def __init__(
        self,
        cfg: AppConfig,
        broker: BrokerAdapter,
        strategy: Strategy,
        session: MarketSession,
        alerter: Alerter,
        repo=None,
        ml_scorer=None,
        ml_gate: float = 0.0,
    ):
        self.cfg = cfg
        self.broker = broker
        self.strategy = strategy
        self.session = session
        self.alerter = alerter
        self.repo = repo
        # Optional ML entry gate: skip any signal the model scores below ml_gate (0..1).
        # ml_gate=0 disables it (ML stays pure shadow). PLAN §4.7.
        self.ml_scorer = ml_scorer
        self.ml_gate = float(ml_gate)
        # Live re-rating advisor (set on the live() path). None elsewhere -> no behaviour change
        # for replay/backtest, so they stay deterministic.
        self.advisor = None
        # Microstructure shadow collector (order-book imbalance + volume-delta). Set on the
        # live() path only; None everywhere else. Pure observability — logged + scored, never
        # gates a trade. See signal_engine/microstructure/.
        self._micro = None

        # Two cost models with DIFFERENT slippage semantics (V3 regression fix, 2026-07-07):
        # - cost_model (charges only) feeds the PaperTrader and the ₹ ledger: the trader applies
        #   slippage adversely to BOTH fills, so its net subtraction must be statutory charges
        #   only — folding slippage in here would double-count it.
        # - gate_cost_model (charges + round-trip slippage) feeds plan BUILDING: a plan's true
        #   friction includes the slippage its fills WILL pay — what the V3 edge-after-cost
        #   comment in risk.yaml promised and what the P0 friction-in-R gate (max_cost_r)
        #   prices. Without this, gates priced friction at ~8bps vs the ~14bps fills realize.
        self.cost_model = CostModel(cfg.risk.costs)
        self.gate_cost_model = CostModel(cfg.risk.costs, cfg.risk.slippage)
        self.risk_manager = RiskManager(cfg.risk.risk)
        # Portfolio book (PORTFOLIO §4): the ONE ₹1,00,000 paper book. Built only with a repo
        # (live / replay-with-repo); without one the ledger stays None and every ledger hook
        # no-ops, so backtests and scan previews keep the capital-agnostic legacy behaviour exactly.
        self.ledger = None
        self._debited: set = set()             # position ids whose fill notional the book blocked
        self._reserved: Dict[str, float] = {}  # position id -> ₹ reserved at surface until fill/cancel
        self._last_equity_snap_min = None      # throttle for intraday equity snapshots
        if repo is not None:
            try:
                from signal_engine.portfolio.ledger import PortfolioLedger

                self.ledger = PortfolioLedger(
                    repo, float(getattr(cfg.risk.portfolio, "starting_capital", 100000.0)))
            except Exception:  # noqa: BLE001 - the book is a mirror; never block the engine on it
                self.ledger = None
        self.paper = PaperTrader(
            self.cost_model,
            slippage_pct=cfg.risk.slippage.pct_per_side,
            max_hold_minutes=cfg.risk.risk.max_hold_minutes,
            on_fill=self._on_paper_fill,  # block the ₹ book's cash the instant an entry fills
        )

        self.params = dict(cfg.settings.strategy.params)
        self._aggs: Dict[str, BarAggregator] = {}
        self._history: Dict[str, List[Bar]] = {}
        self._cooldown_until: Dict[str, object] = {}
        self._daily_trades = 0
        self._daily_per_symbol: Dict[str, int] = {}  # entries opened per symbol today (freq cap)
        self.summary = RunSummary()

        # M1: session capital-preservation breaker. daily_max_loss_pct reuses the existing
        # daily_loss_pct config (a % of the user's chosen capital, applied to the paper book).
        self.breaker = _LossBreaker(
            daily_max_loss_pct=float(getattr(cfg.risk.risk, "daily_loss_pct", 0.0)),
            max_consecutive_losses=int(getattr(cfg.risk.risk, "max_consecutive_losses", 0)),
        )

        # D1b: per-bar candidate buffer for ranked top-N surfacing. Candidates that clear the
        # gates within one event-time minute are collected, then ranked and the top-N opened
        # when the bar minute advances. top_n_alerts==0 => fall back to max_trades_per_day.
        self._pending: List[tuple] = []   # (rank, tiebreak, plan) for the current minute
        self._pending_minute = None       # event-time minute currently being collected

        # Hardening (PLAN §9.3): structured logging + opt-in stale-data fail-safe.
        from signal_engine.obs.freshness import FreshnessGuard
        from signal_engine.obs.logging_setup import get_logger

        self.log = get_logger("engine")
        # enforce_freshness is opt-in and OFF by default: historical replay/backtest use past
        # timestamps that would always look "stale" vs wall-clock. Only the live() loop turns
        # it on, since the staleness fail-safe (PLAN §9.3) only makes sense on a live feed.
        self.enforce_freshness = False
        # 30s tolerates sparse ticks on slower names while still flagging a dead feed.
        self.freshness = FreshnessGuard(max_staleness_seconds=30.0)
        self._errors = 0
        self._suppress_alerts = False  # True while replaying warm-start bars (no live alerts)
        # Liveness heartbeat: the live loop is otherwise silent between trades, so a long quiet
        # spell looks like a stall even when the feed is healthy. Log a proof-of-life line at most
        # once per this many minutes of bar time (set in on_closed_bar).
        self._heartbeat_every_min = 5
        self._last_heartbeat_min = None
        self._last_status_min = None  # throttle the per-minute live_status DB upsert

    def _alert(self, msg: str, level: str, meta: Optional[dict] = None) -> None:
        if not self._suppress_alerts:
            send_alert(self.alerter, msg, level=level, meta=meta)

    # -- feed callback ------------------------------------------------------
    def on_tick(self, tick) -> None:
        # Feed liveness is measured by TICK ARRIVAL (wall-clock), not bar-open age: a 1-min bar's
        # open ts is always ~60s behind 'now' when it closes, so marking freshness off bar.ts made
        # the staleness guard suppress every live entry. Mark on each received tick instead.
        self.freshness.mark(self.freshness.clock.now())
        # Intra-bar (sub-second) re-rating: flag when price runs most of the way to the target.
        if self.advisor is not None and tick.ltp:
            try:
                msg = self.advisor.on_price(tick.symbol, float(tick.ltp))
                if msg:
                    self._alert(msg, "signal", meta={"kind": "advice", "symbol": tick.symbol})
            except Exception:  # noqa: BLE001 - price probe must never break the feed
                pass
        if self._micro is not None:
            self._micro.on_tick(tick)  # shadow: buffer the tick for its 1-min bar signal
        agg = self._aggs.get(tick.symbol)
        if agg is None:
            agg = self._aggs[tick.symbol] = BarAggregator(tick.symbol, 1)
        bar = agg.add_tick(tick)
        if bar is not None:
            # Error resilience: one symbol's failure must not kill the whole run.
            try:
                self.on_closed_bar(bar)
            except Exception as exc:  # noqa: BLE001 - deliberate catch-all at the boundary
                self._errors += 1
                self.log.error("on_closed_bar failed for %s @ %s: %s", bar.symbol, bar.ts, exc)
                send_alert(self.alerter, f"engine error on {bar.symbol}: {exc}",
                           level="warning", meta={"kind": "error", "symbol": bar.symbol})

    # -- core per-bar logic -------------------------------------------------
    def on_closed_bar(self, bar: Bar) -> None:
        self.summary.bars_processed += 1

        if self._micro is not None:
            self._micro.on_bar_close(bar)  # shadow: compute + persist this bar's signal

        # D1b: as soon as the event-time MINUTE advances, the previous minute's collection of
        # qualified candidates is complete — rank it and open the top-N. Doing this at the TOP
        # (before this bar's position-management / fills) preserves the original fill timing: a
        # candidate that fired on bar T is opened before bar T+1's fills, exactly as immediate
        # surfacing did. Single-candidate minutes are surfaced unchanged, so replay stays
        # byte-identical for the advisor=None path.
        cur_minute = bar.ts.replace(second=0, microsecond=0)
        if self._pending_minute is not None and cur_minute > self._pending_minute:
            self._flush_pending()
        self._pending_minute = cur_minute

        # Liveness beacon (live only). Mirror a status row to the DB EVERY minute so the dashboard
        # shows an always-fresh "feed alive, last update HH:MM"; log a proof-of-life LINE less
        # often (every few minutes) to keep the log readable.
        if self.enforce_freshness:
            m = cur_minute
            watching = len(self._aggs) or len(self.cfg.settings.watchlist)
            if self.repo and m != self._last_status_min:
                self._last_status_min = m
                try:
                    self.repo.update_live_status(
                        bar_ts=m.isoformat(), bars_processed=self.summary.bars_processed,
                        open_count=len(self.paper.open_positions),
                        closed_today=len(self.summary.closed), watching=watching)
                except Exception:  # noqa: BLE001 - status mirroring must never break the feed
                    pass
            if self._last_heartbeat_min is None or (
                (m - self._last_heartbeat_min).total_seconds() >= self._heartbeat_every_min * 60
            ):
                self._last_heartbeat_min = m
                self.log.info(
                    "heartbeat %s IST | bars=%d | open positions=%d | closed today=%d | "
                    "watching=%d symbols",
                    m.strftime("%H:%M"), self.summary.bars_processed,
                    len(self.paper.open_positions), len(self.summary.closed), watching,
                )

        hist = self._history.setdefault(bar.symbol, [])
        hist.append(bar)
        if len(hist) > _MAX_HISTORY:
            del hist[0]

        # 1) Manage existing positions against this bar.
        for pos in self.paper.on_bar(bar):
            self._on_position_closed(pos, bar)

        # Mirror this symbol's currently-open position (if any) to the DB with a fresh mark, so
        # the read-only dashboard can show the live entry + unrealized P&L the moment it fills.
        self._sync_open_position(bar)
        self._maybe_snapshot_equity(bar)

        # 2) Forced square-off window: flatten, no new entries.
        if self.session.is_square_off_time(bar.ts):
            for pos in self.paper.force_square_off(bar):
                self._on_position_closed(pos, bar)
            return

        # Fail-safe: never trade on a stale/dead feed (PLAN §9.3). Freshness is marked on tick
        # arrival (see on_tick); here we only CHECK it. Opt-in (live only).
        if self.enforce_freshness and self.freshness.is_stale():
            self.log.warning("feed stale for %s — suppressing entries", bar.symbol)
            return

        # 3) Re-rate the outlook every bar inside the entry window — this drives both the
        #    live "prediction changed" alerts (advisor) AND the actual entry decision. We
        #    compute it even when we already hold the symbol or hit the daily limit, so the
        #    advisor can flag a thesis change on a position we're already in.
        if not self.session.can_enter(bar.ts):
            return

        features = compute_features(self._history_df(bar.symbol), self.params)
        ctx = StrategyContext(
            symbol=bar.symbol,
            ts=bar.ts,
            features=features,
            bars=self._history_df(bar.symbol),
            session_state=MarketState.OPEN,
            params=self.params,
        )
        signal = self.strategy.on_bar(ctx)
        plan = self.risk_manager.build_trade_plan(signal, features, self.gate_cost_model) if signal else None
        if plan is not None and self.ml_scorer is not None and self.ml_gate > 0.0:
            from signal_engine.ml.features import build_matrix
            prob = float(self.ml_scorer.score_matrix(build_matrix([features]))[0]) / 100.0
            if prob < self.ml_gate:
                plan = None  # ML says this setup is below the win-probability threshold

        # D1 (gate-before-advisor): decide ACTIONABILITY with a READ-ONLY gate check BEFORE
        # re-rating, so a symbol we can't actually enter (already held, daily/concurrent limit
        # hit, breaker tripped, in cooldown) never emits a fresh-looking NEW alert. Re-rating /
        # reversal / invalidation of an already-tracked symbol stays exempt (handled inside the
        # advisor) — a thesis change on a position we hold is still worth flagging.
        actionable = plan is not None and self._gate_ok(bar.symbol, bar.ts)

        # Live re-rating: alert the moment the plan materially changes (target/dir/conviction).
        if self.advisor is not None:
            msg = self.advisor.update(bar.symbol, plan, actionable=actionable)
            if msg:
                self._alert(msg, "signal", meta={"kind": "advice", "symbol": bar.symbol})

        # 4) Collect a NEW candidate only if the plan is valid and all execution gates pass.
        #    Actual opening is deferred to the ranked top-N flush when the minute advances.
        if not actionable:
            return
        self._log_entry_context(plan, features)  # C3/B1: measurement only, never gates
        self._collect_candidate(plan)

    def _log_entry_context(self, plan: TradePlan, features: dict) -> None:
        """C3 + B1 (SHADOW, measurement only — never gates an entry). Log the at-entry regime
        context for every candidate, plus whether a counter-regime LONG-block WOULD have fired.
        This is the data the EOD analysis (2026-06-25) says we need to VALIDATE a regime filter
        across many sessions before ever gating it live. Pure logging; zero behavior change."""
        if self._suppress_alerts:
            return  # don't emit during warm-start replay
        try:
            import math

            def g(k):
                v = features.get(k)
                return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

            close, vwap = g("close"), g("vwap")
            rt, adx, rvol = g("regime_trend"), g("adx"), g("rvol")
            orb_low, orb_high = g("orb_low"), g("orb_high")
            side = "—" if (close is None or vwap is None) else ("above-VWAP" if close >= vwap else "below-VWAP")
            # SHADOW counter-regime LONG flag: a fresh LONG while price is below VWAP, the short-window
            # trend is down, AND price is below the opening range low = the "buying a fading gap" setup
            # that bled today. LOG ONLY — we are NOT blocking it (needs multi-day validation first).
            faded = (plan.direction == Direction.LONG and close is not None and vwap is not None
                     and rt is not None and close < vwap and rt < 0
                     and orb_low is not None and close < orb_low)
            self.log.info(
                "ENTRY-CTX %s %s | %s | regime_trend=%s adx=%s rvol=%s | close=%s vwap=%s "
                "orb=[%s,%s]%s",
                plan.symbol, plan.direction.value, side,
                f"{rt:+.3f}" if rt is not None else "NA",
                f"{adx:.1f}" if adx is not None else "NA",
                f"{rvol:.2f}" if rvol is not None else "NA",
                f"{close:.2f}" if close is not None else "NA",
                f"{vwap:.2f}" if vwap is not None else "NA",
                f"{orb_low:.2f}" if orb_low is not None else "NA",
                f"{orb_high:.2f}" if orb_high is not None else "NA",
                "  [SHADOW: faded-gap LONG — would block if filter were live]" if faded else "",
            )
        except Exception:  # noqa: BLE001 - measurement must never break the feed
            pass

    # -- ranking / candidate collection (D1b) -------------------------------
    def _collect_candidate(self, plan: TradePlan) -> None:
        """Buffer a gate-cleared candidate for this event-time minute (ranked at flush)."""
        rank = self._rank(plan)
        # Deterministic tie-break: higher rank first, then symbol, then direction.
        tiebreak = (plan.symbol, plan.direction.value)
        self._pending.append((rank, tiebreak, plan))

    @staticmethod
    def _rank(plan: TradePlan) -> float:
        """rank = (t1_pct - cost_pct) / stop_pct * confidence (PLAN D1b).

        Rewards net-of-cost reward per unit risk, scaled by conviction. stop_pct is always
        > 0 for a surfaced plan (the risk manager floors it), so no divide-by-zero here.
        """
        stop_pct = plan.stop_pct or 1e-9
        edge = plan.target_pcts[0] - plan.cost_to_break_even_pct
        return (edge / stop_pct) * plan.confidence

    def _top_n(self) -> int:
        """N for top-N alerting: ``top_n_alerts`` (0 => fall back to max_trades_per_day)."""
        n = int(getattr(self.cfg.risk.alerts, "top_n_alerts", 0) or 0)
        return n if n > 0 else int(self.cfg.risk.risk.max_trades_per_day)

    def _flush_pending(self) -> None:
        """Rank the buffered candidates for the just-finished minute and open the top-N.

        Re-checks each gate at open time (counts evolve as we open within the flush), so the
        daily/concurrent caps and the breaker stay authoritative. Empty/clears the buffer.
        """
        pending, self._pending = self._pending, []
        if not pending:
            return
        # Highest rank first; deterministic tie-break by (symbol, direction).
        pending.sort(key=lambda item: (-item[0], item[1]))
        n = self._top_n()
        # Per-minute burst cap (paper-analysis #1) — 0 disables; otherwise tighten this minute's
        # opens below top_n to break up correlated same-minute baskets.
        max_per_min = int(getattr(self.cfg.risk.risk, "max_entries_per_minute", 0) or 0)
        if max_per_min > 0:
            n = min(n, max_per_min)
        opened = 0
        for _rank, _tb, plan in pending:
            if opened >= n:
                break
            # Re-validate against live counts/breaker — gates may have closed mid-flush.
            if not self._gate_ok(plan.symbol, plan.ts):
                continue
            self._surface(plan)
            opened += 1

    # -- helpers ------------------------------------------------------------
    def _gate_ok(self, symbol: str, ts) -> bool:
        """READ-ONLY combined entry gate (D1 / M1). Mutates nothing — never increments any
        counter — so it can be probed before the advisor AND re-checked at flush time.

        A symbol is actionable only if: the session breaker hasn't halted NEW entries, the
        symbol is free (no open position + not in post-stop cooldown), the daily-trade cap
        isn't reached, and a concurrent-position slot is free.
        """
        if self.breaker.halted:
            return False
        if not self._symbol_free(symbol, ts):
            return False
        if self._daily_trades >= self.cfg.risk.risk.max_trades_per_day:
            return False
        if len(self.paper.open_positions) >= self.cfg.risk.risk.max_concurrent_positions:
            return False
        # Per-symbol/day frequency cap (paper-analysis #1) — 0 disables (only 1-open + cooldown).
        max_sym = int(getattr(self.cfg.risk.risk, "max_entries_per_symbol_per_day", 0) or 0)
        if max_sym > 0 and self._daily_per_symbol.get(symbol, 0) >= max_sym:
            return False
        return True

    def _symbol_free(self, symbol: str, ts) -> bool:
        # one position per symbol at a time + cooldown after a stop-out
        if any(p.symbol == symbol for p in self.paper.open_positions):
            return False
        cd = self._cooldown_until.get(symbol)
        return not (cd is not None and ts < cd)

    def _history_df(self, symbol: str) -> pd.DataFrame:
        rows = [
            {"open": b.open, "high": b.high, "low": b.low, "close": b.close,
             "volume": b.volume, "ts": b.ts}
            for b in self._history[symbol]
        ]
        df = pd.DataFrame(rows).set_index("ts")
        return df

    def _on_paper_fill(self, pos: PaperPosition) -> None:
        """PaperTrader callback fired the instant a paper entry FILLS. Blocks the fill notional
        (qty * entry_fill) in the ₹ book (PORTFOLIO §4). Best-effort + idempotent: the book is a
        mirror and must never break the feed. No-op without a ledger or for an unsized position."""
        if self.ledger is None:
            return
        qty = int(getattr(pos, "qty", 0) or 0)
        if qty <= 0 or pos.id in self._debited or pos.entry_fill is None:
            return
        try:
            self.ledger.on_entry(pos, qty)
            self._debited.add(pos.id)
        except Exception:  # noqa: BLE001 - book mirror must never break the live feed
            self.log.exception("ledger on_entry failed for %s", pos.id)
        finally:
            self._reserved.pop(pos.id, None)  # reservation is now a real block (or the debit failed)

    def _surface(self, plan: TradePlan) -> None:
        # Portfolio sizing (PORTFOLIO §4): size against the book's LIVE equity (so gains/losses
        # compound), then cap by the free cash NOT already reserved by other candidates surfaced
        # this same minute — two same-minute picks can't both spend the whole book (no leverage).
        # qty 0 => the book can't afford even one share, so we sit out with a plain-English skip.
        qty = 0
        fill_est = float(plan.entry or 0.0)
        if self.ledger is not None:
            size = size_plan(plan, self.cfg.risk.risk, capital=self.ledger.equity())
            entry = float(plan.entry or 0.0)
            # Reserve/afford at the ADVERSE-slipped fill price (what on_entry actually blocks), not
            # the plan price — else two same-minute picks that each reserve half the cash jointly
            # over-commit once slippage lifts the fill, breaching the no-leverage invariant.
            slip = float(getattr(self.paper, "slippage_pct", 0.0) or 0.0) / 100.0
            fill_est = entry * (1.0 + slip)
            free = self.ledger.cash - sum(self._reserved.values())
            affordable = int(free // fill_est) if fill_est > 0 else 0
            qty = max(0, min(int(size.get("qty", 0)), affordable))
            if qty <= 0:
                self._alert_skip(plan, fill_est, free)
                return
        self.summary.picks.append(plan)
        self._daily_trades += 1
        self._daily_per_symbol[plan.symbol] = self._daily_per_symbol.get(plan.symbol, 0) + 1
        if self.repo:
            self.repo.save_plan(plan)
        pos = self.paper.open_from_plan(plan)
        pos.qty = qty  # carried to the fill hook, which blocks qty * entry_fill in the book
        if qty > 0:
            self._reserved[pos.id] = qty * fill_est  # freed on fill or cancel
        # Log entries to the live log (not just Telegram) so the session is auditable from the log.
        if not self._suppress_alerts:
            self.log.info("ENTRY %s %s @~%.2f SL %.2f T1 %.2f conf %.0f qty %d",
                          plan.symbol, plan.direction.value, plan.entry, plan.stop_loss,
                          plan.t1, plan.confidence, qty)
        self._alert(self._format_alert(plan, qty), level="signal", meta=self._plan_meta(plan, qty))

    def _alert_skip(self, plan: TradePlan, price: float, cash: float) -> None:
        """Affordability skip (PORTFOLIO §4): a valid setup the book can't afford even one share
        of. Recorded to the predictions feed (kind 'skip') with a plain reason; opens nothing and
        burns no daily-trade slot. Suppressed (like entries) during warm-start replay."""
        plain = explain_skip(plan, price, cash)
        if not self._suppress_alerts:
            self.log.info("SKIP %s %s @~%.2f — 1 share ₹%.2f > free cash ₹%.2f",
                          plan.symbol, plan.direction.value, plan.entry, price, cash)
        self._alert("💤 " + plain, level="info", meta={
            "kind": "skip", "symbol": plan.symbol, "direction": plan.direction.value,
            "strategy": plan.strategy, "entry": plan.entry, "stop_loss": plan.stop_loss,
            "confidence": plan.confidence, "reason_plain": plain, "reasons": plan.reasons,
        })

    def _book_equity(self) -> Optional[float]:
        return self.ledger.equity() if self.ledger is not None else None

    def _plan_meta(self, plan: TradePlan, qty: Optional[int] = None) -> dict:
        """Structured parameters for the predictions log — everything the alert text says, plus
        the ₹-book sizing and the plain-English 'why' (PORTFOLIO §4/§6)."""
        equity = self._book_equity()
        size = size_plan(plan, self.cfg.risk.risk, capital=equity)
        q = int(size["qty"] if qty is None else qty)
        notional = round(q * float(plan.entry or 0.0), 2)
        return {
            "kind": "entry", "symbol": plan.symbol, "direction": plan.direction.value,
            "strategy": plan.strategy, "entry": plan.entry, "stop_loss": plan.stop_loss,
            "stop_pct": plan.stop_pct, "target": plan.t1 if plan.targets else None,
            "target_pct": plan.target_pcts[0] if plan.target_pcts else None,
            "risk_reward": plan.risk_reward, "expected_move_pct": plan.expected_move_pct,
            "confidence": plan.confidence, "qty": q,
            "rupee_risk": size["rupee_risk"], "notional": notional,
            "portfolio_equity": round(equity, 2) if equity is not None else None,
            "reason_plain": explain_entry(plan, q, notional, equity),
            "reasons": plan.reasons,
        }

    def _format_alert(self, plan: TradePlan, qty: Optional[int] = None) -> str:
        """D4 alert content + the ₹-book position + a plain-English 'Why:' line (PORTFOLIO §6).

        ``qty`` is the shares the book actually deploys (affordability-capped in ``_surface``);
        when absent (no-ledger paths) it falls back to the reference sizing. The rule score is
        labelled "rule score (uncalibrated)" (P2, STRATEGY_IMPROVEMENT_PLAN_2026-07: three reviews
        found it non-predictive) — never "conf"/"win-rate"; ML surfaces elsewhere as "model score"."""
        equity = self._book_equity()
        size = size_plan(plan, self.cfg.risk.risk, capital=equity)
        q = int(size["qty"] if qty is None else qty)
        tgt = f"{plan.t1:.2f} (+{plan.target_pcts[0]:.2f}%)"
        notional = q * float(plan.entry or 0.0)
        qty_part = ""
        if q > 0:
            book = f", book {_inr(equity)}" if equity is not None else ""
            pct = f", {int(round(100.0 * notional / equity))}% of book" if equity else ""
            qty_part = (f" | qty {q} (~{_inr(notional)}{pct}, "
                        f"~{_inr(size['rupee_risk'])} risk{book})")
        reasons = f" [{', '.join(plan.reasons)}]" if plan.reasons else ""
        head = (
            f"{plan.symbol} {plan.direction.value} @~{plan.entry:.2f} "
            f"SL {plan.stop_loss:.2f} (-{plan.stop_pct:.2f}%) "
            f"T1 {tgt} (key level {plan.t1:.2f}) "
            f"exp move {plan.expected_move_pct:.2f}% R:R {plan.risk_reward:.2f} "
            f"rule score {plan.confidence:.0f} (uncalibrated){qty_part}{reasons}"
        )
        return head + "\nWhy: " + explain_entry(plan, q, notional, equity)

    def _sync_open_position(self, bar: Bar) -> None:
        """Upsert the OPEN position for ``bar.symbol`` (if one is filled) to the DB with the
        current mark, so the dashboard sees live entries + unrealized P&L. No-op without a repo."""
        if not self.repo:
            return
        pos = next((p for p in self.paper.open_positions
                    if p.symbol == bar.symbol and p.status == PositionStatus.OPEN
                    and p.entry_fill), None)
        if pos is None:
            return
        # Gross unrealized % on the move (direction-aware); the realized net figure lands on close.
        entry = pos.entry_fill
        if pos.direction == Direction.LONG:
            upnl = (bar.close - entry) / entry * 100.0 if entry else None
        else:
            upnl = (entry - bar.close) / entry * 100.0 if entry else None
        try:
            self.repo.save_open_position(pos, last_price=bar.close, unrealized_pnl_pct=upnl)
        except Exception:  # noqa: BLE001 - dashboard mirroring must never break the feed
            pass

    def _maybe_snapshot_equity(self, bar: Bar) -> None:
        """Append an intraday equity point every ``portfolio.mark_snapshot_minutes`` (live only),
        so the portfolio page can draw an equity curve. Best-effort; values open rows at their
        stored marks. Off during warm-start (enforce_freshness is still False there)."""
        if self.ledger is None or not self.enforce_freshness:
            return
        every = int(getattr(self.cfg.risk.portfolio, "mark_snapshot_minutes", 5) or 5)
        m = bar.ts.replace(second=0, microsecond=0)
        last = self._last_equity_snap_min
        if last is not None and (m - last).total_seconds() < every * 60:
            return
        self._last_equity_snap_min = m
        try:
            self.ledger.snapshot("mark")
        except Exception:  # noqa: BLE001 - snapshots must never break the feed
            pass

    def _on_position_closed(self, pos: PaperPosition, bar: Bar) -> None:
        self.summary.closed.append(pos)
        # Settle the ₹ book (PORTFOLIO §4): credit the blocked notional back plus realized ₹ P&L
        # net of the real modeled charges. on_exit ALSO persists the closed trade row (with the ₹
        # fields) and drops the open-position mirror, so for a sized position we must NOT save/remove
        # again here. Unsized (no-ledger / legacy) and never-filled positions take the old path.
        money = None
        if self.ledger is not None and int(getattr(pos, "qty", 0) or 0) > 0 \
                and pos.entry_fill is not None and pos.exit_fill is not None:
            try:
                money = self.ledger.on_exit(pos, pos.qty, self.cost_model)
            except Exception:  # noqa: BLE001 - book mirror must never break the feed
                self.log.exception("ledger on_exit failed for %s", pos.id)
                money = None
            self._debited.discard(pos.id)
        self._reserved.pop(pos.id, None)  # free any surface reservation (position filled OR cancelled)
        if self.repo and money is None:
            self.repo.save_position(pos)
            self.repo.remove_open_position(pos.id)  # no longer open — drop the live mirror row
        # Arm the per-symbol cooldown after ANY losing exit (not just a hard STOP). Previously
        # only STOP armed it, so a TIME_STOP loss let the same name re-enter the very next bar
        # (e.g. SWIGGY on 2026-06-25) and double the cost bleed. A loss is a loss.
        if pos.entry_fill is not None and (pos.pnl_pct_net or 0.0) < 0:
            self._cooldown_until[pos.symbol] = bar.ts + timedelta(
                minutes=self.cfg.risk.risk.per_symbol_cooldown_minutes
            )
        # M1: fold the realized result into the session breaker. Only FILLED trades count
        # toward the loss tally / consecutive-loss run (never-filled cancels are skipped).
        if pos.entry_fill is not None:
            was_halted = self.breaker.halted
            self.breaker.record(pos.pnl_pct_net)
            if not self._suppress_alerts:
                if money:
                    self.log.info("EXIT  %s %s %s net (R %+.2f)", pos.symbol,
                                  pos.exit_reason.value, _inr(money["pnl_inr"]), pos.r_multiple)
                else:
                    self.log.info("EXIT  %s %s net %+.2f%% R %+.2f", pos.symbol,
                                  pos.exit_reason.value, pos.pnl_pct_net, pos.r_multiple)
            self._alert(self._format_exit(pos, money), level="info",
                        meta=self._exit_meta(pos, money))
            if self.breaker.halted and not was_halted:
                self.log.warning("session breaker tripped: %s — halting NEW entries",
                                 self.breaker.halt_reason)
                halt_plain = explain_halt(self.breaker.halt_reason,
                                          self.breaker.realized_pnl_pct)
                self._alert(
                    f"⛔ session halt — no new entries: {self.breaker.halt_reason} "
                    f"(session {self.breaker.realized_pnl_pct:+.2f}%)\n{halt_plain}",
                    level="warning",
                    meta={"kind": "halt", "reason": self.breaker.halt_reason,
                          "session_pnl_pct": self.breaker.realized_pnl_pct,
                          "reason_plain": halt_plain},
                )

    def _format_exit(self, pos: PaperPosition, money: Optional[dict]) -> str:
        """Exit alert text: ₹ P&L after real charges + the book's new value + a plain line when the
        position was sized (PORTFOLIO §6); the %/R-only phrasing for unsized/legacy positions."""
        if money:
            return (
                f"{pos.symbol} CLOSED {pos.exit_reason.value} "
                f"{_inr(money['pnl_inr'])} net (after {_inr(money['charges_inr'])} charges) "
                f"{pos.pnl_pct_net:+.2f}% R {pos.r_multiple:+.2f} | book {_inr(money['equity_after'])}"
                f"\n{explain_exit(pos, money)}"
            )
        return (f"{pos.symbol} CLOSED {pos.exit_reason.value} "
                f"net {pos.pnl_pct_net:+.2f}% R {pos.r_multiple:+.2f}")

    def _exit_meta(self, pos: PaperPosition, money: Optional[dict]) -> dict:
        meta = {"kind": "exit", "symbol": pos.symbol,
                "direction": pos.direction.value, "strategy": pos.plan.strategy,
                "entry": pos.entry_fill, "confidence": pos.plan.confidence,
                "exit_reason": pos.exit_reason.value,
                "pnl_pct_net": pos.pnl_pct_net, "r_multiple": pos.r_multiple,
                "exit_fill": pos.exit_fill, "hold_minutes": pos.hold_minutes}
        if money:
            meta.update({"pnl_inr": money["pnl_inr"], "notional": money["notional"],
                         "charges_inr": money["charges_inr"],
                         "portfolio_equity": money["equity_after"],
                         "reason_plain": explain_exit(pos, money)})
        return meta

    # -- entrypoints --------------------------------------------------------
    def replay(self, watchlist: Optional[List[str]] = None) -> RunSummary:
        """Drive a full synthetic/historical session through the pipeline."""
        symbols = watchlist or self.cfg.settings.watchlist
        self.broker.connect()
        self.broker.subscribe(symbols)
        self.broker.set_tick_callback(self.on_tick)
        self.broker.run()
        # Flush any forming bars (this also triggers end-of-day square-off in on_closed_bar).
        for agg in self._aggs.values():
            last = agg.flush()
            if last is not None:
                self.on_closed_bar(last)
        # Open any candidates still buffered from the final minute (D1b), then flatten.
        self._flush_pending()
        # Safety net: flatten anything still open at end of feed.
        for hist in self._history.values():
            if not hist:
                continue
            for pos in self.paper.force_square_off(hist[-1]):
                self._on_position_closed(pos, hist[-1])
        if self.ledger is not None:
            try:
                self.ledger.snapshot("eod")  # close-of-day equity point for the portfolio curve
            except Exception:  # noqa: BLE001
                pass
        self.broker.disconnect()
        return self.summary

    def live(self, watchlist: Optional[List[str]] = None) -> RunSummary:
        """Stream a live broker feed through the same pipeline until market close.

        Only meaningful with a streaming source (Dhan). Blocks during market hours; the
        broker pushes ticks to ``on_tick`` and we stop once the session closes, then flush
        forming bars and force-flat any open paper positions — the same tail as ``replay``.
        Decision-support only: positions are paper, never live orders.
        """
        from signal_engine.engine.advisor import LiveAdvisor

        ist = pytz.timezone("Asia/Kolkata")
        symbols = watchlist or self.cfg.settings.watchlist
        # Live re-rating + prediction-change alerts, with D2 debounce/hysteresis wired from
        # the alert config (min_realert_seconds / entry_band_bps).
        alerts = self.cfg.risk.alerts
        self.advisor = LiveAdvisor(
            min_realert_seconds=float(getattr(alerts, "min_realert_seconds", 0) or 0),
            entry_band_bps=float(getattr(alerts, "entry_band_bps", 0) or 0),
        )
        self.broker.connect()

        # Single source of truth for today: warm-start re-derives the WHOLE session from 09:15, so
        # clear today's trades + any stale open-position rows first. Without this, a mid-session
        # restart double-records trades a prior process logged live (live vs. historical bar
        # timestamps differ, so the id-keyed REPLACE can't dedupe them) — inflating the tracker.
        if self.repo:
            today = datetime.now(ist).date().isoformat()
            removed = self.repo.delete_trades_for_day(today)
            self.repo.clear_open_positions()
            if removed:
                self.log.info("cleared %d existing trade(s) for %s; warm-start will re-derive "
                              "them as the single source of truth", removed, today)

        # Warm-start: seed today's elapsed bars (09:15 -> now) so indicators are ready
        # immediately and any setups already missed this session are processed — otherwise a
        # cold start needs ~35 min of live bars before it can signal at all.
        self._warm_start_today(symbols, ist)

        # Warm-start REPLAYS today's already-happened trades to rebuild state — those are not live
        # decisions and must NOT pre-spend the live session's risk budget. Reset the daily trade
        # counter and the loss breaker so live trading starts clean. Without this, restarting after
        # a busy/losing morning starts already capped or breaker-halted, so ZERO live entries fire
        # and NO alerts go out (the symptom the client hit). On a normal 09:15 start warm-start is
        # empty, so this is a harmless no-op there.
        self._reset_live_risk_budget()

        # Re-derive the ₹ book from the DB (closed trades + still-open positions) now that warm-start
        # has rebuilt today as the single source of truth — a restart can never double-spend cash
        # (PORTFOLIO §4). Any positions carried out of warm-start keep their notional blocked.
        if self.ledger is not None:
            try:
                self.ledger.rebuild()
                self.ledger.snapshot("mark")  # anchor an opening equity point for today's curve
                self.log.info("portfolio book: ₹%.0f equity (cash ₹%.0f) at live start",
                              self.ledger.equity(), self.ledger.cash)
            except Exception:  # noqa: BLE001
                self.log.exception("ledger rebuild after warm-start failed")

        # Microstructure shadow collector (live only, config-gated, needs a repo/db to log to).
        # Guarded construction: a failure here must never stop the live session starting.
        if self.repo is not None and getattr(self.cfg.env, "microstructure_shadow", False):
            try:
                from signal_engine.microstructure.collector import MicrostructureCollector
                from signal_engine.microstructure.store import MicrostructureStore

                self._micro = MicrostructureCollector(
                    MicrostructureStore(self.cfg.env.db_url, run_id=getattr(self.repo, "run_id", None)))
                self.log.info("microstructure shadow: ON (feed_mode=%s) — logging order-book/"
                              "volume-delta signals, never gating",
                              getattr(self.cfg.env, "dhan_feed_mode", "quote"))
            except Exception:  # noqa: BLE001
                self._micro = None
                self.log.exception("microstructure shadow init failed — continuing without it")

        self.enforce_freshness = True  # live feed: activate the stale-data fail-safe (§9.3)
        self.broker.subscribe(symbols)
        self.broker.set_tick_callback(self.on_tick)

        def _market_closed() -> bool:
            return self.session.state_at(datetime.now(ist)) == MarketState.CLOSED

        self.log.info("live feed starting for %d symbols", len(symbols))
        self.broker.run(stop=_market_closed)

        # 2026-07-13 hardening: broker.run returning BEFORE the close means the feed died
        # (gave up reconnecting / fatal transport). Say so loudly — the scheduler's live_job
        # relaunches a fresh runner (warm-start re-derives today), so this is recoverable —
        # but it must never again be silent.
        if not _market_closed():
            self.log.error("live feed ended before market close — feed died")
            try:
                from signal_engine.alerts import send_alert
                send_alert(self.alerter,
                           "Live feed ended before market close — restarting automatically "
                           "(positions are safe; warm-start re-derives today's state).",
                           level="warning")
            except Exception:  # noqa: BLE001 - alerting must not break the shutdown path
                pass

        for agg in self._aggs.values():
            last = agg.flush()
            if last is not None:
                self.on_closed_bar(last)
        self._flush_pending()  # open any candidates buffered from the final minute (D1b)
        for hist in self._history.values():
            if not hist:
                continue
            for pos in self.paper.force_square_off(hist[-1]):
                self._on_position_closed(pos, hist[-1])
        if self.ledger is not None:
            try:
                self.ledger.snapshot("eod")  # close-of-day equity point for the portfolio curve
            except Exception:  # noqa: BLE001
                pass
        self.broker.disconnect()
        return self.summary

    def _reset_live_risk_budget(self) -> None:
        """Clear the live-session risk budget after warm-start. The day's replayed trades have
        rebuilt indicator/position state but are NOT live decisions, so they must not pre-spend the
        daily trade cap or pre-trip the loss breaker. Open positions stay tracked (state), only the
        counters reset. Logs how many replayed trades were discounted."""
        n_warm = self._daily_trades
        self._daily_trades = 0
        self._daily_per_symbol = {}  # warm-start replays don't count against the per-symbol cap
        self.breaker = _LossBreaker(
            daily_max_loss_pct=float(getattr(self.cfg.risk.risk, "daily_loss_pct", 0.0)),
            max_consecutive_losses=int(getattr(self.cfg.risk.risk, "max_consecutive_losses", 0)),
        )
        if n_warm:
            self.log.info("live risk budget reset after warm-start: %d replayed trade(s) don't "
                          "count against the live cap/breaker (live trading starts clean)", n_warm)

    def _warm_start_today(self, symbols: List[str], ist) -> None:
        """Replay today's elapsed 1-min bars through the pipeline before going live.

        Best-effort: a fetch failure for one symbol never blocks the live session. Freshness
        enforcement is kept OFF here (these are past bars, intentionally not 'fresh')."""
        import time as _wallclock
        from datetime import time as _time_of_day

        now = datetime.now(ist)
        start = ist.localize(datetime.combine(now.date(), _time_of_day(9, 15)))
        if now <= start:
            return

        seeded = []
        for i, sym in enumerate(symbols):
            try:
                seeded.extend(self.broker.historical(sym, "1m", start, now))
            except Exception as exc:  # noqa: BLE001
                self.log.warning("warm-start fetch failed for %s: %s", sym, exc)
            if i < len(symbols) - 1:
                _wallclock.sleep(0.25)  # stay under Dhan's 5 req/s Data-API cap
        seeded.sort(key=lambda b: b.ts)
        self._suppress_alerts = True  # replaying past bars must not fire live alerts
        try:
            for bar in seeded:
                try:
                    self.on_closed_bar(bar)
                except Exception as exc:  # noqa: BLE001
                    self._errors += 1
                    self.log.error("warm-start bar failed for %s: %s", bar.symbol, exc)
        finally:
            self._suppress_alerts = False
        self.log.info("warm-start: seeded %d bars across %d symbols (%d picks so far)",
                      len(seeded), len(symbols), len(self.summary.picks))
