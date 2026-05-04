"""/llm — toggle the LLM features at runtime.

  /llm        → show current state
  /llm on     → enable (DB flag)
  /llm off    → disable (DB flag)

The compile-time floor (`config.llm.enabled`) is independent — when it's
False, this command can only display state, not flip it.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.runtime import is_llm_enabled, set_llm_runtime


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
    if arg not in ("on", "off"):
        await update.message.reply_text(
            "Usage: <code>/llm</code> | <code>/llm on</code> | <code>/llm off</code>",
            parse_mode="HTML",
        )
        return

    if not config.llm.enabled:
        await update.message.reply_text(
            "Can't toggle: LLM is disabled at the config level (config.llm.enabled=false).\n"
            "Edit config.json + restart to re-enable.",
        )
        return

    set_llm_runtime(config.db_path, enabled=(arg == "on"))
    await update.message.reply_text(_format_state(config), parse_mode="HTML")
