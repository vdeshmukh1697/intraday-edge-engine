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

from signal_engine.alerts.base import Alerter
from signal_engine.brokers.base import BrokerAdapter
from signal_engine.config import AppConfig
from signal_engine.domain.enums import ExitReason, MarketState
from signal_engine.domain.models import Bar, PaperPosition, TradePlan
from signal_engine.indicators import compute_features
from signal_engine.ingestion.aggregator import BarAggregator
from signal_engine.market.session import MarketSession
from signal_engine.paper.trader import PaperTrader
from signal_engine.risk.costs import CostModel
from signal_engine.risk.manager import RiskManager
from signal_engine.strategies.base import Strategy, StrategyContext

_MAX_HISTORY = 300  # bars kept per symbol for feature computation


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
    ):
        self.cfg = cfg
        self.broker = broker
        self.strategy = strategy
        self.session = session
        self.alerter = alerter
        self.repo = repo

        self.cost_model = CostModel(cfg.risk.costs)
        self.risk_manager = RiskManager(cfg.risk.risk)
        self.paper = PaperTrader(
            self.cost_model,
            slippage_pct=cfg.risk.slippage.pct_per_side,
            max_hold_minutes=cfg.risk.risk.max_hold_minutes,
        )

        self.params = dict(cfg.settings.strategy.params)
        self._aggs: Dict[str, BarAggregator] = {}
        self._history: Dict[str, List[Bar]] = {}
        self._cooldown_until: Dict[str, object] = {}
        self._daily_trades = 0
        self.summary = RunSummary()

        # Hardening (PLAN §9.3): structured logging + opt-in stale-data fail-safe.
        from signal_engine.obs.freshness import FreshnessGuard
        from signal_engine.obs.logging_setup import get_logger

        self.log = get_logger("engine")
        # enforce_freshness is opt-in and OFF by default: historical replay/backtest use past
        # timestamps that would always look "stale" vs wall-clock. Only the live() loop turns
        # it on, since the staleness fail-safe (PLAN §9.3) only makes sense on a live feed.
        self.enforce_freshness = False
        self.freshness = FreshnessGuard(max_staleness_seconds=5.0)
        self._errors = 0

    # -- feed callback ------------------------------------------------------
    def on_tick(self, tick) -> None:
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
                self.alerter.send(f"engine error on {bar.symbol}: {exc}", level="warning")

    # -- core per-bar logic -------------------------------------------------
    def on_closed_bar(self, bar: Bar) -> None:
        self.summary.bars_processed += 1
        hist = self._history.setdefault(bar.symbol, [])
        hist.append(bar)
        if len(hist) > _MAX_HISTORY:
            del hist[0]

        # 1) Manage existing positions against this bar.
        for pos in self.paper.on_bar(bar):
            self._on_position_closed(pos, bar)

        # 2) Forced square-off window: flatten, no new entries.
        if self.session.is_square_off_time(bar.ts):
            for pos in self.paper.force_square_off(bar):
                self._on_position_closed(pos, bar)
            return

        # Fail-safe: never trade on a stale/dead feed (PLAN §9.3). Opt-in (live only).
        self.freshness.mark(bar.ts)
        if self.enforce_freshness and self.freshness.is_stale():
            self.log.warning("feed stale for %s — suppressing entries", bar.symbol)
            return

        # 3) Entry generation only inside the allowed window.
        if not self.session.can_enter(bar.ts):
            return
        if not self._symbol_free(bar.symbol, bar.ts):
            return
        if self._daily_trades >= self.cfg.risk.risk.max_trades_per_day:
            return
        if len(self.paper.open_positions) >= self.cfg.risk.risk.max_concurrent_positions:
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
        if signal is None:
            return
        plan = self.risk_manager.build_trade_plan(signal, features, self.cost_model)
        if plan is None:
            return  # vetoed by R:R floor / edge-after-cost gate
        self._surface(plan)

    # -- helpers ------------------------------------------------------------
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

    def _surface(self, plan: TradePlan) -> None:
        self.summary.picks.append(plan)
        self._daily_trades += 1
        if self.repo:
            self.repo.save_plan(plan)
        self.paper.open_from_plan(plan)
        tgt = f"{plan.t1:.2f} (+{plan.target_pcts[0]:.2f}%)"
        self.alerter.send(
            f"{plan.symbol} {plan.direction.value} @~{plan.entry:.2f} "
            f"SL {plan.stop_loss:.2f} (-{plan.stop_pct:.2f}%) T1 {tgt} "
            f"R:R {plan.risk_reward:.2f} conf {plan.confidence:.0f} "
            f"[{', '.join(plan.reasons)}]",
            level="signal",
        )

    def _on_position_closed(self, pos: PaperPosition, bar: Bar) -> None:
        self.summary.closed.append(pos)
        if self.repo:
            self.repo.save_position(pos)
        if pos.exit_reason == ExitReason.STOP:
            self._cooldown_until[pos.symbol] = bar.ts + timedelta(
                minutes=self.cfg.risk.risk.per_symbol_cooldown_minutes
            )
        if pos.entry_fill is not None:  # don't alert never-filled cancels noisily
            self.alerter.send(
                f"{pos.symbol} CLOSED {pos.exit_reason.value} "
                f"net {pos.pnl_pct_net:+.2f}% R {pos.r_multiple:+.2f}",
                level="info",
            )

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
        # Safety net: flatten anything still open at end of feed.
        for hist in self._history.values():
            if not hist:
                continue
            for pos in self.paper.force_square_off(hist[-1]):
                self._on_position_closed(pos, hist[-1])
        self.broker.disconnect()
        return self.summary

    def live(self, watchlist: Optional[List[str]] = None) -> RunSummary:
        """Stream a live broker feed through the same pipeline until market close.

        Only meaningful with a streaming source (Dhan). Blocks during market hours; the
        broker pushes ticks to ``on_tick`` and we stop once the session closes, then flush
        forming bars and force-flat any open paper positions — the same tail as ``replay``.
        Decision-support only: positions are paper, never live orders.
        """
        ist = pytz.timezone("Asia/Kolkata")
        self.enforce_freshness = True  # live feed: activate the stale-data fail-safe (§9.3)
        symbols = watchlist or self.cfg.settings.watchlist
        self.broker.connect()
        self.broker.subscribe(symbols)
        self.broker.set_tick_callback(self.on_tick)

        def _market_closed() -> bool:
            return self.session.state_at(datetime.now(ist)) == MarketState.CLOSED

        self.log.info("live feed starting for %d symbols", len(symbols))
        self.broker.run(stop=_market_closed)

        for agg in self._aggs.values():
            last = agg.flush()
            if last is not None:
                self.on_closed_bar(last)
        for hist in self._history.values():
            if not hist:
                continue
            for pos in self.paper.force_square_off(hist[-1]):
                self._on_position_closed(pos, hist[-1])
        self.broker.disconnect()
        return self.summary
