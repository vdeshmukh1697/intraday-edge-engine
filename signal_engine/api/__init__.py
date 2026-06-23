"""FastAPI engine API (PLAN §7.1, §11.6): serves leaderboard/health/premarket/chart data
to the Vercel dashboard over HTTPS + WebSocket. Read-only — never places orders."""

from signal_engine.api.app import create_app

__all__ = ["create_app"]
