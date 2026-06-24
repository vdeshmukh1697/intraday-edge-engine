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

    # Targets / exits (PLAN §5.2) — structure-aware exit construction.
    hard_floor_pct: float = 0.20          # safety stop floor, decoupled from target sizing
    target_atr_multiple: float = 2.0      # vol target = target_atr_multiple * atr_pct
    structure_buffer_pct: float = 0.05    # buffer placed inside a structure level
    vwap_band_mult: float = 2.0           # VWAP band = VWAP +/- vwap_band_mult * sigma
    round_number_step_pct: float = 0.5    # round-number level granularity as % of price

    # Sizing / risk (PLAN §5.3) — capital-aware position sizing (user overrides capital).
    account_capital: float = 100000.0     # reference capital for qty; user overrides
    risk_per_trade_pct: float = 0.5       # % of capital risked per trade
    kelly_fraction_cap: float = 0.25      # cap on Kelly fraction when sizing
    max_consecutive_losses: int = 4       # halt after N straight losses


class CostParams(BaseModel):
    brokerage_flat: float = 20.0
    brokerage_pct: float = 0.0003
    stt_pct: float = 0.00025
    exchange_txn_pct: float = 0.0000297
    gst_pct: float = 0.18
    sebi_pct: float = 0.000001
    stamp_pct: float = 0.00003
    reference_trade_value: float = 100000.0
    slippage_scalar: float = 1.0          # multiplies slippage_pct in the breakeven/cost gate


class AlertParams(BaseModel):
    min_realert_seconds: int = 180        # minimum gap before re-alerting the same setup
    entry_band_bps: int = 25              # re-alert hysteresis band (basis points)
    top_n_alerts: int = 0                 # max alerts/day; 0 => use risk.max_trades_per_day


class SlippageParams(BaseModel):
    pct_per_side: float = 0.03


class LiquidityParams(BaseModel):
    min_avg_daily_turnover_cr: float = 25.0
    max_spread_pct: float = 0.10
    min_price: float = 20.0


class RiskConfig(BaseModel):
    risk: RiskParams = Field(default_factory=RiskParams)
    costs: CostParams = Field(default_factory=CostParams)
    alerts: AlertParams = Field(default_factory=AlertParams)
    slippage: SlippageParams = Field(default_factory=SlippageParams)
    liquidity: LiquidityParams = Field(default_factory=LiquidityParams)


# --------------------------------------------------------------------------- #
# Environment (secrets + runtime mode)
# --------------------------------------------------------------------------- #
_DOTENV_LOADED = False


def _load_dotenv(path: Optional[Path] = None) -> None:
    """Minimal, dependency-free ``.env`` loader for local (non-Docker) runs.

    Reads ``KEY=VALUE`` lines from the repo-root ``.env`` into ``os.environ``.
    Real environment variables already set take precedence (so Docker's
    ``env_file`` and explicit exports are never overridden). Runs once.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


class EnvConfig(BaseModel):
    data_source: str = "mock"            # "mock" | "yahoo_nse" | "angelone" | "dhan"
    news_source: str = "mock"            # "mock" | "rss" (rss = live current headlines)
    cues_source: str = "mock"            # "mock" | "yahoo" (yahoo = live yfinance cues)
    allow_live_orders: bool = False      # safety switch; live orders are NOT implemented
    alerter: str = "console"             # "console" | "telegram" | "whatsapp" | "callmebot"
    dhan_client_id: Optional[str] = None
    dhan_access_token: Optional[str] = None
    dhan_api_key: Optional[str] = None        # 12-month app key for the consent (OTP) flow
    dhan_api_secret: Optional[str] = None
    dhan_redirect_url: Optional[str] = None   # registered Dhan redirect (the Vercel callback)
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    whatsapp_phone_id: Optional[str] = None
    whatsapp_token: Optional[str] = None
    whatsapp_to: Optional[str] = None
    callmebot_phone: Optional[str] = None
    callmebot_apikey: Optional[str] = None
    angelone_api_key: Optional[str] = None
    angelone_client_id: Optional[str] = None
    angelone_password: Optional[str] = None
    angelone_totp_secret: Optional[str] = None
    db_url: str = "sqlite:///data/signal_engine.sqlite3"
    parquet_dir: str = "data/parquet"

    @classmethod
    def from_env(cls) -> "EnvConfig":
        _load_dotenv()  # populate os.environ from .env for local (non-Docker) runs

        def _bool(v: Optional[str]) -> bool:
            return str(v).strip().lower() in ("1", "true", "yes", "on")

        return cls(
            data_source=os.getenv("SE_DATA_SOURCE", "mock"),
            news_source=os.getenv("SE_NEWS_SOURCE", "mock"),
            cues_source=os.getenv("SE_CUES_SOURCE", "mock"),
            allow_live_orders=_bool(os.getenv("SE_ALLOW_LIVE_ORDERS", "false")),
            alerter=os.getenv("SE_ALERTER", "console"),
            dhan_client_id=os.getenv("DHAN_CLIENT_ID") or None,
            dhan_access_token=os.getenv("DHAN_ACCESS_TOKEN") or None,
            dhan_api_key=os.getenv("DHAN_API_KEY") or None,
            dhan_api_secret=os.getenv("DHAN_API_SECRET") or None,
            dhan_redirect_url=os.getenv("DHAN_REDIRECT_URL") or None,
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            whatsapp_phone_id=os.getenv("WHATSAPP_PHONE_ID") or None,
            whatsapp_token=os.getenv("WHATSAPP_TOKEN") or None,
            whatsapp_to=os.getenv("WHATSAPP_TO") or None,
            callmebot_phone=os.getenv("CALLMEBOT_PHONE") or None,
            callmebot_apikey=os.getenv("CALLMEBOT_APIKEY") or None,
            angelone_api_key=os.getenv("ANGELONE_API_KEY") or None,
            angelone_client_id=os.getenv("ANGELONE_CLIENT_ID") or None,
            angelone_password=os.getenv("ANGELONE_PASSWORD") or None,
            angelone_totp_secret=os.getenv("ANGELONE_TOTP_SECRET") or None,
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
