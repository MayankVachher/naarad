"""SQLite layer.

Two tables:
- tickers: symbols the user wants tracked.
- water_state: single-row state machine for the water reminders.

Datetimes are stored as ISO8601 TEXT (with offset) to avoid the lossy
behavior of sqlite3's built-in TIMESTAMP adapter for aware datetimes.
Dates are stored as ISO TEXT (YYYY-MM-DD).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

SCHEMA_VERSION = 4


def _to_iso_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("datetimes stored in DB must be timezone-aware")
    return value.isoformat()


def _from_iso_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _to_iso_date(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _from_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Explicit transaction on an autocommit connection.

    We run with ``isolation_level=None`` so each statement is normally
    autocommitted. Multi-statement work that must be atomic (the migrations
    in init_db) wraps itself in this manager so a crash mid-init can't
    leave half-applied schema state.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def init_db(db_path: str | Path, seed_tickers: list[str] | None = None) -> None:
    """Create tables if missing, run migrations, seed tickers on first run.

    Each migration step runs inside an explicit transaction so a crash
    mid-init can't leave the DB with tables created but the schema_version
    row missing (which would re-trigger the v0 path on next boot and
    double-seed or double-create).
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cur.fetchone()
        current = row["version"] if row else 0

        if current == 0:
            with _transaction(conn):
                # executescript would auto-commit our BEGIN; emit individual
                # CREATEs instead so the whole init is one atomic unit.
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS tickers ("
                    "  symbol     TEXT PRIMARY KEY,"
                    "  added_at   TEXT NOT NULL"
                    ")"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS water_state ("
                    "  id                       INTEGER PRIMARY KEY CHECK (id = 1),"
                    "  last_drink_at            TEXT,"
                    "  last_reminder_at         TEXT,"
                    "  level                    INTEGER NOT NULL DEFAULT 0,"
                    "  last_msg_id              INTEGER,"
                    "  day_started_on           TEXT,"
                    "  start_button_message_id  INTEGER,"
                    "  chain_started_at         TEXT"
                    ")"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS settings ("
                    "  key   TEXT PRIMARY KEY,"
                    "  value TEXT"
                    ")"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO water_state (id, level) VALUES (1, 0)"
                )
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            current = SCHEMA_VERSION

        if current < 2:
            # v1 -> v2: add day_started_on and start_button_message_id columns.
            with _transaction(conn):
                cols = {
                    r["name"] for r in conn.execute("PRAGMA table_info(water_state)")
                }
                if "day_started_on" not in cols:
                    conn.execute(
                        "ALTER TABLE water_state ADD COLUMN day_started_on TEXT"
                    )
                if "start_button_message_id" not in cols:
                    conn.execute(
                        "ALTER TABLE water_state ADD COLUMN start_button_message_id INTEGER"
                    )
                conn.execute("UPDATE schema_version SET version = ?", (2,))
            current = 2

        if current < 3:
            # v2 -> v3: add settings key-value table for runtime flags.
            with _transaction(conn):
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS settings ("
                    "  key   TEXT PRIMARY KEY,"
                    "  value TEXT"
                    ")"
                )
                conn.execute("UPDATE schema_version SET version = ?", (3,))
            current = 3

        if current < 4:
            # v3 -> v4: add chain_started_at column. Records the timestamp
            # of the most recent start_day call so the first reminder can
            # apply a grace period (default 5 min) instead of firing
            # immediately.
            with _transaction(conn):
                cols = {
                    r["name"] for r in conn.execute("PRAGMA table_info(water_state)")
                }
                if "chain_started_at" not in cols:
                    conn.execute(
                        "ALTER TABLE water_state ADD COLUMN chain_started_at TEXT"
                    )
                conn.execute("UPDATE schema_version SET version = ?", (4,))
            current = 4

        if seed_tickers:
            existing = {r["symbol"] for r in conn.execute("SELECT symbol FROM tickers")}
            if not existing:
                with _transaction(conn):
                    now_iso = datetime.now().astimezone().isoformat()
                    conn.executemany(
                        "INSERT INTO tickers (symbol, added_at) VALUES (?, ?)",
                        [(s.upper(), now_iso) for s in seed_tickers],
                    )


# ---------- Tickers ----------

def list_tickers(db_path: str | Path) -> list[str]:
    with connect(db_path) as conn:
        return [
            r["symbol"]
            for r in conn.execute("SELECT symbol FROM tickers ORDER BY symbol")
        ]


def add_ticker(db_path: str | Path, symbol: str) -> bool:
    """Returns True if added, False if already present."""
    sym = symbol.strip().upper()
    if not sym:
        raise ValueError("empty symbol")
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tickers (symbol, added_at) VALUES (?, ?)",
            (sym, datetime.now().astimezone().isoformat()),
        )
        return cur.rowcount > 0


def remove_ticker(db_path: str | Path, symbol: str) -> bool:
    """Returns True if removed, False if it wasn't tracked."""
    sym = symbol.strip().upper()
    with connect(db_path) as conn:
        cur = conn.execute("DELETE FROM tickers WHERE symbol = ?", (sym,))
        return cur.rowcount > 0


# ---------- Water state ----------

_DT_FIELDS = ("last_drink_at", "last_reminder_at", "chain_started_at")


def get_water_state(db_path: str | Path) -> dict:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_drink_at, last_reminder_at, level, last_msg_id, "
            "       day_started_on, start_button_message_id, chain_started_at "
            "FROM water_state WHERE id = 1"
        ).fetchone()
    if row is None:
        return {
            "last_drink_at": None,
            "last_reminder_at": None,
            "level": 0,
            "last_msg_id": None,
            "day_started_on": None,
            "start_button_message_id": None,
            "chain_started_at": None,
        }
    return {
        "last_drink_at": _from_iso_dt(row["last_drink_at"]),
        "last_reminder_at": _from_iso_dt(row["last_reminder_at"]),
        "level": row["level"],
        "last_msg_id": row["last_msg_id"],
        "day_started_on": _from_iso_date(row["day_started_on"]),
        "start_button_message_id": row["start_button_message_id"],
        "chain_started_at": _from_iso_dt(row["chain_started_at"]),
    }


def update_water_state(db_path: str | Path, **fields) -> None:
    """Update one or more columns of water_state where id=1.

    Accepts python-native types (datetime, date, int, None) and serializes them.
    """
    if not fields:
        return
    allowed = {
        "last_drink_at",
        "last_reminder_at",
        "level",
        "last_msg_id",
        "day_started_on",
        "start_button_message_id",
        "chain_started_at",
    }
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown water_state fields: {bad}")

    serialized: dict[str, object] = {}
    for k, v in fields.items():
        if k in _DT_FIELDS:
            serialized[k] = _to_iso_dt(v)  # type: ignore[arg-type]
        elif k == "day_started_on":
            serialized[k] = _to_iso_date(v)  # type: ignore[arg-type]
        else:
            serialized[k] = v

    set_clause = ", ".join(f"{k} = ?" for k in serialized)
    values = list(serialized.values()) + [1]
    with connect(db_path) as conn:
        conn.execute(f"UPDATE water_state SET {set_clause} WHERE id = ?", values)


def is_day_started(db_path: str | Path, today: date) -> bool:
    """True iff the morning Start has been triggered for `today`."""
    state = get_water_state(db_path)
    return state["day_started_on"] == today


def mark_day_started(db_path: str | Path, today: date) -> None:
    """Mark today as started and reset the water chain state for a fresh day."""
    update_water_state(
        db_path,
        day_started_on=today,
        last_drink_at=None,
        last_reminder_at=None,
        level=0,
    )


# ---------- Settings (key-value) ----------

def get_setting(db_path: str | Path, key: str, default: str | None = None) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(db_path: str | Path, key: str, value: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
