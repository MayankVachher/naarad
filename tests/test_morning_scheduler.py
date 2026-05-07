"""Tests for morning.scheduler.kickoff: daily-brief + fallback scheduling
and the two boot-time catch-up paths.
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
from naarad.jobs.daily_brief import LAST_BRIEF_SETTING
from naarad.morning import scheduler as morning_scheduler

TZ = ZoneInfo("America/Toronto")


def make_config(tmp_path: Path) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(),
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


def _scheduled_names(jq) -> set[str]:
    daily = {c.kwargs.get("name") for c in jq.run_daily.call_args_list}
    once = {c.kwargs.get("name") for c in jq.run_once.call_args_list}
    return daily | once


# ---- Daily schedules always wired ------------------------------------------

@pytest.mark.asyncio
async def test_kickoff_schedules_daily_brief_and_fallback(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    app = make_app(config)

    # Pre-08:00, no catch-ups should fire.
    with patch("naarad.morning.scheduler.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 5, 30, tzinfo=TZ)
        m_dt.combine = datetime.combine
        await morning_scheduler.kickoff(app)

    names = _scheduled_names(app.job_queue)
    assert morning_scheduler.BRIEF_JOB_NAME in names
    assert morning_scheduler.FALLBACK_JOB_NAME in names
    assert morning_scheduler.BRIEF_CATCHUP_NAME not in names
    assert morning_scheduler.FALLBACK_CATCHUP_NAME not in names


# ---- Brief catch-up --------------------------------------------------------

@pytest.mark.asyncio
async def test_brief_catchup_fires_when_late_boot_and_no_brief_today(
    tmp_path: Path,
) -> None:
    """Boot at 07:00, brief never sent today → schedule catch-up."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
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
    db.set_setting(config.db_path, LAST_BRIEF_SETTING, "2026-05-06")
    app = make_app(config)

    with patch("naarad.morning.scheduler.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 7, 0, tzinfo=TZ)
        m_dt.combine = datetime.combine
        await morning_scheduler.kickoff(app)

    assert morning_scheduler.BRIEF_CATCHUP_NAME in _scheduled_names(app.job_queue)


# ---- Fallback catch-up (regression guard) ----------------------------------

@pytest.mark.asyncio
async def test_fallback_catchup_fires_when_late_boot_and_day_not_started(
    tmp_path: Path,
) -> None:
    """Boot at 11:30 with no day_started_on → schedule fallback catch-up.

    With the brief catch-up addition, both should fire on a very late boot.
    """
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, LAST_BRIEF_SETTING, "2026-05-07")  # suppress brief
    app = make_app(config)

    with patch("naarad.morning.scheduler.datetime") as m_dt:
        m_dt.now.return_value = datetime(2026, 5, 7, 11, 30, tzinfo=TZ)
        m_dt.combine = datetime.combine
        await morning_scheduler.kickoff(app)

    assert morning_scheduler.FALLBACK_CATCHUP_NAME in _scheduled_names(app.job_queue)
