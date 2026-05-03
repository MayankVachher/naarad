"""Authorization helper: drop messages from any chat besides the configured one."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from naarad.config import Config


def is_authorized(update: Update, config: Config) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.id == config.telegram.chat_id


async def reject_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return False if the update should be processed; True if we rejected it.

    Always answers callback queries (so the client doesn't spin), but silently
    drops plain messages.
    """
    config: Config = context.application.bot_data["config"]
    if is_authorized(update, config):
        return False
    if update.callback_query is not None:
        await update.callback_query.answer()
    return True
