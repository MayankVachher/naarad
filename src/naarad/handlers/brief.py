"""/brief — manual trigger for today's brief.

Same prompt + post-process pipeline as the 06:00 scheduled job, minus
the [Start day] button. Use it to re-fire after a fallback or to
iterate on the prompt.

Fallback is the deterministic plain renderer — identical to the
scheduled flow. LLM unavailability is a logged event, not a user-
visible failure: the plain renderer uses the same source data and
produces a fully-formed brief.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.brief.plain_renderer import safe_render_plain_brief
from naarad.brief.prompt import build_prompt, format_brief_body
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.jobs.daily_brief import LAST_BRIEF_SETTING
from naarad.llm import LLMTask, render

log = logging.getLogger(__name__)

BRIEF_TIMEOUT = 600  # seconds; LLMs can take a while


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
            fallback=lambda: safe_render_plain_brief(today, config),
            timeout=BRIEF_TIMEOUT,
            log_label="brief-cmd",
        ),
        config,
    )

    # Replace the placeholder with the real thing so the chat stays tidy.
    try:
        await ack.edit_text(body, parse_mode="HTML")
    except Exception:
        log.exception("/brief edit_text failed; sending as a new message")
        await update.message.reply_text(body, parse_mode="HTML")

    _record_brief_sent(config, today.isoformat())
