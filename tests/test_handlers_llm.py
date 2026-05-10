"""Tests for /llm command handler — state display + on/off toggling +
config-floor refusal.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from naarad import db
from naarad.config import (
    BriefConfig,
    Config,
    EodhdConfig,
    LLMConfig,
    MorningConfig,
    SchedulesConfig,
    TelegramConfig,
    WaterConfig,
)
from naarad.handlers.llm import llm_command
from naarad.runtime import LLM_FLAG_KEY


def make_config(tmp_path: Path, *, llm_enabled: bool = True) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        llm=LLMConfig(enabled=llm_enabled),
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


def make_context(config: Config, args: list[str] | None = None):
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"config": config}),
        args=args or [],
    )


def make_update(chat_id: int = 42):
    message = AsyncMock()
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=message,
        callback_query=None,
    )


def _reply_text(update) -> str:
    update.message.reply_text.assert_awaited_once()
    return update.message.reply_text.await_args.args[0]


# ---- show state --------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_args_shows_on_state_when_enabled(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config))

    text = _reply_text(update)
    assert "<b>on</b>" in text


@pytest.mark.asyncio
async def test_no_args_shows_runtime_off_when_db_disabled(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, LLM_FLAG_KEY, "0")
    update = make_update()

    await llm_command(update, make_context(config))

    text = _reply_text(update)
    assert "<b>off</b>" in text
    assert "runtime" in text


@pytest.mark.asyncio
async def test_no_args_shows_config_off_state(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config))

    text = _reply_text(update)
    assert "config" in text
    assert "<b>off</b>" in text


# ---- toggle ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_off_disables_runtime_flag(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["off"]))

    assert db.get_setting(config.db_path, LLM_FLAG_KEY) == "0"


@pytest.mark.asyncio
async def test_on_enables_runtime_flag(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, LLM_FLAG_KEY, "0")
    update = make_update()

    await llm_command(update, make_context(config, args=["on"]))

    assert db.get_setting(config.db_path, LLM_FLAG_KEY) == "1"


@pytest.mark.asyncio
async def test_refuses_to_enable_when_config_floor_is_off(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["on"]))

    text = _reply_text(update)
    assert "Can't toggle" in text
    # And the DB flag must NOT have been mutated.
    assert db.get_setting(config.db_path, LLM_FLAG_KEY) is None


@pytest.mark.asyncio
async def test_invalid_arg_shows_usage(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["maybe"]))

    text = _reply_text(update)
    assert "Usage" in text
    # Flag stays untouched.
    assert db.get_setting(config.db_path, LLM_FLAG_KEY) is None


# ---- /llm test --------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_test_success_path(tmp_path: Path, monkeypatch) -> None:
    """/llm test acks, runs smoketest, edits ack with ✓ + line."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()
    ack = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=ack)

    async def _ok(config):
        return True, "🌙 Online and slightly bored."
    monkeypatch.setattr(
        "naarad.handlers.llm.run_smoketest", _ok,
    )

    await llm_command(update, make_context(config, args=["test"]))

    update.message.reply_text.assert_awaited_once()  # the "⏳ Testing LLM…" ack
    ack.edit_text.assert_awaited_once()
    text = ack.edit_text.await_args.args[0]
    assert "✓" in text
    assert "Online and slightly bored" in text


@pytest.mark.asyncio
async def test_llm_test_failure_path(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()
    ack = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=ack)

    async def _fail(config):
        return False, "copilot CLI not found on PATH"
    monkeypatch.setattr(
        "naarad.handlers.llm.run_smoketest", _fail,
    )

    await llm_command(update, make_context(config, args=["test"]))

    text = ack.edit_text.await_args.args[0]
    assert "✗" in text
    assert "copilot CLI not found" in text


# ---- auth gate ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_unauthorized_chat_is_silently_dropped(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update(chat_id=999)  # not config.telegram.chat_id

    await llm_command(update, make_context(config, args=["off"]))

    update.message.reply_text.assert_not_awaited()
    assert db.get_setting(config.db_path, LLM_FLAG_KEY) is None
