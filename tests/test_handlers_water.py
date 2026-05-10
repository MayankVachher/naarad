"""Tests for the /water + button + reply handlers.

Each path: confirm via scheduler.confirm_drink (returns the new glass
count) → edit the prior reminder with "✅ Glass #N logged at HH:MM" →
reply with "💧 Glass #N logged. Next nudge in 2h." Auth gate drops
unauthorized chats.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

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
    TickersConfig,
    WaterConfig,
)
from naarad.handlers import water as water_handlers
from naarad.water.scheduler import water_config_from

TZ = ZoneInfo("America/Toronto")


def make_config(tmp_path: Path) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(
            active_end="21:00",
            intervals_minutes=[120, 60, 30, 15, 5],
        ),
        brief=BriefConfig(),
        morning=MorningConfig(),
        llm=LLMConfig(),
        tickers=TickersConfig(),
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


def make_application(config: Config) -> SimpleNamespace:
    bot = AsyncMock()
    return SimpleNamespace(
        bot=bot,
        bot_data={
            "config": config,
            "water_cfg": water_config_from(config),
            "water_lock": asyncio.Lock(),
        },
        job_queue=None,
    )


def make_context(app):
    ctx = SimpleNamespace(application=app)
    ctx.bot = app.bot
    return ctx


# ---- /water -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_water_command_replies_with_glass_count(tmp_path, monkeypatch):
    """First /water tap with day already started → glass #1 in reply."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    # Pretend day is started — otherwise confirm_drink still increments
    # but next_action returns Idle, which doesn't affect the response.
    db.update_water_state(
        config.db_path,
        day_started_on=date(2026, 5, 2),
        chain_started_at=datetime(2026, 5, 2, 8, 30, tzinfo=TZ),
    )
    app = make_application(config)
    ctx = make_context(app)

    message = AsyncMock()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=42),
        message=message,
        callback_query=None,
    )

    with patch("naarad.handlers.water.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 2, 9, 0, tzinfo=TZ)
        await water_handlers.water_command(update, ctx)

    message.reply_text.assert_awaited_once()
    text = message.reply_text.await_args.args[0]
    assert "#1" in text
    assert "2h" in text  # intervals_minutes[0] = 120 → "2h"

    # State reflects the increment.
    assert db.get_water_state(config.db_path)["glasses_today"] == 1


@pytest.mark.asyncio
async def test_water_command_increments_across_calls(tmp_path):
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.update_water_state(
        config.db_path,
        day_started_on=date(2026, 5, 2),
        chain_started_at=datetime(2026, 5, 2, 8, 30, tzinfo=TZ),
    )
    app = make_application(config)
    ctx = make_context(app)
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=42),
        message=AsyncMock(),
        callback_query=None,
    )

    with patch("naarad.handlers.water.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 2, 9, 0, tzinfo=TZ)
        await water_handlers.water_command(update, ctx)
        await water_handlers.water_command(update, ctx)
        await water_handlers.water_command(update, ctx)

    assert db.get_water_state(config.db_path)["glasses_today"] == 3
    # Last reply mentions glass #3.
    last_text = update.message.reply_text.await_args_list[-1].args[0]
    assert "#3" in last_text


# ---- 💧 button --------------------------------------------------------------

@pytest.mark.asyncio
async def test_water_button_edits_reminder_with_glass_count(tmp_path):
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.update_water_state(
        config.db_path,
        day_started_on=date(2026, 5, 2),
        chain_started_at=datetime(2026, 5, 2, 8, 30, tzinfo=TZ),
    )
    app = make_application(config)
    ctx = make_context(app)

    query = AsyncMock()
    query.message = SimpleNamespace(
        chat_id=42,
        message_id=100,
        text="💧 Time for water",
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=42),
        callback_query=query,
    )

    with patch("naarad.handlers.water.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 2, 9, 15, tzinfo=TZ)
        await water_handlers.water_button(update, ctx)

    # The bot edited the reminder text to include glass count + time.
    app.bot.edit_message_text.assert_awaited_once()
    edit_text = app.bot.edit_message_text.await_args.kwargs["text"]
    assert "Glass #1 logged" in edit_text
    assert "09:15" in edit_text


# ---- auth gate --------------------------------------------------------------

@pytest.mark.asyncio
async def test_water_command_unauthorized_dropped(tmp_path):
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    app = make_application(config)
    ctx = make_context(app)

    message = AsyncMock()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=999),  # not config.telegram.chat_id
        message=message,
        callback_query=None,
    )

    await water_handlers.water_command(update, ctx)

    message.reply_text.assert_not_awaited()
    # State untouched.
    assert db.get_water_state(config.db_path)["glasses_today"] == 0
