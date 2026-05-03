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
import os
import shutil
import subprocess
from datetime import datetime

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


def _copilot_bin() -> str:
    explicit = os.environ.get("COPILOT_BIN")
    if explicit:
        return explicit
    found = shutil.which("copilot")
    if found:
        return found
    return "copilot"


def _run_copilot_sync(prompt: str, timeout: int) -> str:
    """Synchronous subprocess call. Returns the trimmed first line, or "" on failure."""
    cmd = [
        _copilot_bin(),
        "-p", prompt,
        "--no-color",
        "--log-level", "none",
        "--deny-tool=shell",
        "--deny-tool=write",
        "--disable-builtin-mcps",
        "--no-ask-user",
        "--no-auto-update",
    ]
    started = datetime.now()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        log.warning("copilot CLI not found; falling back to hardcoded reminder")
        return ""
    except subprocess.TimeoutExpired:
        log.warning("copilot reminder timed out after %ds; falling back", timeout)
        return ""
    except Exception:
        log.exception("copilot reminder subprocess crashed; falling back")
        return ""

    elapsed = (datetime.now() - started).total_seconds()
    log.info(
        "copilot reminder exit=%d in %.1fs (stdout=%dB)",
        result.returncode, elapsed, len(result.stdout or ""),
    )
    if result.returncode != 0:
        return ""
    body = (result.stdout or "").strip()
    if not body:
        return ""
    # Take the first non-empty line — guard against multi-line drift.
    for line in body.splitlines():
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
