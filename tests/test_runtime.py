"""Tests for `naarad.runtime.is_llm_enabled` truth table and DB settings helpers."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from naarad import db
from naarad.config import (
    BriefConfig,
    Config,
    EodhdConfig,
    LLMConfig,
    MorningConfig,
    SchedulesConfig,
    TelegramConfig,
    TickersConfig,
    WaterConfig,
)
from naarad.runtime import (
    LLM_FLAG_KEY,
    TICKERS_FLAG_KEY,
    is_llm_enabled,
    is_tickers_enabled,
    set_llm_runtime,
    set_tickers_runtime,
    tickers_off_reason,
)


def make_config(
    tmp_path: Path,
    *,
    llm_enabled: bool = True,
    tickers_enabled: bool = True,
    eodhd_key: str = "x",
) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key=eodhd_key),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        llm=LLMConfig(enabled=llm_enabled),
        tickers=TickersConfig(enabled=tickers_enabled),
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "state.db"
    db.init_db(db_path)
    return db_path


# ---- settings table ----------------------------------------------------------

def test_settings_table_exists_after_init(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        ).fetchall()
    assert rows == [("settings",)]


def test_get_setting_returns_default_when_missing(fresh_db: Path) -> None:
    assert db.get_setting(fresh_db, "missing", default="fallback") == "fallback"
    assert db.get_setting(fresh_db, "missing") is None


def test_set_setting_inserts_then_updates(fresh_db: Path) -> None:
    db.set_setting(fresh_db, "k", "v1")
    assert db.get_setting(fresh_db, "k") == "v1"
    db.set_setting(fresh_db, "k", "v2")
    assert db.get_setting(fresh_db, "k") == "v2"


# ---- is_llm_enabled truth table ---------------------------------------------

def test_default_is_enabled_when_unset(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    assert is_llm_enabled(config, config.db_path) is True


def test_config_floor_overrides_runtime(tmp_path: Path) -> None:
    config = make_config(tmp_path, llm_enabled=False)
    db.init_db(config.db_path)
    set_llm_runtime(config.db_path, enabled=True)
    assert is_llm_enabled(config, config.db_path) is False


def test_runtime_off_disables(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    set_llm_runtime(config.db_path, enabled=False)
    assert is_llm_enabled(config, config.db_path) is False


def test_runtime_toggle_round_trip(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    set_llm_runtime(config.db_path, enabled=False)
    assert is_llm_enabled(config, config.db_path) is False
    set_llm_runtime(config.db_path, enabled=True)
    assert is_llm_enabled(config, config.db_path) is True


def test_runtime_persistence_in_db(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    set_llm_runtime(config.db_path, enabled=False)
    # Simulate restart by re-reading via raw helper.
    assert db.get_setting(config.db_path, LLM_FLAG_KEY) == "0"


def test_db_path_defaults_to_config_when_omitted(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    set_llm_runtime(config.db_path, enabled=False)
    # Call without db_path; helper should fall back to config.db_path.
    assert is_llm_enabled(config) is False


# ---- is_tickers_enabled truth table -----------------------------------------

def test_tickers_default_is_enabled_when_unset(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    assert is_tickers_enabled(config, config.db_path) is True


def test_tickers_config_floor_overrides_runtime(tmp_path: Path) -> None:
    config = make_config(tmp_path, tickers_enabled=False)
    db.init_db(config.db_path)
    set_tickers_runtime(config.db_path, enabled=True)
    assert is_tickers_enabled(config, config.db_path) is False


def test_tickers_runtime_off_disables(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    set_tickers_runtime(config.db_path, enabled=False)
    assert is_tickers_enabled(config, config.db_path) is False


def test_tickers_runtime_toggle_round_trip(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    set_tickers_runtime(config.db_path, enabled=False)
    assert is_tickers_enabled(config, config.db_path) is False
    set_tickers_runtime(config.db_path, enabled=True)
    assert is_tickers_enabled(config, config.db_path) is True


def test_tickers_runtime_persists_in_db(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    set_tickers_runtime(config.db_path, enabled=False)
    assert db.get_setting(config.db_path, TICKERS_FLAG_KEY) == "0"


def test_tickers_db_path_defaults_to_config(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    set_tickers_runtime(config.db_path, enabled=False)
    assert is_tickers_enabled(config) is False


def test_tickers_and_llm_flags_are_independent(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    set_llm_runtime(config.db_path, enabled=False)
    set_tickers_runtime(config.db_path, enabled=True)
    assert is_llm_enabled(config) is False
    assert is_tickers_enabled(config) is True


# ---- no-EODHD-key gate (graceful degradation) -------------------------------

def test_tickers_off_when_eodhd_key_empty(tmp_path: Path) -> None:
    config = make_config(tmp_path, eodhd_key="")
    db.init_db(config.db_path)
    assert is_tickers_enabled(config) is False


def test_tickers_off_when_eodhd_key_whitespace(tmp_path: Path) -> None:
    config = make_config(tmp_path, eodhd_key="   ")
    db.init_db(config.db_path)
    assert is_tickers_enabled(config) is False


def test_tickers_off_when_key_empty_even_if_runtime_on(tmp_path: Path) -> None:
    """Runtime flag can't override a missing key — the API call would 401."""
    config = make_config(tmp_path, eodhd_key="")
    db.init_db(config.db_path)
    set_tickers_runtime(config.db_path, enabled=True)
    assert is_tickers_enabled(config) is False


# ---- tickers_off_reason truth table -----------------------------------------

def test_off_reason_none_when_all_three_layers_on(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    assert tickers_off_reason(config) is None


def test_off_reason_config_takes_precedence(tmp_path: Path) -> None:
    """Config floor wins over missing key + runtime off — fix the deepest issue first."""
    config = make_config(tmp_path, tickers_enabled=False, eodhd_key="")
    db.init_db(config.db_path)
    set_tickers_runtime(config.db_path, enabled=False)
    assert tickers_off_reason(config) == "config"


def test_off_reason_no_key_when_only_key_missing(tmp_path: Path) -> None:
    config = make_config(tmp_path, eodhd_key="")
    db.init_db(config.db_path)
    assert tickers_off_reason(config) == "no_key"


def test_off_reason_no_key_takes_precedence_over_runtime(tmp_path: Path) -> None:
    """Missing key is more permanent than a flipped runtime toggle."""
    config = make_config(tmp_path, eodhd_key="")
    db.init_db(config.db_path)
    set_tickers_runtime(config.db_path, enabled=False)
    assert tickers_off_reason(config) == "no_key"


def test_off_reason_runtime_when_only_runtime_off(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    set_tickers_runtime(config.db_path, enabled=False)
    assert tickers_off_reason(config) == "runtime"
