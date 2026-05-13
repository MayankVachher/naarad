"""Config loader: reads config.json (the single secrets surface).

The config file is treated as a secret — gitignored, chmod 600.
Validation happens via pydantic so misconfiguration fails loud at startup.
"""
from __future__ import annotations

import json
from datetime import time as dtime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


def _parse_hhmm(value: str) -> dtime:
    hh, mm = value.split(":")
    return dtime(int(hh), int(mm))


class TelegramConfig(BaseModel):
    token: str = Field(description="BotFather token.")
    chat_id: int = Field(description="Your personal chat ID — the bot only talks to this chat.")


class EodhdConfig(BaseModel):
    api_key: str = Field(description="EODHD API key (real-time quotes + per-exchange holiday calendar).")


class LLMConfig(BaseModel):
    """Compile-time floor for LLM features.

    `enabled: false` permanently disables LLM-powered brief + water reminders;
    the runtime `/llm` command can't override this. When `enabled: true`, the
    runtime DB-backed flag (settings.llm_enabled) decides the live state.

    `backend` selects which CLI to shell out to:
      - ``"copilot"`` → GitHub Copilot CLI (default; needs ``copilot auth login``)
      - ``"claude"``  → Anthropic Claude Code CLI (needs ``claude login`` or
        ``ANTHROPIC_API_KEY``)
    """
    enabled: bool = Field(default=True, description="Compile-time floor for LLM features. `false` makes the `/llm` toggle inert.")
    backend: Literal["copilot", "claude"] = Field(default="copilot", description="Which CLI to shell out to: `\"copilot\"` or `\"claude\"`.")


class TickersConfig(BaseModel):
    """Compile-time floor + market-clock TZ for ticker features.

    `enabled: false` permanently disables the market_open/market_close jobs
    and the `/quote` command; the runtime `/ticker on|off` toggle is inert
    in that case. When `enabled: true`, `settings.tickers_enabled` decides
    the live state.

    `market_timezone` is the wall-clock timezone the open/close schedules
    fire in (default America/New_York). It's deliberately separate from the
    user-facing `Config.timezone` so the morning/water schedulers can stay
    on local time without dragging the market jobs along.
    """
    enabled: bool = Field(default=True, description="Compile-time floor for the market jobs + `/quote`. `false` makes the `/ticker on|off` toggle inert.")
    market_timezone: str = Field(default="America/New_York", description="Timezone the market_open/market_close jobs fire in; separate from user-facing `timezone`.")

    @field_validator("market_timezone")
    @classmethod
    def _valid_market_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown market_timezone: {v}") from exc
        return v

    @property
    def market_tz(self) -> ZoneInfo:
        return ZoneInfo(self.market_timezone)


class WaterConfig(BaseModel):
    active_end: str = Field(default="21:00", description="After this time-of-day, no reminders fire until next morning's start.")
    intervals_minutes: list[int] = Field(
        default_factory=lambda: [120, 60, 30, 15, 5],
        description="Escalation curve. The Nth value is the gap before reminder N+1 if you keep ignoring them.",
    )
    first_reminder_delay_minutes: int = Field(
        default=3,
        description="Grace between the morning brief's [Start day] tap and the first reminder. Welcome-button taps bypass it.",
    )
    daily_target_glasses: int = Field(
        default=8,
        description="Target glass count per day. Drives `/status` progress + pace-adjusted intervals. Set `0` to disable pace adjustment.",
    )
    pace_floor: float = Field(
        default=0.3,
        description="Minimum interval multiplier when behind pace. Caps the squeeze (default 0.3 = at most ~3× normal cadence). Range (0, 1].",
    )

    @field_validator("intervals_minutes")
    @classmethod
    def _intervals_nonempty(cls, v: list[int]) -> list[int]:
        if not v or any(i <= 0 for i in v):
            raise ValueError("intervals_minutes must be a non-empty list of positive ints")
        return v

    @field_validator("first_reminder_delay_minutes")
    @classmethod
    def _grace_nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("first_reminder_delay_minutes must be ≥ 0")
        return v

    @field_validator("daily_target_glasses")
    @classmethod
    def _target_nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("daily_target_glasses must be ≥ 0 (0 disables pace)")
        return v

    @field_validator("pace_floor")
    @classmethod
    def _pace_floor_in_range(cls, v: float) -> float:
        if not (0.0 < v <= 1.0):
            raise ValueError("pace_floor must be in (0, 1]")
        return v

    @property
    def active_end_time(self) -> dtime:
        return _parse_hhmm(self.active_end)


class BriefConfig(BaseModel):
    location_name: str = Field(default="Toronto", description="City name shown in the morning brief.")
    location_lat: float = Field(default=43.6532, description="Latitude for the weather + sunrise lookup.")
    location_lon: float = Field(default=-79.3832, description="Longitude for the weather + sunrise lookup.")


class MorningConfig(BaseModel):
    start_time: str = Field(default="06:00", description="When the daily brief is generated and sent.")
    fallback_time: str = Field(default="11:00", description="If you haven't tapped [Start day] by this time, the water chain auto-starts.")

    @property
    def start_time_t(self) -> dtime:
        return _parse_hhmm(self.start_time)

    @property
    def fallback_time_t(self) -> dtime:
        return _parse_hhmm(self.fallback_time)


class SchedulesConfig(BaseModel):
    market_open: str = Field(default="09:35", description="Market-open snapshot time in `tickers.market_timezone`.")
    market_close: str = Field(default="16:05", description="Market-close snapshot time in `tickers.market_timezone`.")


class Config(BaseModel):
    telegram: TelegramConfig
    eodhd: EodhdConfig
    timezone: str = Field(default="America/Toronto", description="IANA timezone for water + morning schedules.")
    water: WaterConfig = Field(default_factory=WaterConfig)
    brief: BriefConfig = Field(default_factory=BriefConfig)
    morning: MorningConfig = Field(default_factory=MorningConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tickers: TickersConfig = Field(default_factory=TickersConfig)
    tickers_default: list[str] = Field(default_factory=list, description="Seed tickers planted into the DB on first init. Edits after first boot go through `/ticker`.")
    schedules: SchedulesConfig = Field(default_factory=SchedulesConfig)
    db_path: str = Field(default="state.db", description="SQLite file path.")

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {v}") from exc
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.json"


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate config.json. Pass an explicit path or rely on the project root."""
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.json not found at {config_path}. "
            "Copy config.example.json -> config.json and fill in secrets."
        )
    with config_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return Config.model_validate(raw)
