"""/status and /help."""
from __future__ import annotations

from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.runtime import is_llm_enabled
from naarad.water.scheduler import water_config_from
from naarad.water.state import Idle, Reminder, Sleep, WaterState, next_action

HELP_TEXT = (
    "<b>Naarad commands</b>\n"
    "/water — confirm you drank water (resets the chain)\n"
    "/brief — re-run today's morning brief on demand\n"
    "/llm on|off — toggle LLM features at runtime\n"
    "/status — bot health: last drink, day-started, next reminder, LLM\n"
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


def _describe_next_action(action) -> str:
    if isinstance(action, Reminder):
        return f"now (level {action.level})"
    if isinstance(action, Sleep):
        return action.until.strftime("%H:%M %Z")
    if isinstance(action, Idle):
        return "idle until tomorrow"
    return "unknown"


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    if update.message is None:
        return
    config: Config = context.application.bot_data["config"]
    raw = db.get_water_state(config.db_path)
    tickers = db.list_tickers(config.db_path)

    last = raw["last_drink_at"]
    last_str = (
        last.astimezone(config.tz).strftime("%Y-%m-%d %H:%M %Z") if last else "never"
    )

    now = datetime.now(config.tz)
    today = now.date()
    day_started = raw["day_started_on"] == today

    state = WaterState(
        last_drink_at=raw["last_drink_at"],
        last_reminder_at=raw["last_reminder_at"],
        level=raw["level"],
        last_msg_id=raw["last_msg_id"],
        day_started_on=raw["day_started_on"],
    )
    next_str = _describe_next_action(next_action(state, now, water_config_from(config)))

    llm_state = "on" if is_llm_enabled(config, config.db_path) else "off"

    await update.message.reply_text(
        f"<b>Naarad status</b>\n"
        f"Day started: {'yes' if day_started else 'no'}\n"
        f"Next reminder: {next_str}\n"
        f"Last drink: {last_str}\n"
        f"Water level: {raw['level']}\n"
        f"LLM: {llm_state}\n"
        f"Tickers (dormant): {', '.join(tickers) if tickers else '(none)'}\n"
        f"Timezone: {config.timezone}",
        parse_mode="HTML",
    )
