"""Tests for morning.scheduler.kickoff: daily-brief + fallback scheduling,
welcome gate, brief catch-up cap.
"""
from __future__ import annotations

from datetime import datetime
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
from naarad.handlers.welcome import WELCOME_SENT_SETTING
from naarad.jobs.daily_brief import LAST_BRIEF_SETTING
from naarad.morning import scheduler as morning_scheduler

TZ = ZoneInfo("America/Toronto")


def make_config(tmp_path: Path) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(active_end="21:00"),
        brief=BriefConfig(),
        morning=MorningConfig(start_time="06:00", fallback_time="11:00"),
        llm=LLMConfig(),
        tickers=TickersConfig(),
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


def make_app(config: Config) -> SimpleNamespace:
    jq = MagicMock()
    jq.get_jobs_by_name.return_value = []
    return SimpleNamespace(bot=AsyncMock(), bot_data={"config": config}, job_queue=jq)


def _seed_welcome_sent(config: Config) -> None:
    """Mark welcome already sent so kickoff exercises the catch-up branch
    rather than sending a welcome and returning early. Tests that
    specifically cover the welcome flow override this."""
    db.set_setting(config.db_path, WELCOME_SENT_SETTING, "1")


def _scheduled_names(jq) -> set[str]:
    daily = {c.kwargs.get("name") for c in jq.run_daily.call_args_list}
    once = {c.kwargs.get("name") for c in jq.run_once.call_args_list}
    return daily | once


# ---- Daily schedules always wired ------------------------------------------

@pytest.mark.asyncio
async def test_kickoff_schedules_daily_brief_and_fallback(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    _seed_welcome_sent(config)
    app = make_app(config)

    # Pre-06:00 — neither catch-up nor fallback-catchup should fire.
    with patch("naarad.morning.scheduler.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 5, 30, tzinfo=TZ)
        m_dt.combine = datetime.combine
        await morning_scheduler.kickoff(app)

    names = _scheduled_names(app.job_queue)
    assert morning_scheduler.BRIEF_JOB_NAME in names
    assert morning_scheduler.FALLBACK_JOB_NAME in names
    assert morning_scheduler.BRIEF_CATCHUP_NAME not in names


# ---- Welcome gate ----------------------------------------------------------

@pytest.mark.asyncio
async def test_first_boot_sends_welcome_and_skips_catchup(
    tmp_path: Path, monkeypatch
) -> None:
    """Fresh state.db, late boot: welcome fires, brief catch-up does NOT
    (welcome is the recovery signal on first install)."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    app = make_app(config)
    # LLM disabled in this test so we don't trigger smoke-test threading.
    db.set_setting(config.db_path, "llm_enabled", "0")

    with patch("naarad.morning.scheduler.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 7, 0, tzinfo=TZ)
        m_dt.combine = datetime.combine
        with patch("naarad.handlers.welcome.datetime") as w_dt:
            w_dt.now.return_value = datetime(2026, 5, 7, 7, 0, tzinfo=TZ)
            w_dt.combine = datetime.combine
            await morning_scheduler.kickoff(app)

    # Welcome was sent (one send_message call to the bot).
    assert app.bot.send_message.await_count == 1
    # Marker persisted.
    assert db.get_setting(config.db_path, WELCOME_SENT_SETTING) == "1"
    # Brief catch-up did NOT fire — welcome takes precedence.
    assert morning_scheduler.BRIEF_CATCHUP_NAME not in _scheduled_names(app.job_queue)


@pytest.mark.asyncio
async def test_welcome_skipped_when_already_sent(tmp_path: Path) -> None:
    """Subsequent boots don't re-send the welcome."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    _seed_welcome_sent(config)
    app = make_app(config)

    with patch("naarad.morning.scheduler.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 5, 30, tzinfo=TZ)
        m_dt.combine = datetime.combine
        await morning_scheduler.kickoff(app)

    # No welcome sent (marker was already set).
    app.bot.send_message.assert_not_awaited()


# ---- Brief catch-up --------------------------------------------------------

@pytest.mark.asyncio
async def test_brief_catchup_fires_when_late_boot_and_no_brief_today(
    tmp_path: Path,
) -> None:
    """Boot at 07:00, brief never sent today, welcome already done →
    schedule catch-up (the canonical Pi-rebooted-mid-morning case)."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    _seed_welcome_sent(config)
    app = make_app(config)

    with patch("naarad.morning.scheduler.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 7, 0, tzinfo=TZ)
        m_dt.combine = datetime.combine
        await morning_scheduler.kickoff(app)

    assert morning_scheduler.BRIEF_CATCHUP_NAME in _scheduled_names(app.job_queue)


@pytest.mark.asyncio
async def test_brief_catchup_skipped_when_brief_already_sent_today(
    tmp_path: Path,
) -> None:
    """Boot at 07:00 but last_brief_on==today → no catch-up."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    _seed_welcome_sent(config)
    db.set_setting(config.db_path, LAST_BRIEF_SETTING, "2026-05-07")
    app = make_app(config)

    with patch("naarad.morning.scheduler.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 7, 0, tzinfo=TZ)
        m_dt.combine = datetime.combine
        await morning_scheduler.kickoff(app)

    assert morning_scheduler.BRIEF_CATCHUP_NAME not in _scheduled_names(app.job_queue)


@pytest.mark.asyncio
async def test_brief_catchup_skipped_when_yesterday_brief_marker(
    tmp_path: Path,
) -> None:
    """Yesterday's marker is not enough to suppress today's catch-up."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    _seed_welcome_sent(config)
    db.set_setting(config.db_path, LAST_BRIEF_SETTING, "2026-05-06")
    app = make_app(config)

    with patch("naarad.morning.scheduler.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 7, 0, tzinfo=TZ)
        m_dt.combine = datetime.combine
        await morning_scheduler.kickoff(app)

    assert morning_scheduler.BRIEF_CATCHUP_NAME in _scheduled_names(app.job_queue)


@pytest.mark.asyncio
async def test_brief_catchup_skipped_past_active_end(tmp_path: Path) -> None:
    """Boot at 23:30 — past active_end (21:00). Brief catch-up skipped
    even though the brief wasn't sent today; tomorrow's normal schedule
    handles it. This is the late-night-install fix."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    _seed_welcome_sent(config)
    app = make_app(config)

    with patch("naarad.morning.scheduler.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 23, 30, tzinfo=TZ)
        m_dt.combine = datetime.combine
        await morning_scheduler.kickoff(app)

    assert morning_scheduler.BRIEF_CATCHUP_NAME not in _scheduled_names(app.job_queue)
