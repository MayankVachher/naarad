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
    pace_status,
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


# ---- pace_status ------------------------------------------------------------

def test_pace_status_target_hit_when_at_or_above() -> None:
    s, d = pace_status(actual=8, expected=4.0, target=8)
    assert s == "target_hit"
    assert d == 0.0


def test_pace_status_on_track_when_actual_ahead_of_expected() -> None:
    s, d = pace_status(actual=3, expected=2.5, target=8)
    assert s == "on_track"
    assert d == 0.0


def test_pace_status_at_risk_for_sub_glass_deficit() -> None:
    s, d = pace_status(actual=3, expected=3.6, target=8)
    assert s == "at_risk"
    assert 0 < d < 1


def test_pace_status_behind_for_one_or_more_glass_deficit() -> None:
    s, d = pace_status(actual=1, expected=3.0, target=8)
    assert s == "behind"
    assert d == 2.0


def test_pace_status_unknown_when_target_disabled() -> None:
    s, d = pace_status(actual=3, expected=0.0, target=0)
    assert s == "unknown"
    assert d == 0.0


# ---- confirm_response -------------------------------------------------------

def _confirm_call(**overrides):
    """Defaults that all confirm_response tests can override per case."""
    base = dict(
        glasses_today=3,
        daily_target=8,
        status="on_track",
        deficit=0.0,
        next_reminder_at=datetime(2026, 5, 7, 15, 32, tzinfo=TZ),
    )
    base.update(overrides)
    return confirm_response(**base)


def test_confirm_response_count_pace_and_time() -> None:
    out = _confirm_call()
    assert "💧 Glass #3/8 logged" in out
    assert "🟢 on track" in out
    assert "Next reminder at 15:32" in out


def test_confirm_response_omits_target_when_pace_disabled() -> None:
    out = _confirm_call(daily_target=0, status="unknown")
    assert "Glass #3 logged" in out
    assert "/0" not in out
    # No badge when pace is unknown.
    assert "🟢" not in out
    assert "🚨" not in out


def test_confirm_response_target_hit_badge() -> None:
    out = _confirm_call(glasses_today=8, status="target_hit")
    assert "🎯 target hit" in out


def test_confirm_response_at_risk_badge() -> None:
    out = _confirm_call(status="at_risk", deficit=0.7)
    assert "⚠️ at risk" in out


def test_confirm_response_behind_includes_deficit() -> None:
    out = _confirm_call(status="behind", deficit=2.3)
    assert "behind" in out
    assert "2.3" in out
    assert "glasses" in out  # plural for deficit >= 1.5


def test_confirm_response_behind_singular_for_small_deficit() -> None:
    out = _confirm_call(status="behind", deficit=1.2)
    # deficit < 1.5 → "glass" singular
    assert "1.2 glass" in out
    assert "glasses" not in out


def test_confirm_response_idle_says_no_more_reminders() -> None:
    out = _confirm_call(next_reminder_at=None)
    assert "No more reminders today" in out
    assert "Next reminder at" not in out


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
