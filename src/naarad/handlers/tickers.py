"""/ticker subcommand router: add | remove | list."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized

USAGE = (
    "Usage:\n"
    "  /ticker add SYMBOL\n"
    "  /ticker remove SYMBOL\n"
    "  /ticker list"
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
