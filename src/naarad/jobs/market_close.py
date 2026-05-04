"""In-process market-close job.

Fires daily at config.schedules.market_close in config.tickers.market_timezone
(default 16:05 America/New_York). Skipped on weekends, when the runtime kill
switch is off, or when the watchlist is empty.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram.ext import Application, ContextTypes

from naarad import db
from naarad.config import Config
from naarad.jobs._common import (
    fetch_quotes_concurrent,
    fmt_pct,
    fmt_price,
    fmt_volume,
)
from naarad.runtime import is_tickers_enabled
from naarad.tickers.eodhd import EODHDClient, Quote

log = logging.getLogger(__name__)

JOB_NAME = "market-close"


def _format_close(quotes: list[Quote]) -> str:
    lines = ["📉 <b>Market close</b>"]
    for q in quotes:
        if q.is_empty:
            lines.append(f"  • <code>{q.symbol}</code> — data unavailable")
            continue
        lines.append(
            f"  • <code>{q.symbol}</code>  "
            f"close {fmt_price(q.close)}  "
            f"({fmt_pct(q.change_pct)})  "
            f"hi {fmt_price(q.high)}  "
            f"lo {fmt_price(q.low)}  "
            f"vol {fmt_volume(q.volume)}"
        )
    return "\n".join(lines)


async def run(app: Application) -> None:
    config: Config = app.bot_data["config"]

    now_market = datetime.now(config.tickers.market_tz)
    if now_market.weekday() >= 5:
        log.info("market_close: weekend, skipping")
        return

    if not is_tickers_enabled(config):
        log.info("market_close: disabled, skipping")
        return

    symbols = db.list_tickers(config.db_path)
    if not symbols:
        log.info("market_close: empty watchlist, skipping")
        return

    client: EODHDClient = app.bot_data["eodhd_client"]
    try:
        quotes = await fetch_quotes_concurrent(client, symbols)
    except Exception:
        log.exception("market_close: fetch failed")
        return

    body = _format_close(quotes)
    try:
        await app.bot.send_message(
            chat_id=config.telegram.chat_id,
            text=body,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("market_close: send failed")


async def callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await run(context.application)
    except Exception:
        log.exception("market_close job crashed")
