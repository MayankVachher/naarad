"""Runtime flag state.

`is_llm_enabled` and `is_tickers_enabled` each consult layers of state:

1. Compile-time floor in `Config` (`config.llm.enabled` /
   `config.tickers.enabled`). False means the feature is permanently
   disabled for this deployment; the matching runtime toggle is inert.
2. For tickers only: presence of `config.eodhd.api_key`. Without a key,
   the EODHD client would 401 every fetch, so we treat a missing key as
   the same kind of "feature unavailable" as the config floor — the bot
   still boots and everything else works, but ticker jobs / /quote
   silently skip.
3. SQLite `settings` row (`settings.llm_enabled` / `settings.tickers_enabled`).
   Default '1' (on) unless a runtime command has flipped it. Survives bot
   restart.

All applicable layers must be on for the feature to fire. Off routes
consumers to their non-feature fallbacks (LLM → plain renderer / hardcoded
water tones; tickers → silent skip + /ticker on|off no-op).

`tickers_off_reason` returns a short label explaining *why* tickers are
off, for surfacing in /status, /ticker, and /quote.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from naarad import db
from naarad.config import Config

LLM_FLAG_KEY = "llm_enabled"
LLM_BACKEND_KEY = "llm_backend"
TICKERS_FLAG_KEY = "tickers_enabled"

TickersOffReason = Literal["config", "no_key", "runtime"]


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


def get_llm_backend(config: Config, db_path: str | Path | None = None) -> str:
    """Return the effective LLM backend name.

    DB-backed runtime override wins; otherwise falls back to
    ``config.llm.backend``. An unknown or empty stored value falls
    through too — config is the floor of trust.
    """
    # Late import: naarad.llm depends on naarad.runtime indirectly via
    # the dispatch module, so importing at module load would create a
    # cycle.
    from naarad.llm import BACKENDS
    path = db_path if db_path is not None else config.db_path
    try:
        override = db.get_setting(path, LLM_BACKEND_KEY, "") or ""
    except Exception:
        # DB missing / table not yet created (e.g. startup checks
        # firing before init_db). Config is the floor of trust — degrade
        # silently rather than raising on a read.
        return config.llm.backend
    if override and override in BACKENDS:
        return override
    return config.llm.backend


def set_llm_backend(db_path: str | Path, name: str) -> None:
    """Persist a runtime backend override. Validates against the known
    backends; raises ValueError otherwise. Passing the same value as
    ``config.llm.backend`` is allowed — clearing back to config is the
    caller's responsibility via :func:`clear_llm_backend`.
    """
    from naarad.llm import BACKENDS
    if name not in BACKENDS:
        raise ValueError(
            f"unknown LLM backend {name!r}; choose from {sorted(BACKENDS)}"
        )
    db.set_setting(db_path, LLM_BACKEND_KEY, name)


def clear_llm_backend(db_path: str | Path) -> None:
    """Drop the runtime backend override; fall back to ``config.llm.backend``."""
    db.set_setting(db_path, LLM_BACKEND_KEY, "")


def tickers_off_reason(
    config: Config, db_path: str | Path | None = None
) -> TickersOffReason | None:
    """If tickers are off, return WHY (``config`` | ``no_key`` | ``runtime``).

    Returns None when tickers are fully operable. The order matters — we
    report the most-permanent reason first so 'fix the config' beats 'flip
    the toggle'.
    """
    if not config.tickers.enabled:
        return "config"
    if not (config.eodhd.api_key or "").strip():
        return "no_key"
    path = db_path if db_path is not None else config.db_path
    raw = db.get_setting(path, TICKERS_FLAG_KEY, "1")
    if raw != "1":
        return "runtime"
    return None


def is_tickers_enabled(config: Config, db_path: str | Path | None = None) -> bool:
    """Return True iff tickers are operable: config floor + key + runtime flag
    all green. The single gate jobs and /quote should consult.
    """
    return tickers_off_reason(config, db_path) is None


def set_tickers_runtime(db_path: str | Path, enabled: bool) -> None:
    """Persist the runtime ticker override. Same semantics as set_llm_runtime."""
    db.set_setting(db_path, TICKERS_FLAG_KEY, "1" if enabled else "0")
