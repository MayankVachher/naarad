"""Shared helpers for market_open and market_close in-process jobs.

Per-exchange grouping + holiday status lookup live here so both jobs share
the same logic. Quote formatters are reused too. The final pretty-format
helpers live in the job modules themselves.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from naarad.tickers.eodhd import (
    EODHDClient,
    ExchangeDay,
    ExchangeStatus,
    Quote,
    _classify_symbol,
)

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
    """Synchronous serial fetch. Used by tests and any callers outside the
    bot event loop. On any per-symbol failure, returns an empty Quote.
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


def partition_by_exchange(symbols: list[str]) -> dict[str, list[str]]:
    """Group watchlist symbols by exchange (``US`` or ``TSX``).

    Symbols whose suffix doesn't classify are logged + dropped rather than
    propagated, so a stale or typo'd watchlist row can't crash the job.
    Order within each exchange preserves the watchlist order so the user
    sees a stable layout day to day.
    """
    groups: dict[str, list[str]] = {}
    for sym in symbols:
        try:
            ex = _classify_symbol(sym)
        except ValueError:
            log.warning("skipping unclassifiable symbol %r", sym)
            continue
        groups.setdefault(ex, []).append(sym)
    return groups


async def evaluate_exchange_statuses(
    client: EODHDClient, exchanges: list[str], on: date
) -> dict[str, ExchangeDay]:
    """Look up holiday status per exchange. Soft-fails to OPEN on errors.

    The client already caches per (exchange, year), so repeated calls within
    the same year are cheap; we still hop to a worker thread because the
    first call of the year does network I/O.
    """
    statuses: dict[str, ExchangeDay] = {}
    for ex in exchanges:
        try:
            statuses[ex] = await asyncio.to_thread(
                client.get_exchange_status, ex, on
            )
        except Exception:
            log.exception("get_exchange_status failed for %s; assuming OPEN", ex)
            statuses[ex] = ExchangeDay(status=ExchangeStatus.OPEN)
    return statuses


def split_open_vs_closed(
    groups: dict[str, list[str]], statuses: dict[str, ExchangeDay]
) -> tuple[dict[str, list[str]], dict[str, ExchangeDay]]:
    """Bucket exchanges into OPEN/EarlyClose (will fetch) vs CLOSED_HOLIDAY
    (will not fetch — just announce).
    """
    fetchable: dict[str, list[str]] = {}
    closed: dict[str, ExchangeDay] = {}
    for ex, syms in groups.items():
        if statuses[ex].status == ExchangeStatus.CLOSED_HOLIDAY:
            closed[ex] = statuses[ex]
        else:
            fetchable[ex] = syms
    return fetchable, closed


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


def closed_holiday_lines(closed: dict[str, ExchangeDay]) -> list[str]:
    """One ``📅 EX closed today — Name`` line per closed exchange."""
    out: list[str] = []
    for ex, day in closed.items():
        name = day.name or "holiday"
        out.append(f"📅 <b>{ex} closed today</b> — {name}")
    return out


def early_close_lines(statuses: dict[str, ExchangeDay]) -> list[str]:
    """One ``⏰ EX on early close — Name`` line per EarlyClose exchange."""
    out: list[str] = []
    for ex, day in statuses.items():
        if day.status != ExchangeStatus.EARLY_CLOSE:
            continue
        name = day.name or "early close"
        out.append(f"⏰ <b>{ex} on early close</b> — {name}")
    return out


def unavailable_message(prefix: str, error: str) -> str:
    return f"⚠️ <b>{prefix}</b>: market data unavailable ({error})"
