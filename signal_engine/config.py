"""Configuration: typed (pydantic v2) models loaded from YAML + environment.

Non-secret settings live in ``config/settings.yaml`` and ``config/risk.yaml``.
Secrets and runtime mode come from environment variables / ``.env`` (see .env.example).
Nothing here reads secrets from the YAML files.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"


# --------------------------------------------------------------------------- #
# settings.yaml
# --------------------------------------------------------------------------- #
class MarketConfig(BaseModel):
    timezone: str = "Asia/Kolkata"
    pre_open_start: str = "09:00"
    session_open: str = "09:15"
    no_new_entry_after: str = "15:00"
    square_off: str = "15:20"
    session_close: str = "15:30"


class IngestionConfig(BaseModel):
    base_timeframe_minutes: int = 1
    rollup_timeframes: List[int] = Field(default_factory=lambda: [5, 15])


class StrategyConfig(BaseModel):
    active: str = "vwap_ema_adx"
    params: Dict[str, float] = Field(default_factory=dict)


class Settings(BaseModel):
    market: MarketConfig = Field(default_factory=MarketConfig)
    watchlist: List[str] = Field(default_factory=list)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)


# --------------------------------------------------------------------------- #
# risk.yaml
# --------------------------------------------------------------------------- #
class RiskParams(BaseModel):
    atr_stop_multiple: float = 1.5
    min_stop_pct: float = 0.30
    max_stop_pct: float = 3.0
    target_rr: float = 1.5
    rr_floor: float = 1.5
    second_target_rr: float = 2.5
    edge_cost_multiple: float = 3.0
    max_hold_minutes: int = 90
    daily_loss_pct: float = 2.0
    max_trades_per_day: int = 6
    max_concurrent_positions: int = 3
    per_symbol_cooldown_minutes: int = 15


class CostParams(BaseModel):
    brokerage_flat: float = 20.0
    brokerage_pct: float = 0.0003
    stt_pct: float = 0.00025
    exchange_txn_pct: float = 0.0000297
    gst_pct: float = 0.18
    sebi_pct: float = 0.000001
    stamp_pct: float = 0.00003
    reference_trade_value: float = 100000.0


class SlippageParams(BaseModel):
    pct_per_side: float = 0.03


class LiquidityParams(BaseModel):
    min_avg_daily_turnover_cr: float = 25.0
    max_spread_pct: float = 0.10
    min_price: float = 20.0


class RiskConfig(BaseModel):
    risk: RiskParams = Field(default_factory=RiskParams)
    costs: CostParams = Field(default_factory=CostParams)
    slippage: SlippageParams = Field(default_factory=SlippageParams)
    liquidity: LiquidityParams = Field(default_factory=LiquidityParams)


# --------------------------------------------------------------------------- #
# Environment (secrets + runtime mode)
# --------------------------------------------------------------------------- #
class EnvConfig(BaseModel):
    data_source: str = "mock"            # "mock" | "dhan"
    allow_live_orders: bool = False      # safety switch; live orders are NOT implemented
    alerter: str = "console"             # "console" | "telegram"
    dhan_client_id: Optional[str] = None
    dhan_access_token: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    db_url: str = "sqlite:///data/signal_engine.sqlite3"
    parquet_dir: str = "data/parquet"

    @classmethod
    def from_env(cls) -> "EnvConfig":
        def _bool(v: Optional[str]) -> bool:
            return str(v).strip().lower() in ("1", "true", "yes", "on")

        return cls(
            data_source=os.getenv("SE_DATA_SOURCE", "mock"),
            allow_live_orders=_bool(os.getenv("SE_ALLOW_LIVE_ORDERS", "false")),
            alerter=os.getenv("SE_ALERTER", "console"),
            dhan_client_id=os.getenv("DHAN_CLIENT_ID") or None,
            dhan_access_token=os.getenv("DHAN_ACCESS_TOKEN") or None,
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            db_url=os.getenv("SE_DB_URL", "sqlite:///data/signal_engine.sqlite3"),
            parquet_dir=os.getenv("SE_PARQUET_DIR", "data/parquet"),
        )


class AppConfig(BaseModel):
    """The single config object passed around the engine."""

    settings: Settings
    risk: RiskConfig
    env: EnvConfig


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(config_dir: Optional[Path] = None) -> AppConfig:
    """Load settings.yaml + risk.yaml + environment into a typed AppConfig.

    Missing YAML files fall back to the pydantic defaults, so this never crashes
    just because a config file is absent — useful for tests.
    """
    cdir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    settings = Settings(**_read_yaml(cdir / "settings.yaml"))
    risk = RiskConfig(**_read_yaml(cdir / "risk.yaml"))
    env = EnvConfig.from_env()
    return AppConfig(settings=settings, risk=risk, env=env)
