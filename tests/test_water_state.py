from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from naarad.water.state import (
    Idle,
    Reminder,
    Sleep,
    WaterConfig,
    WaterState,
    apply_confirm,
    apply_day_started,
    apply_reminder_sent,
    next_action,
)

TZ = ZoneInfo("America/Toronto")
CFG = WaterConfig(
    active_end=time(21, 0),
    intervals_minutes=(120, 60, 30, 15, 5),
    tz=TZ,
)


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


# ---------- Day-not-started gating ----------

def test_day_not_started_returns_idle():
    """Until the morning Start fires, nothing schedules — including pre-6am, mid-day, anytime."""
    state = WaterState()
    for hour in (6, 8, 10, 14, 20):
        action = next_action(state, at(2026, 5, 2, hour, 0), CFG)
        assert isinstance(action, Idle), f"hour {hour} should be Idle, got {action}"


def test_yesterday_started_today_idle():
    """day_started_on for a prior date must not satisfy 'today is started'."""
    state = WaterState(day_started_on=date(2026, 5, 1))
    action = next_action(state, at(2026, 5, 2, 10, 0), CFG)
    assert isinstance(action, Idle)


def test_after_active_end_returns_idle():
    state = WaterState(day_started_on=date(2026, 5, 2))
    action = next_action(state, at(2026, 5, 2, 21, 0), CFG)
    assert isinstance(action, Idle)
    action2 = next_action(state, at(2026, 5, 2, 22, 30), CFG)
    assert isinstance(action2, Idle)


# ---------- First reminder semantics ----------

def test_day_started_with_no_anchors_fires_first_reminder_immediately():
    """Right after the Start tap, level=0 anchors=None -> Reminder(0) right now."""
    state = WaterState(day_started_on=date(2026, 5, 2), level=0)
    action = next_action(state, at(2026, 5, 2, 8, 30), CFG)
    assert action == Reminder(level=0)


def test_first_reminder_sent_advances_level_and_schedules_next():
    state = WaterState(day_started_on=date(2026, 5, 2), level=0)
    fired = apply_reminder_sent(state, at(2026, 5, 2, 8, 30), msg_id=42, cfg=CFG)
    assert fired.level == 1
    assert fired.last_reminder_at == at(2026, 5, 2, 8, 30)
    # Next due = 8:30 + 60min = 9:30
    action = next_action(fired, at(2026, 5, 2, 8, 31), CFG)
    assert action == Sleep(at(2026, 5, 2, 9, 30))


# ---------- Escalation curve ----------

def test_escalation_uses_anchored_intervals():
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        last_drink_at=at(2026, 5, 2, 10, 0),
        level=0,
    )
    action = next_action(state, at(2026, 5, 2, 11, 0), CFG)
    # 10:00 + 120min = 12:00, still in the future at 11:00
    assert action == Sleep(at(2026, 5, 2, 12, 0))


def test_reminder_due_fires_at_current_level():
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        last_drink_at=at(2026, 5, 2, 10, 0),
        level=0,
    )
    action = next_action(state, at(2026, 5, 2, 12, 0), CFG)
    assert action == Reminder(level=0)


def test_level_caps_at_max_and_uses_smallest_interval():
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        last_drink_at=at(2026, 5, 2, 10, 0),
        last_reminder_at=at(2026, 5, 2, 14, 0),
        level=4,
    )
    # Last interval is 5 minutes; 14:00 + 5min = 14:05.
    action = next_action(state, at(2026, 5, 2, 14, 1), CFG)
    assert action == Sleep(at(2026, 5, 2, 14, 5))

    fired = apply_reminder_sent(state, at(2026, 5, 2, 14, 5), msg_id=99, cfg=CFG)
    # Already at max, must not exceed.
    assert fired.level == 4


# ---------- Confirm reset ----------

def test_confirm_resets_level_and_anchor():
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        last_drink_at=at(2026, 5, 2, 10, 0),
        last_reminder_at=at(2026, 5, 2, 14, 0),
        level=3,
    )
    confirmed = apply_confirm(state, at(2026, 5, 2, 14, 30))
    assert confirmed.level == 0
    assert confirmed.last_reminder_at is None
    assert confirmed.last_drink_at == at(2026, 5, 2, 14, 30)
    # Next reminder is 14:30 + 120min = 16:30.
    action = next_action(confirmed, at(2026, 5, 2, 14, 31), CFG)
    assert action == Sleep(at(2026, 5, 2, 16, 30))


# ---------- Active-window edge ----------

def test_reminder_due_after_bedtime_is_idle():
    """A reminder that would land after 21:00 is dropped, scheduler goes Idle."""
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        last_drink_at=at(2026, 5, 2, 19, 30),
        level=0,
    )
    # Next reminder would be 21:30, but active_end is 21:00.
    action = next_action(state, at(2026, 5, 2, 20, 0), CFG)
    assert isinstance(action, Idle)


# ---------- apply_day_started ----------

def test_apply_day_started_resets_state():
    state = WaterState(
        last_drink_at=at(2026, 5, 1, 18, 0),
        last_reminder_at=at(2026, 5, 1, 20, 0),
        level=3,
        last_msg_id=99,
    )
    after = apply_day_started(state, date(2026, 5, 2))
    assert after.day_started_on == date(2026, 5, 2)
    assert after.last_drink_at is None
    assert after.last_reminder_at is None
    assert after.level == 0
    # last_msg_id is preserved (not reset by day start).
    assert after.last_msg_id == 99


def test_after_apply_day_started_first_reminder_fires():
    """Sequencing test: apply_day_started -> next_action -> Reminder(0)."""
    state = apply_day_started(WaterState(), date(2026, 5, 2))
    action = next_action(state, at(2026, 5, 2, 8, 30), CFG)
    assert action == Reminder(level=0)


# ---------- Naive datetime guard ----------

def test_naive_datetime_rejected():
    state = WaterState(day_started_on=date(2026, 5, 2))
    with pytest.raises(ValueError):
        next_action(state, datetime(2026, 5, 2, 10, 0), CFG)


# ---------- Anchor selection ----------

def test_anchor_is_max_of_drink_and_reminder():
    """Whichever is more recent — drink or reminder — is the anchor."""
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        last_drink_at=at(2026, 5, 2, 14, 0),
        last_reminder_at=at(2026, 5, 2, 13, 0),  # older than last_drink_at
        level=0,
    )
    # Anchor must be 14:00 (the later one), next due 16:00.
    action = next_action(state, at(2026, 5, 2, 14, 30), CFG)
    assert action == Sleep(at(2026, 5, 2, 16, 0))


def test_anchor_uses_reminder_when_more_recent():
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        last_drink_at=at(2026, 5, 2, 10, 0),
        last_reminder_at=at(2026, 5, 2, 14, 0),
        level=2,
    )
    # Anchor 14:00, level=2 -> 14:00 + 30min = 14:30
    action = next_action(state, at(2026, 5, 2, 14, 1), CFG)
    assert action == Sleep(at(2026, 5, 2, 14, 30))
