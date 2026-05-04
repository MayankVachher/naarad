"""Tests for TickersConfig validation + market_tz property."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from naarad.config import TickersConfig


def test_defaults_enabled_with_ny_market_tz() -> None:
    cfg = TickersConfig()
    assert cfg.enabled is True
    assert cfg.market_timezone == "America/New_York"
    assert cfg.market_tz == ZoneInfo("America/New_York")


def test_disabled_floor_round_trips() -> None:
    cfg = TickersConfig(enabled=False)
    assert cfg.enabled is False
    # Even when disabled, market_timezone is still expected to be valid.
    assert cfg.market_timezone == "America/New_York"


def test_custom_valid_market_tz() -> None:
    cfg = TickersConfig(market_timezone="America/Toronto")
    assert cfg.market_tz == ZoneInfo("America/Toronto")


def test_invalid_market_tz_raises() -> None:
    with pytest.raises(ValueError, match="unknown market_timezone"):
        TickersConfig(market_timezone="Mars/Olympus_Mons")
