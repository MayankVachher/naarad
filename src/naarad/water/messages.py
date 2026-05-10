"""Copy for water reminders, indexed by level.

Level 0..3 are escalating in tone; level 4+ is the floor.
"""
from __future__ import annotations

from datetime import datetime

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


# Sent as the very first reminder of the day, after the start-day grace
# window expires. Different from the level-0 nudge because the user has
# just woken up — no escalation, no urgency, just a friendly opener.
FIRST_OF_DAY_MESSAGE = "💧 Morning. First sip when you're ready."


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


def confirm_response(glasses_today: int, next_interval_minutes: int) -> str:
    """The text sent back after /water, the 💧 button tap, or a reply
    to a reminder. Includes the running glass count and the dynamically
    formatted next-nudge interval (pulled from intervals_minutes[0]).
    """
    interval = humanize_minutes(next_interval_minutes)
    return f"💧 Glass #{glasses_today} logged. Next nudge in {interval}."


def logged_edit_text(original: str, now: datetime, glasses_today: int) -> str:
    """Edit-on-reminder body: append a small italic line confirming the
    log and including the running glass count.
    """
    base = (original or "").rstrip()
    stamp = now.strftime("%H:%M")
    return f"{base}\n\n<i>✅ Glass #{glasses_today} logged at {stamp}</i>"
