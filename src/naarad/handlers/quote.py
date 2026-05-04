"""``/quote SYMBOL`` — on-demand single-quote in DEMO 2 format.

Behaviour:
  - ``/quote`` (no args) → USAGE.
  - ``/quote GOOGL`` → fetch + render in the same per-symbol bullet block
    market_open uses (Price / Prev / Chng).
  - Symbol must classify (US bare or .TO suffix); otherwise a ValueError
    message goes back to the user instead of a stale or silently-mistyped
    quote.
  - If the symbol's exchange is closed *today* (weekend or recognised
    holiday), a ``📅 X closed today`` line is prepended to the reply so
    the user knows the price is the last trade, not a live one.
    EarlyClose days don't get a note (the market did trade today).
    The holiday lookup soft-fails to "no note" — we'd rather show the
    last-trade quote without context than refuse to answer.
  - Honors the runtime kill switch + config floor: refuses with the same
    'tickers are off' messaging used by /ticker on|off so the user has one
    mental model.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import date, datetime

from telegram import Update
from telegram.ext import ContextTypes

from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.jobs._common import header_with_date, render_open_block
from naarad.runtime import is_tickers_enabled, tickers_off_reason
from naarad.tickers.eodhd import EODHDClient, ExchangeStatus, _classify_symbol

log = logging.getLogger(__name__)

USAGE = "Usage: /quote SYMBOL  (e.g. /quote GOOGL or /quote VFV.TO)"


def _refusal(config: Config) -> str:
    reason = tickers_off_reason(config, config.db_path)
    if reason == "config":
        return (
            "Tickers are disabled at the config level "
            "(config.tickers.enabled=false). Edit config.json + restart."
        )
    if reason == "no_key":
        return (
            "Tickers are off — no EODHD API key configured. "
            "Add config.eodhd.api_key and restart."
        )
    return (
        "Tickers are off at runtime. Use <code>/ticker on</code> to re-enable."
    )


async def _exchange_closed_note(
    client: EODHDClient, exchange: str, on: date
) -> str | None:
    """Return a ``📅 X closed today`` line if the exchange isn't trading on
    ``on``, else None. Soft-fails to None on any lookup error — better to
    show the last-trade quote without context than to refuse the command.
    """
    if on.weekday() >= 5:
        return f"📅 <b>{exchange} closed today</b> — weekend"
    try:
        status = await asyncio.to_thread(client.get_exchange_status, exchange, on)
    except Exception:
        log.exception(
            "/quote: get_exchange_status failed for %s; skipping note", exchange
        )
        return None
    if status.status == ExchangeStatus.CLOSED_HOLIDAY:
        name = html.escape(status.name or "holiday")
        return f"📅 <b>{exchange} closed today</b> — {name}"
    return None


async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    msg = update.message
    if msg is None:
        return
    config: Config = context.application.bot_data["config"]

    args = context.args or []
    if not args:
        await msg.reply_text(USAGE)
        return

    if not is_tickers_enabled(config):
        await msg.reply_text(_refusal(config), parse_mode="HTML")
        return

    raw = args[0]
    symbol = raw.strip().upper()
    try:
        exchange = _classify_symbol(symbol)
    except ValueError as exc:
        await msg.reply_text(f"⚠️ {exc}")
        return

    client: EODHDClient = context.application.bot_data["eodhd_client"]
    now_market = datetime.now(config.tickers.market_tz)

    closed_note = await _exchange_closed_note(client, exchange, now_market.date())

    try:
        quote = await asyncio.to_thread(client.real_time_quote, symbol)
    except Exception:
        log.exception("/quote: fetch failed for %s", symbol)
        await msg.reply_text(
            f"⚠️ Couldn't fetch <b>{symbol}</b> right now.",
            parse_mode="HTML",
        )
        return

    body_parts = [header_with_date("📊", "Quote", now_market), ""]
    if closed_note:
        body_parts.extend([closed_note, ""])
    body_parts.extend(render_open_block(quote))
    await msg.reply_text(
        "\n".join(body_parts),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
