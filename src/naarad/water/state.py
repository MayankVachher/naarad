"""Water reminder state machine — pure logic, no I/O.

Phase 7 redesign: the chain is gated by `day_started_on` (set by the morning
flow's Start tap or 11 AM fallback), not by a soft morning ping or first
confirm of the day. When day_started_on is not today, the state machine
returns Idle — the morning scheduler is responsible for triggering start.

The state machine has a single function `next_action(state, now, config)` that
computes what to do at this moment. The bot's scheduler loops:

  1. action = next_action(state, now, config)
  2. dispatch action (send message / sleep / idle)
  3. apply_* updates state
  4. repeat

This separation keeps all timing logic testable with `freezegun`.

Conceptual model
----------------
- `level` is the level of the *next* reminder to fire (0..max).
- `intervals[level]` is how long to wait *until* that next reminder.
- When a reminder fires at level N, level becomes min(N+1, max).
- When the user confirms, level resets to 0 and the chain restarts from now.
- `day_started_on` records whether today's morning Start has fired. While
  unset (or set to a prior date), no reminders fire — Idle.
- On day start (via apply_day_started), state is reset and the very next
  next_action returns Reminder(0) — the first reminder fires immediately.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class WaterState:
    last_drink_at: datetime | None = None
    last_reminder_at: datetime | None = None
    level: int = 0
    last_msg_id: int | None = None
    day_started_on: date | None = None


@dataclass(frozen=True)
class WaterConfig:
    active_end: time            # e.g. time(21, 0)
    intervals_minutes: tuple[int, ...]  # e.g. (120, 60, 30, 15, 5)
    tz: ZoneInfo


# --- Action types ---

@dataclass(frozen=True)
class Reminder:
    """Send an escalating water reminder at the given level."""
    level: int


@dataclass(frozen=True)
class Sleep:
    """Nothing to do until `until`. Scheduler should park a job at this time."""
    until: datetime


@dataclass(frozen=True)
class Idle:
    """Day not started yet, or past active_end. No scheduled wake-up — the
    morning scheduler will trigger the next start_day transition.
    """


Action = Reminder | Sleep | Idle


# --- Helpers ---

def _max_level(cfg: WaterConfig) -> int:
    return len(cfg.intervals_minutes) - 1


def _interval_for(cfg: WaterConfig, level: int) -> timedelta:
    idx = min(max(level, 0), _max_level(cfg))
    return timedelta(minutes=cfg.intervals_minutes[idx])


def _at(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=tz)


def _select_anchor(state: WaterState) -> datetime | None:
    """Most recent of last_drink_at / last_reminder_at, or None if neither is set."""
    if state.last_drink_at is None and state.last_reminder_at is None:
        return None
    if state.last_drink_at is None:
        return state.last_reminder_at
    if state.last_reminder_at is None:
        return state.last_drink_at
    return max(state.last_drink_at, state.last_reminder_at)


# --- Core ---

def next_action(state: WaterState, now: datetime, cfg: WaterConfig) -> Action:
    """Decide the next action given current state, current time, and config.

    `now` must be timezone-aware. The returned datetimes (in Sleep) are also
    timezone-aware in cfg.tz.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(cfg.tz)
    today = now.date()
    end_today = _at(today, cfg.active_end, cfg.tz)

    # Day not started by morning flow yet: silent.
    if state.day_started_on != today:
        return Idle()

    # Past end of active window: silent until tomorrow's start_day.
    if now >= end_today:
        return Idle()

    anchor = _select_anchor(state)
    if anchor is None:
        # day_started but nothing recorded yet -> fire first reminder NOW.
        return Reminder(level=state.level)

    next_due = anchor + _interval_for(cfg, state.level)

    if next_due >= end_today:
        # Would fall after bedtime; idle until tomorrow's start_day.
        return Idle()
    if next_due > now:
        return Sleep(next_due)

    # next_due <= now -> fire a reminder at the current level.
    return Reminder(level=state.level)


# --- State transitions ---

def apply_day_started(state: WaterState, today: date) -> WaterState:
    """Morning Start fired (via tap or fallback): reset chain for the new day."""
    return replace(
        state,
        day_started_on=today,
        last_drink_at=None,
        last_reminder_at=None,
        level=0,
    )


def apply_confirm(state: WaterState, now: datetime) -> WaterState:
    """User said they drank water: reset chain, level=0, anchor=now."""
    return replace(
        state,
        last_drink_at=now,
        last_reminder_at=None,
        level=0,
    )


def apply_reminder_sent(
    state: WaterState, now: datetime, msg_id: int, cfg: WaterConfig
) -> WaterState:
    """A reminder was just sent: bump level (capped) and record send time."""
    new_level = min(state.level + 1, _max_level(cfg))
    return replace(
        state,
        last_reminder_at=now,
        level=new_level,
        last_msg_id=msg_id,
    )
