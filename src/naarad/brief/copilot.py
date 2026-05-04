"""Daily brief generator: orchestrate prompt → Copilot CLI → sanitize.

The actual work lives in three sibling modules:
- prompt.py    — assembles the prompt from sources + config
- sanitizer.py — makes Copilot output safe for Telegram HTML parse mode
- ../copilot_runner.py — owns the subprocess invocation

This module just wires them together and adds the date header + safe
fallback so the morning brief is never silently missing.
"""
from __future__ import annotations

import logging
from datetime import date

from naarad.brief.prompt import build_prompt
from naarad.brief.sanitizer import sanitize_html
from naarad.config import Config
from naarad.copilot_runner import run_copilot

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 600  # seconds; copilot can take a while


def _fallback_brief(today: date, reason: str) -> str:
    return (
        f"<b>☀️ {today.strftime('%a %b ')}{today.day}{today.strftime(', %Y')}</b>\n"
        "\n"
        "(Copilot brief unavailable today — falling back to a placeholder.)\n"
        f"<i>{reason}</i>"
    )


def get_daily_brief(today: date, config: Config, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Generate today's brief by invoking `copilot -p <prompt>` non-interactively.

    Returns the brief body (already formatted for Telegram HTML parse mode).
    On failure, returns a fallback string — never raises.
    """
    prompt = build_prompt(today, config)
    result = run_copilot(prompt, timeout=timeout, log_label="daily-brief")
    if not result.ok:
        return _fallback_brief(today, result.error_reason)

    body = sanitize_html(result.stdout)

    # Header line at top — date in the user's preferred format ("Fri May 1, 2026").
    header = today.strftime("%a %b ") + str(today.day) + today.strftime(", %Y")
    return f"<b>☀️ {header}</b>\n\n{body}"
