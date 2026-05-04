"""Shared helpers for market_open and market_close in-process jobs.

Per-exchange grouping + holiday status lookup live here so both jobs share
the same logic. Quote formatting helpers and per-symbol bullet renderers
are reused too.

Final message layout (DEMO 2, locked):

    📈 Market open · Wed Oct 15

    GOOGL
      • Price:  $182.45
      • Prev:   $180.10
      • Chng:   +1.31% 🟢

The header carries a localized date (in market_tz). Each symbol becomes
its own bullet block. Manual spaces after the colons — Telegram doesn't
honour tabs in proportional fonts, and wrapping in <code> would strip the
inline bold/colour from nested formatting.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import date, datetime

from naarad.tickers.eodhd import (
    EODHDClient,
    ExchangeDay,
    ExchangeStatus,
    Quote,
    _classify_symbol,
)

log = logging.getLogger(__name__)


# --- quote fetching ---------------------------------------------------------


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


# --- exchange grouping ------------------------------------------------------


def partition_by_exchange(symbols: list[str]) -> dict[str, list[str]]:
    """Group watchlist symbols by exchange (``US`` or ``TSX``)."""
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
    """Look up holiday status per exchange. Soft-fails to OPEN on errors."""
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
    """Bucket exchanges into OPEN/EarlyClose (will fetch) vs CLOSED_HOLIDAY."""
    fetchable: dict[str, list[str]] = {}
    closed: dict[str, ExchangeDay] = {}
    for ex, syms in groups.items():
        if statuses[ex].status == ExchangeStatus.CLOSED_HOLIDAY:
            closed[ex] = statuses[ex]
        else:
            fetchable[ex] = syms
    return fetchable, closed


# --- value formatting -------------------------------------------------------


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


def chng_dot(change_pct: float | None) -> str:
    """Status dot trailing the change-percent value."""
    if change_pct is None:
        return "⚪"
    if change_pct > 0:
        return "🟢"
    if change_pct < 0:
        return "🔴"
    return "⚪"


# --- DEMO 2 per-symbol bullet rendering -------------------------------------

# Two-space gap between bullet label and value. Manual spacing — proportional
# fonts mean perfect alignment is impossible, but a fixed gap reads cleanly.
_GAP = "  "


def _bullet(label: str, value: str, suffix: str = "") -> str:
    tail = f" {suffix}" if suffix else ""
    return f"  • {label}:{_GAP}{value}{tail}"


def render_open_block(q: Quote) -> list[str]:
    """Per-symbol block for market_open: Price (open) / Prev / Chng %."""
    safe_sym = html.escape(q.symbol)
    if q.is_empty:
        return [
            f"<b>{safe_sym}</b>",
            "  • <i>data unavailable</i>",
        ]
    return [
        f"<b>{safe_sym}</b>",
        _bullet("<b>Price</b>", fmt_price(q.open)),
        _bullet("<b>Prev</b>", fmt_price(q.previous_close)),
        _bullet("<b>Chng</b>", fmt_pct(q.change_pct), chng_dot(q.change_pct)),
    ]


def render_close_block(q: Quote) -> list[str]:
    """Per-symbol block for market_close: Close / Chng / Hi / Lo / Vol."""
    safe_sym = html.escape(q.symbol)
    if q.is_empty:
        return [
            f"<b>{safe_sym}</b>",
            "  • <i>data unavailable</i>",
        ]
    return [
        f"<b>{safe_sym}</b>",
        _bullet("<b>Close</b>", fmt_price(q.close)),
        _bullet("<b>Chng</b>", fmt_pct(q.change_pct), chng_dot(q.change_pct)),
        _bullet("<b>Hi</b>", fmt_price(q.high)),
        _bullet("<b>Lo</b>", fmt_price(q.low)),
        _bullet("<b>Vol</b>", fmt_volume(q.volume)),
    ]


def join_blocks(blocks: list[list[str]]) -> str:
    """Join per-symbol blocks with a blank line between each."""
    return "\n\n".join("\n".join(block) for block in blocks)


def header_with_date(emoji: str, label: str, when: datetime) -> str:
    """``📈 Market open · Wed Oct 15``."""
    # %-d isn't portable on Windows; use lstrip to avoid leading zero.
    day = when.strftime("%d").lstrip("0")
    pretty = when.strftime(f"%a %b {day}")
    return f"{emoji} <b>{label}</b> · {pretty}"


def closed_holiday_lines(closed: dict[str, ExchangeDay]) -> list[str]:
    """One ``📅 EX closed today — Name`` line per closed exchange."""
    out: list[str] = []
    for ex, day in closed.items():
        name = html.escape(day.name or "holiday")
        out.append(f"📅 <b>{ex} closed today</b> — {name}")
    return out


def early_close_lines(statuses: dict[str, ExchangeDay]) -> list[str]:
    """One ``⏰ EX on early close — Name`` line per EarlyClose exchange."""
    out: list[str] = []
    for ex, day in statuses.items():
        if day.status != ExchangeStatus.EARLY_CLOSE:
            continue
        name = html.escape(day.name or "early close")
        out.append(f"⏰ <b>{ex} on early close</b> — {name}")
    return out


def unavailable_message(prefix: str, error: str) -> str:
    return f"⚠️ <b>{prefix}</b>: market data unavailable ({error})"
