"""Tests for /status — sectioned layout, pace badge, Idle reason strings."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

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
from naarad.handlers.status import status_command
from naarad.runtime import LLM_FLAG_KEY, TICKERS_FLAG_KEY

TZ = ZoneInfo("America/Toronto")


def make_config(
    tmp_path: Path,
    *,
    llm_enabled: bool = True,
    eodhd_key: str = "x",
    tickers_enabled: bool = True,
) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key=eodhd_key),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        llm=LLMConfig(enabled=llm_enabled, backend="copilot"),
        tickers=TickersConfig(enabled=tickers_enabled),
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


def make_context(config: Config):
    return SimpleNamespace(application=SimpleNamespace(bot_data={"config": config}))


def make_update(chat_id: int = 42):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=AsyncMock(),
        callback_query=None,
    )


def _reply(update) -> str:
    update.message.reply_text.assert_awaited_once()
    return update.message.reply_text.await_args.args[0]


# ---- layout -----------------------------------------------------------------

@pytest.mark.asyncio
@freeze_time("2026-05-12 14:00:00", tz_offset=0)
async def test_status_renders_all_four_sections(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await status_command(update, make_context(config))
    text = _reply(update)

    # Section headers, in order, each with its emoji anchor.
    for header in (
        "<b>📋 Naarad status</b>",
        "<b>💧 Water</b>",
        "<b>🤖 LLM</b>",
        "<b>📈 Tickers</b>",
        "<b>🌐 System</b>",
    ):
        assert header in text
    assert "America/Toronto" in text


@pytest.mark.asyncio
async def test_status_rejects_unauthorized_chat(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update(chat_id=999)

    await status_command(update, make_context(config))

    # Unauthorized chats: handler silently drops the message.
    update.message.reply_text.assert_not_awaited()


# ---- water section ----------------------------------------------------------

@pytest.mark.asyncio
@freeze_time("2026-05-12 14:00:00")
async def test_water_section_day_not_started(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await status_command(update, make_context(config))
    text = _reply(update)

    assert "Day started: <b>no</b>" in text
    assert "day not started" in text
    assert "Last drink: <b>never</b>" in text


@pytest.mark.asyncio
async def test_water_section_target_hit_shows_idle_reason(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    now = datetime(2026, 5, 12, 14, 0, tzinfo=TZ)
    today = now.date()
    db.update_water_state(
        config.db_path,
        last_drink_at=now,
        day_started_on=today,
        chain_started_at=now.replace(hour=7),
        glasses_today=8,
    )
    update = make_update()

    with freeze_time(now):
        await status_command(update, make_context(config))
    text = _reply(update)

    assert "🎯 target hit" in text
    # Pace badge in the glasses line:
    assert "8 / 8" in text


@pytest.mark.asyncio
async def test_water_section_past_active_end_shows_active_hours_ended(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    now = datetime(2026, 5, 12, 22, 0, tzinfo=TZ)  # after 21:00 active_end
    today = now.date()
    db.update_water_state(
        config.db_path,
        day_started_on=today,
        chain_started_at=now.replace(hour=7),
        glasses_today=3,
    )
    update = make_update()

    with freeze_time(now):
        await status_command(update, make_context(config))
    text = _reply(update)

    assert "🌙 active hours ended" in text


@pytest.mark.asyncio
async def test_water_section_behind_includes_deficit(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    # Chain started at 07:00, now is 15:00 → 8h elapsed of 14h active window
    # → expected ~4.6 glasses; actual 1 → deficit ~3.6 → "behind".
    now = datetime(2026, 5, 12, 15, 0, tzinfo=TZ)
    today = now.date()
    db.update_water_state(
        config.db_path,
        last_drink_at=now.replace(hour=8),
        day_started_on=today,
        chain_started_at=now.replace(hour=7),
        glasses_today=1,
    )
    update = make_update()

    with freeze_time(now):
        await status_command(update, make_context(config))
    text = _reply(update)

    assert "🚨 behind by ~" in text
    assert "glasses" in text


# ---- llm section ------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_off_config(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    update = make_update()

    await status_command(update, make_context(config))
    text = _reply(update)
    assert "<b>off</b> (config)" in text


@pytest.mark.asyncio
async def test_llm_off_runtime_shows_backend(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, LLM_FLAG_KEY, "0")
    update = make_update()

    await status_command(update, make_context(config))
    text = _reply(update)
    assert "<b>off</b> (runtime)" in text
    assert "copilot" in text


@pytest.mark.asyncio
async def test_llm_on_shows_backend(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await status_command(update, make_context(config))
    text = _reply(update)
    assert "<b>on</b> (copilot)" in text


# ---- tickers section --------------------------------------------------------

@pytest.mark.asyncio
async def test_tickers_off_no_key(tmp_path: Path) -> None:
    config = make_config(tmp_path, eodhd_key="")
    db.init_db(config.db_path)
    update = make_update()

    await status_command(update, make_context(config))
    text = _reply(update)
    assert "off</b> (no EODHD key)" in text


@pytest.mark.asyncio
async def test_tickers_off_runtime(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, TICKERS_FLAG_KEY, "0")
    update = make_update()

    await status_command(update, make_context(config))
    text = _reply(update)
    assert "off</b> (runtime)" in text


@pytest.mark.asyncio
async def test_tickers_watchlist_listed(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.add_ticker(config.db_path, "GOOGL")
    db.add_ticker(config.db_path, "VFV.TO")
    update = make_update()

    await status_command(update, make_context(config))
    text = _reply(update)
    # Each ticker is its own indented sub-bullet under "Watchlist:".
    assert "• Watchlist:" in text
    assert "  ◦ <b>GOOGL</b>" in text
    assert "  ◦ <b>VFV.TO</b>" in text


@pytest.mark.asyncio
async def test_tickers_empty_watchlist_shows_placeholder(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await status_command(update, make_context(config))
    text = _reply(update)
    assert "<i>(none)</i>" in text
