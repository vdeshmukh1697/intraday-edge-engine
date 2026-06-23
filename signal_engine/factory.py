"""Factories that build the concrete components from config + environment.

Keeps construction/wiring in one place and enforces the safety default: the mock
data source unless the operator explicitly opts into (data-only, gated) Dhan.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, Optional

from signal_engine.alerts.base import Alerter
from signal_engine.alerts.console import ConsoleAlerter
from signal_engine.brokers.base import BrokerAdapter
from signal_engine.config import AppConfig


def build_alerter(cfg: AppConfig) -> Alerter:
    if cfg.env.alerter == "telegram":
        from signal_engine.alerts.telegram import TelegramAlerter

        return TelegramAlerter(cfg.env.telegram_bot_token, cfg.env.telegram_chat_id)
    if cfg.env.alerter == "whatsapp":
        from signal_engine.alerts.whatsapp import WhatsAppAlerter

        return WhatsAppAlerter(cfg.env.whatsapp_phone_id, cfg.env.whatsapp_token, cfg.env.whatsapp_to)
    if cfg.env.alerter == "callmebot":
        from signal_engine.alerts.callmebot import CallMeBotAlerter

        return CallMeBotAlerter(cfg.env.callmebot_phone, cfg.env.callmebot_apikey)
    return ConsoleAlerter()


def build_broker(
    cfg: AppConfig,
    day: date,
    seed: int = 42,
    regime_map: Optional[Dict[str, str]] = None,
) -> BrokerAdapter:
    if cfg.env.data_source == "yahoo_nse":
        from signal_engine.brokers.yahoo_nse import YahooNSEBroker

        return YahooNSEBroker()
    if cfg.env.data_source == "angelone":
        from signal_engine.brokers.angelone import AngelOneBroker

        return AngelOneBroker(
            api_key=cfg.env.angelone_api_key,
            client_id=cfg.env.angelone_client_id,
            password=cfg.env.angelone_password,
            totp_secret=cfg.env.angelone_totp_secret,
        )
    if cfg.env.data_source == "dhan":
        from signal_engine.brokers.dhan import DhanBroker
        from signal_engine.universe.instruments import DhanInstrumentMaster

        # Dhan addresses instruments by numeric security_id, so quote/historical and the
        # live feed need the (free, no-auth) scrip master loaded to map symbols both ways.
        instruments = DhanInstrumentMaster.fetch()
        return DhanBroker(cfg.env.dhan_client_id, cfg.env.dhan_access_token,
                          instruments=instruments)
    from signal_engine.brokers.mock import MockBroker

    return MockBroker(day=day, seed=seed, regime_map=regime_map)


def build_cues_provider(cfg: AppConfig):
    """Real Yahoo cues if SE_CUES_SOURCE=yahoo, else None (caller falls back to mock)."""
    if cfg.env.cues_source == "yahoo":
        from signal_engine.premarket.yahoo_cues import YahooCuesProvider

        return YahooCuesProvider()
    return None


def build_news_provider(cfg: AppConfig):
    """Real RSS provider if SE_NEWS_SOURCE=rss (live current headlines), else None (mock).

    Note: RSS yields *current* headlines — meaningful for live/today runs, not for historical
    backtest dates (use mock there).
    """
    if cfg.env.news_source == "rss":
        from signal_engine.news.rss import RSSNewsProvider

        return RSSNewsProvider()
    return None
