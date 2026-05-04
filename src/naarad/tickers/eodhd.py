"""EODHD API client — real-time quotes and per-exchange holiday calendar.

Used by the in-process market_open/market_close jobs and the /quote handler.

Symbols carry their exchange via suffix:
  - bare (e.g. ``GOOGL``) → US (NYSE/NASDAQ)
  - ``.TO`` (e.g. ``VFV.TO``) → TSX (Toronto)

Other suffixes are not supported today; ``_classify_symbol`` raises
``ValueError`` rather than silently treating them as US.

Endpoints used:
  - ``GET /api/real-time/{symbol}`` — current open/high/low/close/prev-close.
  - ``GET /api/v2/exchange-details/{code}`` — yearly holiday calendar with
    ``Official`` / ``EarlyClose`` / ``Bank`` types. Costs 5 quota units per
    call, so results are cached per ``(exchange_code, year)`` on the client
    instance. The cache lives for the bot's uptime; restarts re-fetch.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)

_API_BASE = "https://eodhd.com/api"
_API_BASE_V2 = "https://eodhd.com/api/v2"

# Mapping from our internal exchange label to EODHD's exchange code.
_EXCHANGE_CODES: dict[str, str] = {
    "US": "US",
    "TSX": "TO",
}


def _classify_symbol(symbol: str) -> Literal["US", "TSX"]:
    """Classify a watchlist symbol into an exchange.

    Raises ``ValueError`` if the suffix isn't recognised. We deliberately
    don't fall back to US so that a typo'd ticker is loud at add-time.
    """
    s = symbol.strip().upper()
    if not s:
        raise ValueError("empty symbol")
    if "." not in s:
        return "US"
    suffix = s.rsplit(".", 1)[1]
    if suffix == "TO":
        return "TSX"
    raise ValueError(
        f"unsupported exchange suffix '.{suffix}' on {symbol!r} "
        f"(supported: bare for US, .TO for TSX)"
    )


class ExchangeStatus(StrEnum):
    """Per-day status for a single exchange.

    ``OPEN`` covers normal trading days *and* bank-only holidays where the
    market is still trading (we map non-Official, non-EarlyClose entries to
    OPEN).
    ``CLOSED_HOLIDAY`` is a full-day market closure (EODHD ``Type=Official``).
    ``EARLY_CLOSE`` means the market is open with shortened hours (e.g.
    Christmas Eve, day after Thanksgiving). Jobs should still post on
    EarlyClose days; the 16:05 ET market_close post may be ~3h stale vs
    the actual 13:00 ET early close, which we accept.
    """

    OPEN = "open"
    CLOSED_HOLIDAY = "closed_holiday"
    EARLY_CLOSE = "early_close"


@dataclass(frozen=True)
class ExchangeDay:
    """Result of a per-exchange holiday lookup."""

    status: ExchangeStatus
    name: str | None = None  # holiday name when status != OPEN


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
        # Cache by (exchange_code, year) → mapping of "YYYY-MM-DD" → entry dict.
        self._holiday_cache: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}

    def real_time_quote(self, symbol: str) -> Quote:
        """Single-symbol real-time quote. EODHD requires the exchange suffix
        for non-US exchanges (e.g. 'VFV.TO'); plain symbols default to US.
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

    def get_exchange_status(self, exchange: str, on: date) -> ExchangeDay:
        """Look up the trading status for ``exchange`` on ``on``.

        ``exchange`` is one of our internal labels (``"US"`` or ``"TSX"``).
        Soft-fails to ``ExchangeDay(OPEN)`` on any HTTP/parse error so a
        transient EODHD outage doesn't suppress the morning post.
        """
        code = _EXCHANGE_CODES.get(exchange)
        if code is None:
            raise ValueError(f"unknown exchange label {exchange!r}")
        calendar = self._load_holiday_calendar(code, on.year)
        entry = calendar.get(on.isoformat())
        if not entry:
            return ExchangeDay(status=ExchangeStatus.OPEN)
        entry_type = str(entry.get("Type") or "").strip()
        name = (
            entry.get("HolidayName")
            or entry.get("Holiday")
            or entry.get("Name")
            or entry_type
            or None
        )
        if entry_type == "EarlyClose":
            return ExchangeDay(status=ExchangeStatus.EARLY_CLOSE, name=name)
        if entry_type in ("Official", ""):
            # Treat unlabelled entries as full closures: erring toward "we
            # don't post" is safer than posting stale data on a real holiday.
            return ExchangeDay(status=ExchangeStatus.CLOSED_HOLIDAY, name=name)
        # Bank-only (or any future type we don't recognise) → market is open.
        return ExchangeDay(status=ExchangeStatus.OPEN)

    def _load_holiday_calendar(
        self, code: str, year: int
    ) -> dict[str, dict[str, Any]]:
        """Fetch (or return cached) holiday calendar for ``(code, year)``.

        EODHD returns the full current-year calendar in one call; we cache
        the parsed mapping to avoid burning quota on every job tick.
        """
        cache_key = (code, year)
        if cache_key in self._holiday_cache:
            return self._holiday_cache[cache_key]

        url = f"{_API_BASE_V2}/exchange-details/{code}"
        params = {"api_token": self._api_key, "fmt": "json"}
        try:
            resp = httpx.get(url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            log.warning(
                "could not fetch %s holiday calendar; assuming open", code
            )
            # Cache the empty result so a sustained outage doesn't keep
            # retrying on every quote tick. Bot restart re-attempts.
            self._holiday_cache[cache_key] = {}
            return {}

        raw = data.get("ExchangeHolidays") or {}
        normalised: dict[str, dict[str, Any]] = {}
        # v2 keys are "YYYY-MM-DD"; v1 was numeric strings with "Date" inside.
        # Accept both shapes so a future endpoint version-bump doesn't blow up.
        if isinstance(raw, dict):
            for key, entry in raw.items():
                if not isinstance(entry, dict):
                    continue
                date_str = key if _looks_like_iso_date(key) else entry.get("Date")
                if isinstance(date_str, str):
                    normalised[date_str] = entry
        self._holiday_cache[cache_key] = normalised
        return normalised


def _looks_like_iso_date(s: str) -> bool:
    return len(s) == 10 and s[4] == "-" and s[7] == "-"
