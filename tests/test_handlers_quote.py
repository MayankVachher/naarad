"""Tests for the /quote handler."""
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
from naarad.handlers.quote import quote_command
from naarad.runtime import TICKERS_FLAG_KEY
from naarad.tickers.eodhd import ExchangeDay, ExchangeStatus, Quote

# Frozen Tuesday so the weekend gate doesn't fire by accident.
WEEKDAY = datetime(2025, 10, 14, 11, 0, tzinfo=ZoneInfo("America/New_York"))
SATURDAY = datetime(2025, 10, 18, 11, 0, tzinfo=ZoneInfo("America/New_York"))
CHRISTMAS = datetime(2025, 12, 25, 11, 0, tzinfo=ZoneInfo("America/New_York"))


def make_config(tmp_path: Path, *, tickers_enabled: bool = True, eodhd_key: str = "x") -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key=eodhd_key),
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


def make_context(config: Config, args: list[str] | None = None, *, client=None):
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "config": config,
                "eodhd_client": client or MagicMock(),
            }
        ),
        args=args or [],
    )


def make_update(chat_id: int = 42):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=AsyncMock(),
        callback_query=None,
    )


def _last_reply(update) -> str:
    update.message.reply_text.assert_awaited()
    return update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_no_args_shows_usage(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await quote_command(update, make_context(config))

    assert "Usage" in _last_reply(update)


@pytest.mark.asyncio
async def test_unauthorized_silently_dropped(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update(chat_id=999)

    await quote_command(update, make_context(config, args=["GOOGL"]))

    update.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_refuses_when_runtime_off(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, TICKERS_FLAG_KEY, "0")
    update = make_update()

    await quote_command(update, make_context(config, args=["GOOGL"]))

    text = _last_reply(update)
    assert "off" in text.lower()


@pytest.mark.asyncio
async def test_refuses_when_config_floor_off(tmp_path: Path) -> None:
    config = make_config(tmp_path, tickers_enabled=False)
    db.init_db(config.db_path)
    update = make_update()

    await quote_command(update, make_context(config, args=["GOOGL"]))

    text = _last_reply(update)
    assert "config" in text.lower()


@pytest.mark.asyncio
async def test_refuses_when_eodhd_key_missing(tmp_path: Path) -> None:
    config = make_config(tmp_path, eodhd_key="")
    db.init_db(config.db_path)
    update = make_update()

    await quote_command(update, make_context(config, args=["GOOGL"]))

    text = _last_reply(update)
    assert "EODHD" in text or "key" in text.lower()


@pytest.mark.asyncio
async def test_invalid_symbol_returns_error(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await quote_command(update, make_context(config, args=["FOO.XYZ"]))

    text = _last_reply(update)
    assert "unsupported exchange suffix" in text


@pytest.mark.asyncio
async def test_us_quote_renders_demo2_block(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    client = MagicMock()
    client.real_time_quote.return_value = make_quote("GOOGL")
    update = make_update()

    await quote_command(update, make_context(config, args=["googl"], client=client))

    text = _last_reply(update)
    # Symbol normalised + bolded.
    assert "<b>GOOGL</b>" in text
    assert "<b>Price</b>:" in text
    assert "<b>Prev</b>:" in text
    assert "<b>Chng</b>:" in text
    assert "🟢" in text
    # The fetch was invoked with the upper-cased symbol.
    client.real_time_quote.assert_called_once_with("GOOGL")


@pytest.mark.asyncio
async def test_tsx_suffix_passed_through(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    client = MagicMock()
    client.real_time_quote.return_value = make_quote("VFV.TO")
    update = make_update()

    await quote_command(update, make_context(config, args=["vfv.to"], client=client))

    text = _last_reply(update)
    assert "VFV.TO" in text
    client.real_time_quote.assert_called_once_with("VFV.TO")


@pytest.mark.asyncio
async def test_fetch_failure_is_user_friendly(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    client = MagicMock()
    client.real_time_quote.side_effect = RuntimeError("network down")
    update = make_update()

    await quote_command(update, make_context(config, args=["GOOGL"], client=client))

    text = _last_reply(update)
    assert "Couldn't fetch" in text
    assert "GOOGL" in text


# ---- closed-exchange notes --------------------------------------------------

@pytest.mark.asyncio
async def test_prepends_weekend_note(tmp_path: Path) -> None:
    """Saturday → '📅 US closed today — weekend' line above the quote block."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    client = MagicMock()
    client.real_time_quote.return_value = make_quote("GOOGL")
    update = make_update()

    with patch("naarad.handlers.quote.datetime") as m_dt:
        m_dt.now.return_value = SATURDAY
        await quote_command(update, make_context(config, args=["GOOGL"], client=client))

    text = _last_reply(update)
    assert "<b>US closed today</b>" in text
    assert "weekend" in text
    # Quote still rendered (last trade is what the user wants to see).
    assert "<b>GOOGL</b>" in text
    assert "<b>Price</b>:" in text
    # Weekend short-circuits the holiday lookup.
    client.get_exchange_status.assert_not_called()


@pytest.mark.asyncio
async def test_prepends_holiday_note(tmp_path: Path) -> None:
    """Weekday holiday → '📅 US closed today — Christmas Day' line."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    client = MagicMock()
    client.get_exchange_status.return_value = ExchangeDay(
        status=ExchangeStatus.CLOSED_HOLIDAY, name="Christmas Day"
    )
    client.real_time_quote.return_value = make_quote("GOOGL")
    update = make_update()

    with patch("naarad.handlers.quote.datetime") as m_dt:
        m_dt.now.return_value = CHRISTMAS
        await quote_command(update, make_context(config, args=["GOOGL"], client=client))

    text = _last_reply(update)
    assert "<b>US closed today</b>" in text
    assert "Christmas Day" in text
    assert "<b>GOOGL</b>" in text
    client.get_exchange_status.assert_called_once()


@pytest.mark.asyncio
async def test_no_note_when_exchange_open(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    client = MagicMock()
    client.get_exchange_status.return_value = ExchangeDay(status=ExchangeStatus.OPEN)
    client.real_time_quote.return_value = make_quote("GOOGL")
    update = make_update()

    with patch("naarad.handlers.quote.datetime") as m_dt:
        m_dt.now.return_value = WEEKDAY
        await quote_command(update, make_context(config, args=["GOOGL"], client=client))

    text = _last_reply(update)
    assert "closed today" not in text


@pytest.mark.asyncio
async def test_no_note_on_early_close_day(tmp_path: Path) -> None:
    """EarlyClose days still trade — no closed-today note (matches market_open)."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    client = MagicMock()
    client.get_exchange_status.return_value = ExchangeDay(
        status=ExchangeStatus.EARLY_CLOSE, name="Christmas Eve"
    )
    client.real_time_quote.return_value = make_quote("GOOGL")
    update = make_update()

    with patch("naarad.handlers.quote.datetime") as m_dt:
        m_dt.now.return_value = WEEKDAY
        await quote_command(update, make_context(config, args=["GOOGL"], client=client))

    text = _last_reply(update)
    assert "closed today" not in text


@pytest.mark.asyncio
async def test_holiday_lookup_failure_falls_back_to_no_note(tmp_path: Path) -> None:
    """If the EODHD calendar call raises, /quote still answers — just without context."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    client = MagicMock()
    client.get_exchange_status.side_effect = RuntimeError("network down")
    client.real_time_quote.return_value = make_quote("GOOGL")
    update = make_update()

    with patch("naarad.handlers.quote.datetime") as m_dt:
        m_dt.now.return_value = WEEKDAY
        await quote_command(update, make_context(config, args=["GOOGL"], client=client))

    text = _last_reply(update)
    assert "closed today" not in text
    assert "<b>GOOGL</b>" in text
    assert "<b>Price</b>:" in text


@pytest.mark.asyncio
async def test_tsx_weekend_note(tmp_path: Path) -> None:
    """Weekend note uses the symbol's exchange (TSX, not US)."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    client = MagicMock()
    client.real_time_quote.return_value = make_quote("VFV.TO")
    update = make_update()

    with patch("naarad.handlers.quote.datetime") as m_dt:
        m_dt.now.return_value = SATURDAY
        await quote_command(update, make_context(config, args=["vfv.to"], client=client))

    text = _last_reply(update)
    assert "<b>TSX closed today</b>" in text
    assert "weekend" in text
