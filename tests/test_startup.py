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
    TickersConfig,
    WaterConfig,
)
from naarad.startup import StartupValidationError, validate_startup


def make_config(
    tmp_path: Path,
    *,
    eodhd_key: str = "unused",
    tickers_enabled: bool = True,
    **telegram_overrides,
) -> Config:
    telegram = {
        "token": "123456:abcdefghijklmnopqrstuvwxyz0123456789",
        "chat_id": 42,
        **telegram_overrides,
    }
    return Config(
        telegram=TelegramConfig(**telegram),
        eodhd=EodhdConfig(api_key=eodhd_key),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        tickers=TickersConfig(enabled=tickers_enabled),
        tickers_default=[],
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


@pytest.fixture
def stub_llm_check(monkeypatch):
    """Don't actually shell out to the LLM CLI during these tests."""
    monkeypatch.setattr("naarad.startup._check_llm_backend", lambda config: None)


def test_passes_with_valid_config(tmp_path, stub_llm_check, caplog):
    cfg = make_config(tmp_path)
    with caplog.at_level(logging.INFO):
        validate_startup(cfg)  # should not raise
    assert "startup validation passed" in caplog.text


def test_rejects_empty_token(tmp_path, stub_llm_check):
    cfg = make_config(tmp_path, token="")
    with pytest.raises(StartupValidationError, match="token"):
        validate_startup(cfg)


def test_rejects_malformed_token(tmp_path, stub_llm_check):
    cfg = make_config(tmp_path, token="not-a-token")
    with pytest.raises(StartupValidationError, match="token"):
        validate_startup(cfg)


def test_rejects_short_token_secret(tmp_path, stub_llm_check):
    # token shape is digits:secret-of-at-least-20-chars
    cfg = make_config(tmp_path, token="123456:short")
    with pytest.raises(StartupValidationError, match="token"):
        validate_startup(cfg)


def test_rejects_zero_chat_id(tmp_path, stub_llm_check):
    cfg = make_config(tmp_path, chat_id=0)
    with pytest.raises(StartupValidationError, match="chat_id"):
        validate_startup(cfg)


def test_rejects_unwritable_db_path(tmp_path, stub_llm_check, monkeypatch):
    # Force mkdir to fail to simulate an unwritable parent.
    def boom(*args, **kwargs):
        raise OSError("permission denied")
    monkeypatch.setattr(Path, "mkdir", boom)
    cfg = make_config(tmp_path)
    with pytest.raises(StartupValidationError, match="db parent dir"):
        validate_startup(cfg)


def test_llm_check_logs_warning_on_missing_binary(tmp_path, monkeypatch, caplog):
    cfg = make_config(tmp_path)
    monkeypatch.setattr(
        "naarad.startup.resolve_bin",
        lambda backend: "definitely-not-a-binary-xyz",
    )
    with caplog.at_level(logging.WARNING):
        validate_startup(cfg)  # should NOT raise
    assert "CLI not found" in caplog.text
    assert "copilot" in caplog.text  # default backend label appears in the warning


def test_warns_on_empty_eodhd_key_when_tickers_enabled(tmp_path, stub_llm_check, caplog):
    cfg = make_config(tmp_path, eodhd_key="", tickers_enabled=True)
    with caplog.at_level(logging.WARNING):
        validate_startup(cfg)  # must NOT raise — bot should still boot
    assert any(
        "eodhd.api_key" in r.message and r.levelname == "WARNING"
        for r in caplog.records
    )


def test_warns_on_whitespace_only_eodhd_key_when_tickers_enabled(
    tmp_path, stub_llm_check, caplog
):
    cfg = make_config(tmp_path, eodhd_key="   ", tickers_enabled=True)
    with caplog.at_level(logging.WARNING):
        validate_startup(cfg)  # must NOT raise
    assert any(
        "eodhd.api_key" in r.message and r.levelname == "WARNING"
        for r in caplog.records
    )


def test_skips_eodhd_check_when_tickers_disabled(tmp_path, stub_llm_check, caplog):
    # If the user explicitly turned tickers off at the config floor, an empty
    # EODHD key is fine — the feature is dormant anyway, no warning needed.
    cfg = make_config(tmp_path, eodhd_key="", tickers_enabled=False)
    with caplog.at_level(logging.WARNING):
        validate_startup(cfg)  # must not raise
    assert not any(
        "eodhd.api_key" in r.message for r in caplog.records
    )
