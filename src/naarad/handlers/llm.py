"""/llm — toggle, verify, and reconfigure the LLM features at runtime.

  /llm                       → show current state + effective backend
  /llm on                    → enable (DB flag)
  /llm off                   → disable (DB flag)
  /llm test                  → fire a one-shot prompt at the configured
                                backend and show ✓/✗ — useful for catching
                                auth / wrong-backend issues without waiting
                                for the morning brief.
  /llm backend               → show effective backend (override vs config)
  /llm backend copilot|claude
                             → flip backend at runtime; setting it to the
                                config default clears the override so DB
                                state stays minimal.

The compile-time floor (`config.llm.enabled`) is independent — when it's
False, this command can only display state, not flip it.
"""
from __future__ import annotations

import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.llm import BACKENDS
from naarad.llm.smoketest import run_smoketest
from naarad.runtime import (
    clear_llm_backend,
    get_llm_backend,
    is_llm_enabled,
    set_llm_backend,
    set_llm_runtime,
)

log = logging.getLogger(__name__)

USAGE = (
    "Usage: <code>/llm</code> | <code>/llm on</code> | "
    "<code>/llm off</code> | <code>/llm test</code> | "
    "<code>/llm backend [copilot|claude]</code>"
)


def _backend_summary(config: Config) -> str:
    effective = get_llm_backend(config, config.db_path)
    if effective == config.llm.backend:
        return f"Backend: <b>{html.escape(effective)}</b> (config default)."
    return (
        f"Backend: <b>{html.escape(effective)}</b> (runtime override).\n"
        f"Config default: <code>{html.escape(config.llm.backend)}</code>. "
        f"Use <code>/llm backend {html.escape(config.llm.backend)}</code> to revert."
    )


def _format_state(config: Config) -> str:
    if not config.llm.enabled:
        return (
            "LLM: <b>off</b> (disabled in config — runtime toggle inert).\n"
            "Brief uses the plain renderer; water reminders use hardcoded tones.\n"
            "Try <code>/llm test</code> to smoke-test the configured backend."
        )
    enabled = is_llm_enabled(config, config.db_path)
    backend_line = _backend_summary(config)
    if enabled:
        return (
            f"LLM: <b>on</b>. Use <code>/llm off</code> to disable at runtime, "
            f"or <code>/llm test</code> to smoke-test the call.\n{backend_line}"
        )
    return (
        f"LLM: <b>off</b> (runtime). Use <code>/llm on</code> to re-enable.\n"
        f"Brief uses the plain renderer; water reminders use hardcoded tones.\n"
        f"{backend_line}"
    )


async def _run_test_command(update: Update, config: Config) -> None:
    """Send an ack, fire the smoke test, and edit the ack with the result."""
    if update.message is None:
        return
    ack = await update.message.reply_text("⏳ Testing LLM…")
    ok, output = await run_smoketest(config)
    if ok:
        text = f"<b>LLM check ✓</b>\n<i>{html.escape(output)}</i>"
    else:
        text = f"<b>LLM check ✗</b>\n{html.escape(output)}"
    try:
        await ack.edit_text(text, parse_mode="HTML")
    except Exception:
        log.exception("/llm test: edit_text failed; sending fresh message")
        await update.message.reply_text(text, parse_mode="HTML")


async def _run_backend_command(
    update: Update, config: Config, rest: list[str],
) -> None:
    """Show or set the runtime backend override.

    No arg → show effective backend (and the override status).
    With arg → validate, persist, and confirm. Setting the override
    equal to ``config.llm.backend`` clears it so the DB state stays
    minimal.
    """
    if update.message is None:
        return
    if not config.llm.enabled:
        await update.message.reply_text(
            "Can't change backend: LLM is disabled at the config level "
            "(config.llm.enabled=false). Edit config.json + restart to re-enable.",
        )
        return

    if not rest:
        await update.message.reply_text(_backend_summary(config), parse_mode="HTML")
        return

    target = rest[0].lower()
    if target not in BACKENDS:
        choices = ", ".join(sorted(BACKENDS))
        await update.message.reply_text(
            f"Unknown backend <code>{html.escape(target)}</code>. "
            f"Choose from: {choices}.",
            parse_mode="HTML",
        )
        return

    if target == config.llm.backend:
        clear_llm_backend(config.db_path)
        text = (
            f"Backend reverted to <b>{html.escape(target)}</b> (config default); "
            "runtime override cleared."
        )
    else:
        set_llm_backend(config.db_path, target)
        text = (
            f"Backend switched to <b>{html.escape(target)}</b> (runtime override). "
            "Next LLM call uses this backend; "
            f"<code>/llm test</code> to verify."
        )
    await update.message.reply_text(text, parse_mode="HTML")


async def llm_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    if update.message is None:
        return
    config: Config = context.application.bot_data["config"]
    args = context.args or []

    if not args:
        await update.message.reply_text(_format_state(config), parse_mode="HTML")
        return

    arg = args[0].lower()

    if arg == "test":
        await _run_test_command(update, config)
        return

    if arg == "backend":
        await _run_backend_command(update, config, args[1:])
        return

    if arg not in ("on", "off"):
        await update.message.reply_text(USAGE, parse_mode="HTML")
        return

    if not config.llm.enabled:
        await update.message.reply_text(
            "Can't toggle: LLM is disabled at the config level (config.llm.enabled=false).\n"
            "Edit config.json + restart to re-enable.",
        )
        return

    set_llm_runtime(config.db_path, enabled=(arg == "on"))
    await update.message.reply_text(_format_state(config), parse_mode="HTML")
