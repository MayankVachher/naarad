from __future__ import annotations

from datetime import date, datetime, time, timedelta
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
# Default test config disables pace adjustment so the timing-sensitive
# tests below can assert exact next_due values. Pace-specific tests
# construct their own config with daily_target_glasses > 0.
CFG = WaterConfig(
    active_end=time(21, 0),
    intervals_minutes=(120, 60, 30, 15, 5),
    tz=TZ,
    daily_target_glasses=0,
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

def test_confirm_increments_glasses_today():
    state = WaterState(glasses_today=2, day_started_on=date(2026, 5, 2))
    after = apply_confirm(state, at(2026, 5, 2, 10, 0))
    assert after.glasses_today == 3


def test_day_started_resets_glasses_today():
    state = WaterState(glasses_today=7, last_drink_at=at(2026, 5, 1, 18, 0))
    after = apply_day_started(state, date(2026, 5, 2), at(2026, 5, 2, 8, 0))
    assert after.glasses_today == 0


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
    started_at = at(2026, 5, 2, 8, 30)
    after = apply_day_started(state, date(2026, 5, 2), started_at)
    assert after.day_started_on == date(2026, 5, 2)
    assert after.last_drink_at is None
    assert after.last_reminder_at is None
    assert after.level == 0
    # last_msg_id is preserved (not reset by day start).
    assert after.last_msg_id == 99
    # chain_started_at stamped so the grace period can be applied.
    assert after.chain_started_at == started_at


def test_after_apply_day_started_first_reminder_waits_for_grace():
    """Sequencing test: apply_day_started → next_action → Sleep(now+grace),
    not immediate Reminder. After the grace expires it fires."""
    started_at = at(2026, 5, 2, 8, 30)
    state = apply_day_started(WaterState(), date(2026, 5, 2), started_at)

    # During grace: Sleep, not Reminder. Default grace is 3 min.
    action = next_action(state, at(2026, 5, 2, 8, 31), CFG)
    assert action == Sleep(at(2026, 5, 2, 8, 33))  # 8:30 + 3 min default

    # After grace: Reminder fires at level 0.
    action = next_action(state, at(2026, 5, 2, 8, 33), CFG)
    assert action == Reminder(level=0)


def test_grace_period_yields_idle_if_it_overlaps_active_end():
    """If the grace would push the first reminder past active_end (21:00)
    we go Idle — let tomorrow handle it."""
    # User taps Start at 20:58, default 5min grace → would land 21:03,
    # past active_end. Expected: Idle.
    started_at = at(2026, 5, 2, 20, 58)
    state = apply_day_started(WaterState(), date(2026, 5, 2), started_at)
    action = next_action(state, at(2026, 5, 2, 20, 59), CFG)
    assert isinstance(action, Idle)


def test_grace_zero_fires_immediately():
    """first_reminder_delay_minutes=0 disables the grace — immediate Reminder."""
    cfg_nograce = WaterConfig(
        active_end=time(21, 0),
        intervals_minutes=(120, 60, 30, 15, 5),
        tz=TZ,
        first_reminder_delay_minutes=0,
    )
    started_at = at(2026, 5, 2, 8, 30)
    state = apply_day_started(WaterState(), date(2026, 5, 2), started_at)
    action = next_action(state, started_at, cfg_nograce)
    assert action == Reminder(level=0)


def test_legacy_state_without_chain_started_at_fires_immediately():
    """Pre-v4 state (chain_started_at unset) takes the legacy path:
    fire immediately. No half-state where the user is permanently stuck."""
    state = WaterState(day_started_on=date(2026, 5, 2), chain_started_at=None)
    action = next_action(state, at(2026, 5, 2, 8, 30), CFG)
    assert action == Reminder(level=0)


# ---------- Pace-adjusted intervals ----------

_PACE_CFG = WaterConfig(
    active_end=time(21, 0),
    intervals_minutes=(120, 60, 30, 15, 5),
    tz=TZ,
    daily_target_glasses=8,
    pace_floor=0.3,
)


def test_pace_unchanged_when_on_target():
    """At hour 1 of a 15h window with target 8, expected ≈ 0.53 glasses.
    1 glass logged → ahead of pace → base interval unchanged (2h)."""
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        chain_started_at=at(2026, 5, 2, 6, 0),
        last_drink_at=at(2026, 5, 2, 7, 0),
        level=0,
        glasses_today=1,
    )
    action = next_action(state, at(2026, 5, 2, 7, 1), _PACE_CFG)
    # Anchor 07:00 + base 120min = 09:00 unmodified.
    assert action == Sleep(at(2026, 5, 2, 9, 0))


def test_pace_tightens_when_behind():
    """At hour 6 (12:00, chain started 06:00) target 8 → expected 3.2
    glasses. 1 logged → deficit 2.2 → factor 1 - 2.2/8 = 0.725 → 87 min."""
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        chain_started_at=at(2026, 5, 2, 6, 0),
        last_drink_at=at(2026, 5, 2, 11, 30),
        level=0,
        glasses_today=1,
    )
    action = next_action(state, at(2026, 5, 2, 12, 0), _PACE_CFG)
    # Anchor 11:30 + 0.725 * 120min = 11:30 + 87min = 12:57.
    assert isinstance(action, Sleep)
    # Allow up to 1s rounding tolerance.
    expected_due = at(2026, 5, 2, 11, 30) + timedelta(minutes=87)
    assert abs((action.until - expected_due).total_seconds()) < 1


def test_pace_floors_at_pace_floor():
    """Far enough behind that the linear factor would go below pace_floor
    (0.3). Floor kicks in — interval is 0.3 * base = 36 min."""
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        chain_started_at=at(2026, 5, 2, 6, 0),
        last_drink_at=at(2026, 5, 2, 17, 0),
        level=0,
        glasses_today=0,  # ridiculous deficit (would be ~6.4 behind at 18:00)
    )
    # Check at 17:10 (before next_due of 17:36) so we see the Sleep,
    # not an immediate Reminder.
    action = next_action(state, at(2026, 5, 2, 17, 10), _PACE_CFG)
    assert isinstance(action, Sleep)
    # Anchor 17:00 + 0.3 * 120min = 17:36.
    assert action.until == at(2026, 5, 2, 17, 36)


def test_pace_disabled_when_target_zero():
    cfg = WaterConfig(
        active_end=time(21, 0),
        intervals_minutes=(120, 60, 30, 15, 5),
        tz=TZ,
        daily_target_glasses=0,  # disabled
        pace_floor=0.3,
    )
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        chain_started_at=at(2026, 5, 2, 6, 0),
        last_drink_at=at(2026, 5, 2, 17, 0),
        level=0,
        glasses_today=0,
    )
    action = next_action(state, at(2026, 5, 2, 18, 0), cfg)
    # No pace adjustment — exact 2h interval.
    assert action == Sleep(at(2026, 5, 2, 19, 0))


def test_expected_glasses_now_linear():
    from naarad.water.state import expected_glasses_now
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        chain_started_at=at(2026, 5, 2, 6, 0),
    )
    # Half-way through the 15h window → half the target (4 of 8).
    halfway = at(2026, 5, 2, 13, 30)
    assert abs(expected_glasses_now(state, halfway, _PACE_CFG) - 4.0) < 0.01

    # Before chain started → 0.
    assert expected_glasses_now(state, at(2026, 5, 2, 5, 0), _PACE_CFG) == 0.0

    # After active_end → full target.
    after_end = at(2026, 5, 2, 22, 0)
    assert expected_glasses_now(state, after_end, _PACE_CFG) == 8.0


# ---------- Target-hit auto-idle ----------

def test_target_hit_returns_idle():
    """Once glasses_today >= daily_target_glasses, no more reminders."""
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        chain_started_at=at(2026, 5, 2, 6, 0),
        last_drink_at=at(2026, 5, 2, 14, 0),
        glasses_today=8,
        level=0,
    )
    action = next_action(state, at(2026, 5, 2, 15, 0), _PACE_CFG)
    assert isinstance(action, Idle)


def test_target_overshoot_still_idle():
    """Logging beyond target (over-drinking, double-tap) stays silent."""
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        chain_started_at=at(2026, 5, 2, 6, 0),
        last_drink_at=at(2026, 5, 2, 14, 0),
        glasses_today=11,
        level=0,
    )
    action = next_action(state, at(2026, 5, 2, 15, 0), _PACE_CFG)
    assert isinstance(action, Idle)


def test_one_short_of_target_keeps_scheduling():
    """Just below target → normal interval-based scheduling resumes."""
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        chain_started_at=at(2026, 5, 2, 6, 0),
        last_drink_at=at(2026, 5, 2, 14, 0),
        glasses_today=7,  # one short of 8
        level=0,
    )
    action = next_action(state, at(2026, 5, 2, 14, 30), _PACE_CFG)
    assert isinstance(action, Sleep)


def test_target_disabled_keeps_nudging_indefinitely():
    """When daily_target_glasses=0 (pace tracking off), the auto-stop
    doesn't engage — bot keeps cadence regardless of count. Lets users
    who want fixed-interval reminders opt out of the target-hit logic."""
    # CFG (the default for state tests) has daily_target_glasses=0.
    state = WaterState(
        day_started_on=date(2026, 5, 2),
        chain_started_at=at(2026, 5, 2, 6, 0),
        last_drink_at=at(2026, 5, 2, 14, 0),
        glasses_today=20,  # absurd count, target disabled
        level=0,
    )
    action = next_action(state, at(2026, 5, 2, 14, 30), CFG)
    assert isinstance(action, Sleep)


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
