"""EODHD API client — real-time quotes and US exchange holiday calendar.

DEPRECATED: this module is no longer wired into the live bot. The market_open
/ market_close cron jobs are commented out in deploy/crontab.txt, and the
plan is to replace EODHD with yfinance (see Phase 10 in plan.md). Kept as
reference for the upcoming migration. Don't add new callers here.

Docs: https://eodhd.com/financial-apis/
We use:
- /api/real-time/{symbol} for current open/high/low/close/prev-close/change.
- /api/exchange-details/US to detect market holidays.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

_API_BASE = "https://eodhd.com/api"


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp: datetime | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None  # current/last price during the session
    previous_close: float | None
    change: float | None
    change_pct: float | None
    volume: int | None

    @property
    def is_empty(self) -> bool:
        return self.close is None and self.open is None


def _to_float(v: Any) -> float | None:
    if v is None or v == "NA":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


class EODHDClient:
    def __init__(self, api_key: str, timeout: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def real_time_quote(self, symbol: str) -> Quote:
        """Single-symbol real-time quote. EODHD requires the exchange suffix for
        non-US exchanges (e.g. 'BHP.AX'); plain symbols default to US.
        """
        url = f"{_API_BASE}/real-time/{symbol}"
        params = {"api_token": self._api_key, "fmt": "json"}
        resp = httpx.get(url, params=params, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        ts = _to_int(data.get("timestamp"))
        return Quote(
            symbol=str(data.get("code") or symbol).upper(),
            timestamp=datetime.fromtimestamp(ts) if ts else None,
            open=_to_float(data.get("open")),
            high=_to_float(data.get("high")),
            low=_to_float(data.get("low")),
            close=_to_float(data.get("close")),
            previous_close=_to_float(data.get("previousClose")),
            change=_to_float(data.get("change")),
            change_pct=_to_float(data.get("change_p")),
            volume=_to_int(data.get("volume")),
        )

    def is_us_market_holiday(self, on: date) -> tuple[bool, str | None]:
        """Returns (is_holiday, name). Soft-fails to (False, None) on error."""
        url = f"{_API_BASE}/exchange-details/US"
        params = {"api_token": self._api_key, "fmt": "json"}
        try:
            resp = httpx.get(url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            log.warning("could not fetch holiday calendar; assuming open")
            return False, None
        holidays = data.get("ExchangeHolidays") or {}
        target = on.isoformat()
        for entry in holidays.values():
            if not isinstance(entry, dict):
                continue
            if entry.get("Date") == target:
                return True, entry.get("HolidayName") or "Holiday"
        return False, None
