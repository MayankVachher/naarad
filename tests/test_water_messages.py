"""Tests for water/messages.py — pure copy helpers."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from naarad.water.messages import (
    FIRST_OF_DAY_MESSAGE,
    confirm_response,
    humanize_minutes,
    logged_edit_text,
    reminder_text,
)

TZ = ZoneInfo("America/Toronto")


# ---- humanize_minutes -------------------------------------------------------

@pytest.mark.parametrize(
    "minutes, expected",
    [
        (5, "5m"),
        (60, "1h"),
        (90, "1h30m"),
        (120, "2h"),
        (125, "2h5m"),
        (0, "0m"),
        (-5, "0m"),
    ],
)
def test_humanize_minutes(minutes: int, expected: str) -> None:
    assert humanize_minutes(minutes) == expected


# ---- confirm_response -------------------------------------------------------

def test_confirm_response_includes_count_and_interval() -> None:
    out = confirm_response(glasses_today=3, next_interval_minutes=120)
    assert "#3" in out
    assert "2h" in out
    assert "💧" in out


def test_confirm_response_formats_non_round_intervals() -> None:
    out = confirm_response(glasses_today=1, next_interval_minutes=90)
    assert "1h30m" in out


# ---- logged_edit_text -------------------------------------------------------

def test_logged_edit_text_appends_count_and_time() -> None:
    now = datetime(2026, 5, 7, 14, 5, tzinfo=TZ)
    out = logged_edit_text("💧 Time for water", now, glasses_today=4)
    assert "💧 Time for water" in out
    assert "Glass #4" in out
    assert "14:05" in out
    assert "<i>" in out and "</i>" in out


def test_logged_edit_text_handles_empty_original() -> None:
    now = datetime(2026, 5, 7, 14, 5, tzinfo=TZ)
    out = logged_edit_text("", now, glasses_today=1)
    assert "Glass #1 logged at 14:05" in out


# ---- regression: reminder_text + FIRST_OF_DAY_MESSAGE unchanged ------------

def test_reminder_text_level_0() -> None:
    assert reminder_text(0).startswith("💧")


def test_first_of_day_message_constant() -> None:
    assert FIRST_OF_DAY_MESSAGE.startswith("💧")
    assert "First sip" in FIRST_OF_DAY_MESSAGE
    # Deliberately not time-of-day specific.
    for stamp in ("Morning", "Afternoon", "Evening", "Night"):
        assert stamp not in FIRST_OF_DAY_MESSAGE
