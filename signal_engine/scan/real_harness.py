"""Real-data scan harness — full NSE universe on free Yahoo bars (no broker feed).

Same pipeline as :mod:`signal_engine.scan.harness`, but the survivor intraday history
comes from **real** Yahoo Finance bars instead of synthetic sessions, and news comes
from the **configured** provider (live RSS when ``SE_NEWS_SOURCE=rss``):

  real NSE universe -> static liquidity screen (real turnover/price)
        -> fetch real intraday bars for survivors only (batched, throttled)
        -> Scanner.scan -> ranked leaderboard

This is the honest free-tier realization of PLAN §4.0: Yahoo is fine for an
end-of-day full-universe scan, but cannot be polled live for ~2,000 names (that
needs the paid Dhan sharded websocket, §3.3).
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Callable, Dict, List, Optional

import pytz

from signal_engine.config import AppConfig
from signal_engine.obs.logging_setup import get_logger
from signal_engine.risk.costs import CostModel
from signal_engine.risk.manager import RiskManager
from signal_engine.scan.filter import LiquidityCostFilter
from signal_engine.scan.scanner import Scanner, ScanResult
from signal_engine.state.store import InMemoryStateStore
from signal_engine.strategies.base import create_strategy
from signal_engine.universe.base import UniverseProvider
from signal_engine.universe.models import InstrumentMeta

IST = pytz.timezone("Asia/Kolkata")
log = get_logger(__name__)

# symbols -> {symbol: intraday DataFrame (IST 'ts' index, ohlcv + symbol)}
IntradayFetch = Callable[[List[str]], Dict[str, "object"]]


def run_real_scan(
    cfg: AppConfig,
    universe: UniverseProvider,
    day: date,
    as_of: Optional[time] = None,
    top_n: int = 20,
    with_news: bool = True,
    intraday_fetch: Optional[IntradayFetch] = None,
) -> ScanResult:
    cost_model = CostModel(cfg.risk.costs)
    liquidity_filter = LiquidityCostFilter(cfg.risk.liquidity, cost_model)

    metas = universe.instruments()

    # 1) Static screen on REAL liquidity metadata -> survivors worth deep-scanning.
    survivors: List[InstrumentMeta] = [
        m for m in metas if liquidity_filter.evaluate(m, features=None).tradeable
    ]
    log.info("real scan: %d/%d symbols passed static liquidity screen", len(survivors), len(metas))

    # 2) Fetch REAL intraday bars for survivors only (batched + throttled).
    if intraday_fetch is None:
        from signal_engine.data.yahoo_batch import fetch_intraday
        def intraday_fetch(syms: List[str]) -> Dict[str, "object"]:  # noqa: E306
            return fetch_intraday(syms, interval="1m", period="1d")

    raw = intraday_fetch([m.symbol for m in survivors])

    cutoff = IST.localize(datetime.combine(day, as_of)) if as_of is not None else None
    histories: Dict[str, "object"] = {}
    for sym, df in raw.items():
        if cutoff is not None:
            df = df[df.index <= cutoff]
        if df is not None and not df.empty:
            histories[sym.upper()] = df

    # Only keep survivors that actually returned usable history.
    survivors = [m for m in survivors if m.symbol in histories]
    log.info("real scan: %d survivors have intraday data", len(survivors))

    # 3) News (configured provider: live RSS or mock) -> point-in-time features + overlay.
    news_features = None
    news_overlay = None
    if with_news and survivors:
        from signal_engine.factory import build_news_provider
        from signal_engine.news.features import compute_news_features
        from signal_engine.news.overlay import NewsOverlay
        from signal_engine.news.provider import MockNewsProvider

        provider = build_news_provider(cfg)
        if provider is None:
            provider = MockNewsProvider([m.symbol for m in survivors], day)
        items = provider.fetch(as_of=cutoff)
        news_features = {
            m.symbol: compute_news_features(items, m.symbol, cutoff or datetime.now(IST))
            for m in survivors
        }
        news_overlay = NewsOverlay()

    # 4) Scan + rank (same core as live + backtest).
    strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
    scanner = Scanner(
        params=dict(cfg.settings.strategy.params),
        strategy=strategy,
        cost_model=cost_model,
        risk_manager=RiskManager(cfg.risk.risk),
        liquidity_filter=liquidity_filter,
        state_store=InMemoryStateStore(),
        news_overlay=news_overlay,
        ml_scorer=None,
    )
    result = scanner.scan(survivors, histories, top_n=top_n, news_features=news_features)
    result.universe_size = len(metas)  # report against the FULL universe
    return result
