"""/llm — toggle and verify the LLM features at runtime.

  /llm        → show current state
  /llm on     → enable (DB flag)
  /llm off    → disable (DB flag)
  /llm test   → fire a one-shot prompt at the configured backend and
                show ✓/✗ — useful for catching auth / wrong-backend
                issues without waiting for the morning brief.

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
from naarad.llm.smoketest import run_smoketest
from naarad.runtime import is_llm_enabled, set_llm_runtime

log = logging.getLogger(__name__)

USAGE = (
    "Usage: <code>/llm</code> | <code>/llm on</code> | "
    "<code>/llm off</code> | <code>/llm test</code>"
)


def _format_state(config: Config) -> str:
    if not config.llm.enabled:
        return (
            "LLM: <b>off</b> (disabled in config — runtime toggle inert).\n"
            "Brief uses the plain renderer; water reminders use hardcoded tones."
        )
    enabled = is_llm_enabled(config, config.db_path)
    if enabled:
        return "LLM: <b>on</b>. Use <code>/llm off</code> to disable at runtime."
    return (
        "LLM: <b>off</b> (runtime). Use <code>/llm on</code> to re-enable.\n"
        "Brief uses the plain renderer; water reminders use hardcoded tones."
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
