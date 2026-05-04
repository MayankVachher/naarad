"""/status and /help."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized

HELP_TEXT = (
    "<b>Naarad commands</b>\n"
    "/water — confirm you drank water (resets the chain)\n"
    "/status — bot health: last drink, day-started, next reminder\n"
    "/help — this message\n"
    "\n"
    "<b>Daily flow</b>\n"
    "• 06:00 — silent morning brief drops with a [☀️ Start day] button.\n"
    "• Tap Start (or wait until 11:00 for the auto-fallback) to kick off "
    "the water reminder chain.\n"
    "• Confirm water by tapping the 💧 button on a reminder, replying to "
    "it with anything, or sending /water.\n"
    "\n"
    "<b>Tickers</b> (currently dormant pending yfinance migration):\n"
    "/ticker add SYMBOL · /ticker remove SYMBOL · /ticker list"
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
