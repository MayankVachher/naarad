"""/brief — manual trigger for the daily brief.

Runs the same prompt + sanitizer pipeline as the 06:00 scheduled job, but
without the [Start day] button or any state mutation. Use it to iterate on
the prompt template, or to re-fire a brief that fell back this morning.

Copilot can take 30–90 seconds, so we acknowledge immediately and run the
subprocess in a worker thread to keep the event loop responsive.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from naarad.brief.copilot import get_daily_brief
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized

log = logging.getLogger(__name__)


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

    try:
        body = await asyncio.to_thread(get_daily_brief, today, config)
    except Exception:
        log.exception("/brief generation crashed")
        await ack.edit_text("❌ Brief generation crashed — see logs.")
        return

    # Replace the placeholder with the real thing so the chat stays tidy.
    try:
        await ack.edit_text(body, parse_mode="HTML")
    except Exception:
        log.exception("/brief edit_text failed; sending as a new message")
        await update.message.reply_text(body, parse_mode="HTML")
