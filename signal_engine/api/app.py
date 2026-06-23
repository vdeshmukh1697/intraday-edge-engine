"""FastAPI engine API (PLAN §7.1, §11.6).

Read-only endpoints the Vercel/Next.js dashboard consumes over HTTPS + WebSocket. The
engine runs on the always-on box; this app exposes its data. There is NO order endpoint —
this is a decision-support tool.

Auth: if env ``SE_API_TOKEN`` is set, every /api route requires header ``X-API-Key`` to match
(open in local dev when unset). CORS is permissive for the (separate-origin) Vercel app;
lock ``allow_origins`` to your dashboard URL in production.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, time

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from signal_engine.api.serializers import (
    backtest_to_json,
    chart_to_json,
    leaderboard_to_json,
    premarket_to_json,
)
from signal_engine.config import load_config
from signal_engine.data.synthetic import bars_to_ticks, generate_session
from signal_engine.market.calendar import NSECalendar

_DISCLAIMER = (
    "Decision-support only. Not investment advice. No live orders are placed. "
    "Intraday trading carries substantial risk of loss. (PLAN §9)"
)


def _require_token(x_api_key: str = Header(default=None)) -> None:
    expected = os.getenv("SE_API_TOKEN")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


def _parse_date(s: str = None) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date() if s else date(2025, 6, 23)


def create_app() -> FastAPI:
    cfg = load_config()
    cal = NSECalendar()
    app = FastAPI(title="Intraday Signal Engine API", version="0.1.0",
                  description="Read-only signal/decision-support API. No order placement.")

    # Lock this to your Vercel URL in production (env SE_CORS_ORIGINS, comma-separated).
    origins = os.getenv("SE_CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"],
    )

    @app.get("/")
    def root():
        return {"name": "intraday-signal-engine", "version": "0.1.0",
                "disclaimer": _DISCLAIMER, "live_orders": False}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/leaderboard", dependencies=[Depends(_require_token)])
    def leaderboard(
        date_str: str = Query(default=None, alias="date"),
        universe: int = Query(default=500, ge=10, le=3000),
        top: int = Query(default=20, ge=1, le=100),
        seed: int = 42, news: bool = True, ml: bool = False,
    ):
        from signal_engine.scan.harness import run_scan
        from signal_engine.universe.mock import MockUniverseProvider

        d = _parse_date(date_str)
        if not cal.is_trading_day(d):
            raise HTTPException(400, f"{d} is not an NSE trading day")
        uni = MockUniverseProvider(n=universe, seed=seed)
        res = run_scan(cfg, uni, d, as_of=time(11, 0), seed=seed, top_n=top,
                       with_news=news, with_ml=ml)
        return leaderboard_to_json(res, d)

    @app.get("/api/premarket", dependencies=[Depends(_require_token)])
    def premarket(date_str: str = Query(default=None, alias="date"), seed: int = 42):
        from signal_engine.premarket.briefing import build_briefing

        d = _parse_date(date_str)
        return premarket_to_json(build_briefing(cfg, day=d, seed=seed))

    @app.get("/api/backtest", dependencies=[Depends(_require_token)])
    def backtest(
        start: str = Query(default=None), days: int = Query(default=10, ge=1, le=120),
        seed: int = 42,
    ):
        from signal_engine.backtest.engine import run_backtest

        start_d = _parse_date(start) if start else date(2025, 6, 2)
        res = run_backtest(cfg, cfg.settings.watchlist, start_d, days, seed=seed)
        return backtest_to_json(res)

    @app.get("/api/chart/{symbol}", dependencies=[Depends(_require_token)])
    def chart(symbol: str, date_str: str = Query(default=None, alias="date"), seed: int = 42):
        d = _parse_date(date_str)
        df = generate_session(symbol, d, seed=seed, regime="trend_up")
        return chart_to_json(symbol, df, dict(cfg.settings.strategy.params))

    @app.websocket("/ws/chart/{symbol}")
    async def ws_chart(ws: WebSocket, symbol: str, date_str: str = None, seed: int = 42,
                       speed: float = 0.0):
        """Stream a session's bars to simulate the live tick->bar push (PLAN §11.6).

        ``speed`` is the delay (seconds) between bars; 0 = as fast as possible (tests).
        Replaces the live Dhan websocket in this offline build.
        """
        await ws.accept()
        d = _parse_date(date_str)
        df = generate_session(symbol, d, seed=seed, regime="trend_up")
        from signal_engine.ingestion.aggregator import BarAggregator
        agg = BarAggregator(symbol, 1)
        try:
            for tick in bars_to_ticks(df, symbol):
                bar = agg.add_tick(tick)
                if bar is not None:
                    await ws.send_json({"time": int(bar.ts.timestamp()), "open": bar.open,
                                        "high": bar.high, "low": bar.low, "close": bar.close})
                    if speed:
                        await asyncio.sleep(speed)
            last = agg.flush()
            if last is not None:
                await ws.send_json({"time": int(last.ts.timestamp()), "open": last.open,
                                    "high": last.high, "low": last.low, "close": last.close})
            await ws.send_json({"done": True})
        except WebSocketDisconnect:
            return

    return app


# uvicorn entrypoint: `uvicorn signal_engine.api.app:app`
app = create_app()
