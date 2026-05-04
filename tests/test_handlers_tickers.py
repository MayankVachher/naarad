"""Tests for the /ticker handler — add | remove | list + on | off toggle."""
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
    TickersConfig,
    WaterConfig,
)
from naarad.handlers.tickers import ticker_command
from naarad.runtime import TICKERS_FLAG_KEY


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


def _last_reply(update) -> str:
    update.message.reply_text.assert_awaited()
    return update.message.reply_text.await_args.args[0]


# ---- existing surface (add | remove | list) regression ----------------------

@pytest.mark.asyncio
async def test_no_args_shows_usage_with_on_off(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await ticker_command(update, make_context(config))

    text = _last_reply(update)
    assert "Usage" in text
    assert "/ticker on | off" in text


@pytest.mark.asyncio
async def test_list_when_empty(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await ticker_command(update, make_context(config, args=["list"]))

    assert "No tickers tracked." in _last_reply(update)


@pytest.mark.asyncio
async def test_add_then_list(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    update1 = make_update()
    await ticker_command(update1, make_context(config, args=["add", "GOOGL"]))
    assert "Added GOOGL" in _last_reply(update1)

    update2 = make_update()
    await ticker_command(update2, make_context(config, args=["list"]))
    assert "GOOGL" in _last_reply(update2)


@pytest.mark.asyncio
async def test_remove_unknown(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await ticker_command(update, make_context(config, args=["remove", "AAPL"]))

    assert "wasn't tracked" in _last_reply(update)


@pytest.mark.asyncio
async def test_add_rejects_unknown_suffix(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await ticker_command(update, make_context(config, args=["add", "FOO.XYZ"]))

    text = _last_reply(update)
    assert "unsupported exchange suffix" in text
    # And the symbol must NOT have been persisted.
    assert "FOO.XYZ" not in db.list_tickers(config.db_path)


@pytest.mark.asyncio
async def test_add_accepts_us_bare(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await ticker_command(update, make_context(config, args=["add", "GOOGL"]))

    assert "GOOGL" in db.list_tickers(config.db_path)


@pytest.mark.asyncio
async def test_add_accepts_to_suffix(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await ticker_command(update, make_context(config, args=["add", "VFV.TO"]))

    assert "VFV.TO" in db.list_tickers(config.db_path)


# ---- on/off toggle ----------------------------------------------------------

@pytest.mark.asyncio
async def test_off_disables_runtime_flag(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await ticker_command(update, make_context(config, args=["off"]))

    assert db.get_setting(config.db_path, TICKERS_FLAG_KEY) == "0"
    text = _last_reply(update)
    assert "<b>off</b>" in text
    assert "runtime" in text


@pytest.mark.asyncio
async def test_on_enables_runtime_flag(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, TICKERS_FLAG_KEY, "0")
    update = make_update()

    await ticker_command(update, make_context(config, args=["on"]))

    assert db.get_setting(config.db_path, TICKERS_FLAG_KEY) == "1"
    text = _last_reply(update)
    assert "<b>on</b>" in text


@pytest.mark.asyncio
async def test_refuses_to_enable_when_config_floor_is_off(tmp_path: Path) -> None:
    config = make_config(tmp_path, tickers_enabled=False)
    db.init_db(config.db_path)
    update = make_update()

    await ticker_command(update, make_context(config, args=["on"]))

    text = _last_reply(update)
    assert "Can't toggle" in text
    # And the DB flag must NOT have been mutated.
    assert db.get_setting(config.db_path, TICKERS_FLAG_KEY) is None


@pytest.mark.asyncio
async def test_refuses_to_disable_when_config_floor_is_off(tmp_path: Path) -> None:
    config = make_config(tmp_path, tickers_enabled=False)
    db.init_db(config.db_path)
    update = make_update()

    await ticker_command(update, make_context(config, args=["off"]))

    text = _last_reply(update)
    assert "Can't toggle" in text
    assert db.get_setting(config.db_path, TICKERS_FLAG_KEY) is None


# ---- auth gate --------------------------------------------------------------

@pytest.mark.asyncio
async def test_unauthorized_chat_is_silently_dropped(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update(chat_id=999)  # not config.telegram.chat_id

    await ticker_command(update, make_context(config, args=["off"]))

    update.message.reply_text.assert_not_awaited()
    assert db.get_setting(config.db_path, TICKERS_FLAG_KEY) is None
