"""/ticker subcommand router: add | remove | list | on | off.

``/ticker`` with no args renders a panel: current state, the watchlist,
and the full sub-command list with concrete examples. A ⏸️/▶️ inline
button flips the runtime toggle. add/remove need a symbol argument so
they stay text-only.

The on/off toggle is a runtime kill switch backed by settings.tickers_enabled.
The compile-time floor (config.tickers.enabled) is independent — when it's
False (or no EODHD key), this command can only display state, not flip it.
"""
from __future__ import annotations

import html
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from naarad import db
from naarad.config import Config
from naarad.handlers.auth import reject_unauthorized
from naarad.runtime import (
    is_tickers_enabled,
    set_tickers_runtime,
    tickers_off_reason,
)
from naarad.tickers.eodhd import _classify_symbol

log = logging.getLogger(__name__)

TICKER_CALLBACK_PREFIX = "ticker:"
_CB_TOGGLE = "ticker:toggle"

USAGE = (
    "Usage:\n"
    "  <code>/ticker</code> — show state + watchlist + actions\n"
    "  <code>/ticker add SYMBOL</code> — track a new symbol\n"
    "  <code>/ticker remove SYMBOL</code> — stop tracking\n"
    "  <code>/ticker list</code> — list tracked symbols\n"
    "  <code>/ticker on</code> | <code>/ticker off</code> — runtime toggle"
)

_SUBCOMMANDS_BLOCK = (
    "\n<b>Sub-commands</b>\n"
    "• <code>/ticker add SYMBOL</code> — track (US bare or <code>.TO</code>)\n"
    "• <code>/ticker remove SYMBOL</code> — stop tracking\n"
    "• <code>/ticker list</code> — list tracked symbols\n"
    "• <code>/ticker on</code> / <code>/ticker off</code> — runtime toggle"
)


def _state_line(config: Config) -> str:
    reason = tickers_off_reason(config, config.db_path)
    if reason == "config":
        return (
            "📈 <b>Tickers: off</b> (disabled in config — runtime toggle inert).\n"
            "Set <code>config.tickers.enabled=true</code> and restart to re-enable."
        )
    if reason == "no_key":
        return (
            "📈 <b>Tickers: off</b> (no EODHD API key — runtime toggle inert).\n"
            "Add <code>config.eodhd.api_key</code> and restart to enable."
        )
    if reason == "runtime":
        return (
            "📈 <b>Tickers: off</b> (runtime). "
            "Market open/close briefs and <code>/quote</code> are paused."
        )
    return "📈 <b>Tickers: on</b>."


def _watchlist_line(config: Config) -> str:
    symbols = db.list_tickers(config.db_path)
    if not symbols:
        return "Watchlist: <i>(none)</i>"
    rendered = ", ".join(html.escape(s) for s in symbols)
    return f"Watchlist: <b>{rendered}</b>"


def _format_state(config: Config) -> str:
    return f"{_state_line(config)}\n{_watchlist_line(config)}{_SUBCOMMANDS_BLOCK}"


def _panel_keyboard(config: Config) -> InlineKeyboardMarkup | None:
    """Just the toggle button. None when the kill switch is inert (config
    floor off or no EODHD key) — a button row would imply it does something.
    """
    reason = tickers_off_reason(config, config.db_path)
    if reason in ("config", "no_key"):
        return None
    enabled = is_tickers_enabled(config, config.db_path)
    label = "⏸️ Disable" if enabled else "▶️ Enable"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=_CB_TOGGLE)]])


async def ticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    msg = update.message
    if msg is None:
        return
    config: Config = context.application.bot_data["config"]

    args = context.args or []
    if not args:
        await msg.reply_text(
            _format_state(config),
            parse_mode="HTML",
            reply_markup=_panel_keyboard(config),
        )
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
        reason = tickers_off_reason(config, config.db_path)
        # 'config' and 'no_key' are inert — flipping the runtime flag won't
        # bring tickers back, so refuse loudly. 'runtime' is the only state
        # where the toggle actually does anything.
        if reason in ("config", "no_key"):
            await msg.reply_text(
                "Can't toggle: " + _state_line(config),
                parse_mode="HTML",
            )
            return
        set_tickers_runtime(config.db_path, enabled=(sub == "on"))
        await msg.reply_text(
            _format_state(config),
            parse_mode="HTML",
            reply_markup=_panel_keyboard(config),
        )
        return

    if sub in ("add", "remove"):
        if len(args) < 2:
            await msg.reply_text(USAGE, parse_mode="HTML")
            return
        symbol = args[1]
        if sub == "add":
            try:
                _classify_symbol(symbol)
            except ValueError as exc:
                await msg.reply_text(f"⚠️ {exc}")
                return
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

    await msg.reply_text(USAGE, parse_mode="HTML")


async def ticker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single entrypoint for every ticker:* inline-button callback."""
    if await reject_unauthorized(update, context):
        return
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return
    config: Config = context.application.bot_data["config"]
    data = query.data

    await query.answer()

    if data == _CB_TOGGLE:
        reason = tickers_off_reason(config, config.db_path)
        if reason in ("config", "no_key"):
            await query.answer(
                "Tickers disabled at the config level; runtime toggle inert.",
                show_alert=True,
            )
            return
        enabled = is_tickers_enabled(config, config.db_path)
        set_tickers_runtime(config.db_path, enabled=not enabled)
        await query.message.edit_text(
            _format_state(config),
            parse_mode="HTML",
            reply_markup=_panel_keyboard(config),
        )
        return

    log.warning("ticker_callback: unrecognised callback_data %r", data)
