"""Tests for /llm command handler — state display + on/off toggling +
config-floor refusal.
"""
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
    WaterConfig,
)
from naarad.handlers.llm import llm_callback, llm_command
from naarad.runtime import LLM_BACKEND_KEY, LLM_FLAG_KEY


def make_config(tmp_path: Path, *, llm_enabled: bool = True) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        llm=LLMConfig(enabled=llm_enabled),
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


def _reply_text(update) -> str:
    update.message.reply_text.assert_awaited_once()
    return update.message.reply_text.await_args.args[0]


def _reply_markup(update):
    """Return the InlineKeyboardMarkup the last reply_text was sent with."""
    return update.message.reply_text.await_args.kwargs.get("reply_markup")


def make_callback_update(data: str, chat_id: int = 42):
    """An update shaped like a callback-query press."""
    query = AsyncMock()
    query.data = data
    query.message = AsyncMock()
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=None,
        callback_query=query,
    )


# ---- show state --------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_args_shows_on_state_when_enabled(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config))

    text = _reply_text(update)
    assert "LLM: on" in text


@pytest.mark.asyncio
async def test_no_args_shows_runtime_off_when_db_disabled(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, LLM_FLAG_KEY, "0")
    update = make_update()

    await llm_command(update, make_context(config))

    text = _reply_text(update)
    assert "LLM: off" in text
    assert "runtime" in text


@pytest.mark.asyncio
async def test_no_args_shows_config_off_state(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config))

    text = _reply_text(update)
    assert "config" in text
    assert "LLM: off" in text


# ---- toggle ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_off_disables_runtime_flag(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["off"]))

    assert db.get_setting(config.db_path, LLM_FLAG_KEY) == "0"


@pytest.mark.asyncio
async def test_on_enables_runtime_flag(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, LLM_FLAG_KEY, "0")
    update = make_update()

    await llm_command(update, make_context(config, args=["on"]))

    assert db.get_setting(config.db_path, LLM_FLAG_KEY) == "1"


@pytest.mark.asyncio
async def test_refuses_to_enable_when_config_floor_is_off(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["on"]))

    text = _reply_text(update)
    assert "Can't toggle" in text
    # And the DB flag must NOT have been mutated.
    assert db.get_setting(config.db_path, LLM_FLAG_KEY) is None


@pytest.mark.asyncio
async def test_invalid_arg_shows_usage(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["maybe"]))

    text = _reply_text(update)
    assert "Usage" in text
    # Flag stays untouched.
    assert db.get_setting(config.db_path, LLM_FLAG_KEY) is None


# ---- /llm test --------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_test_success_path(tmp_path: Path, monkeypatch) -> None:
    """/llm test acks, runs smoketest, edits ack with ✓ + line."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()
    ack = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=ack)

    async def _ok(config):
        return True, "🌙 Online and slightly bored."
    monkeypatch.setattr(
        "naarad.handlers.llm.run_smoketest", _ok,
    )

    await llm_command(update, make_context(config, args=["test"]))

    update.message.reply_text.assert_awaited_once()  # the "⏳ Testing LLM…" ack
    ack.edit_text.assert_awaited_once()
    text = ack.edit_text.await_args.args[0]
    assert "✓" in text
    assert "Online and slightly bored" in text


@pytest.mark.asyncio
async def test_llm_test_failure_path(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()
    ack = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=ack)

    async def _fail(config):
        return False, "copilot CLI not found on PATH"
    monkeypatch.setattr(
        "naarad.handlers.llm.run_smoketest", _fail,
    )

    await llm_command(update, make_context(config, args=["test"]))

    text = ack.edit_text.await_args.args[0]
    assert "✗" in text
    assert "copilot CLI not found" in text


# ---- /llm backend -----------------------------------------------------------

@pytest.mark.asyncio
async def test_backend_no_arg_shows_config_default(tmp_path: Path) -> None:
    config = make_config(tmp_path)  # default backend = "copilot"
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["backend"]))

    text = _reply_text(update)
    assert "copilot" in text
    assert "config default" in text


@pytest.mark.asyncio
async def test_backend_set_to_other_persists_override(tmp_path: Path) -> None:
    from naarad.runtime import LLM_BACKEND_KEY, get_llm_backend
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["backend", "claude"]))

    assert db.get_setting(config.db_path, LLM_BACKEND_KEY) == "claude"
    assert get_llm_backend(config, config.db_path) == "claude"
    text = _reply_text(update)
    assert "claude" in text
    assert "runtime override" in text


@pytest.mark.asyncio
async def test_backend_set_to_config_default_clears_override(tmp_path: Path) -> None:
    from naarad.runtime import LLM_BACKEND_KEY
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    # Seed an existing override that we expect this command to clear.
    db.set_setting(config.db_path, LLM_BACKEND_KEY, "claude")
    update = make_update()

    await llm_command(update, make_context(config, args=["backend", "copilot"]))

    # Override was the config default → cleared (stored as empty string).
    assert (db.get_setting(config.db_path, LLM_BACKEND_KEY) or "") == ""
    text = _reply_text(update)
    assert "reverted" in text


@pytest.mark.asyncio
async def test_backend_rejects_unknown(tmp_path: Path) -> None:
    from naarad.runtime import LLM_BACKEND_KEY
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["backend", "gpt4"]))

    text = _reply_text(update)
    assert "Unknown backend" in text
    # DB untouched.
    assert db.get_setting(config.db_path, LLM_BACKEND_KEY) is None


@pytest.mark.asyncio
async def test_backend_refused_when_config_floor_off(tmp_path: Path) -> None:
    from naarad.runtime import LLM_BACKEND_KEY
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["backend", "claude"]))

    text = _reply_text(update)
    assert "config" in text
    assert db.get_setting(config.db_path, LLM_BACKEND_KEY) is None


# ---- /llm shows test + backend in state -------------------------------------

@pytest.mark.asyncio
async def test_state_output_mentions_test_and_backend(tmp_path: Path) -> None:
    """Regression: `test` and `backend` should appear in /llm's no-arg panel
    so the user doesn't have to guess at sub-commands.
    """
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config))

    text = _reply_text(update)
    # Every sub-command must appear, with concrete examples:
    for cmd in (
        "/llm on",
        "/llm off",
        "/llm test",
        "/llm backend copilot",
        "/llm backend claude",
    ):
        assert cmd in text
    assert "Backend:" in text


# ---- panel keyboard --------------------------------------------------------

def _kb_button_texts(markup):
    """Flatten an InlineKeyboardMarkup to a list of button labels."""
    if markup is None:
        return []
    return [btn.text for row in markup.inline_keyboard for btn in row]


@pytest.mark.asyncio
async def test_no_args_panel_includes_action_buttons(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config))

    labels = _kb_button_texts(_reply_markup(update))
    assert any("Test" in lbl for lbl in labels)
    assert any("Switch backend" in lbl for lbl in labels)
    assert any("Disable" in lbl or "Enable" in lbl for lbl in labels)


@pytest.mark.asyncio
async def test_panel_has_no_buttons_when_config_floor_off(tmp_path: Path) -> None:
    """No actions to take when the config gates everything off — skip the
    button row rather than render greyed-out buttons.
    """
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config))

    assert _reply_markup(update) is None


# ---- /llm backend renders a button selector --------------------------------

@pytest.mark.asyncio
async def test_backend_no_arg_renders_keyboard_with_both_backends(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update()

    await llm_command(update, make_context(config, args=["backend"]))

    labels = _kb_button_texts(_reply_markup(update))
    # Both backends present; the current one has a ✓.
    assert any("copilot" in lbl and "✓" in lbl for lbl in labels)
    assert any("claude" in lbl for lbl in labels)


# ---- callback paths --------------------------------------------------------

@pytest.mark.asyncio
async def test_callback_backend_set_persists_override(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_callback_update("llm:backend:claude")

    await llm_callback(update, make_context(config))

    assert db.get_setting(config.db_path, LLM_BACKEND_KEY) == "claude"
    # Message refreshed via edit_text with the menu (✓ now on claude).
    update.callback_query.message.edit_text.assert_awaited_once()
    args, kwargs = update.callback_query.message.edit_text.await_args
    labels = _kb_button_texts(kwargs.get("reply_markup"))
    assert any("claude" in lbl and "✓" in lbl for lbl in labels)


@pytest.mark.asyncio
async def test_callback_toggle_flips_runtime_flag(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    # Currently on (default) — toggle should turn off.
    update = make_callback_update("llm:toggle")

    await llm_callback(update, make_context(config))

    assert db.get_setting(config.db_path, LLM_FLAG_KEY) == "0"


@pytest.mark.asyncio
async def test_callback_backend_set_blocked_when_floor_off(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    update = make_callback_update("llm:backend:claude")

    await llm_callback(update, make_context(config))

    # Override must NOT have been written.
    assert db.get_setting(config.db_path, LLM_BACKEND_KEY) is None


@pytest.mark.asyncio
async def test_callback_rejects_unauthorized_chat(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_callback_update("llm:backend:claude", chat_id=999)

    await llm_callback(update, make_context(config))

    assert db.get_setting(config.db_path, LLM_BACKEND_KEY) is None
    update.callback_query.message.edit_text.assert_not_awaited()


# ---- auth gate ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_unauthorized_chat_is_silently_dropped(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    update = make_update(chat_id=999)  # not config.telegram.chat_id

    await llm_command(update, make_context(config, args=["off"]))

    update.message.reply_text.assert_not_awaited()
    assert db.get_setting(config.db_path, LLM_FLAG_KEY) is None
