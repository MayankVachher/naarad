"""Copy for water reminders, indexed by level.

Level 0..3 are escalating in tone; level 4+ is the floor.
"""
from __future__ import annotations

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

CONFIRM_RESPONSE = "💧 Logged. Next nudge in 2h."
