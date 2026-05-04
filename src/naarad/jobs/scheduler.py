"""In-process scheduler for the daily market_open + market_close jobs.

Runs daily at config.schedules.market_open / market_close in the
config.tickers.market_timezone (NOT config.timezone — markets fire on
Eastern time, not the user's local TZ). The job callbacks themselves do
the weekday + kill-switch + watchlist gating.
"""
from __future__ import annotations

import logging
from datetime import time as dtime

from telegram.ext import Application

from naarad.config import Config
from naarad.jobs import market_close, market_open

log = logging.getLogger(__name__)


def _parse_hhmm(value: str) -> dtime:
    h, m = value.split(":", 1)
    return dtime(int(h), int(m))


async def kickoff(app: Application) -> None:
    """Schedule market_open and market_close (replacing any priors)."""
    config: Config = app.bot_data["config"]
    jq = app.job_queue
    if jq is None:
        log.error("JobQueue not available; install python-telegram-bot[job-queue]")
        return

    market_tz = config.tickers.market_tz

    open_t = _parse_hhmm(config.schedules.market_open).replace(tzinfo=market_tz)
    for job in jq.get_jobs_by_name(market_open.JOB_NAME):
        job.schedule_removal()
    jq.run_daily(market_open.callback, time=open_t, name=market_open.JOB_NAME)
    log.info("scheduled market_open at %s %s", config.schedules.market_open, market_tz.key)

    close_t = _parse_hhmm(config.schedules.market_close).replace(tzinfo=market_tz)
    for job in jq.get_jobs_by_name(market_close.JOB_NAME):
        job.schedule_removal()
    jq.run_daily(market_close.callback, time=close_t, name=market_close.JOB_NAME)
    log.info(
        "scheduled market_close at %s %s",
        config.schedules.market_close,
        market_tz.key,
    )
