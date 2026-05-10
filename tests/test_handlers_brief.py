"""Tests for /brief command handler — ack + edit pattern.

The handler now composes its own LLMTask and calls render(). Tests
patch ``brief_handlers.render`` (the imported name) so each test can
control whether the LLM "succeeds" (returns body text) or "fails"
(returns the failure sentinel and triggers the user-visible error).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from naarad import db
from naarad.config import (
    BriefConfig,
    Config,
    EodhdConfig,
    MorningConfig,
    SchedulesConfig,
    TelegramConfig,
    WaterConfig,
)
from naarad.handlers import brief as brief_handlers
from naarad.jobs.daily_brief import LAST_BRIEF_SETTING


def make_config(tmp_path: Path) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


def make_context(config: Config):
    return SimpleNamespace(application=SimpleNamespace(bot_data={"config": config}))


def make_update(chat_id: int = 42):
    ack = AsyncMock()
    message = AsyncMock()
    message.reply_text = AsyncMock(return_value=ack)
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=message,
        callback_query=None,
    ), ack


def stub_render(monkeypatch, body: str) -> None:
    """Replace handlers.brief.render with a coroutine that returns ``body``.

    Bypasses both the LLM CLI and the LLMTask post-process / fallback
    plumbing — handler tests just want to control "what body did render
    produce?".
    """
    async def _fake(task, config):
        return body
    monkeypatch.setattr(brief_handlers, "render", _fake)


@pytest.mark.asyncio
async def test_brief_acks_then_edits_with_body(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update, ack = make_update()
    stub_render(monkeypatch, "<b>Test brief body</b>")

    await brief_handlers.brief_command(update, make_context(config))

    update.message.reply_text.assert_awaited_once()
    ack.edit_text.assert_awaited_once()
    edit_args = ack.edit_text.await_args
    assert edit_args.args[0] == "<b>Test brief body</b>"
    assert edit_args.kwargs.get("parse_mode") == "HTML"


@pytest.mark.asyncio
async def test_brief_shows_error_on_llm_failure(tmp_path: Path, monkeypatch) -> None:
    """When the LLM fails, /brief tells the user explicitly instead of
    silently demoting to the deterministic plain renderer (which is the
    scheduled flow's job)."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update, ack = make_update()
    stub_render(monkeypatch, brief_handlers._FAILURE_SENTINEL)

    await brief_handlers.brief_command(update, make_context(config))

    ack.edit_text.assert_awaited_once()
    msg = ack.edit_text.await_args.args[0]
    assert "❌" in msg or "failed" in msg.lower()
    # And the marker MUST NOT be set, so a later catch-up still fires.
    assert db.get_setting(config.db_path, LAST_BRIEF_SETTING) is None


@pytest.mark.asyncio
async def test_brief_falls_back_to_new_message_when_edit_fails(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update, ack = make_update()
    ack.edit_text = AsyncMock(side_effect=RuntimeError("can't edit"))
    stub_render(monkeypatch, "<b>Body</b>")

    await brief_handlers.brief_command(update, make_context(config))

    # Two reply_text awaits: once for the ack, once for the fallback send.
    assert update.message.reply_text.await_count == 2
    # The fallback path must still set the marker so the morning catch-up
    # doesn't fire a redundant brief later.
    today_iso = datetime.now(config.tz).date().isoformat()
    assert db.get_setting(config.db_path, LAST_BRIEF_SETTING) == today_iso


@pytest.mark.asyncio
async def test_brief_records_last_brief_marker_on_success(
    tmp_path: Path, monkeypatch
) -> None:
    """A successful /brief must set last_brief_on so the morning catch-up
    doesn't fire a redundant scheduled brief later that day.
    """
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update, ack = make_update()
    stub_render(monkeypatch, "<b>Body</b>")

    await brief_handlers.brief_command(update, make_context(config))

    today_iso = datetime.now(config.tz).date().isoformat()
    assert db.get_setting(config.db_path, LAST_BRIEF_SETTING) == today_iso


@pytest.mark.asyncio
async def test_brief_unauthorized_chat_is_dropped(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    update, ack = make_update(chat_id=999)
    rendered = []

    async def _track(task, config):
        rendered.append(True)
        return "x"
    monkeypatch.setattr(brief_handlers, "render", _track)

    await brief_handlers.brief_command(update, make_context(config))

    update.message.reply_text.assert_not_awaited()
    assert rendered == []
