"""Tests for the EODHD client refactor: classify, holiday calendar v2, cache."""
from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from naarad.tickers.eodhd import (
    EODHDClient,
    ExchangeDay,
    ExchangeStatus,
    _classify_symbol,
)

# ---- _classify_symbol --------------------------------------------------------


def test_classify_us_bare_symbol() -> None:
    assert _classify_symbol("GOOGL") == "US"
    assert _classify_symbol("googl") == "US"  # case-insensitive
    assert _classify_symbol("  AAPL  ") == "US"  # whitespace tolerant


def test_classify_tsx_dot_to_suffix() -> None:
    assert _classify_symbol("VFV.TO") == "TSX"
    assert _classify_symbol("vcn.to") == "TSX"


def test_classify_unknown_suffix_raises() -> None:
    # ASX, LSE, etc. are not supported today — fail loud on add.
    with pytest.raises(ValueError, match="unsupported exchange suffix"):
        _classify_symbol("BHP.AX")
    with pytest.raises(ValueError, match="unsupported exchange suffix"):
        _classify_symbol("VOD.L")


def test_classify_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty symbol"):
        _classify_symbol("")
    with pytest.raises(ValueError, match="empty symbol"):
        _classify_symbol("   ")


# ---- get_exchange_status: parsing v2 response -------------------------------

V2_US_FIXTURE: dict[str, Any] = {
    "Name": "USA Stock Exchange",
    "Code": "US",
    "ExchangeHolidays": {
        "2026-01-01": {"Date": "2026-01-01", "HolidayName": "New Year's Day", "Type": "Official"},
        "2026-11-26": {"Date": "2026-11-26", "HolidayName": "Thanksgiving Day", "Type": "Official"},
        "2026-11-27": {"Date": "2026-11-27", "HolidayName": "Day after Thanksgiving", "Type": "EarlyClose"},
        "2026-12-24": {"Date": "2026-12-24", "HolidayName": "Christmas Eve", "Type": "EarlyClose"},
        "2026-12-25": {"Date": "2026-12-25", "HolidayName": "Christmas Day", "Type": "Official"},
        "2026-04-15": {"Date": "2026-04-15", "HolidayName": "Tax Day", "Type": "Bank"},
    },
}


@pytest.fixture
def patched_get(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch httpx.get used by EODHDClient. Track call count + last URL."""
    state: dict[str, Any] = {"calls": 0, "url": None, "params": None, "payload": V2_US_FIXTURE}

    class _Resp:
        def __init__(self, payload: Any, status: int = 200) -> None:
            self._payload = payload
            self.status_code = status

        def raise_for_status(self) -> None:
            if not (200 <= self.status_code < 300):
                raise httpx.HTTPStatusError(
                    f"{self.status_code}", request=httpx.Request("GET", "x"), response=self  # type: ignore[arg-type]
                )

        def json(self) -> Any:
            return self._payload

    def _fake_get(url: str, params: dict | None = None, timeout: float = 0.0) -> _Resp:
        state["calls"] += 1
        state["url"] = url
        state["params"] = params
        return _Resp(state["payload"])

    monkeypatch.setattr("naarad.tickers.eodhd.httpx.get", _fake_get)
    return state


def test_get_exchange_status_open_when_not_in_calendar(patched_get: dict[str, Any]) -> None:
    client = EODHDClient(api_key="k")
    result = client.get_exchange_status("US", date(2026, 6, 15))
    assert result == ExchangeDay(status=ExchangeStatus.OPEN)


def test_get_exchange_status_closed_holiday_for_official(patched_get: dict[str, Any]) -> None:
    client = EODHDClient(api_key="k")
    result = client.get_exchange_status("US", date(2026, 1, 1))
    assert result.status is ExchangeStatus.CLOSED_HOLIDAY
    assert result.name == "New Year's Day"


def test_get_exchange_status_early_close_kept_distinct(patched_get: dict[str, Any]) -> None:
    client = EODHDClient(api_key="k")
    result = client.get_exchange_status("US", date(2026, 11, 27))
    assert result.status is ExchangeStatus.EARLY_CLOSE
    assert result.name == "Day after Thanksgiving"


def test_get_exchange_status_bank_holiday_treated_as_open(patched_get: dict[str, Any]) -> None:
    # "Bank"-typed entries (e.g. Tax Day) don't close US markets.
    client = EODHDClient(api_key="k")
    result = client.get_exchange_status("US", date(2026, 4, 15))
    assert result.status is ExchangeStatus.OPEN


def test_get_exchange_status_uses_v2_endpoint(patched_get: dict[str, Any]) -> None:
    client = EODHDClient(api_key="secret")
    client.get_exchange_status("US", date(2026, 1, 1))
    assert "/api/v2/exchange-details/US" in patched_get["url"]
    assert patched_get["params"] == {"api_token": "secret", "fmt": "json"}


def test_get_exchange_status_caches_by_year(patched_get: dict[str, Any]) -> None:
    client = EODHDClient(api_key="k")
    client.get_exchange_status("US", date(2026, 1, 1))
    client.get_exchange_status("US", date(2026, 6, 15))
    client.get_exchange_status("US", date(2026, 12, 25))
    assert patched_get["calls"] == 1, "second+ same-year lookups must hit the cache"


def test_get_exchange_status_separate_cache_per_exchange(patched_get: dict[str, Any]) -> None:
    client = EODHDClient(api_key="k")
    client.get_exchange_status("US", date(2026, 1, 1))
    client.get_exchange_status("TSX", date(2026, 1, 1))
    assert patched_get["calls"] == 2


def test_get_exchange_status_separate_cache_per_year(patched_get: dict[str, Any]) -> None:
    client = EODHDClient(api_key="k")
    client.get_exchange_status("US", date(2026, 1, 1))
    client.get_exchange_status("US", date(2027, 1, 1))
    assert patched_get["calls"] == 2


def test_get_exchange_status_soft_fails_to_open_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(url: str, params: dict | None = None, timeout: float = 0.0) -> Any:
        raise httpx.ConnectError("network down")

    monkeypatch.setattr("naarad.tickers.eodhd.httpx.get", _boom)
    client = EODHDClient(api_key="k")
    result = client.get_exchange_status("US", date(2026, 1, 1))
    assert result == ExchangeDay(status=ExchangeStatus.OPEN)


def test_get_exchange_status_caches_failures_to_avoid_retry_storms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"calls": 0}

    def _boom(url: str, params: dict | None = None, timeout: float = 0.0) -> Any:
        state["calls"] += 1
        raise httpx.ConnectError("network down")

    monkeypatch.setattr("naarad.tickers.eodhd.httpx.get", _boom)
    client = EODHDClient(api_key="k")
    client.get_exchange_status("US", date(2026, 1, 1))
    client.get_exchange_status("US", date(2026, 6, 15))
    # Even on failure, we cache the empty calendar so the next quote tick
    # doesn't immediately re-fetch.
    assert state["calls"] == 1


def test_get_exchange_status_unknown_label_raises(patched_get: dict[str, Any]) -> None:
    client = EODHDClient(api_key="k")
    with pytest.raises(ValueError, match="unknown exchange label"):
        client.get_exchange_status("LSE", date(2026, 1, 1))


def test_get_exchange_status_handles_v1_legacy_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # Belt-and-suspenders: if EODHD ever reverts to numeric keys with "Date"
    # inside, we shouldn't blow up.
    legacy = {
        "ExchangeHolidays": {
            "0": {"Date": "2026-01-01", "HolidayName": "New Year's Day", "Type": "Official"},
            "1": {"Date": "2026-12-25", "HolidayName": "Christmas Day", "Type": "Official"},
        }
    }

    class _Resp:
        status_code = 200
        def raise_for_status(self) -> None: pass
        def json(self) -> Any: return legacy

    monkeypatch.setattr(
        "naarad.tickers.eodhd.httpx.get",
        lambda url, params=None, timeout=0.0: _Resp(),
    )
    client = EODHDClient(api_key="k")
    result = client.get_exchange_status("US", date(2026, 1, 1))
    assert result.status is ExchangeStatus.CLOSED_HOLIDAY
