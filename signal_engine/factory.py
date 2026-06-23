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
    return ConsoleAlerter()


def build_broker(
    cfg: AppConfig,
    day: date,
    seed: int = 42,
    regime_map: Optional[Dict[str, str]] = None,
) -> BrokerAdapter:
    if cfg.env.data_source == "dhan":
        # Data-only, and gated: it will refuse to connect (see DhanBroker / ground rules).
        from signal_engine.brokers.dhan import DhanBroker

        return DhanBroker(cfg.env.dhan_client_id, cfg.env.dhan_access_token)
    from signal_engine.brokers.mock import MockBroker

    return MockBroker(day=day, seed=seed, regime_map=regime_map)
