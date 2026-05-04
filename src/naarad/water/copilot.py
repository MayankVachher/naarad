"""Generate the water reminder line via Copilot CLI.

Each time the water loop fires a reminder, we ask Copilot for a fresh
one-liner at the appropriate escalation level. On any failure (timeout,
non-zero exit, empty output) we fall back to the hardcoded `_TONES`
table in `water/messages.py` — the user always gets a reminder.

The subprocess runs in a worker thread so we don't block the event loop.
"""
from __future__ import annotations

import asyncio
import logging

from naarad.copilot_runner import run_copilot

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 45  # seconds; reminders shouldn't block the loop too long


PROMPT_TEMPLATE = """\
Write ONE single-line water reminder for Mayank (warm tone, dry wit, Toronto-based engineer).

Escalation level {level} (out of 4):
  0 = gentle, casual nudge
  1 = friendly check-in with mild concern
  2 = firm reminder, slightly impatient
  3 = strong demand, almost annoyed
  4 = alarm / emergency tone, all caps allowed

Hard rules:
- Output EXACTLY one line. Plain text. No markdown, no HTML.
- Lead with droplet emojis: 1 droplet at level 0, 2 at level 1, 3 at level 2, 4 at level 3, 🚨 + 💧 at level 4.
- ≤10 words after the emojis.
- Vary the wording — surprise him, don't just say "Time for water".
- No quotes, no preamble, no explanation. Just the line itself.
"""


def _run_copilot_sync(prompt: str, timeout: int) -> str:
    """Synchronous call. Returns the trimmed first non-empty line, or "" on failure."""
    result = run_copilot(prompt, timeout=timeout, log_label="water-reminder")
    if not result.ok:
        return ""
    # Take the first non-empty line — guard against multi-line drift.
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


async def generate_reminder_line(level: int, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Async wrapper. Returns the generated line, or "" on any failure.

    Caller is responsible for falling back to `messages.reminder_text(level)`
    when this returns empty.
    """
    prompt = PROMPT_TEMPLATE.format(level=max(0, min(level, 4)))
    try:
        return await asyncio.to_thread(_run_copilot_sync, prompt, timeout)
    except Exception:
        log.exception("generate_reminder_line wrapper crashed; falling back")
        return ""
