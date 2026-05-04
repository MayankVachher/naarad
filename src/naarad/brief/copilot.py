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

from naarad.brief.plain_renderer import render_plain_brief
from naarad.brief.prompt import build_prompt
from naarad.brief.sanitizer import sanitize_html
from naarad.config import Config
from naarad.copilot_runner import run_copilot
from naarad.runtime import is_llm_enabled

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 600  # seconds; copilot can take a while


def get_daily_brief(today: date, config: Config, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Generate today's brief by invoking `copilot -p <prompt>` non-interactively.

    Returns the brief body (already formatted for Telegram HTML parse mode).
    On failure (or when LLM is disabled), returns the plain non-LLM brief.
    Never raises.
    """
    if not is_llm_enabled(config):
        log.info("LLM disabled; using plain brief renderer")
        return _render_plain_brief_safe(today, config)

    prompt = build_prompt(today, config)
    result = run_copilot(prompt, timeout=timeout, log_label="daily-brief")
    if not result.ok:
        log.warning("daily brief LLM call failed (%s); falling back to plain renderer", result.error_reason)
        return _render_plain_brief_safe(today, config)

    body = sanitize_html(result.stdout)

    # Header line at top — date in the user's preferred format ("Fri May 1, 2026").
    header = today.strftime("%a %b ") + str(today.day) + today.strftime(", %Y")
    return f"<b>☀️ {header}</b>\n\n{body}"


def _render_plain_brief_safe(today: date, config: Config) -> str:
    """Run the plain renderer, with one last try/except so a crash here can't
    take down the morning post. The renderer already handles source-fetch
    failures internally — this catches anything truly unexpected.
    """
    try:
        return render_plain_brief(today, config)
    except Exception:
        log.exception("plain renderer crashed; emitting bare placeholder")
        header = today.strftime("%a %b ") + str(today.day) + today.strftime(", %Y")
        return (
            f"<b>☀️ {header}</b>\n\n"
            "(Brief unavailable today — both the LLM and the plain renderer failed. "
            "See logs.)"
        )
