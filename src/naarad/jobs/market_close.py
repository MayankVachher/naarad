"""In-process market-close job.

Fires daily at config.schedules.market_close in config.tickers.market_timezone
(default 16:05 America/New_York). Skipped on weekends, when the runtime kill
switch is off, or when the watchlist is empty.

Per-exchange holiday handling: same as market_open, except EarlyClose days
get an extra ``⏰ X on early close`` header line — the 16:05 tick is hours
after the actual ~13:00 early close, so the data is technically stale but
the header makes that explicit.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram.ext import Application, ContextTypes

from naarad import db
from naarad.config import Config
from naarad.jobs._common import (
    closed_holiday_lines,
    early_close_lines,
    evaluate_exchange_statuses,
    fetch_quotes_concurrent,
    header_with_date,
    join_blocks,
    partition_by_exchange,
    render_close_block,
    split_open_vs_closed,
)
from naarad.runtime import is_tickers_enabled
from naarad.tickers.eodhd import EODHDClient, ExchangeDay, Quote

log = logging.getLogger(__name__)

JOB_NAME = "market-close"


def _format_close(
    quotes: list[Quote],
    statuses: dict[str, ExchangeDay],
    closed: dict[str, ExchangeDay],
    when: datetime,
) -> str:
    parts = [header_with_date("📉", "Market close", when)]
    early = early_close_lines(statuses)
    if early:
        parts.extend(early)
    parts.append("")
    parts.append(join_blocks([render_close_block(q) for q in quotes]))
    closed_lines = closed_holiday_lines(closed)
    if closed_lines:
        parts.append("")
        parts.extend(closed_lines)
    return "\n".join(parts)


def _format_all_closed(closed: dict[str, ExchangeDay], when: datetime) -> str:
    lines = [header_with_date("📉", "Market close", when), ""]
    lines.extend(closed_holiday_lines(closed))
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

    groups = partition_by_exchange(symbols)
    if not groups:
        log.warning("market_close: no classifiable symbols in watchlist")
        return

    client: EODHDClient = app.bot_data["eodhd_client"]
    today = now_market.date()
    statuses = await evaluate_exchange_statuses(client, list(groups), today)
    fetchable, closed = split_open_vs_closed(groups, statuses)

    if not fetchable:
        body = _format_all_closed(closed, now_market)
    else:
        flat = [s for syms in fetchable.values() for s in syms]
        try:
            quotes = await fetch_quotes_concurrent(client, flat)
        except Exception:
            log.exception("market_close: fetch failed")
            return
        body = _format_close(quotes, statuses, closed, now_market)

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
