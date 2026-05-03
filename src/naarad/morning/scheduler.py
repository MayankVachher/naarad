"""Morning fallback scheduler.

Daily job at config.morning.fallback_time: if the user hasn't tapped
[☀️ Start day] yet, send a gentle "starting anyway" message, strip the
button from the brief message, and kick off water tracking.

On bot startup, also runs catch-up: if we're past today's fallback time and
the day isn't started, run the fallback once after a short delay.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from telegram.ext import Application, ContextTypes

from naarad import db
from naarad.config import Config
from naarad.water import scheduler as water_scheduler

log = logging.getLogger(__name__)

FALLBACK_JOB_NAME = "morning-fallback"
FALLBACK_CATCHUP_NAME = "morning-fallback-catchup"

FALLBACK_MESSAGE = "👋 Quiet morning — starting water tracking anyway."


async def _fallback_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    app = context.application
    config: Config = app.bot_data["config"]
    today = datetime.now(config.tz).date()

    if db.is_day_started(config.db_path, today):
        log.info("morning fallback: day already started, no-op")
        return

    # Strip the [☀️ Start day] button from the brief message if we know its id.
    state = db.get_water_state(config.db_path)
    msg_id = state.get("start_button_message_id")
    if msg_id:
        try:
            await app.bot.edit_message_reply_markup(
                chat_id=config.telegram.chat_id,
                message_id=msg_id,
                reply_markup=None,
            )
        except Exception:
            log.debug("edit_message_reply_markup (fallback) failed", exc_info=True)

    try:
        await app.bot.send_message(
            chat_id=config.telegram.chat_id,
            text=FALLBACK_MESSAGE,
        )
    except Exception:
        log.exception("fallback message send failed")

    await water_scheduler.start_day(app)


async def kickoff(app: Application) -> None:
    """Schedule the daily fallback, plus a catch-up if we're past it today."""
    config: Config = app.bot_data["config"]
    jq = app.job_queue
    if jq is None:
        log.error("JobQueue not available; install python-telegram-bot[job-queue]")
        return

    fallback_t = config.morning.fallback_time_t
    fallback_t_aware = fallback_t.replace(tzinfo=config.tz)

    # Replace any prior fallback job (idempotent on restart).
    for job in jq.get_jobs_by_name(FALLBACK_JOB_NAME):
        job.schedule_removal()
    jq.run_daily(_fallback_callback, time=fallback_t_aware, name=FALLBACK_JOB_NAME)
    log.info("scheduled daily morning fallback at %s", fallback_t_aware.isoformat())

    # Catch-up: bot started after fallback time, day not yet started.
    now = datetime.now(config.tz)
    today = now.date()
    today_fallback = datetime.combine(today, fallback_t, tzinfo=config.tz)
    if now >= today_fallback and not db.is_day_started(config.db_path, today):
        log.info("running morning fallback catch-up (now %s past %s)", now, today_fallback)
        for job in jq.get_jobs_by_name(FALLBACK_CATCHUP_NAME):
            job.schedule_removal()
        jq.run_once(
            _fallback_callback,
            when=timedelta(seconds=3),
            name=FALLBACK_CATCHUP_NAME,
        )
