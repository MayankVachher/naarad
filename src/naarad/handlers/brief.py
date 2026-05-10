"""/brief — manual trigger for today's brief.

Runs the same prompt + post-process pipeline as the 06:00 scheduled job,
but without the [Start day] button. Use it to iterate on the prompt or
to re-fire after a fallback.

Fallback policy is deliberately different from the scheduled flow:
when /brief fails the user is *expecting* output and is sitting at the
chat — show them an explicit "❌ generation failed" so they can decide
what to do, instead of silently demoting to the deterministic plain
renderer (which is the right choice at 06:00 when there's nobody
watching). The scheduled job's fallback lives in
``jobs/daily_brief.run_brief``.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.brief.prompt import build_prompt, format_brief_body
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.jobs.daily_brief import LAST_BRIEF_SETTING
from naarad.llm import LLMTask, render

log = logging.getLogger(__name__)

BRIEF_TIMEOUT = 600  # seconds; LLMs can take a while

# Sentinel returned by the /brief LLMTask fallback. The handler checks
# for it and shows an explicit error to the user instead of editing in
# placeholder text.
_FAILURE_SENTINEL = "__brief_failed__"


def _record_brief_sent(config: Config, today_iso: str) -> None:
    """Mark today as having had a successful brief send. Best-effort —
    logged-and-swallowed because the user-visible brief already succeeded.

    /brief is otherwise side-effect free, but this single setting is
    intentional: it lets the morning catch-up job see "a brief went out
    today" and skip the scheduled redundant send.
    """
    try:
        db.set_setting(config.db_path, LAST_BRIEF_SETTING, today_iso)
    except Exception:
        log.exception("failed to persist last_brief_on from /brief")


async def brief_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    if update.message is None:
        return

    config: Config = context.application.bot_data["config"]
    today = datetime.now(config.tz).date()

    ack = await update.message.reply_text(
        "🔄 Generating today's brief — this can take a minute…",
    )

    body = await render(
        LLMTask(
            prompt_builder=lambda: build_prompt(today, config),
            post_process=lambda raw: format_brief_body(today, raw),
            fallback=lambda: _FAILURE_SENTINEL,
            timeout=BRIEF_TIMEOUT,
            log_label="brief-cmd",
        ),
        config,
    )

    if body == _FAILURE_SENTINEL:
        await ack.edit_text(
            "❌ Brief generation failed (LLM unavailable or timed out). "
            "Try /brief again, or check the logs."
        )
        return

    # Replace the placeholder with the real thing so the chat stays tidy.
    try:
        await ack.edit_text(body, parse_mode="HTML")
    except Exception:
        log.exception("/brief edit_text failed; sending as a new message")
        await update.message.reply_text(body, parse_mode="HTML")

    _record_brief_sent(config, today.isoformat())
