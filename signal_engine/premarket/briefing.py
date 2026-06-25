"""Pre-market briefing orchestrator (PLAN §4.8).

Fuses, before the bell:
  global cues (GIFT Nifty/US/Asia/ADRs)  ->  index outlook (expected gap, risk tone)
  overnight news (latest catalyst/sentiment)  +  prior-day technical state  +  ADR move
      ->  per-symbol gap/bias pick  ->  ranked pre-open watchlist.

Synthetic/offline by default (MockGlobalCuesProvider + MockNewsProvider for the prior
session). Overnight news uses the LATEST item's sentiment (not the intraday time-decayed
average) because a catalyst from yesterday evening still matters at today's open.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Callable, List, Optional

import pytz

from signal_engine.config import AppConfig
from signal_engine.data.synthetic import generate_session
from signal_engine.market.calendar import NSECalendar
from signal_engine.news.features import compute_news_features
from signal_engine.news.provider import MockNewsProvider
from signal_engine.premarket.cues import GlobalCuesProvider, MockGlobalCuesProvider
from signal_engine.premarket.models import PreMarketBriefing, PreMarketPick
from signal_engine.premarket.scoring import index_outlook, stock_bias

IST = pytz.timezone("Asia/Kolkata")
_OVERNIGHT_WINDOW_MIN = 24 * 60  # include the whole prior session as "overnight"


def prior_trading_day(day: date, calendar: NSECalendar) -> date:
    d = day - timedelta(days=1)
    guard = 0
    while not calendar.is_trading_day(d) and guard < 15:
        d -= timedelta(days=1)
        guard += 1
    return d


def _prior_day_state(symbol: str, prior_day: date, seed: int) -> dict:
    """Prior session's return % and where it closed within its range (0=low,1=high)."""
    df = generate_session(symbol, prior_day, seed=seed, regime="choppy")
    o = float(df["open"].iloc[0])
    c = float(df["close"].iloc[-1])
    hi = float(df["high"].max())
    lo = float(df["low"].min())
    prev_return_pct = (c / o - 1.0) * 100.0 if o else 0.0
    close_position = (c - lo) / (hi - lo) if hi > lo else 0.5
    return {"prev_return_pct": prev_return_pct, "close_position": close_position}


def build_briefing(
    cfg: AppConfig,
    symbols: Optional[List[str]] = None,
    day: Optional[date] = None,
    seed: int = 42,
    top_n: int = 20,
    cues_provider: Optional[GlobalCuesProvider] = None,
    calendar: Optional[NSECalendar] = None,
    news_provider=None,
    prior_state_fn: Optional[Callable[[str, date], dict]] = None,
) -> PreMarketBriefing:
    """Build the pre-open briefing. Real-data injection (used by the dashboard API):
    ``cues_provider`` = real Yahoo global cues, ``news_provider`` = real RSS headlines (its
    ``fetch()`` is called once), ``prior_state_fn(sym, prior_day)`` = real prior-session momentum
    from the archive. All default to the synthetic/offline path so tests stay deterministic."""
    cal = calendar or NSECalendar()
    day = day or date(2025, 6, 23)
    symbols = symbols or cfg.settings.watchlist

    cues = (cues_provider or MockGlobalCuesProvider(seed=seed, adr_symbols=symbols)).get_cues(day)
    outlook = index_outlook(cues)

    prior = prior_trading_day(day, cal)
    pre_open = IST.localize(datetime.combine(day, time(8, 30)))  # briefing time

    # Overnight news: real RSS headlines (current) if provided, else synthetic against the prior
    # session. Either way, compute_news_features maps items to each symbol point-in-time.
    if news_provider is not None:
        news_items = news_provider.fetch()
    else:
        news_items = MockNewsProvider(symbols, prior, seed=seed).fetch()

    picks: List[PreMarketPick] = []
    for i, sym in enumerate(symbols):
        nf = compute_news_features(news_items, sym, pre_open, window_min=_OVERNIGHT_WINDOW_MIN)
        state = (prior_state_fn(sym, prior) if prior_state_fn is not None
                 else _prior_day_state(sym, prior, seed + i))
        pick = stock_bias(
            sym,
            news_sentiment_avg=nf["news_sentiment"],       # latest overnight catalyst
            news_event_type=nf["news_event_type"],
            adr_move_pct=cues.adr_moves.get(sym, 0.0),
            index_gap_pct=outlook.expected_gap_pct,
            prev_return_pct=state["prev_return_pct"],
            close_position=state["close_position"],
        )
        if pick is not None:
            picks.append(pick)

    picks.sort(key=lambda p: p.confidence, reverse=True)
    return PreMarketBriefing(day=day, index_outlook=outlook, picks=picks[:top_n])
