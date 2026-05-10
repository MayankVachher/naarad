"""Daily brief scheduler entry point.

Async-only: invoked from ``morning/scheduler._brief_callback`` inside the
bot's event loop. Builds today's brief, posts it silently with a
[☀️ Start day] button, and persists the message_id so the bot can edit
the button off later.

Idempotent within a day via the ``last_brief_on`` marker — protects
against the catch-up + scheduled jobs both firing on a near-start_time
boot. /brief manual sends bypass this guard but write the marker on
success, so a later catch-up sees "already sent today" and skips.

Fallback policy: scheduled flow falls back to the deterministic plain
renderer (silent send, user always gets *something*). The /brief manual
path uses a different fallback — see ``handlers/brief.py``.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

from naarad import db
from naarad.brief.plain_renderer import safe_render_plain_brief
from naarad.brief.prompt import build_prompt, format_brief_body
from naarad.config import Config
from naarad.handlers.morning import START_DAY_CALLBACK
from naarad.llm import LLMTask, render

log = logging.getLogger(__name__)

BRIEF_TIMEOUT = 600

# Marker that today's brief sent successfully. Read by
# morning.scheduler.kickoff to decide whether to fire a catch-up after a
# late boot, and by run_brief itself to short-circuit if a /brief manual
# already covered today.
LAST_BRIEF_SETTING = "last_brief_on"


def _start_day_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("☀️ Start day", callback_data=START_DAY_CALLBACK)
    ]])


async def run_brief(app: Application) -> int:
    """Build, send, and persist today's brief. Returns 0 on success or
    skip, non-zero on send failure.

    Idempotent within a day: if ``last_brief_on`` already records today
    we skip and return 0. Protects against the catch-up and scheduled
    jobs both firing on a near-start_time boot.
    """
    config: Config = app.bot_data["config"]
    today = datetime.now(config.tz).date()

    last = db.get_setting(config.db_path, LAST_BRIEF_SETTING)
    if last == today.isoformat():
        log.info("daily brief already sent today (last=%s); skipping", last)
        return 0

    body = await render(
        LLMTask(
            prompt_builder=lambda: build_prompt(today, config),
            post_process=lambda raw: format_brief_body(today, raw),
            fallback=lambda: safe_render_plain_brief(today, config),
            timeout=BRIEF_TIMEOUT,
            log_label="daily-brief",
        ),
        config,
    )

    try:
        msg = await app.bot.send_message(
            chat_id=config.telegram.chat_id,
            text=body,
            parse_mode="HTML",
            disable_web_page_preview=True,
            disable_notification=True,           # silent — user wakes ~8:30
            reply_markup=_start_day_keyboard(),
        )
    except Exception:
        log.exception("daily brief send failed")
        return 1

    if msg.message_id is not None:
        try:
            db.update_water_state(
                config.db_path,
                start_button_message_id=msg.message_id,
            )
        except Exception:
            log.exception("failed to persist start_button_message_id")
    try:
        db.set_setting(config.db_path, LAST_BRIEF_SETTING, today.isoformat())
    except Exception:
        log.exception("failed to persist last_brief_on marker")
    return 0
