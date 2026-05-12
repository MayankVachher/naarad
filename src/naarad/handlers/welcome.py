"""First-boot welcome flow.

On the very first ``morning/scheduler.kickoff`` after a fresh
``state.db`` (no ``welcome_sent`` setting), send a single welcome
message with config echo + [👋 Start day] button. Then asynchronously
run an LLM smoke test and edit the welcome message to fill in the
``LLM check`` line with ✓/✗.

One message. Instant feedback (the message arrives before the LLM
call completes) plus delayed verification of the LLM (via the edit).
Subsequent boots skip the welcome — the marker doesn't reset.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.jobs.daily_brief import LAST_BRIEF_SETTING
from naarad.llm.smoketest import run_smoketest
from naarad.runtime import is_llm_enabled
from naarad.water import scheduler as water_scheduler

log = logging.getLogger(__name__)

WELCOME_SENT_SETTING = "welcome_sent"
WELCOME_BUTTON_CALLBACK = "welcome:start"

_LLM_LINE_PENDING = "LLM check: ⏳ pending…"
_LLM_LINE_DISABLED = "LLM check: <i>(disabled in config)</i>"


def _format_welcome(config: Config, llm_line: str) -> str:
    intervals = " → ".join(f"{m}m" for m in config.water.intervals_minutes)
    tickers = ", ".join(config.tickers_default) or "(none configured)"
    return (
        "👋 <b>Hello, I'm Naarad.</b>\n\n"
        f"Running on <b>{html.escape(config.timezone)}</b>. "
        f"Brief at <b>{html.escape(config.morning.start_time)}</b>. "
        f"Water {html.escape(intervals)} (active until "
        f"<b>{html.escape(config.water.active_end)}</b>). "
        f"LLM: <b>{html.escape(config.llm.backend)}</b>. "
        f"Tickers: <b>{html.escape(tickers)}</b>.\n\n"
        f"{llm_line}\n\n"
        "Tap below to start tracking now, or wait for tomorrow's "
        f"{html.escape(config.morning.start_time)} brief."
    )


def _start_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👋 Start day", callback_data=WELCOME_BUTTON_CALLBACK)
    ]])


async def maybe_send_welcome(app: Application) -> bool:
    """If the welcome hasn't been sent, send it. Returns True iff sent.

    On success, schedules a background smoke-test edit when LLM is
    enabled. Marker is set only after a successful send so a Telegram
    outage retries on the next boot.
    """
    config: Config = app.bot_data["config"]
    if db.get_setting(config.db_path, WELCOME_SENT_SETTING):
        return False

    llm_on = is_llm_enabled(config)
    initial_line = _LLM_LINE_PENDING if llm_on else _LLM_LINE_DISABLED
    body = _format_welcome(config, initial_line)

    try:
        msg = await app.bot.send_message(
            chat_id=config.telegram.chat_id,
            text=body,
            parse_mode="HTML",
            disable_notification=True,
            reply_markup=_start_button(),
        )
    except Exception:
        log.exception("welcome: send failed; will retry on next boot")
        return False

    try:
        db.set_setting(config.db_path, WELCOME_SENT_SETTING, "1")
    except Exception:
        log.exception("welcome: failed to persist welcome_sent marker")

    # Also claim today's brief slot: the welcome IS today's intro, so a
    # brief catch-up later in the same boot (or on a follow-up systemd
    # start after the install-time smoke test) would just clutter the
    # chat with a second [Start day] button. Setting last_brief_on tells
    # morning.scheduler.kickoff "today's brief already happened, skip the
    # catch-up." Tomorrow's scheduled brief still fires normally because
    # last_brief_on will be yesterday's date by then.
    today = datetime.now(config.tz).date()
    try:
        db.set_setting(config.db_path, LAST_BRIEF_SETTING, today.isoformat())
    except Exception:
        log.exception("welcome: failed to persist last_brief_on marker")

    if llm_on:
        # Fire-and-forget the smoke-test edit. The task wraps its own
        # exception handling so a crash here can't take the loop down.
        asyncio.create_task(_run_smoketest_and_edit(app, msg.message_id))
    return True


async def _run_smoketest_and_edit(app: Application, message_id: int) -> None:
    config: Config = app.bot_data["config"]
    try:
        ok, output = await run_smoketest(config)
    except Exception as exc:  # noqa: BLE001
        log.exception("welcome: smoke test crashed")
        ok, output = False, f"{type(exc).__name__}: {exc}"

    if ok:
        line = f"LLM check: ✓ <i>{html.escape(output)}</i>"
    else:
        line = (
            f"LLM check: ✗ {html.escape(output)} "
            "<i>— falling back to deterministic mode</i>"
        )

    # If the user already tapped Start during the smoke test, don't
    # re-attach the button on the edit.
    today = datetime.now(config.tz).date()
    keyboard = (
        None if db.is_day_started(config.db_path, today) else _start_button()
    )

    try:
        await app.bot.edit_message_text(
            chat_id=config.telegram.chat_id,
            message_id=message_id,
            text=_format_welcome(config, line),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception:
        log.exception("welcome: failed to edit with smoke-test result")


async def welcome_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the welcome message's [👋 Start day] button.

    Strips the button (one-shot) and starts the day. Deliberately does
    not send the morning brief's "Hope you slept well!" greeting — the
    welcome message itself is the greeting, and at first install that
    line is wrong half the time anyway.
    """
    query = update.callback_query
    if query is None:
        return
    if await reject_unauthorized(update, context):
        return

    try:
        await query.answer()
    except Exception:
        log.debug("welcome: query.answer failed (likely stale tap)", exc_info=True)

    config: Config = context.application.bot_data["config"]
    today = datetime.now(config.tz).date()

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        log.debug("welcome: button-strip failed", exc_info=True)

    if not db.is_day_started(config.db_path, today):
        await water_scheduler.start_day(context.application)
