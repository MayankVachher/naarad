"""/status and /help."""
from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.runtime import get_llm_backend, is_llm_enabled, tickers_off_reason
from naarad.water.state import Idle, Reminder, Sleep
from naarad.water.status import compute_water_status

log = logging.getLogger(__name__)

STATUS_CALLBACK_PREFIX = "status:"
_CB_REFRESH = "status:refresh"


def _panel_keyboard() -> InlineKeyboardMarkup:
    """Refresh-only keyboard for the /status dashboard. The dashboard is
    read-only, so the single useful action is "recompute and re-render"
    without having to re-type /status.
    """
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Refresh", callback_data=_CB_REFRESH)]]
    )

HELP_TEXT = (
    "<b>Naarad commands</b>\n"
    "/water — water status panel with a [💧 Log glass] button\n"
    "/brief — re-run today's morning brief on demand\n"
    "/llm on|off|test|backend — toggle, smoke-test, or swap backend at runtime\n"
    "/ticker add|remove|list|on|off — manage the watchlist + kill switch\n"
    "/quote SYMBOL — on-demand real-time quote\n"
    "/status — bot health: water, day, LLM, tickers\n"
    "/help — this message\n"
    "\n"
    "<b>Daily flow</b>\n"
    "• 06:00 — silent morning brief drops with a [☀️ Start day] button.\n"
    "• Tap Start (or wait until 11:00 for the auto-fallback) to kick off "
    "the water reminder chain.\n"
    "• Log a glass by tapping 💧 on a reminder, tapping [💧 Log glass] on "
    "the /water panel, replying to a reminder with anything, or sending "
    "/water log.\n"
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


def _format_status(config: Config) -> str:
    """Build the full /status dashboard text. Shared by the command path
    and the Refresh callback so both render the same content.
    """
    tickers = db.list_tickers(config.db_path)
    view = compute_water_status(config)

    next_str = _describe_next_action(
        view.action,
        day_started=view.day_started,
        target_hit=view.target_hit,
        past_active_end=view.past_active_end,
    )

    # Pace badge — shares vocabulary with the confirm reply so /status
    # and the post-log message read the same.
    if view.daily_target > 0 and view.day_started:
        badge = {
            "target_hit": "🎯 target hit",
            "on_track":   "🟢 on track",
            "at_risk":    "⚠️ at risk",
            "behind":     "🚨 behind",
            "unknown":    "",
        }[view.pace_status]
        if view.pace_status == "behind" and view.pace_deficit > 0:
            unit = "glass" if view.pace_deficit < 1.5 else "glasses"
            badge = f"🚨 behind by ~{view.pace_deficit:.1f} {unit}"
        progress = f"<b>{view.glasses} / {view.daily_target}</b>"
        glasses_line = f"{progress} — {badge}" if badge else progress
    else:
        glasses_line = f"<b>{view.glasses}</b>"

    last = view.last_drink_at
    last_str = (
        last.astimezone(config.tz).strftime("%H:%M %Z (%Y-%m-%d)") if last else "never"
    )

    # LLM section: distinguish config-disabled vs runtime-disabled so the
    # user knows which lever flips it back on. The backend shown is the
    # effective one (runtime override wins over `llm.backend`); a small
    # suffix flags an active override.
    effective = get_llm_backend(config, config.db_path)
    overridden = effective != config.llm.backend
    backend_label = html.escape(effective)
    if overridden:
        backend_label += f" <i>(override; config: {html.escape(config.llm.backend)})</i>"
    if not config.llm.enabled:
        llm_line = "<b>off</b> (config)"
    elif is_llm_enabled(config, config.db_path):
        llm_line = f"<b>on</b> ({backend_label})"
    else:
        llm_line = f"<b>off</b> (runtime) — backend: {backend_label}"

    reason = tickers_off_reason(config, config.db_path)
    tickers_state = {
        None: "<b>on</b>",
        "config": "<b>off</b> (config)",
        "no_key": "<b>off</b> (no EODHD key)",
        "runtime": "<b>off</b> (runtime)",
    }[reason]
    if tickers:
        bullets = "\n".join(f"  ◦ <b>{html.escape(t)}</b>" for t in tickers)
        watchlist_block = f"• Watchlist:\n{bullets}"
    else:
        watchlist_block = "• Watchlist: <i>(none)</i>"

    return (
        "<b>📋 Naarad status</b>\n"
        "\n"
        "<b>💧 Water</b>\n"
        f"• Day started: <b>{'yes' if view.day_started else 'no'}</b>\n"
        f"• Glasses today: {glasses_line}\n"
        f"• Last drink: <b>{html.escape(last_str)}</b>\n"
        f"• Level: <b>{view.level}</b>\n"
        f"• Next reminder: <b>{html.escape(next_str)}</b>\n"
        "\n"
        "<b>🤖 LLM</b>\n"
        f"• {llm_line}\n"
        "\n"
        "<b>📈 Tickers</b>\n"
        f"• Status: {tickers_state}\n"
        f"{watchlist_block}\n"
        "\n"
        "<b>🌐 System</b>\n"
        f"• Timezone: <b>{html.escape(config.timezone)}</b>"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    if update.message is None:
        return
    config: Config = context.application.bot_data["config"]
    await update.message.reply_text(
        _format_status(config),
        parse_mode="HTML",
        reply_markup=_panel_keyboard(),
    )


async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single entrypoint for status:* callbacks. Right now that's just
    the [🔄 Refresh] button — recomputes the dashboard and edits the
    message in place.
    """
    if await reject_unauthorized(update, context):
        return
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return
    config: Config = context.application.bot_data["config"]

    await query.answer()

    if query.data == _CB_REFRESH:
        try:
            await query.message.edit_text(
                _format_status(config),
                parse_mode="HTML",
                reply_markup=_panel_keyboard(),
            )
        except Exception:
            # "Message is not modified" when nothing has changed since the
            # last render — harmless; the panel is already up to date.
            log.debug("status refresh: edit_text no-op", exc_info=True)
        return

    log.warning("status_callback: unrecognised callback_data %r", query.data)
