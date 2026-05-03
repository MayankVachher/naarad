"""Handler for the [☀️ Start day] button on the morning brief.

When tapped:
  1. ack the callback (Telegram demands this within ~10s),
  2. remove the button from the brief message (so it's a one-shot),
  3. send the "Hope you slept well!" greeting,
  4. mark the day as started and fire the first water reminder.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.water import scheduler as water_scheduler

log = logging.getLogger(__name__)

START_DAY_CALLBACK = "morning:start"

GREETING = "Hope you slept well! ☀️"


async def start_day_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if await reject_unauthorized(update, context):
        return

    try:
        await query.answer()
    except Exception:
        log.debug("query.answer failed (likely stale tap)", exc_info=True)
    config: Config = context.application.bot_data["config"]
    today = datetime.now(config.tz).date()

    # If already started, the button is just stale — remove and bail quietly.
    if db.is_day_started(config.db_path, today):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            log.debug("stale-button cleanup failed", exc_info=True)
        return

    # 1) Strip the button so it can't be tapped twice.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        log.debug("edit_message_reply_markup failed", exc_info=True)

    # 2) Greet.
    try:
        await context.bot.send_message(
            chat_id=config.telegram.chat_id,
            text=GREETING,
        )
    except Exception:
        log.exception("failed to send morning greeting")

    # 3) Mark the day started + fire first water reminder.
    await water_scheduler.start_day(context.application)
