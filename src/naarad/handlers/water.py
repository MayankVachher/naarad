"""Handlers for water-confirm events: /water log, the reminder button,
and the panel button.

Confirm paths apply the confirm via ``scheduler.confirm_drink`` (state
mutation + reschedule, returns the new glass count) and then surface
the post-confirm view (count + pace badge + next reminder) to the user.

Surface choice:
- Reminder button tap → overwrites the reminder message in place.
- ``/water log`` → strips the prior reminder's keyboard (so it can't be
  double-tapped) and replies with the confirm response.
- ``/water`` panel button → edits the panel in place with refreshed
  status, same "panel mutates under your tap" pattern as ``/llm``.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.water import messages, scheduler
from naarad.water.status import compute_water_status

PANEL_LOG_CALLBACK = "water:panel_log"
PANEL_PAUSE_CALLBACK = "water:panel_pause"
PANEL_RESUME_CALLBACK = "water:panel_resume"

log = logging.getLogger(__name__)


def _panel_keyboard(*, paused: bool) -> InlineKeyboardMarkup:
    """Two-button keyboard for the /water status panel.

    Always shows [💧 Log glass]; the second slot toggles between
    [⏸ Pause] and [▶️ Resume] based on the current state so the user
    only sees the action available right now.
    """
    pause_button = (
        InlineKeyboardButton("▶️ Resume", callback_data=PANEL_RESUME_CALLBACK)
        if paused
        else InlineKeyboardButton("⏸ Pause", callback_data=PANEL_PAUSE_CALLBACK)
    )
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("💧 Log glass", callback_data=PANEL_LOG_CALLBACK),
            pause_button,
        ]]
    )


def _confirm_response(config: Config, logged_at: datetime | None = None) -> str:
    """Build the post-confirm reply: count (optionally suffixed with the
    log time) + pace badge + next reminder time. Derived from the shared
    post-confirm view; ``confirm_drink`` has already committed the new
    glass count, so re-reading state here is correct.
    """
    view = compute_water_status(config)
    return messages.confirm_response(
        glasses_today=view.glasses,
        daily_target=view.daily_target,
        status=view.pace_status,
        deficit=view.pace_deficit,
        next_reminder_at=view.next_reminder_at,
        logged_at=logged_at,
        paused=view.paused,
    )


def _status_response(config: Config) -> str:
    """Build the read-only /water reply: same shape as the confirm reply
    but without mutating state and with a hint about /water log.
    """
    view = compute_water_status(config)
    return messages.status_response(
        glasses_today=view.glasses,
        daily_target=view.daily_target,
        status=view.pace_status,
        deficit=view.pace_deficit,
        next_reminder_at=view.next_reminder_at,
        day_started=view.day_started,
        paused=view.paused,
    )


def _panel_for(config: Config) -> InlineKeyboardMarkup:
    """Convenience: build the panel keyboard with the current pause state.

    Used by every code path that posts or edits the /water panel so the
    pause/resume button always matches what the message says.
    """
    return _panel_keyboard(paused=compute_water_status(config).paused)


async def water_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    config: Config = context.application.bot_data["config"]

    # /water (no args) is read-only — status snapshot only. Logging takes
    # the explicit `/water log` form so a quick "where am I at" check
    # can't accidentally reset the chain. `pause` / `resume` are explicit
    # subcommands too — same logic: never silently mutate.
    args = context.args or []
    verb = args[0].lower() if args else ""

    if verb == "pause":
        changed = await scheduler.pause_chain(context.application)
        if update.message is not None:
            note = "" if changed else " (already paused)"
            await update.message.reply_text(
                _status_response(config) + (f"\n{note.strip()}" if note else ""),
                reply_markup=_panel_for(config),
            )
        return

    if verb == "resume":
        changed = await scheduler.resume_chain(context.application)
        if update.message is not None:
            note = "" if changed else " (already running)"
            await update.message.reply_text(
                _status_response(config) + (f"\n{note.strip()}" if note else ""),
                reply_markup=_panel_for(config),
            )
        return

    if verb != "log":
        # Status snapshot. Treat any unknown verb as status too so a
        # typo doesn't leak as "unknown command" — the panel + status
        # text show what the bot understood.
        if update.message is not None:
            await update.message.reply_text(
                _status_response(config),
                reply_markup=_panel_for(config),
            )
        return

    now = datetime.now(config.tz)

    # Snapshot last_msg_id BEFORE confirm so we know which reminder to
    # strip; confirm_drink doesn't change last_msg_id but reads atomically.
    state = db.get_water_state(config.db_path)
    last_msg_id = state.get("last_msg_id")

    await scheduler.confirm_drink(context.application)

    if last_msg_id is not None:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=config.telegram.chat_id,
                message_id=last_msg_id,
                reply_markup=None,
            )
        except Exception:
            log.debug("strip reminder keyboard failed", exc_info=True)
    if update.message is not None:
        await update.message.reply_text(_confirm_response(config, logged_at=now))


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

    await scheduler.confirm_drink(context.application)

    if query.message is not None:
        # Overwrite the reminder with the full confirm response (count +
        # pace badge + next reminder) in place — no follow-up message,
        # so the reminder doesn't double up as two log entries in chat.
        try:
            await context.bot.edit_message_text(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=_confirm_response(config, logged_at=now),
                reply_markup=None,
            )
        except Exception:
            log.debug("water reminder-button edit failed", exc_info=True)


async def water_panel_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for the [💧 Log glass] button on the /water status panel.

    Unlike ``water_button`` (the reminder-message button), this path
    edits the panel itself in place with the refreshed status instead
    of editing a reminder + sending a follow-up reply — the panel is
    the surface the user is looking at, so feedback belongs there.
    """
    query = update.callback_query
    if query is None or query.message is None:
        return
    if await reject_unauthorized(update, context):
        return
    try:
        await query.answer()
    except Exception:
        log.debug("query.answer failed (likely stale tap)", exc_info=True)
    config: Config = context.application.bot_data["config"]

    await scheduler.confirm_drink(context.application)

    try:
        await query.message.edit_text(
            _status_response(config),
            reply_markup=_panel_for(config),
        )
    except Exception:
        log.exception("water panel-log: edit_text failed; sending fresh panel")
        await query.message.reply_text(
            _status_response(config),
            reply_markup=_panel_for(config),
        )


async def water_panel_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for the [⏸ Pause] panel button. Flips the pause flag,
    cancels any parked reminder job, and re-renders the panel with the
    new state (button label flips to [▶️ Resume]).
    """
    query = update.callback_query
    if query is None or query.message is None:
        return
    if await reject_unauthorized(update, context):
        return
    try:
        await query.answer()
    except Exception:
        log.debug("query.answer failed (likely stale tap)", exc_info=True)
    config: Config = context.application.bot_data["config"]

    await scheduler.pause_chain(context.application)
    await _refresh_panel(query, config)


async def water_panel_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback for the [▶️ Resume] panel button. Clears the pause flag
    and re-runs the scheduler loop (which may fire a reminder right
    away if the anchor is overdue).
    """
    query = update.callback_query
    if query is None or query.message is None:
        return
    if await reject_unauthorized(update, context):
        return
    try:
        await query.answer()
    except Exception:
        log.debug("query.answer failed (likely stale tap)", exc_info=True)
    config: Config = context.application.bot_data["config"]

    await scheduler.resume_chain(context.application)
    await _refresh_panel(query, config)


async def _refresh_panel(query, config: Config) -> None:
    """Edit the /water panel message in place with the latest status +
    matching pause/resume button. Shared by the pause and resume
    callback handlers.
    """
    try:
        await query.message.edit_text(
            _status_response(config),
            reply_markup=_panel_for(config),
        )
    except Exception:
        log.exception("water panel refresh: edit_text failed; sending fresh panel")
        await query.message.reply_text(
            _status_response(config),
            reply_markup=_panel_for(config),
        )


