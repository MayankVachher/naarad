"""Shared helpers for market_open and market_close cron jobs.

DEPRECATED alongside jobs/market_open.py and jobs/market_close.py. Pending
replacement by yfinance-backed helpers (Phase 10).
"""
from __future__ import annotations

import logging
from datetime import date

from naarad.config import Config
from naarad.tickers.eodhd import EODHDClient, Quote

log = logging.getLogger(__name__)


def fetch_quotes(client: EODHDClient, symbols: list[str]) -> list[Quote]:
    quotes: list[Quote] = []
    for sym in symbols:
        try:
            quotes.append(client.real_time_quote(sym))
        except Exception:
            log.exception("failed to fetch %s", sym)
            quotes.append(
                Quote(
                    symbol=sym,
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
            )
    return quotes


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


def holiday_message(name: str | None, today: date) -> str:
    label = name or "Market holiday"
    return f"📅 <b>Market closed today</b> — {label} ({today.isoformat()})"


def unavailable_message(prefix: str, error: str) -> str:
    return f"⚠️ <b>{prefix}</b>: market data unavailable ({error})"


def check_holiday_or_proceed(client: EODHDClient, config: Config, today: date) -> str | None:
    """Returns a holiday-message string if today is a holiday, else None."""
    is_hol, name = client.is_us_market_holiday(today)
    if is_hol:
        return holiday_message(name, today)
    return None
