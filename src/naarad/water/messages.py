"""Copy for water reminders, indexed by level.

Level 0..3 are escalating in tone; level 4+ is the floor.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

_TONES = (
    "💧 Time for water",
    "💧💧 Hey, hydrate",
    "💧💧💧 You really should drink water",
    "💧💧💧💧 DRINK. WATER. NOW.",
    "🚨 HYDRATION EMERGENCY 🚨",
)


def reminder_text(level: int) -> str:
    """Return the message body for a reminder at the given level (0-indexed)."""
    if level < 0:
        level = 0
    return _TONES[min(level, len(_TONES) - 1)]


# Sent as the very first reminder of a day (after grace, or immediately
# if the user tapped the welcome button). Deliberately time-agnostic —
# the chain might start at 06:00, mid-afternoon, or any time the user
# installs the bot or wipes state.db; "Morning, …" would be jarringly
# wrong half the time.
FIRST_OF_DAY_MESSAGE = "💧 First sip when you're ready."


def humanize_minutes(m: int) -> str:
    """Format a minute count as ``2h``, ``1h30m``, or ``45m``."""
    if m <= 0:
        return "0m"
    hours, mins = divmod(m, 60)
    if hours and mins:
        return f"{hours}h{mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


PaceStatus = Literal["target_hit", "on_track", "at_risk", "behind", "unknown"]


def pace_status(actual: int, expected: float, target: int) -> tuple[PaceStatus, float]:
    """Classify the user's progress vs. expected pace.

    Returns (status, deficit). ``deficit`` is ``expected - actual`` for
    the "behind" / "at_risk" branches (always > 0 there); 0.0 otherwise.

    Bands:
      - ``target_hit``   → actual >= target
      - ``on_track``     → at or ahead of pace
      - ``at_risk``      → behind by less than 1 glass
      - ``behind``       → behind by 1 glass or more
      - ``unknown``      → target disabled or expected not yet meaningful
                          (no chain_started_at, before active window, etc.)
    """
    if target <= 0 or expected <= 0:
        return "unknown", 0.0
    if actual >= target:
        return "target_hit", 0.0
    deficit = expected - actual
    if deficit <= 0:
        return "on_track", 0.0
    if deficit < 1.0:
        return "at_risk", deficit
    return "behind", deficit


_PACE_BADGES: dict[PaceStatus, str] = {
    "target_hit": "🎯 target hit",
    "on_track":   "🟢 on track",
    "at_risk":    "⚠️ at risk",
    "behind":     "🚨 behind",   # suffix gets appended with the deficit
}


def confirm_response(
    *,
    glasses_today: int,
    daily_target: int,
    status: PaceStatus,
    deficit: float,
    next_reminder_at: datetime | None,
) -> str:
    """The text sent back after /water, the 💧 button tap, or a reply
    to a reminder. Two lines:

    1. ``💧 Glass #N/T logged · <pace badge>`` (or just ``Glass #N``
       if pace tracking is disabled).
    2. ``Next reminder at HH:MM.`` (or ``No more reminders today.``
       when Idle through to tomorrow).
    """
    # Line 1: count + optional pace badge.
    if daily_target > 0:
        count = f"Glass #{glasses_today}/{daily_target}"
    else:
        count = f"Glass #{glasses_today}"
    badge = _PACE_BADGES.get(status, "")
    if status == "behind" and deficit > 0:
        # Round to 0.1 glass for a readable hint; pluralize.
        glasses_word = "glass" if deficit < 1.5 else "glasses"
        badge = f"🚨 behind by ~{deficit:.1f} {glasses_word}"
    line1 = f"💧 {count} logged"
    if badge:
        line1 = f"{line1} · {badge}"

    # Line 2: next reminder.
    if next_reminder_at is None:
        line2 = "No more reminders today."
    else:
        line2 = f"Next reminder at {next_reminder_at.strftime('%H:%M')}."

    return f"{line1}\n{line2}"


def logged_edit_text(original: str, now: datetime, glasses_today: int) -> str:
    """Edit-on-reminder body: append a small italic line confirming the
    log and including the running glass count.
    """
    base = (original or "").rstrip()
    stamp = now.strftime("%H:%M")
    return f"{base}\n\n<i>✅ Glass #{glasses_today} logged at {stamp}</i>"
