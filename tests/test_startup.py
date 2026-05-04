"""Unit tests for naarad.startup.validate_startup."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from naarad.config import (
    BriefConfig,
    Config,
    EodhdConfig,
    MorningConfig,
    SchedulesConfig,
    TelegramConfig,
    WaterConfig,
)
from naarad.startup import StartupValidationError, validate_startup


def make_config(tmp_path: Path, **telegram_overrides) -> Config:
    telegram = {
        "token": "123456:abcdefghijklmnopqrstuvwxyz0123456789",
        "chat_id": 42,
        **telegram_overrides,
    }
    return Config(
        telegram=TelegramConfig(**telegram),
        eodhd=EodhdConfig(api_key="unused"),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        tickers_default=[],
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


@pytest.fixture
def stub_copilot(monkeypatch):
    """Don't actually shell out to copilot during these tests."""
    monkeypatch.setattr("naarad.startup._check_copilot_available", lambda: None)


def test_passes_with_valid_config(tmp_path, stub_copilot, caplog):
    cfg = make_config(tmp_path)
    with caplog.at_level(logging.INFO):
        validate_startup(cfg)  # should not raise
    assert "startup validation passed" in caplog.text


def test_rejects_empty_token(tmp_path, stub_copilot):
    cfg = make_config(tmp_path, token="")
    with pytest.raises(StartupValidationError, match="token"):
        validate_startup(cfg)


def test_rejects_malformed_token(tmp_path, stub_copilot):
    cfg = make_config(tmp_path, token="not-a-token")
    with pytest.raises(StartupValidationError, match="token"):
        validate_startup(cfg)


def test_rejects_short_token_secret(tmp_path, stub_copilot):
    # token shape is digits:secret-of-at-least-20-chars
    cfg = make_config(tmp_path, token="123456:short")
    with pytest.raises(StartupValidationError, match="token"):
        validate_startup(cfg)


def test_rejects_zero_chat_id(tmp_path, stub_copilot):
    cfg = make_config(tmp_path, chat_id=0)
    with pytest.raises(StartupValidationError, match="chat_id"):
        validate_startup(cfg)


def test_rejects_unwritable_db_path(tmp_path, stub_copilot, monkeypatch):
    # Force mkdir to fail to simulate an unwritable parent.
    def boom(*args, **kwargs):
        raise OSError("permission denied")
    monkeypatch.setattr(Path, "mkdir", boom)
    cfg = make_config(tmp_path)
    with pytest.raises(StartupValidationError, match="db parent dir"):
        validate_startup(cfg)


def test_copilot_check_logs_warning_on_missing_binary(tmp_path, monkeypatch, caplog):
    cfg = make_config(tmp_path)
    monkeypatch.setattr("naarad.startup.copilot_bin", lambda: "definitely-not-a-binary-xyz")
    with caplog.at_level(logging.WARNING):
        validate_startup(cfg)  # should NOT raise
    assert "copilot CLI not found" in caplog.text
