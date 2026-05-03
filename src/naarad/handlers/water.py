"""Handlers for water-confirm events: /water, the inline button, and replies."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.water import messages, scheduler

log = logging.getLogger(__name__)


async def water_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    await scheduler.confirm_drink(context.application)
    if update.message is not None:
        await update.message.reply_text(messages.CONFIRM_RESPONSE)


async def water_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if await reject_unauthorized(update, context):
        return
    await query.answer("💧 Logged")
    await scheduler.confirm_drink(context.application)


async def water_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered when the user replies to *anything*. Confirm only if reply is to
    the most recent water reminder we sent.
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
    await scheduler.confirm_drink(context.application)
    await msg.reply_text(messages.CONFIRM_RESPONSE)
