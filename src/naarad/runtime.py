"""Runtime flag state.

`is_llm_enabled` consults two layers:

1. `config.llm.enabled` — compile-time floor. False means LLM features are
   permanently disabled for this deployment; the runtime `/llm` toggle is
   inert in that case.
2. `settings.llm_enabled` in SQLite — runtime override. Default '1' (on)
   unless `/llm off` has been sent. Survives bot restart.

Both must be on for LLM features to fire. Either being off routes both the
brief and water reminders to their non-LLM fallbacks.
"""
from __future__ import annotations

from pathlib import Path

from naarad import db
from naarad.config import Config

LLM_FLAG_KEY = "llm_enabled"


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
