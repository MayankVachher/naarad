"""/ticker subcommand router: add | remove | list | on | off.

The on/off toggle is a runtime kill switch backed by settings.tickers_enabled.
The compile-time floor (config.tickers.enabled) is independent — when it's
False, this command can only display state, not flip it.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.runtime import is_tickers_enabled, set_tickers_runtime

USAGE = (
    "Usage:\n"
    "  /ticker add SYMBOL\n"
    "  /ticker remove SYMBOL\n"
    "  /ticker list\n"
    "  /ticker on | off"
)


def _format_state(config: Config) -> str:
    if not config.tickers.enabled:
        return (
            "Tickers: <b>off</b> (disabled in config — runtime toggle inert).\n"
            "Set config.tickers.enabled=true and restart to re-enable."
        )
    enabled = is_tickers_enabled(config, config.db_path)
    if enabled:
        return "Tickers: <b>on</b>. Use <code>/ticker off</code> to disable at runtime."
    return (
        "Tickers: <b>off</b> (runtime). Use <code>/ticker on</code> to re-enable.\n"
        "Market open / close briefs and /quote are paused while off."
    )


async def ticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    msg = update.message
    if msg is None:
        return
    config: Config = context.application.bot_data["config"]

    args = context.args or []
    if not args:
        await msg.reply_text(USAGE)
        return

    sub = args[0].lower()

    if sub == "list":
        symbols = db.list_tickers(config.db_path)
        if not symbols:
            await msg.reply_text("No tickers tracked.")
        else:
            await msg.reply_text("Tracked: " + ", ".join(symbols))
        return

    if sub in ("on", "off"):
        if not config.tickers.enabled:
            await msg.reply_text(
                "Can't toggle: tickers are disabled at the config level "
                "(config.tickers.enabled=false).\n"
                "Edit config.json + restart to re-enable.",
            )
            return
        set_tickers_runtime(config.db_path, enabled=(sub == "on"))
        await msg.reply_text(_format_state(config), parse_mode="HTML")
        return

    if sub in ("add", "remove"):
        if len(args) < 2:
            await msg.reply_text(USAGE)
            return
        symbol = args[1]
        if sub == "add":
            added = db.add_ticker(config.db_path, symbol)
            if added:
                await msg.reply_text(f"✅ Added {symbol.upper()}")
            else:
                await msg.reply_text(f"{symbol.upper()} is already tracked")
        else:
            removed = db.remove_ticker(config.db_path, symbol)
            if removed:
                await msg.reply_text(f"🗑️ Removed {symbol.upper()}")
            else:
                await msg.reply_text(f"{symbol.upper()} wasn't tracked")
        return

    await msg.reply_text(USAGE)
