"""LLM smoke-test prompt + runner.

Used by the first-boot welcome flow and the ``/llm test`` command to
confirm the configured backend actually answers a single prompt — so
LLM auth or backend-name issues are caught immediately instead of
showing up tomorrow morning when the brief silently uses the
deterministic renderer.
"""
from __future__ import annotations

import asyncio
import logging

from naarad.config import Config
from naarad.llm import get_backend, run_llm
from naarad.runtime import get_llm_backend
from naarad.water.prompt import first_nonempty_line

log = logging.getLogger(__name__)

SMOKETEST_PROMPT = """\
You are Naarad, a Telegram bot that delivers Mayank's morning brief, water
reminders, and market quotes. Tone: warm, dry-witted, plain-spoken — never
cheesy. You're slightly self-aware (you know you're a bot).

Write ONE single line confirming you're online and ready. ≤20 words.
Lead with one emoji of your choice.

Output: just the line. No quotes, no preamble, no markdown.
"""

SMOKETEST_TIMEOUT = 30  # seconds


async def run_smoketest(config: Config) -> tuple[bool, str]:
    """Run a single smoke-test prompt against the configured backend.

    Returns ``(True, line)`` on success or ``(False, error_reason)`` on
    any failure (unknown backend, subprocess error, timeout, empty
    output). Never raises.
    """
    try:
        backend = get_backend(get_llm_backend(config))
    except Exception as exc:  # noqa: BLE001
        return False, f"unknown backend: {exc}"

    result = await asyncio.to_thread(
        run_llm, backend, SMOKETEST_PROMPT, SMOKETEST_TIMEOUT, "smoketest",
    )
    if not result.ok:
        return False, result.error_reason or "no output"

    line = first_nonempty_line(result.stdout)
    if not line:
        return False, "empty output"
    return True, line
