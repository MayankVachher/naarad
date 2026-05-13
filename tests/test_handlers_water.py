"""Tests for the /water + button + reply handlers.

Logging paths (``/water log``, button, reply): confirm via
scheduler.confirm_drink (returns the new glass count) → edit the prior
reminder with "✅ Glass #N logged at HH:MM" → reply with the multi-line
confirm response. ``/water`` with no args is read-only and returns a
status snapshot. Auth gate drops unauthorized chats.
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


def make_context(app, args: list[str] | None = None):
    ctx = SimpleNamespace(application=app, args=args or [])
    ctx.bot = app.bot
    return ctx


# ---- /water -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_water_log_replies_with_glass_count(tmp_path, monkeypatch):
    """First /water log with day already started → glass #1 in reply."""
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
    ctx = make_context(app, args=["log"])

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
    # Glass count formatted as #N/8 (default target).
    assert "#1/8" in text
    # Either a pace badge or the next-reminder time line.
    assert "Next reminder at" in text or "No more reminders" in text

    # State reflects the increment.
    assert db.get_water_state(config.db_path)["glasses_today"] == 1


@pytest.mark.asyncio
async def test_water_log_increments_across_calls(tmp_path):
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.update_water_state(
        config.db_path,
        day_started_on=date(2026, 5, 2),
        chain_started_at=datetime(2026, 5, 2, 8, 30, tzinfo=TZ),
    )
    app = make_application(config)
    ctx = make_context(app, args=["log"])
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


@pytest.mark.asyncio
async def test_water_no_args_is_read_only_status(tmp_path):
    """/water with no args replies with a status snapshot, no DB mutation."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.update_water_state(
        config.db_path,
        day_started_on=date(2026, 5, 2),
        chain_started_at=datetime(2026, 5, 2, 8, 30, tzinfo=TZ),
        glasses_today=2,
    )
    app = make_application(config)
    ctx = make_context(app)  # no args → status path

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
    # Status reflects current count without "logged" framing.
    assert "2/8 today" in text
    assert "logged" not in text
    # Panel attaches the [💧 Log glass] button — no text hint needed.
    markup = message.reply_text.await_args.kwargs.get("reply_markup")
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("Log glass" in lbl for lbl in labels)
    # State untouched (no increment).
    assert db.get_water_state(config.db_path)["glasses_today"] == 2


@pytest.mark.asyncio
async def test_water_panel_log_button_increments_and_edits_in_place(tmp_path):
    """Tapping [💧 Log glass] on the /water panel confirms a glass and
    edits the same message in place with the refreshed status + the
    same button (so the user can tap again).
    """
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.update_water_state(
        config.db_path,
        day_started_on=date(2026, 5, 2),
        chain_started_at=datetime(2026, 5, 2, 8, 30, tzinfo=TZ),
        glasses_today=1,
    )
    app = make_application(config)
    ctx = make_context(app)

    query = AsyncMock()
    query.message = AsyncMock()
    query.message.chat_id = 42
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=42),
        message=None,
        callback_query=query,
    )

    with patch("naarad.handlers.water.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 2, 9, 30, tzinfo=TZ)
        await water_handlers.water_panel_log(update, ctx)

    # DB incremented.
    assert db.get_water_state(config.db_path)["glasses_today"] == 2

    # Panel edited in place with the new count and the same button.
    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.await_args.args[0]
    assert "2/8 today" in text
    markup = query.message.edit_text.await_args.kwargs.get("reply_markup")
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("Log glass" in lbl for lbl in labels)


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

    # And it sent a follow-up multi-line confirm reply (pace + next
    # reminder), matching /water log so button taps don't silently hide
    # the pace info.
    app.bot.send_message.assert_awaited_once()
    reply_text = app.bot.send_message.await_args.kwargs["text"]
    assert "#1/8" in reply_text
    assert "logged" in reply_text


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
