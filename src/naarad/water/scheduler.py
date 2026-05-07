"""Async scheduler glue between the pure water state machine and python-telegram-bot.

The bot keeps exactly one named JobQueue job called "water-loop". Each time the
job fires, it re-reads state from the DB and runs the loop fresh — so even a
stale callback (one fired after a confirm) is just a no-op recompute.

Locking model
-------------
``app.bot_data["water_lock"]`` is an ``asyncio.Lock`` that guards state
transitions only — it is NOT held across the slow Copilot subprocess. The
loop pattern is:

  1. Under lock: read state, decide next action, capture reminder level.
  2. Release lock; render the reminder line (Copilot, ~45s in the worst
     case) without blocking confirm taps.
  3. Re-acquire lock; re-read state and verify the same Reminder is still
     wanted. If the user confirmed during the render, the second pass
     returns Sleep/Idle and the rendered line is discarded.
  4. Send + persist still under the lock.

This removes the previous behaviour where a /water tap or button press
during reminder generation would block on the lock for the duration of
the subprocess.

Phase 7: chain is started by the morning flow (Start tap or 11 AM
fallback) calling start_day(). Until that fires, run_loop is Idle.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import Application, ContextTypes

from naarad import db
from naarad.config import Config
from naarad.runtime import is_llm_enabled
from naarad.water import copilot as water_copilot
from naarad.water import messages
from naarad.water.state import (
    Idle,
    Reminder,
    Sleep,
    WaterConfig,
    WaterState,
    apply_confirm,
    apply_day_started,
    apply_reminder_sent,
    next_action,
)

log = logging.getLogger(__name__)

JOB_NAME = "water-loop"
CONFIRM_CALLBACK = "water:confirm"


def water_config_from(config: Config) -> WaterConfig:
    return WaterConfig(
        active_end=config.water.active_end_time,
        intervals_minutes=tuple(config.water.intervals_minutes),
        tz=config.tz,
    )


def _state_from_db(config: Config) -> WaterState:
    raw = db.get_water_state(config.db_path)
    return WaterState(
        last_drink_at=raw["last_drink_at"],
        last_reminder_at=raw["last_reminder_at"],
        level=raw["level"],
        last_msg_id=raw["last_msg_id"],
        day_started_on=raw["day_started_on"],
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💧 Drank water", callback_data=CONFIRM_CALLBACK)]]
    )


async def _render_reminder_text(config: Config, level: int) -> str:
    """Generate the line for a reminder at this level. May call Copilot —
    deliberately not under any lock so a slow subprocess can't block
    concurrent confirms.
    """
    text = ""
    if is_llm_enabled(config):
        text = await water_copilot.generate_reminder_line(level)
    if not text:
        text = messages.reminder_text(level)
    return text


async def _send_reminder_text(
    app: Application, config: Config, text: str
) -> Message:
    return await app.bot.send_message(
        chat_id=config.telegram.chat_id,
        text=text,
        reply_markup=_confirm_keyboard(),
    )


def _now(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def _cancel_existing_job(app: Application) -> None:
    jq = app.job_queue
    if jq is None:
        return
    for job in jq.get_jobs_by_name(JOB_NAME):
        job.schedule_removal()


# ---------- The loop ----------

async def run_loop(app: Application) -> None:
    """Compute and dispatch actions until the next action is Sleep or Idle.

    Self-locking — callers must NOT hold ``water_lock`` when invoking this.
    The lock is taken per state transition and released around the slow
    Copilot subprocess (see module docstring).
    """
    config: Config = app.bot_data["config"]
    wcfg: WaterConfig = app.bot_data["water_cfg"]
    lock: asyncio.Lock = app.bot_data["water_lock"]

    for _ in range(8):
        # ---- Phase 1: decide ------------------------------------------------
        async with lock:
            state = _state_from_db(config)
            action = next_action(state, _now(config.tz), wcfg)

            if isinstance(action, Idle):
                # Make sure no stale water-loop job is still parked.
                _cancel_existing_job(app)
                return

            if isinstance(action, Sleep):
                _schedule_at(app, action.until)
                return

            # Reminder: capture the level, then drop the lock for the render.
            assert isinstance(action, Reminder)
            level = action.level

        # ---- Phase 2: render (lock released) --------------------------------
        text = await _render_reminder_text(config, level)

        # ---- Phase 3: re-check + send + persist -----------------------------
        async with lock:
            state = _state_from_db(config)
            action = next_action(state, _now(config.tz), wcfg)
            if not isinstance(action, Reminder) or action.level != level:
                # State drifted while we were rendering — either the user
                # confirmed (next_action returns Sleep) or the active window
                # ended (Idle). Discard the rendered text and let the next
                # loop iteration handle whatever the new state wants.
                continue

            try:
                msg = await _send_reminder_text(app, config, text)
            except Exception:
                log.exception("failed to send water reminder")
                # Bump anchor to now to avoid hot-looping on transient failures.
                db.update_water_state(
                    config.db_path,
                    last_reminder_at=_now(config.tz),
                )
                _schedule_at(app, _now(config.tz) + timedelta(minutes=5))
                return
            new_state = apply_reminder_sent(
                state, _now(config.tz), msg.message_id, wcfg
            )
            db.update_water_state(
                config.db_path,
                last_reminder_at=new_state.last_reminder_at,
                level=new_state.level,
                last_msg_id=new_state.last_msg_id,
            )

    log.warning("water loop hit iteration cap; scheduling a retry in 5min")
    async with lock:
        _schedule_at(app, _now(config.tz) + timedelta(minutes=5))


def _schedule_at(app: Application, when: datetime) -> None:
    """Park exactly one water-loop job at `when`. Replaces any existing job."""
    jq = app.job_queue
    if jq is None:
        log.error("JobQueue not available; install python-telegram-bot[job-queue]")
        return
    for job in jq.get_jobs_by_name(JOB_NAME):
        job.schedule_removal()
    jq.run_once(_job_callback, when=when, name=JOB_NAME)
    log.info("water-loop scheduled at %s", when.isoformat())


async def _job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    # run_loop is self-locking; don't wrap it externally.
    await run_loop(context.application)


# ---------- External entry points ----------

async def kickoff(app: Application) -> None:
    """Called once on bot startup to recover scheduling from persisted state.

    If day_started_on != today, this is a no-op (Idle). The morning scheduler
    will trigger start_day later.
    """
    await run_loop(app)


async def start_day(app: Application) -> None:
    """Mark today as started and run the loop, which fires the first reminder.

    Called by the Start button handler and by the 11 AM fallback job. Idempotent
    if today is already started — second call sees day_started_on==today and
    just continues whatever the chain is doing.
    """
    config: Config = app.bot_data["config"]
    lock: asyncio.Lock = app.bot_data["water_lock"]
    async with lock:
        today = _now(config.tz).date()
        state = _state_from_db(config)
        if state.day_started_on != today:
            new_state = apply_day_started(state, today)
            db.update_water_state(
                config.db_path,
                day_started_on=new_state.day_started_on,
                last_drink_at=None,
                last_reminder_at=None,
                level=0,
            )
    # Lock released — run_loop will reacquire per transition.
    await run_loop(app)


async def confirm_drink(app: Application) -> None:
    """Apply a confirm event from a handler (button / cmd / reply) and reschedule.

    Note: a confirm before day_start is silently ignored (no escalation runs).
    """
    config: Config = app.bot_data["config"]
    lock: asyncio.Lock = app.bot_data["water_lock"]
    async with lock:
        state = _state_from_db(config)
        new_state = apply_confirm(state, _now(config.tz))
        db.update_water_state(
            config.db_path,
            last_drink_at=new_state.last_drink_at,
            last_reminder_at=new_state.last_reminder_at,
            level=new_state.level,
        )
    await run_loop(app)
