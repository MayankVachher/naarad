"""Tests for the bot entrypoint — specifically the NAARAD_SMOKE_TEST=1
early-exit mode used by the install scripts to validate config without
firing Telegram traffic.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_config_file(tmp_path: Path, monkeypatch) -> Path:
    """Write a minimal valid config.json and point load_config at it."""
    config = tmp_path / "config.json"
    config.write_text(
        """{
            "telegram": {
                "token": "123:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                "chat_id": 42
            },
            "eodhd": {"api_key": ""},
            "timezone": "America/Toronto",
            "db_path": "%s"
        }""" % (tmp_path / "state.db")
    )
    monkeypatch.setattr("naarad.config._DEFAULT_CONFIG_PATH", config)
    return config


def test_smoke_test_mode_exits_without_polling(fake_config_file, monkeypatch):
    """With NAARAD_SMOKE_TEST=1 set, main() runs validate_startup but
    returns before run_polling. No Telegram contact, no post_init."""
    monkeypatch.setenv("NAARAD_SMOKE_TEST", "1")

    from naarad import bot

    # Spy on build_application's return value's run_polling to confirm
    # it's never invoked.
    fake_app = MagicMock()
    monkeypatch.setattr(bot, "build_application", MagicMock(return_value=fake_app))

    bot.main()

    fake_app.run_polling.assert_not_called()


def test_normal_mode_invokes_run_polling(fake_config_file, monkeypatch):
    """Without NAARAD_SMOKE_TEST set, main() calls run_polling as usual."""
    monkeypatch.delenv("NAARAD_SMOKE_TEST", raising=False)

    from naarad import bot

    fake_app = MagicMock()
    monkeypatch.setattr(bot, "build_application", MagicMock(return_value=fake_app))
    # Make run_polling a no-op (returns immediately) so the test exits.
    fake_app.run_polling = MagicMock()

    bot.main()

    fake_app.run_polling.assert_called_once()


def test_smoke_test_mode_with_value_other_than_1_still_polls(
    fake_config_file, monkeypatch
):
    """Only the literal string "1" triggers smoke-test mode; arbitrary
    truthy strings shouldn't accidentally short-circuit normal startup.
    """
    monkeypatch.setenv("NAARAD_SMOKE_TEST", "true")

    from naarad import bot

    fake_app = MagicMock()
    monkeypatch.setattr(bot, "build_application", MagicMock(return_value=fake_app))

    with patch.object(fake_app, "run_polling") as mock_poll:
        bot.main()

    mock_poll.assert_called_once()
