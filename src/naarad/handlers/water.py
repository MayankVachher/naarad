"""Handlers for water-confirm events: /water log, the reminder button,
the panel button, and replies.

Confirm paths (reminder button, /water log, reply-to-reminder) do three
things:
1. Apply the confirm via ``scheduler.confirm_drink`` (state mutation +
   reschedule). Returns the new glass count for the day.
2. Edit the prior reminder (if known) to show "✅ Glass #N logged at HH:MM".
3. Reply with the confirm response (which also includes the count).

``/water`` with no args renders a status panel with a single
``[💧 Log glass]`` button. Tapping it confirms a glass and edits the
panel in place with the refreshed status — same "panel mutates under
your tap" pattern as ``/llm``.
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


def _panel_keyboard() -> InlineKeyboardMarkup:
    """Single-button keyboard for the /water status panel."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💧 Log glass", callback_data=PANEL_LOG_CALLBACK)]]
    )

log = logging.getLogger(__name__)


def _confirm_response(config: Config) -> str:
    """Build the post-confirm reply: count + pace badge + next reminder
    time. Derived from the shared post-confirm view; ``confirm_drink``
    has already committed the new glass count, so re-reading state here
    is correct.
    """
    view = compute_water_status(config)
    return messages.confirm_response(
        glasses_today=view.glasses,
        daily_target=view.daily_target,
        status=view.pace_status,
        deficit=view.pace_deficit,
        next_reminder_at=view.next_reminder_at,
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

    # /water (no args) is read-only — status snapshot only. Logging takes
    # the explicit `/water log` form so a quick "where am I at" check
    # can't accidentally reset the chain.
    args = context.args or []
    if not args or args[0].lower() != "log":
        if update.message is not None:
            await update.message.reply_text(
                _status_response(config),
                reply_markup=_panel_keyboard(),
            )
        return

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
        await update.message.reply_text(_confirm_response(config))


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
        # Follow-up reply with the multi-line confirm response (count +
        # pace badge + next reminder), matching /water log and the
        # reply-to-reminder path. Without this, button taps surface only
        # the inline "✅ Glass #N logged at HH:MM" edit and hide pace info.
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=_confirm_response(config),
        )


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
            reply_markup=_panel_keyboard(),
        )
    except Exception:
        log.exception("water panel-log: edit_text failed; sending fresh panel")
        await query.message.reply_text(
            _status_response(config),
            reply_markup=_panel_keyboard(),
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
    await msg.reply_text(_confirm_response(config))
