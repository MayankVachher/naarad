"""Runtime flag state.

`is_llm_enabled` and `is_tickers_enabled` each consult two layers:

1. Compile-time floor in `Config` (`config.llm.enabled` /
   `config.tickers.enabled`). False means the feature is permanently
   disabled for this deployment; the matching runtime toggle is inert.
2. SQLite `settings` row (`settings.llm_enabled` / `settings.tickers_enabled`).
   Default '1' (on) unless a runtime command has flipped it. Survives bot
   restart.

Both must be on for the feature to fire. Either being off routes consumers
to their non-feature fallbacks (LLM → plain renderer / hardcoded water
tones; tickers → silent skip + /ticker on|off no-op).
"""
from __future__ import annotations

from pathlib import Path

from naarad import db
from naarad.config import Config

LLM_FLAG_KEY = "llm_enabled"
TICKERS_FLAG_KEY = "tickers_enabled"


def is_llm_enabled(config: Config, db_path: str | Path | None = None) -> bool:
    """Return True iff both the config floor and the DB runtime flag allow LLM."""
    if not config.llm.enabled:
        return False
    path = db_path if db_path is not None else config.db_path
    raw = db.get_setting(path, LLM_FLAG_KEY, "1")
    return raw == "1"


def set_llm_runtime(db_path: str | Path, enabled: bool) -> None:
    """Persist the runtime override. Caller is responsible for checking the
    config floor first — flipping the runtime flag on while config has it off
    is a no-op effect-wise.
    """
    db.set_setting(db_path, LLM_FLAG_KEY, "1" if enabled else "0")


def is_tickers_enabled(config: Config, db_path: str | Path | None = None) -> bool:
    """Return True iff both the config floor and the DB runtime flag allow tickers."""
    if not config.tickers.enabled:
        return False
    path = db_path if db_path is not None else config.db_path
    raw = db.get_setting(path, TICKERS_FLAG_KEY, "1")
    return raw == "1"


def set_tickers_runtime(db_path: str | Path, enabled: bool) -> None:
    """Persist the runtime ticker override. Same semantics as set_llm_runtime."""
    db.set_setting(db_path, TICKERS_FLAG_KEY, "1" if enabled else "0")
