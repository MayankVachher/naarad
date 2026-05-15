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
    # Set on apply_day_started; lets next_action apply a grace period
    # (cfg.first_reminder_delay_minutes) before the first reminder fires
    # on a freshly-started day. Survives bot restart so a crash mid-grace
    # doesn't reset the timer.
    chain_started_at: datetime | None = None
    # Count of confirms since the day started. Reset to 0 by
    # apply_day_started, incremented by apply_confirm. Surfaced back to
    # the user in the confirm response and the "✅ logged" edit.
    glasses_today: int = 0
    # Same-day pause flag. While True, next_action returns Idle and the
    # scheduler parks no jobs. Reset to False by apply_day_started, so
    # tomorrow's chain always begins unpaused; flipped by apply_pause /
    # apply_resume. Logging a glass does NOT clear it.
    paused: bool = False


@dataclass(frozen=True)
class WaterConfig:
    active_end: time            # e.g. time(21, 0)
    intervals_minutes: tuple[int, ...]  # e.g. (120, 60, 30, 15, 5)
    tz: ZoneInfo
    # How long to wait between tapping the morning brief's [Start day]
    # button and the first reminder of the day. Lets the user finish
    # brushing teeth, etc. The welcome message's tap bypasses this
    # entirely — you're actively at the bot. Default 3 min.
    first_reminder_delay_minutes: int = 3
    # Glass-count target for the day. Used by pace-adjusted intervals
    # in next_action and the progress display in /status. Setting this
    # to 0 disables pace adjustment.
    daily_target_glasses: int = 8
    # Minimum multiplier when behind pace — at most this fraction of
    # the base interval, regardless of how far behind. 0.3 = a 120-min
    # base becomes at most ~36 min.
    pace_floor: float = 0.3


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


def expected_glasses_now(
    state: WaterState, now: datetime, cfg: WaterConfig
) -> float:
    """How many glasses we'd expect by ``now`` if the user were on
    pace to hit ``daily_target_glasses`` evenly across the active
    window (chain_started_at → active_end). Returns 0.0 if pace can't
    be computed (target off, chain not started, after end, etc.).
    """
    if cfg.daily_target_glasses <= 0 or state.chain_started_at is None:
        return 0.0
    now = now.astimezone(cfg.tz)
    end_today = _at(now.date(), cfg.active_end, cfg.tz)
    chain_start = state.chain_started_at.astimezone(cfg.tz)
    total = (end_today - chain_start).total_seconds()
    if total <= 0:
        return 0.0
    elapsed = (now - chain_start).total_seconds()
    if elapsed <= 0:
        return 0.0
    if elapsed >= total:
        return float(cfg.daily_target_glasses)
    return cfg.daily_target_glasses * (elapsed / total)


def _pace_adjust(
    base: timedelta, state: WaterState, now: datetime, cfg: WaterConfig
) -> timedelta:
    """Shorten ``base`` if the user is behind the day's glass-count
    pace. Multiplier = ``max(pace_floor, 1 - deficit/target)``, so:

    - on/ahead pace → no change
    - behind by 1 of an 8-target day → multiplier 0.875 (12% tighter)
    - behind by 4 → 0.5 (half interval)
    - behind by 6+ → floored at pace_floor (default 0.3)
    """
    target = cfg.daily_target_glasses
    if target <= 0:
        return base
    expected = expected_glasses_now(state, now, cfg)
    if expected <= 0:
        return base
    deficit = expected - state.glasses_today
    if deficit <= 0:
        return base
    factor = max(cfg.pace_floor, 1.0 - (deficit / target))
    return base * factor


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

    # User paused the chain (via /water pause or the panel button).
    # Same-day only — apply_day_started will clear paused so tomorrow's
    # chain begins unpaused. While paused, the scheduler parks no jobs;
    # /water resume kicks the loop back to life.
    if state.paused:
        return Idle()

    # Past end of active window: silent until tomorrow's start_day.
    if now >= end_today:
        return Idle()

    # Target hit for the day: bot is done nudging, matches the
    # "🎯 Target hit" badge on the confirm reply. The user can still
    # log more glasses via /water if they want; tomorrow's start_day
    # resets glasses_today and reminders resume. Skipped when pace
    # tracking is disabled (target=0) — bot keeps fixed-cadence
    # reminders in that mode.
    if (
        cfg.daily_target_glasses > 0
        and state.glasses_today >= cfg.daily_target_glasses
    ):
        return Idle()

    anchor = _select_anchor(state)
    if anchor is None:
        # day_started but no drink/reminder yet — apply the first-of-day
        # grace period so the bot waits while the user finishes their
        # morning routine before nudging.
        if state.chain_started_at is not None:
            grace_due = state.chain_started_at + timedelta(
                minutes=cfg.first_reminder_delay_minutes
            )
            if grace_due >= end_today:
                return Idle()
            if grace_due > now:
                return Sleep(grace_due)
        # chain_started_at unset (legacy state pre-v4) → fire now.
        return Reminder(level=state.level)

    next_due = anchor + _pace_adjust(
        _interval_for(cfg, state.level), state, now, cfg,
    )

    if next_due >= end_today:
        # Would fall after bedtime; idle until tomorrow's start_day.
        return Idle()
    if next_due > now:
        return Sleep(next_due)

    # next_due <= now -> fire a reminder at the current level.
    return Reminder(level=state.level)


# --- State transitions ---

def apply_day_started(state: WaterState, today: date, now: datetime) -> WaterState:
    """Morning Start fired (via tap or fallback): reset chain for the
    new day, stamp ``chain_started_at`` so next_action can apply the
    first-reminder grace period, zero the day's glass counter, and clear
    any pause carried over from a previous day.
    """
    return replace(
        state,
        day_started_on=today,
        last_drink_at=None,
        last_reminder_at=None,
        level=0,
        chain_started_at=now,
        glasses_today=0,
        paused=False,
    )


def apply_pause(state: WaterState) -> WaterState:
    """Flip the pause flag on. No other state change — anchor + level
    are preserved so a resume picks the chain back up where it left off.
    """
    return replace(state, paused=True)


def apply_resume(state: WaterState) -> WaterState:
    """Flip the pause flag off. next_action then runs normally; if the
    last reminder/drink was long enough ago, a reminder fires immediately.
    """
    return replace(state, paused=False)


def apply_confirm(state: WaterState, now: datetime) -> WaterState:
    """User said they drank water: reset chain, level=0, anchor=now,
    bump the day's glass counter by one.
    """
    return replace(
        state,
        last_drink_at=now,
        last_reminder_at=None,
        level=0,
        glasses_today=state.glasses_today + 1,
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
