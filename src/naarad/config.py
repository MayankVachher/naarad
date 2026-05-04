"""Config loader: reads config.json (the single secrets surface).

The config file is treated as a secret — gitignored, chmod 600.
Validation happens via pydantic so misconfiguration fails loud at startup.
"""
from __future__ import annotations

import json
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


def _parse_hhmm(value: str) -> dtime:
    hh, mm = value.split(":")
    return dtime(int(hh), int(mm))


class TelegramConfig(BaseModel):
    token: str
    chat_id: int


class EodhdConfig(BaseModel):
    api_key: str


class WaterConfig(BaseModel):
    active_end: str = "21:00"
    intervals_minutes: list[int] = Field(default_factory=lambda: [120, 60, 30, 15, 5])

    @field_validator("intervals_minutes")
    @classmethod
    def _intervals_nonempty(cls, v: list[int]) -> list[int]:
        if not v or any(i <= 0 for i in v):
            raise ValueError("intervals_minutes must be a non-empty list of positive ints")
        return v

    @property
    def active_end_time(self) -> dtime:
        return _parse_hhmm(self.active_end)


class BriefConfig(BaseModel):
    location_name: str = "Toronto"
    location_lat: float = 43.6532
    location_lon: float = -79.3832


class MorningConfig(BaseModel):
    start_time: str = "06:00"      # When the brief is generated and sent
    fallback_time: str = "11:00"   # Auto-start water chain if no Start tap by this time

    @property
    def start_time_t(self) -> dtime:
        return _parse_hhmm(self.start_time)

    @property
    def fallback_time_t(self) -> dtime:
        return _parse_hhmm(self.fallback_time)


class SchedulesConfig(BaseModel):
    market_open: str = "09:35"
    market_close: str = "16:05"


class Config(BaseModel):
    telegram: TelegramConfig
    eodhd: EodhdConfig
    timezone: str = "America/Toronto"
    water: WaterConfig = Field(default_factory=WaterConfig)
    brief: BriefConfig = Field(default_factory=BriefConfig)
    morning: MorningConfig = Field(default_factory=MorningConfig)
    tickers_default: list[str] = Field(default_factory=list)
    schedules: SchedulesConfig = Field(default_factory=SchedulesConfig)
    db_path: str = "state.db"

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
