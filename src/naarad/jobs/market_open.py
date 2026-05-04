"""In-process market-open job.

Fires daily at config.schedules.market_open in config.tickers.market_timezone
(default 09:35 America/New_York). Skipped on weekends, when the runtime kill
switch is off, or when the watchlist is empty.

Per-exchange holiday handling: symbols are grouped by exchange (US / TSX),
each exchange's status is looked up via EODHD's holiday calendar, and:
  - CLOSED_HOLIDAY → that exchange's symbols are skipped, a ``📅 X closed
    today`` line is appended.
  - EARLY_CLOSE → quotes still fetched (the *open* isn't affected by an
    early close), no extra header at open time.
  - OPEN → normal fetch + render.
If every exchange in the watchlist is closed, a single combined holiday
message is posted instead of the quote section.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram.ext import Application, ContextTypes

from naarad import db
from naarad.config import Config
from naarad.jobs._common import (
    closed_holiday_lines,
    evaluate_exchange_statuses,
    fetch_quotes_concurrent,
    fmt_pct,
    fmt_price,
    partition_by_exchange,
    split_open_vs_closed,
)
from naarad.runtime import is_tickers_enabled
from naarad.tickers.eodhd import EODHDClient, ExchangeDay, Quote

log = logging.getLogger(__name__)

JOB_NAME = "market-open"


def _format_quote_line(q: Quote) -> str:
    if q.is_empty:
        return f"  • <code>{q.symbol}</code> — data unavailable"
    return (
        f"  • <code>{q.symbol}</code>  "
        f"open {fmt_price(q.open)}  "
        f"prev {fmt_price(q.previous_close)}  "
        f"({fmt_pct(q.change_pct)})"
    )


def _format_open(
    quotes: list[Quote], closed: dict[str, ExchangeDay]
) -> str:
    lines = ["📈 <b>Market open</b>"]
    for q in quotes:
        lines.append(_format_quote_line(q))
    lines.extend(closed_holiday_lines(closed))
    return "\n".join(lines)


def _format_all_closed(closed: dict[str, ExchangeDay]) -> str:
    lines = ["📈 <b>Market open</b>"]
    lines.extend(closed_holiday_lines(closed))
    return "\n".join(lines)


async def run(app: Application) -> None:
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

    groups = partition_by_exchange(symbols)
    if not groups:
        log.warning("market_open: no classifiable symbols in watchlist")
        return

    client: EODHDClient = app.bot_data["eodhd_client"]
    today = now_market.date()
    statuses = await evaluate_exchange_statuses(client, list(groups), today)
    fetchable, closed = split_open_vs_closed(groups, statuses)

    if not fetchable:
        body = _format_all_closed(closed)
    else:
        flat = [s for syms in fetchable.values() for s in syms]
        try:
            quotes = await fetch_quotes_concurrent(client, flat)
        except Exception:
            log.exception("market_open: fetch failed")
            return
        body = _format_open(quotes, closed)

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
    try:
        await run(context.application)
    except Exception:
        log.exception("market_open job crashed")
