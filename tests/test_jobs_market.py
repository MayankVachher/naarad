"""Tests for the in-process market_open / market_close jobs and scheduler."""
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
from naarad.jobs import market_close, market_open
from naarad.jobs import scheduler as ticker_scheduler
from naarad.runtime import TICKERS_FLAG_KEY
from naarad.tickers.eodhd import Quote

# Frozen Tuesday to avoid weekend gate
WEEKDAY = datetime(2025, 10, 14, 9, 35, tzinfo=ZoneInfo("America/New_York"))
SATURDAY = datetime(2025, 10, 18, 9, 35, tzinfo=ZoneInfo("America/New_York"))


def make_config(tmp_path: Path, *, tickers_enabled: bool = True) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        llm=LLMConfig(),
        tickers=TickersConfig(enabled=tickers_enabled),
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


def make_quote(symbol: str = "GOOGL", **overrides) -> Quote:
    fields = dict(
        symbol=symbol,
        timestamp=datetime(2025, 10, 14, 9, 35, tzinfo=ZoneInfo("America/New_York")),
        open=180.0,
        high=183.5,
        low=179.2,
        close=182.45,
        previous_close=180.10,
        change=2.35,
        change_pct=1.31,
        volume=1_234_567,
    )
    fields.update(overrides)
    return Quote(**fields)


def empty_quote(symbol: str) -> Quote:
    return Quote(
        symbol=symbol,
        timestamp=None,
        open=None,
        high=None,
        low=None,
        close=None,
        previous_close=None,
        change=None,
        change_pct=None,
        volume=None,
    )


def make_app(config: Config, *, client=None) -> SimpleNamespace:
    bot = AsyncMock()
    return SimpleNamespace(
        bot=bot,
        bot_data={
            "config": config,
            "eodhd_client": client or MagicMock(),
        },
    )


# ---- weekend / kill-switch / empty gates -----------------------------------

@pytest.mark.asyncio
async def test_market_open_skips_on_weekend(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.add_ticker(config.db_path, "GOOGL")
    app = make_app(config)

    with patch("naarad.jobs.market_open.datetime") as m_dt:
        m_dt.now.return_value = SATURDAY
        await market_open.run(app)

    app.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_open_skips_when_kill_switch_off(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.add_ticker(config.db_path, "GOOGL")
    db.set_setting(config.db_path, TICKERS_FLAG_KEY, "0")
    app = make_app(config)

    with patch("naarad.jobs.market_open.datetime") as m_dt:
        m_dt.now.return_value = WEEKDAY
        await market_open.run(app)

    app.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_open_skips_when_config_floor_off(tmp_path: Path) -> None:
    config = make_config(tmp_path, tickers_enabled=False)
    db.init_db(config.db_path)
    db.add_ticker(config.db_path, "GOOGL")
    app = make_app(config)

    with patch("naarad.jobs.market_open.datetime") as m_dt:
        m_dt.now.return_value = WEEKDAY
        await market_open.run(app)

    app.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_open_skips_when_watchlist_empty(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    app = make_app(config)

    with patch("naarad.jobs.market_open.datetime") as m_dt:
        m_dt.now.return_value = WEEKDAY
        await market_open.run(app)

    app.bot.send_message.assert_not_awaited()


# ---- happy paths ------------------------------------------------------------

@pytest.mark.asyncio
async def test_market_open_sends_formatted_quotes(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.add_ticker(config.db_path, "GOOGL")
    db.add_ticker(config.db_path, "NVDA")

    client = MagicMock()
    client.real_time_quote.side_effect = [make_quote("GOOGL"), make_quote("NVDA", change_pct=-0.5)]

    app = make_app(config, client=client)

    with patch("naarad.jobs.market_open.datetime") as m_dt:
        m_dt.now.return_value = WEEKDAY
        await market_open.run(app)

    app.bot.send_message.assert_awaited_once()
    kwargs = app.bot.send_message.await_args.kwargs
    text = kwargs["text"]
    assert "Market open" in text
    assert "GOOGL" in text
    assert "NVDA" in text
    assert "+1.31%" in text
    assert "-0.50%" in text
    assert kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_market_close_sends_formatted_quotes(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.add_ticker(config.db_path, "GOOGL")

    client = MagicMock()
    client.real_time_quote.return_value = make_quote("GOOGL")

    app = make_app(config, client=client)

    with patch("naarad.jobs.market_close.datetime") as m_dt:
        m_dt.now.return_value = WEEKDAY
        await market_close.run(app)

    app.bot.send_message.assert_awaited_once()
    text = app.bot.send_message.await_args.kwargs["text"]
    assert "Market close" in text
    assert "GOOGL" in text
    assert "$182.45" in text  # close
    assert "1.23M" in text     # volume formatted


# ---- partial-failure rendering ---------------------------------------------

@pytest.mark.asyncio
async def test_market_open_renders_unavailable_for_failed_symbol(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.add_ticker(config.db_path, "GOOGL")
    db.add_ticker(config.db_path, "NVDA")

    client = MagicMock()
    # First succeeds, second raises.
    client.real_time_quote.side_effect = [
        make_quote("GOOGL"),
        RuntimeError("boom"),
    ]

    app = make_app(config, client=client)

    with patch("naarad.jobs.market_open.datetime") as m_dt:
        m_dt.now.return_value = WEEKDAY
        await market_open.run(app)

    app.bot.send_message.assert_awaited_once()
    text = app.bot.send_message.await_args.kwargs["text"]
    assert "GOOGL" in text
    assert "NVDA" in text
    assert "data unavailable" in text


# ---- scheduler.kickoff ------------------------------------------------------

@pytest.mark.asyncio
async def test_kickoff_schedules_both_jobs(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    jq = MagicMock()
    jq.get_jobs_by_name.return_value = []
    bot = AsyncMock()
    app = SimpleNamespace(
        bot=bot,
        bot_data={"config": config},
        job_queue=jq,
    )

    await ticker_scheduler.kickoff(app)

    # Two run_daily calls: open + close.
    assert jq.run_daily.call_count == 2
    names = {c.kwargs.get("name") for c in jq.run_daily.call_args_list}
    assert names == {market_open.JOB_NAME, market_close.JOB_NAME}

    # Times must carry the market timezone, not the user's local tz.
    times = [c.kwargs["time"] for c in jq.run_daily.call_args_list]
    for t in times:
        assert t.tzinfo is not None
        assert t.tzinfo.key == "America/New_York"


@pytest.mark.asyncio
async def test_kickoff_replaces_existing_jobs(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    existing = MagicMock()
    jq = MagicMock()
    jq.get_jobs_by_name.return_value = [existing]
    bot = AsyncMock()
    app = SimpleNamespace(bot=bot, bot_data={"config": config}, job_queue=jq)

    await ticker_scheduler.kickoff(app)

    # schedule_removal called once per existing job per name (open + close).
    assert existing.schedule_removal.call_count == 2


@pytest.mark.asyncio
async def test_kickoff_no_jobqueue_is_noop(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    bot = AsyncMock()
    app = SimpleNamespace(bot=bot, bot_data={"config": config}, job_queue=None)

    await ticker_scheduler.kickoff(app)  # must not raise
