"""Single source of truth for the water-status snapshot.

Both ``/water`` (the standalone read-only command) and the water section
of ``/status`` need the same derived view: glass count, pace classification,
next-reminder time, day-started flag, and the raw next-action so a richer
renderer can distinguish *why* a day is idle. Computing it once here keeps
the two surfaces in lock-step.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from naarad import db
from naarad.config import Config
from naarad.water.messages import PaceStatus, pace_status
from naarad.water.scheduler import water_config_from
from naarad.water.state import (
    Idle,
    Reminder,
    Sleep,
    WaterState,
    expected_glasses_now,
    next_action,
)

NextAction = Reminder | Sleep | Idle


@dataclass(frozen=True)
class WaterStatusView:
    """Everything a renderer needs about the current water state.

    ``action`` is the raw next-action so renderers (notably /status) can
    distinguish Idle-because-target-hit from Idle-because-active-hours-ended;
    ``next_reminder_at`` is the convenience form for renderers that only
    care about a clock time. ``paused`` is the *raw* flag — note that an
    Idle ``action`` can mean paused, target-hit, past active_end, or day
    not started, so renderers should consult both fields.
    """
    now: datetime
    glasses: int
    daily_target: int
    pace_status: PaceStatus
    pace_deficit: float
    day_started: bool
    target_hit: bool
    past_active_end: bool
    paused: bool
    level: int
    last_drink_at: datetime | None
    action: NextAction
    next_reminder_at: datetime | None


def compute_water_status(config: Config) -> WaterStatusView:
    """Read the current water state from the DB and derive the view."""
    raw = db.get_water_state(config.db_path)
    state = WaterState(
        last_drink_at=raw["last_drink_at"],
        last_reminder_at=raw["last_reminder_at"],
        level=raw["level"],
        last_msg_id=raw["last_msg_id"],
        day_started_on=raw["day_started_on"],
        chain_started_at=raw["chain_started_at"],
        glasses_today=raw["glasses_today"],
        paused=raw["paused"],
    )
    wcfg = water_config_from(config)
    now = datetime.now(config.tz)
    today = now.date()
    day_started = raw["day_started_on"] == today

    target = config.water.daily_target_glasses
    glasses = raw["glasses_today"]
    target_hit = target > 0 and glasses >= target
    active_end_today = datetime.combine(today, wcfg.active_end, tzinfo=config.tz)
    past_active_end = now >= active_end_today

    expected = expected_glasses_now(state, now, wcfg)
    pstatus, deficit = pace_status(glasses, expected, target)

    action = next_action(state, now, wcfg)
    if isinstance(action, Sleep):
        next_at: datetime | None = action.until
    elif isinstance(action, Reminder):
        next_at = now
    else:
        next_at = None

    return WaterStatusView(
        now=now,
        glasses=glasses,
        daily_target=target,
        pace_status=pstatus,
        pace_deficit=deficit,
        day_started=day_started,
        target_hit=target_hit,
        past_active_end=past_active_end,
        paused=raw["paused"],
        level=raw["level"],
        last_drink_at=raw["last_drink_at"],
        action=action,
        next_reminder_at=next_at,
    )
