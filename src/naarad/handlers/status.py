"""/status and /help."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized

HELP_TEXT = (
    "<b>Naarad commands</b>\n"
    "/water — confirm you drank water\n"
    "/ticker add SYMBOL — track a ticker\n"
    "/ticker remove SYMBOL — stop tracking\n"
    "/ticker list — list tracked tickers\n"
    "/status — bot health\n"
    "/help — this message\n"
    "\n"
    "You can also tap the 💧 button on a reminder, or reply to a reminder."
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    if update.message:
        await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    if update.message is None:
        return
    config: Config = context.application.bot_data["config"]
    state = db.get_water_state(config.db_path)
    tickers = db.list_tickers(config.db_path)
    last = state["last_drink_at"]
    last_str = last.astimezone(config.tz).strftime("%Y-%m-%d %H:%M %Z") if last else "never"
    await update.message.reply_text(
        f"<b>Naarad status</b>\n"
        f"Last drink: {last_str}\n"
        f"Water level: {state['level']}\n"
        f"Tickers: {', '.join(tickers) if tickers else '(none)'}\n"
        f"Timezone: {config.timezone}",
        parse_mode="HTML",
    )
