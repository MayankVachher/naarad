"""Startup sanity checks. Catches misconfiguration before the bot is live.

Philosophy:
- Things that make the bot useless = FATAL (token shape, chat_id, DB path).
  Better to crash at boot than to silently fail at the next reminder.
- Things that have a graceful fallback = WARN (copilot binary, EODHD key).
  The brief and water reminders fall back to placeholders / hardcoded
  lines, so a missing copilot CLI degrades the bot but doesn't break it.
  Tickers are independent of the rest — a missing EODHD key disables them
  via `is_tickers_enabled` and the bot still serves /water, /brief, etc.

Called from `bot.build_application()` before any handlers are registered.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from naarad.config import Config
from naarad.copilot_runner import copilot_bin

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")


class StartupValidationError(RuntimeError):
    """Raised when fatal misconfiguration is detected at boot."""


def _validate_token(token: str) -> None:
    if not token or not _TOKEN_RE.match(token):
        raise StartupValidationError(
            "telegram.token is missing or malformed "
            "(expected '<bot_id>:<secret>')"
        )


def _validate_chat_id(chat_id: int) -> None:
    if not chat_id:
        raise StartupValidationError(
            "telegram.chat_id is 0 or unset; the bot wouldn't know whom to message"
        )


def _validate_db_writable(db_path: str) -> None:
    p = Path(db_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StartupValidationError(
            f"cannot create db parent dir {p.parent}: {exc}"
        ) from exc
    # Touch test: open append-mode + close. Doesn't affect existing DBs.
    try:
        with p.open("a"):
            pass
    except OSError as exc:
        raise StartupValidationError(
            f"db_path {p} is not writable: {exc}"
        ) from exc


def _check_copilot_available() -> None:
    """Best-effort. Logs WARN if copilot isn't reachable; never raises.

    Brief + water reminder generators already fall back to placeholders /
    hardcoded text, so the bot remains usable.
    """
    bin_path = copilot_bin()
    try:
        result = subprocess.run(
            [bin_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        log.warning(
            "copilot CLI not found at %r — brief + water reminders will use "
            "fallback text. Set COPILOT_BIN or install the GitHub Copilot CLI.",
            bin_path,
        )
        return
    except subprocess.TimeoutExpired:
        log.warning("copilot --version timed out after 5s; assuming degraded mode")
        return
    except Exception:  # noqa: BLE001
        log.exception("copilot --version raised; assuming degraded mode")
        return

    if result.returncode != 0:
        log.warning(
            "copilot --version exited %d (stderr=%r); assuming degraded mode",
            result.returncode, (result.stderr or "").strip(),
        )
        return

    log.info("copilot CLI ok: %s", (result.stdout or "").strip().splitlines()[0])


def _check_eodhd_for_tickers(config: Config) -> None:
    """Best-effort. Logs WARN if tickers are enabled but the key is empty.

    `is_tickers_enabled` already treats an empty key as 'tickers off', so
    the daily jobs and /quote silently skip — no spammy 'data unavailable'
    posts. The user fixes by adding a real key and restarting; /status
    will report the off-reason in the meantime.
    """
    if not config.tickers.enabled:
        return
    if (config.eodhd.api_key or "").strip():
        return
    log.warning(
        "config.tickers.enabled=true but config.eodhd.api_key is empty — "
        "tickers will stay off until a real EODHD key is added. The rest "
        "of the bot is unaffected."
    )


def validate_startup(config: Config) -> None:
    """Run all startup checks. Raises StartupValidationError on fatal issues."""
    _validate_token(config.telegram.token)
    _validate_chat_id(config.telegram.chat_id)
    _validate_db_writable(config.db_path)
    _check_eodhd_for_tickers(config)
    _check_copilot_available()
    log.info("startup validation passed")
