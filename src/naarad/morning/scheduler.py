"""Morning fallback + daily brief schedulers.

In-process schedulers running inside the bot:

- Daily brief at ``config.morning.start_time`` (06:00).
- Daily fallback at ``config.morning.fallback_time`` (11:00): if the
  user hasn't tapped [☀️ Start day] yet, send a gentle "starting
  anyway" message, strip the button, and kick off water tracking.

Boot-time recovery:

- **First boot** (``welcome_sent`` setting unset): send the welcome
  message and skip the brief catch-up. The welcome IS the recovery —
  one informational message instead of a stale catch-up brief plus a
  redundant fallback.
- **Subsequent boots**: brief catch-up fires if we missed today's
  06:00 AND we're still before ``water.active_end`` (no point firing
  a stale brief at bedtime). The fallback catch-up was removed
  entirely — the user can tap the brief catch-up's [Start day]
  button when ready, and the daily 11:00 fallback still handles
  normal "user didn't tap by 11:00" runs.
"""
from __future__ import annotations

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

FALLBACK_MESSAGE = "👋 Quiet morning — starting water tracking anyway."


async def _brief_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the daily brief on the event loop.

    The brief itself runs the LLM subprocess via ``asyncio.to_thread``
    inside ``llm.render``, so the loop stays responsive while waiting on
    the 30-90s call.
    """
    from naarad.jobs.daily_brief import run_brief
    log.info("daily brief job firing")
    try:
        rc = await run_brief(context.application)
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
    """Schedule the daily jobs and run any boot-time recovery."""
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

    # First-boot welcome takes precedence over any catch-up: a fresh
    # install at 23:30 should be one welcome message, not a stale brief
    # plus a redundant fallback.
    from naarad.handlers.welcome import maybe_send_welcome
    if await maybe_send_welcome(app):
        log.info("welcome sent (first boot); skipping brief catch-up")
        return

    now = datetime.now(config.tz)
    today = now.date()

    # No catch-up past active_end: a brief at bedtime is too stale to
    # be useful, and tomorrow's 06:00 brief covers the next day. Avoids
    # the "boot at 23:30, get a stale brief tonight then a fresh one
    # 6 hours later" double-fire.
    active_end = datetime.combine(
        today, config.water.active_end_time, tzinfo=config.tz
    )
    if now >= active_end:
        log.info("kickoff: past active_end (%s); skipping catch-up", active_end)
        return

    # Brief catch-up: bot started after start_time today and the
    # scheduled brief never fired (typical case: Pi rebooted in the
    # morning). The user taps [Start day] on this brief when ready;
    # there's no separate fallback catch-up — the daily 11:00 fallback
    # handles normal "didn't tap by 11" recovery during the day.
    today_brief = datetime.combine(
        today, config.morning.start_time_t, tzinfo=config.tz
    )
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
