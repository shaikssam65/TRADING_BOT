from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from bot.models import Mode

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "bot_state.json"
UI_SETTINGS_PATH = DATA_DIR / "ui_settings.json"

load_dotenv(ENV_PATH)


def get_secret(name: str, default: str = "") -> str:
    """Read from .env / OS env, then Streamlit Cloud secrets if present."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return default

ModeName = Literal["short", "swing"]
BrokerEnv = Literal["sandbox", "live"]


@dataclass(frozen=True)
class RiskLimits:
    max_positions: int = 3
    max_position_pct: float = 0.25
    cash_buffer_pct: float = 0.20
    short_stop_pct: float = 0.05
    swing_stop_pct: float = 0.07
    short_take_profit_pct: float = 0.06
    swing_take_profit_pct: float = 0.10
    trail_activate_pct: float = 0.04
    trail_pct: float = 0.03
    daily_kill_pct: float = 0.03
    min_price: float = 2.0
    min_avg_volume: int = 500_000
    max_day_trades_5d: int = 3
    short_min_hold_days: int = 2
    swing_min_hold_days: int = 5
    short_horizon_days: int = 10
    swing_horizon_days: int = 22
    news_kill_sentiment: float = -0.35
    min_buy_confidence: float = 0.55
    max_risk_score: int = 7


@dataclass
class Settings:
    capital: float = 1000.0
    mode: Mode = "short"
    execute: bool = False
    live: bool = False
    watch: bool = False
    watch_seconds: int = 300
    top_n: int = 5
    finnhub_api_key: str = ""
    webull_app_key: str = ""
    webull_app_secret: str = ""
    webull_account_id: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-latest"
    price_filters: list[str] | None = None
    require_approval: bool = True
    risk: RiskLimits = RiskLimits()

    def __post_init__(self) -> None:
        if self.price_filters is None:
            self.price_filters = []

    @property
    def broker_env(self) -> BrokerEnv:
        return "live" if self.live else "sandbox"

    @property
    def horizon_days(self) -> int:
        if self.mode == "swing":
            return self.risk.swing_horizon_days
        return self.risk.short_horizon_days

    @property
    def stop_pct(self) -> float:
        return self.risk.swing_stop_pct if self.mode == "swing" else self.risk.short_stop_pct

    @property
    def take_profit_pct(self) -> float:
        return (
            self.risk.swing_take_profit_pct
            if self.mode == "swing"
            else self.risk.short_take_profit_pct
        )

    @property
    def min_hold_days(self) -> int:
        return (
            self.risk.swing_min_hold_days
            if self.mode == "swing"
            else self.risk.short_min_hold_days
        )


def load_settings(
    *,
    capital: float | None = None,
    mode: Mode | None = None,
    execute: bool = False,
    live: bool = False,
    watch: bool = False,
    watch_seconds: int = 300,
    top_n: int = 5,
    openai_model: str | None = None,
    price_filters: list[str] | None = None,
    require_approval: bool = True,
    risk: RiskLimits | None = None,
) -> Settings:
    env_live = get_secret("WEBULL_ENV", "sandbox").lower() == "live"
    model = openai_model or get_secret("OPENAI_MODEL", "gpt-5.6") or "gpt-5.6"
    return Settings(
        capital=float(capital if capital is not None else 1000.0),
        mode=mode or "short",
        execute=execute,
        live=live or env_live,
        watch=watch,
        watch_seconds=watch_seconds,
        top_n=top_n,
        finnhub_api_key=get_secret("FINNHUB_API_KEY"),
        webull_app_key=get_secret("WEBULL_APP_KEY"),
        webull_app_secret=get_secret("WEBULL_APP_SECRET"),
        webull_account_id=get_secret("WEBULL_ACCOUNT_ID"),
        openai_api_key=get_secret("OPENAI_API_KEY"),
        openai_model=model,
        anthropic_api_key=get_secret("ANTHROPIC_API_KEY"),
        anthropic_model=get_secret("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
        price_filters=list(price_filters or []),
        require_approval=require_approval,
        risk=risk or RiskLimits(),
    )


def load_ui_settings() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not UI_SETTINGS_PATH.exists():
        return {
            "capital": 1000.0,
            "mode": "short",
            "price_filters": ["top20_overall"],
            "top_n": 6,
            "openai_model": os.getenv("OPENAI_MODEL", "gpt-5.6") or "gpt-5.6",
            "execute_sandbox": True,
            "live": False,
        }
    raw = json.loads(UI_SETTINGS_PATH.read_text(encoding="utf-8"))
    old = raw.get("price_filters") or []
    valid = {"top10_under_10", "top10_under_100", "top20_overall"}
    if not old or any(x not in valid for x in old):
        raw["price_filters"] = ["top20_overall"]
    else:
        raw["price_filters"] = [old[0]]
    return raw


def save_ui_settings(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UI_SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
