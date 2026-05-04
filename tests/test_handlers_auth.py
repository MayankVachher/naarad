"""Tests for the auth gate (`reject_unauthorized`).

This is the single chokepoint protecting every handler. We exercise it
directly so handler-level tests can stub it out cleanly.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from naarad.handlers.auth import is_authorized, reject_unauthorized


def make_config() -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        schedules=SchedulesConfig(),
    )


def make_context(config: Config):
    return SimpleNamespace(application=SimpleNamespace(bot_data={"config": config}))


def make_update(*, chat_id: int | None, with_callback: bool = False):
    chat = SimpleNamespace(id=chat_id) if chat_id is not None else None
    callback = AsyncMock() if with_callback else None
    return SimpleNamespace(effective_chat=chat, callback_query=callback)


# ---- is_authorized -----------------------------------------------------------

def test_is_authorized_matches_chat_id() -> None:
    config = make_config()
    update = make_update(chat_id=42)
    assert is_authorized(update, config) is True


def test_is_authorized_rejects_other_chats() -> None:
    config = make_config()
    update = make_update(chat_id=999)
    assert is_authorized(update, config) is False


def test_is_authorized_handles_missing_chat() -> None:
    config = make_config()
    update = make_update(chat_id=None)
    assert is_authorized(update, config) is False


# ---- reject_unauthorized -----------------------------------------------------

@pytest.mark.asyncio
async def test_reject_returns_false_for_authorized() -> None:
    config = make_config()
    rejected = await reject_unauthorized(make_update(chat_id=42), make_context(config))
    assert rejected is False


@pytest.mark.asyncio
async def test_reject_returns_true_for_other_chat() -> None:
    config = make_config()
    rejected = await reject_unauthorized(make_update(chat_id=999), make_context(config))
    assert rejected is True


@pytest.mark.asyncio
async def test_reject_answers_unauthorized_callback_query() -> None:
    """Stale taps from another chat should be answered (so the client UI
    doesn't spin), but the handler still rejects."""
    config = make_config()
    update = make_update(chat_id=999, with_callback=True)
    rejected = await reject_unauthorized(update, make_context(config))
    assert rejected is True
    update.callback_query.answer.assert_awaited_once()
