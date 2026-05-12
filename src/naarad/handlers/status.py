"""/status and /help."""
from __future__ import annotations

import html
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.runtime import is_llm_enabled, tickers_off_reason
from naarad.water.messages import pace_status
from naarad.water.scheduler import water_config_from
from naarad.water.state import (
    Idle,
    Reminder,
    Sleep,
    WaterState,
    expected_glasses_now,
    next_action,
)

HELP_TEXT = (
    "<b>Naarad commands</b>\n"
    "/water — confirm you drank water (resets the chain)\n"
    "/brief — re-run today's morning brief on demand\n"
    "/llm on|off — toggle LLM features at runtime\n"
    "/ticker add|remove|list|on|off — manage the watchlist + kill switch\n"
    "/quote SYMBOL — on-demand real-time quote\n"
    "/status — bot health: water, day, LLM, tickers\n"
    "/help — this message\n"
    "\n"
    "<b>Daily flow</b>\n"
    "• 06:00 — silent morning brief drops with a [☀️ Start day] button.\n"
    "• Tap Start (or wait until 11:00 for the auto-fallback) to kick off "
    "the water reminder chain.\n"
    "• Confirm water by tapping the 💧 button on a reminder, replying to "
    "it with anything, or sending /water.\n"
    "\n"
    "<b>Tickers</b>\n"
    "• 09:35 ET market_open + 16:05 ET market_close briefs Mon-Fri.\n"
    "• Holidays per exchange (US / TSX) are recognised; a closed exchange "
    "gets a single '📅 closed today' line and its quotes are skipped.\n"
    "• Symbols: bare for US (GOOGL), '.TO' suffix for TSX (VFV.TO)."
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    if update.message:
        await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


def _describe_next_action(
    action,
    *,
    day_started: bool,
    target_hit: bool,
    past_active_end: bool,
) -> str:
    """Single-line summary of the next-reminder state for /status."""
    if isinstance(action, Reminder):
        return f"now (level {action.level})"
    if isinstance(action, Sleep):
        return action.until.strftime("%H:%M %Z")
    if isinstance(action, Idle):
        if not day_started:
            return "day not started"
        if target_hit:
            return "🎯 target hit — done for today"
        if past_active_end:
            return "🌙 active hours ended"
        return "idle"
    return "unknown"


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    if update.message is None:
        return
    config: Config = context.application.bot_data["config"]
    raw = db.get_water_state(config.db_path)
    tickers = db.list_tickers(config.db_path)

    now = datetime.now(config.tz)
    today = now.date()
    day_started = raw["day_started_on"] == today

    state = WaterState(
        last_drink_at=raw["last_drink_at"],
        last_reminder_at=raw["last_reminder_at"],
        level=raw["level"],
        last_msg_id=raw["last_msg_id"],
        day_started_on=raw["day_started_on"],
        chain_started_at=raw["chain_started_at"],
        glasses_today=raw["glasses_today"],
    )
    wcfg = water_config_from(config)

    target = config.water.daily_target_glasses
    glasses = raw["glasses_today"]
    target_hit = target > 0 and glasses >= target
    active_end_today = datetime.combine(today, wcfg.active_end, tzinfo=config.tz)
    past_active_end = now >= active_end_today
    next_str = _describe_next_action(
        next_action(state, now, wcfg),
        day_started=day_started,
        target_hit=target_hit,
        past_active_end=past_active_end,
    )

    # Pace badge — shares vocabulary with the confirm reply so /status
    # and the post-log message read the same.
    if target > 0 and day_started:
        expected = expected_glasses_now(state, now, wcfg)
        pstatus, deficit = pace_status(glasses, expected, target)
        badge = {
            "target_hit": "🎯 target hit",
            "on_track":   "🟢 on track",
            "at_risk":    "⚠️ at risk",
            "behind":     "🚨 behind",
            "unknown":    "",
        }[pstatus]
        if pstatus == "behind" and deficit > 0:
            unit = "glass" if deficit < 1.5 else "glasses"
            badge = f"🚨 behind by ~{deficit:.1f} {unit}"
        progress = f"<b>{glasses} / {target}</b>"
        glasses_line = f"{progress} — {badge}" if badge else progress
    else:
        glasses_line = f"<b>{glasses}</b>"

    last = raw["last_drink_at"]
    last_str = (
        last.astimezone(config.tz).strftime("%H:%M %Z (%Y-%m-%d)") if last else "never"
    )

    # LLM section: distinguish config-disabled vs runtime-disabled so the
    # user knows which lever flips it back on.
    backend = config.llm.backend
    if not config.llm.enabled:
        llm_line = "<b>off</b> (config)"
    elif is_llm_enabled(config, config.db_path):
        llm_line = f"<b>on</b> ({html.escape(backend)})"
    else:
        llm_line = f"<b>off</b> (runtime) — backend: {html.escape(backend)}"

    reason = tickers_off_reason(config, config.db_path)
    tickers_state = {
        None: "<b>on</b>",
        "config": "<b>off</b> (config)",
        "no_key": "<b>off</b> (no EODHD key)",
        "runtime": "<b>off</b> (runtime)",
    }[reason]
    if tickers:
        watchlist_html = f"<b>{html.escape(', '.join(tickers))}</b>"
    else:
        watchlist_html = "<i>(none)</i>"

    text = (
        "<b>📋 Naarad status</b>\n"
        "\n"
        "<b>💧 Water</b>\n"
        f"• Day started: <b>{'yes' if day_started else 'no'}</b>\n"
        f"• Glasses today: {glasses_line}\n"
        f"• Last drink: <b>{html.escape(last_str)}</b>\n"
        f"• Level: <b>{raw['level']}</b>\n"
        f"• Next reminder: <b>{html.escape(next_str)}</b>\n"
        "\n"
        "<b>🤖 LLM</b>\n"
        f"• {llm_line}\n"
        "\n"
        "<b>📈 Tickers</b>\n"
        f"• Status: {tickers_state}\n"
        f"• Watchlist: {watchlist_html}\n"
        "\n"
        "<b>🌐 System</b>\n"
        f"• Timezone: <b>{html.escape(config.timezone)}</b>"
    )

    await update.message.reply_text(text, parse_mode="HTML")
