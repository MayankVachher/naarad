"""Morning fallback + daily brief schedulers.

In-process schedulers running inside the bot:
- Daily brief at config.morning.start_time (06:00) — replaces the cron entry
  for local dev. Production deployments can still use cron via deploy/crontab.txt.
- Daily fallback at config.morning.fallback_time (11:00): if the user hasn't
  tapped [☀️ Start day] yet, send a gentle "starting anyway" message, strip
  the button, and kick off water tracking.

On bot startup, also runs morning-fallback catch-up if we're past today's
fallback time and the day isn't started.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from telegram.ext import Application, ContextTypes

from naarad import db
from naarad.config import Config
from naarad.water import scheduler as water_scheduler

log = logging.getLogger(__name__)

BRIEF_JOB_NAME = "daily-brief"
BRIEF_CATCHUP_NAME = "daily-brief-catchup"
FALLBACK_JOB_NAME = "morning-fallback"
FALLBACK_CATCHUP_NAME = "morning-fallback-catchup"

FALLBACK_MESSAGE = "👋 Quiet morning — starting water tracking anyway."


async def _brief_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the daily brief in a worker thread (subprocess + httpx are blocking)."""
    # Imported lazily so test harnesses don't pull telegram_api in unrelated tests.
    from naarad.jobs.daily_brief import run_brief
    log.info("daily brief job firing")
    try:
        rc = await asyncio.to_thread(run_brief)
        log.info("daily brief job done (rc=%d)", rc)
    except Exception:
        log.exception("daily brief job crashed")


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
    """Schedule the daily brief + fallback jobs (replacing any priors)."""
    config: Config = app.bot_data["config"]
    jq = app.job_queue
    if jq is None:
        log.error("JobQueue not available; install python-telegram-bot[job-queue]")
        return

    # Daily brief at config.morning.start_time.
    brief_t = config.morning.start_time_t.replace(tzinfo=config.tz)
    for job in jq.get_jobs_by_name(BRIEF_JOB_NAME):
        job.schedule_removal()
    jq.run_daily(_brief_callback, time=brief_t, name=BRIEF_JOB_NAME)
    log.info("scheduled daily brief at %s", brief_t.isoformat())

    # Daily morning fallback at config.morning.fallback_time.
    fallback_t = config.morning.fallback_time_t.replace(tzinfo=config.tz)
    for job in jq.get_jobs_by_name(FALLBACK_JOB_NAME):
        job.schedule_removal()
    jq.run_daily(_fallback_callback, time=fallback_t, name=FALLBACK_JOB_NAME)
    log.info("scheduled daily morning fallback at %s", fallback_t.isoformat())

    now = datetime.now(config.tz)
    today = now.date()

    # Brief catch-up: bot started after start_time today and the scheduled
    # brief never fired. Symmetric with the fallback catch-up below — closes
    # the "Pi was off at 06:00, came up at 07:00" silent-degradation case.
    today_brief = datetime.combine(today, config.morning.start_time_t, tzinfo=config.tz)
    # Imported lazily to avoid pulling telegram_api when this module is
    # loaded by tests that don't exercise the brief.
    from naarad.jobs.daily_brief import LAST_BRIEF_SETTING
    last_brief_on = db.get_setting(config.db_path, LAST_BRIEF_SETTING)
    brief_sent_today = last_brief_on == today.isoformat()
    if now >= today_brief and not brief_sent_today:
        log.info("running daily brief catch-up (now %s past %s)", now, today_brief)
        for job in jq.get_jobs_by_name(BRIEF_CATCHUP_NAME):
            job.schedule_removal()
        jq.run_once(
            _brief_callback,
            when=timedelta(seconds=3),
            name=BRIEF_CATCHUP_NAME,
        )

    # Fallback catch-up: bot started after fallback time, day not yet started.
    today_fallback = datetime.combine(today, config.morning.fallback_time_t, tzinfo=config.tz)
    if now >= today_fallback and not db.is_day_started(config.db_path, today):
        log.info("running morning fallback catch-up (now %s past %s)", now, today_fallback)
        for job in jq.get_jobs_by_name(FALLBACK_CATCHUP_NAME):
            job.schedule_removal()
        jq.run_once(
            _fallback_callback,
            when=timedelta(seconds=3),
            name=FALLBACK_CATCHUP_NAME,
        )
