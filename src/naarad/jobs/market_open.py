"""In-process market-open job.

Fires daily at config.schedules.market_open in config.tickers.market_timezone
(default 09:35 America/New_York). Skipped on weekends, when the runtime kill
switch is off, or when the watchlist is empty. Holiday/EarlyClose handling
is layered in by jobs.scheduler in a follow-up.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram.ext import Application, ContextTypes

from naarad import db
from naarad.config import Config
from naarad.jobs._common import fetch_quotes_concurrent, fmt_pct, fmt_price
from naarad.runtime import is_tickers_enabled
from naarad.tickers.eodhd import EODHDClient, Quote

log = logging.getLogger(__name__)

JOB_NAME = "market-open"


def _format_open(quotes: list[Quote]) -> str:
    lines = ["📈 <b>Market open</b>"]
    for q in quotes:
        if q.is_empty:
            lines.append(f"  • <code>{q.symbol}</code> — data unavailable")
            continue
        lines.append(
            f"  • <code>{q.symbol}</code>  "
            f"open {fmt_price(q.open)}  "
            f"prev {fmt_price(q.previous_close)}  "
            f"({fmt_pct(q.change_pct)})"
        )
    return "\n".join(lines)


async def run(app: Application) -> None:
    """Single market-open run. Safe to invoke directly (tests, manual trigger)."""
    config: Config = app.bot_data["config"]

    now_market = datetime.now(config.tickers.market_tz)
    if now_market.weekday() >= 5:
        log.info("market_open: weekend, skipping")
        return

    if not is_tickers_enabled(config):
        log.info("market_open: disabled, skipping")
        return

    symbols = db.list_tickers(config.db_path)
    if not symbols:
        log.info("market_open: empty watchlist, skipping")
        return

    client: EODHDClient = app.bot_data["eodhd_client"]
    try:
        quotes = await fetch_quotes_concurrent(client, symbols)
    except Exception:
        log.exception("market_open: fetch failed")
        return

    body = _format_open(quotes)
    try:
        await app.bot.send_message(
            chat_id=config.telegram.chat_id,
            text=body,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("market_open: send failed")


async def callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue entry point — wraps run() with try/except so a single bad
    day doesn't kill the scheduler.
    """
    try:
        await run(context.application)
    except Exception:
        log.exception("market_open job crashed")
