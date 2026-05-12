"""Tests for the welcome handler: maybe_send_welcome, _run_smoketest_and_edit,
and the welcome_button callback.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
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
from naarad.handlers import welcome as welcome_handlers
from naarad.handlers.welcome import (
    WELCOME_SENT_SETTING,
    maybe_send_welcome,
    welcome_button,
)
from naarad.jobs.daily_brief import LAST_BRIEF_SETTING
from naarad.runtime import LLM_FLAG_KEY

TZ = ZoneInfo("America/Toronto")


def make_config(tmp_path: Path, *, llm_enabled: bool = True) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(active_end="21:00"),
        brief=BriefConfig(),
        morning=MorningConfig(start_time="06:00"),
        llm=LLMConfig(enabled=llm_enabled),
        tickers=TickersConfig(),
        tickers_default=["GOOGL", "NVDA"],
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


def make_app(config: Config) -> SimpleNamespace:
    bot = AsyncMock()
    sent_msg = MagicMock(message_id=999)
    bot.send_message = AsyncMock(return_value=sent_msg)
    return SimpleNamespace(bot=bot, bot_data={"config": config})


# ---- maybe_send_welcome -----------------------------------------------------

@pytest.mark.asyncio
async def test_first_call_sends_welcome_and_persists_marker(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)  # disabled to skip smoketest task
    db.init_db(config.db_path)
    app = make_app(config)

    sent = await maybe_send_welcome(app)

    assert sent is True
    app.bot.send_message.assert_awaited_once()
    body = app.bot.send_message.await_args.kwargs["text"]
    assert "Hello, I'm Naarad" in body
    # Config echo is present.
    assert "America/Toronto" in body
    assert "GOOGL" in body and "NVDA" in body
    # LLM disabled → disabled-line, NOT pending.
    assert "(disabled" in body
    # Marker persisted.
    assert db.get_setting(config.db_path, WELCOME_SENT_SETTING) == "1"


@pytest.mark.asyncio
async def test_first_call_claims_todays_brief_slot(tmp_path: Path) -> None:
    """The welcome doubles as the day's intro, so it sets
    LAST_BRIEF_SETTING=today — preventing brief catch-up from firing a
    second [Start day]-bearing message on a follow-up boot."""
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    app = make_app(config)

    with patch("naarad.handlers.welcome.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 12, 14, 0, tzinfo=TZ)
        await maybe_send_welcome(app)

    today_iso = "2026-05-12"
    assert db.get_setting(config.db_path, LAST_BRIEF_SETTING) == today_iso


@pytest.mark.asyncio
async def test_second_call_is_noop_when_marker_set(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, WELCOME_SENT_SETTING, "1")
    app = make_app(config)

    sent = await maybe_send_welcome(app)

    assert sent is False
    app.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_marker_not_set_when_send_fails(tmp_path: Path) -> None:
    """If the welcome send raises, the marker MUST NOT be set so the
    next boot retries."""
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    app = make_app(config)
    app.bot.send_message = AsyncMock(side_effect=RuntimeError("network down"))

    sent = await maybe_send_welcome(app)

    assert sent is False
    assert db.get_setting(config.db_path, WELCOME_SENT_SETTING) is None


@pytest.mark.asyncio
async def test_welcome_with_llm_enabled_shows_pending_line(
    tmp_path: Path, monkeypatch
) -> None:
    """When LLM is on, the initial message has the 'pending' line; the
    smoke-test edit happens later in a background task that we cancel
    before it can fire (don't want real subprocess in tests)."""
    config = make_config(tmp_path, llm_enabled=True)
    db.init_db(config.db_path)
    app = make_app(config)

    # Stub the smoke test so the background task can't shell out.
    async def _fake_smoketest(config):
        return True, "🌙 Online and slightly bored."
    monkeypatch.setattr(welcome_handlers, "run_smoketest", _fake_smoketest)

    sent = await maybe_send_welcome(app)

    assert sent is True
    body = app.bot.send_message.await_args.kwargs["text"]
    # Initial body has the pending sentinel — edit happens after.
    assert "pending" in body

    # Drain the background task so pytest doesn't warn about un-awaited tasks.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        try:
            await t
        except Exception:
            pass


# ---- _run_smoketest_and_edit -----------------------------------------------

@pytest.mark.asyncio
async def test_smoketest_edit_appends_check_mark_on_success(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path, llm_enabled=True)
    db.init_db(config.db_path)
    app = make_app(config)

    async def _fake(config):
        return True, "🌙 Hello world."
    monkeypatch.setattr(welcome_handlers, "run_smoketest", _fake)

    with patch("naarad.handlers.welcome.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 9, 0, tzinfo=TZ)
        await welcome_handlers._run_smoketest_and_edit(app, message_id=42)

    app.bot.edit_message_text.assert_awaited_once()
    body = app.bot.edit_message_text.await_args.kwargs["text"]
    assert "LLM check: ✓" in body
    assert "Hello world" in body
    # Button still attached because day not started.
    assert app.bot.edit_message_text.await_args.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_smoketest_edit_shows_failure_with_reason(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path, llm_enabled=True)
    db.init_db(config.db_path)
    app = make_app(config)

    async def _fake(config):
        return False, "copilot exit 1: not authenticated"
    monkeypatch.setattr(welcome_handlers, "run_smoketest", _fake)

    with patch("naarad.handlers.welcome.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 9, 0, tzinfo=TZ)
        await welcome_handlers._run_smoketest_and_edit(app, message_id=42)

    body = app.bot.edit_message_text.await_args.kwargs["text"]
    assert "LLM check: ✗" in body
    assert "not authenticated" in body
    assert "deterministic mode" in body


@pytest.mark.asyncio
async def test_smoketest_edit_drops_button_if_user_already_started(
    tmp_path: Path, monkeypatch
) -> None:
    """If the user tapped Start during the smoke test, don't re-add
    the button on the edit (avoids the button reappearing after tap)."""
    config = make_config(tmp_path, llm_enabled=True)
    db.init_db(config.db_path)
    db.update_water_state(config.db_path, day_started_on=date(2026, 5, 7))
    app = make_app(config)

    async def _fake(config):
        return True, "🌙 hi"
    monkeypatch.setattr(welcome_handlers, "run_smoketest", _fake)

    with patch("naarad.handlers.welcome.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 9, 0, tzinfo=TZ)
        await welcome_handlers._run_smoketest_and_edit(app, message_id=42)

    assert app.bot.edit_message_text.await_args.kwargs["reply_markup"] is None


# ---- welcome_button callback -----------------------------------------------

def make_callback_update(chat_id: int = 42):
    query = AsyncMock()
    query.message = SimpleNamespace(chat_id=chat_id, message_id=42)
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        callback_query=query,
    )


@pytest.mark.asyncio
async def test_welcome_button_starts_day(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    update = make_callback_update()

    application = SimpleNamespace(
        bot=AsyncMock(),
        bot_data={
            "config": config,
            "water_cfg": MagicMock(),
            "water_lock": asyncio.Lock(),
        },
        job_queue=None,
    )
    context = SimpleNamespace(application=application)

    # Patch start_day so we don't need the full water scheduler stack.
    with patch(
        "naarad.handlers.welcome.water_scheduler.start_day",
        new_callable=AsyncMock,
    ) as m_start:
        await welcome_button(update, context)

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_reply_markup.assert_awaited_once()
    m_start.assert_awaited_once()


@pytest.mark.asyncio
async def test_welcome_button_unauthorized_dropped(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    update = make_callback_update(chat_id=999)

    application = SimpleNamespace(
        bot=AsyncMock(),
        bot_data={"config": config},
    )
    context = SimpleNamespace(application=application)

    with patch(
        "naarad.handlers.welcome.water_scheduler.start_day",
        new_callable=AsyncMock,
    ) as m_start:
        await welcome_button(update, context)

    m_start.assert_not_awaited()


# ---- Avoid unused-import warning for LLM_FLAG_KEY in CI -----------------

def test_module_imports_cleanly() -> None:
    assert LLM_FLAG_KEY == "llm_enabled"
