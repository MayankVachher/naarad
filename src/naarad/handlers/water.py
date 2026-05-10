"""Handlers for water-confirm events: /water, the inline button, and replies.

Each path does three things in order:
1. Apply the confirm via ``scheduler.confirm_drink`` (state mutation +
   reschedule). Returns the new glass count for the day.
2. Edit the prior reminder (if known) to show "✅ Glass #N logged at HH:MM".
3. Reply with the confirm response (which also includes the count).
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.water import messages, scheduler

log = logging.getLogger(__name__)


def _confirm_response(config: Config, glasses_today: int) -> str:
    return messages.confirm_response(
        glasses_today=glasses_today,
        next_interval_minutes=config.water.intervals_minutes[0],
    )


async def _mark_reminder_logged(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    original_text: str | None,
    now: datetime,
    glasses_today: int,
) -> None:
    """Edit the reminder message to show it's been logged. Best-effort;
    never raises. If we have the original text we rewrite the body
    (preserving the original nudge above an italic confirmation line);
    otherwise we just strip the keyboard so it can't be tapped again.
    """
    try:
        if original_text:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=messages.logged_edit_text(original_text, now, glasses_today),
                parse_mode="HTML",
                reply_markup=None,
            )
        else:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
    except Exception:
        log.debug("mark-logged edit failed", exc_info=True)


async def water_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    config: Config = context.application.bot_data["config"]
    now = datetime.now(config.tz)

    # Snapshot last_msg_id BEFORE confirm so we know which reminder to
    # edit; confirm_drink doesn't change last_msg_id but reads atomically.
    state = db.get_water_state(config.db_path)
    last_msg_id = state.get("last_msg_id")

    glasses = await scheduler.confirm_drink(context.application)

    if last_msg_id is not None:
        await _mark_reminder_logged(
            context, config.telegram.chat_id, last_msg_id, None, now, glasses,
        )
    if update.message is not None:
        await update.message.reply_text(_confirm_response(config, glasses))


async def water_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    now = datetime.now(config.tz)

    glasses = await scheduler.confirm_drink(context.application)

    if query.message is not None:
        await _mark_reminder_logged(
            context,
            query.message.chat_id,
            query.message.message_id,
            query.message.text,
            now,
            glasses,
        )


async def water_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered when the user replies to *anything*. Confirm only if
    the reply is to the most recent water reminder we sent.
    """
    if await reject_unauthorized(update, context):
        return
    msg = update.message
    if msg is None or msg.reply_to_message is None:
        return
    config: Config = context.application.bot_data["config"]
    state = db.get_water_state(config.db_path)
    last_msg_id = state.get("last_msg_id")
    if last_msg_id is None or msg.reply_to_message.message_id != last_msg_id:
        return
    now = datetime.now(config.tz)

    glasses = await scheduler.confirm_drink(context.application)

    await _mark_reminder_logged(
        context,
        msg.reply_to_message.chat_id,
        msg.reply_to_message.message_id,
        msg.reply_to_message.text,
        now,
        glasses,
    )
    await msg.reply_text(_confirm_response(config, glasses))
