"""Shared helpers for market_open and market_close in-process jobs.

The formatters (`fmt_price`, `fmt_pct`, `fmt_volume`) and the parallel
quote-fetch helper (`fetch_quotes_concurrent`) are reused across both
schedules. Per-exchange holiday logic and the final message format
live in the scheduler/job callbacks themselves so this module stays
free of bot-context coupling.
"""
from __future__ import annotations

import asyncio
import logging

from naarad.tickers.eodhd import EODHDClient, Quote

log = logging.getLogger(__name__)


def _empty_quote(symbol: str) -> Quote:
    return Quote(
        symbol=symbol,
        timestamp=None,
        open=None,
        high=None,
        low=None,
        close=None,
        previous_close=None,
        change=None,
        change_pct=None,
        volume=None,
    )


def fetch_quotes(client: EODHDClient, symbols: list[str]) -> list[Quote]:
    """Synchronous serial fetch. Used by tests and any callers outside the bot
    event loop. On any per-symbol failure, returns an empty Quote.
    """
    quotes: list[Quote] = []
    for sym in symbols:
        try:
            quotes.append(client.real_time_quote(sym))
        except Exception:
            log.exception("failed to fetch %s", sym)
            quotes.append(_empty_quote(sym))
    return quotes


async def fetch_quotes_concurrent(
    client: EODHDClient, symbols: list[str]
) -> list[Quote]:
    """Async parallel fetch via asyncio.to_thread.

    EODHDClient.real_time_quote is sync (httpx blocking), so each call is
    offloaded to a worker thread. Watchlists are small (4-10 tickers) so
    we don't bother with a semaphore.
    """
    async def _one(sym: str) -> Quote:
        try:
            return await asyncio.to_thread(client.real_time_quote, sym)
        except Exception:
            log.exception("failed to fetch %s", sym)
            return _empty_quote(sym)

    return await asyncio.gather(*(_one(s) for s in symbols))


def fmt_price(v: float | None) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def fmt_volume(v: int | None) -> str:
    if v is None:
        return "—"
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return str(v)


def unavailable_message(prefix: str, error: str) -> str:
    return f"⚠️ <b>{prefix}</b>: market data unavailable ({error})"
