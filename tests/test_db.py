"""DB layer tests: timestamp round-trip, ticker CRUD, water state."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from naarad import db

TZ = ZoneInfo("America/Toronto")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    db.init_db(p)
    return p


def test_water_state_roundtrip_preserves_timezone(db_path: Path) -> None:
    drink = datetime(2026, 5, 2, 10, 0, tzinfo=TZ)
    reminder = datetime(2026, 5, 2, 12, 0, tzinfo=TZ)
    db.update_water_state(
        db_path,
        last_drink_at=drink,
        last_reminder_at=reminder,
        day_started_on=date(2026, 5, 2),
        start_button_message_id=12345,
        level=2,
        last_msg_id=42,
    )
    state = db.get_water_state(db_path)
    assert state["last_drink_at"] == drink
    assert state["last_drink_at"].tzinfo is not None
    assert state["last_reminder_at"] == reminder
    assert state["day_started_on"] == date(2026, 5, 2)
    assert state["start_button_message_id"] == 12345
    assert state["level"] == 2
    assert state["last_msg_id"] == 42


def test_naive_datetime_rejected_at_db_boundary(db_path: Path) -> None:
    with pytest.raises(ValueError):
        db.update_water_state(db_path, last_drink_at=datetime(2026, 5, 2, 10, 0))


def test_water_state_starts_with_defaults(db_path: Path) -> None:
    state = db.get_water_state(db_path)
    assert state["last_drink_at"] is None
    assert state["last_reminder_at"] is None
    assert state["level"] == 0
    assert state["last_msg_id"] is None
    assert state["day_started_on"] is None
    assert state["start_button_message_id"] is None


def test_is_day_started_and_mark(db_path: Path) -> None:
    today = date(2026, 5, 2)
    assert db.is_day_started(db_path, today) is False

    db.mark_day_started(db_path, today)
    assert db.is_day_started(db_path, today) is True
    # Tomorrow is not started.
    assert db.is_day_started(db_path, date(2026, 5, 3)) is False


def test_mark_day_started_resets_chain(db_path: Path) -> None:
    """A new day's start should clear yesterday's anchors and reset level."""
    db.update_water_state(
        db_path,
        last_drink_at=datetime(2026, 5, 1, 18, 0, tzinfo=TZ),
        last_reminder_at=datetime(2026, 5, 1, 20, 0, tzinfo=TZ),
        level=3,
    )
    db.mark_day_started(db_path, date(2026, 5, 2))
    state = db.get_water_state(db_path)
    assert state["last_drink_at"] is None
    assert state["last_reminder_at"] is None
    assert state["level"] == 0
    assert state["day_started_on"] == date(2026, 5, 2)


def test_seed_tickers_only_on_first_run(tmp_path: Path) -> None:
    p = tmp_path / "seed.db"
    db.init_db(p, seed_tickers=["spy", "QQQ"])
    assert db.list_tickers(p) == ["QQQ", "SPY"]
    db.remove_ticker(p, "QQQ")
    db.init_db(p, seed_tickers=["AAPL"])  # should NOT re-seed
    assert db.list_tickers(p) == ["SPY"]


def test_add_remove_ticker_idempotent(db_path: Path) -> None:
    assert db.add_ticker(db_path, "aapl") is True
    assert db.add_ticker(db_path, "AAPL") is False  # already there
    assert db.remove_ticker(db_path, "aapl") is True
    assert db.remove_ticker(db_path, "AAPL") is False  # already gone


def test_unknown_field_rejected(db_path: Path) -> None:
    with pytest.raises(ValueError):
        db.update_water_state(db_path, bogus="value")
