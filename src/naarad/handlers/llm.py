"""/llm — toggle, verify, and reconfigure the LLM features at runtime.

Three surfaces:

* ``/llm`` with no args → a panel: state, effective backend, full
  sub-command list, and inline shortcut buttons (Test, Switch backend,
  Disable/Enable).
* ``/llm on|off|test|backend …`` → text-driven path; every action is
  also reachable by typing.
* Tap-driven path → ``llm_callback`` dispatches ``llm:<verb>[:...]``
  callback queries to the same logic. Patterns are registered in
  ``bot.py``.

The compile-time floor (``config.llm.enabled``) gates everything that
mutates state — when False, the panel/handler can only show state, not
flip it. Buttons render greyed-out semantics via inline text rather than
disabling them (Telegram has no native disabled state).
"""
from __future__ import annotations

import html
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
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

# Callback-data prefixes. Pattern registration in bot.py keys off these.
LLM_CALLBACK_PREFIX = "llm:"
_CB_TEST = "llm:test"
_CB_TOGGLE = "llm:toggle"
_CB_BACKEND_MENU = "llm:backend_menu"
_CB_BACKEND_SET = "llm:backend:"   # llm:backend:<name>
_CB_BACK = "llm:back"              # return to the main panel

USAGE = (
    "Usage:\n"
    "  <code>/llm</code> — show state + actions\n"
    "  <code>/llm on</code> | <code>/llm off</code> — runtime toggle\n"
    "  <code>/llm test</code> — smoke-test the backend\n"
    "  <code>/llm backend</code> — show / pick backend\n"
    "  <code>/llm backend copilot</code> | <code>/llm backend claude</code> — swap live"
)


# ---- text builders ----------------------------------------------------------

def _backend_summary(config: Config) -> str:
    effective = get_llm_backend(config, config.db_path)
    if effective == config.llm.backend:
        return f"Backend: <b>{html.escape(effective)}</b> (config default)."
    return (
        f"Backend: <b>{html.escape(effective)}</b> (runtime override).\n"
        f"Config default: <code>{html.escape(config.llm.backend)}</code>. "
        f"Tap <b>copilot</b>/<b>claude</b> below or send "
        f"<code>/llm backend {html.escape(config.llm.backend)}</code> to revert."
    )


_SUBCOMMANDS_BLOCK = (
    "\n<b>Sub-commands</b>\n"
    "• <code>/llm on</code> / <code>/llm off</code> — runtime toggle\n"
    "• <code>/llm test</code> — smoke-test the configured backend\n"
    "• <code>/llm backend copilot</code> | <code>/llm backend claude</code> — swap backend live"
)


def _format_state(config: Config) -> str:
    """Full panel text — state, backend, and the explicit sub-command list.

    Used by both the text /llm command and the callback refresh path so
    everything reads the same.
    """
    if not config.llm.enabled:
        return (
            "🤖 <b>LLM: off</b> (disabled in config — runtime toggle inert).\n"
            "Brief uses the plain renderer; water reminders use hardcoded tones.\n"
            "Edit <code>config.json</code> (<code>llm.enabled: true</code>) and "
            "restart to re-enable."
            + _SUBCOMMANDS_BLOCK
        )
    enabled = is_llm_enabled(config, config.db_path)
    state_line = (
        "🤖 <b>LLM: on</b>" if enabled
        else "🤖 <b>LLM: off</b> (runtime)"
    )
    extra = ""
    if not enabled:
        extra = (
            "\nBrief uses the plain renderer; water reminders use hardcoded tones."
        )
    return (
        f"{state_line}\n{_backend_summary(config)}{extra}"
        + _SUBCOMMANDS_BLOCK
    )


# ---- inline keyboards -------------------------------------------------------

def _panel_keyboard(config: Config) -> InlineKeyboardMarkup | None:
    """Buttons for the /llm panel. None when config floor is off — there's
    nothing to flip, so a button row is just visual noise.
    """
    if not config.llm.enabled:
        return None
    enabled = is_llm_enabled(config, config.db_path)
    toggle_label = "⏸️ Disable" if enabled else "▶️ Enable"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🧪 Test", callback_data=_CB_TEST),
            InlineKeyboardButton("🤖 Switch backend", callback_data=_CB_BACKEND_MENU),
            InlineKeyboardButton(toggle_label, callback_data=_CB_TOGGLE),
        ],
    ])


def _backend_menu_keyboard(config: Config) -> InlineKeyboardMarkup:
    """[copilot] [claude] with a ✓ on the current effective backend, plus
    a Back row to return to the main panel.
    """
    current = get_llm_backend(config, config.db_path)
    row: list[InlineKeyboardButton] = []
    for name in sorted(BACKENDS):
        label = f"🤖 {name}"
        if name == current:
            label += " ✓"
        row.append(InlineKeyboardButton(label, callback_data=f"{_CB_BACKEND_SET}{name}"))
    return InlineKeyboardMarkup([
        row,
        [InlineKeyboardButton("⬅️ Back", callback_data=_CB_BACK)],
    ])


def _back_only_keyboard() -> InlineKeyboardMarkup:
    """Single ⬅️ Back row — used for transient sub-states (test result)
    where no other action makes sense."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data=_CB_BACK)],
    ])


def _backend_menu_text(config: Config) -> str:
    return (
        "🤖 <b>LLM backend</b>\n"
        f"{_backend_summary(config)}\n\n"
        "Pick a backend below, or send "
        "<code>/llm backend copilot</code> / <code>/llm backend claude</code>."
    )


# ---- action implementations (shared between text + callback paths) ----------

async def _do_toggle(config: Config) -> None:
    """Flip the runtime LLM flag. Caller is responsible for the config-floor check."""
    enabled = is_llm_enabled(config, config.db_path)
    set_llm_runtime(config.db_path, enabled=not enabled)


def _set_backend(config: Config, target: str) -> str:
    """Apply a backend swap and return a confirmation line. Caller is
    responsible for validating ``target`` against ``BACKENDS`` and the
    config floor.
    """
    if target == config.llm.backend:
        clear_llm_backend(config.db_path)
        return (
            f"Backend reverted to <b>{html.escape(target)}</b> (config default); "
            "runtime override cleared."
        )
    set_llm_backend(config.db_path, target)
    return (
        f"Backend switched to <b>{html.escape(target)}</b> (runtime override). "
        "Next LLM call uses this backend; "
        f"<code>/llm test</code> to verify."
    )


def _format_test_result(ok: bool, output: str) -> str:
    if ok:
        return f"<b>LLM check ✓</b>\n<i>{html.escape(output)}</i>"
    return f"<b>LLM check ✗</b>\n{html.escape(output)}"


async def _run_test_command(reply_target: Message, config: Config) -> None:
    """Text path: send an ack reply and edit it with the smoke-test result.
    The ack is a fresh message so the user's typed /llm test stays in the
    chat history above the result.
    """
    ack = await reply_target.reply_text("⏳ Testing LLM…")
    ok, output = await run_smoketest(config)
    text = _format_test_result(ok, output)
    try:
        await ack.edit_text(text, parse_mode="HTML")
    except Exception:
        log.exception("/llm test: edit_text failed; sending fresh message")
        await reply_target.reply_text(text, parse_mode="HTML")


async def _run_test_in_panel(panel_msg: Message, config: Config) -> None:
    """Button path: run the smoke-test in place on the panel message so
    the test result replaces the panel content without piling on new
    messages. Trailing keyboard is ⬅️ Back only — no other action is
    meaningful while looking at a result.
    """
    try:
        await panel_msg.edit_text(
            "⏳ Testing LLM…",
            reply_markup=_back_only_keyboard(),
        )
    except Exception:
        log.debug("test-in-panel: pre-edit failed", exc_info=True)
    ok, output = await run_smoketest(config)
    text = _format_test_result(ok, output)
    try:
        await panel_msg.edit_text(
            text, parse_mode="HTML", reply_markup=_back_only_keyboard(),
        )
    except Exception:
        log.exception("/llm test (panel): edit_text failed; sending fresh message")
        await panel_msg.reply_text(text, parse_mode="HTML")


# ---- text command -----------------------------------------------------------

async def _run_backend_command(
    update: Update, config: Config, rest: list[str],
) -> None:
    """Text path for /llm backend [target]."""
    if update.message is None:
        return
    if not config.llm.enabled:
        await update.message.reply_text(
            "Can't change backend: LLM is disabled at the config level "
            "(config.llm.enabled=false). Edit config.json + restart to re-enable.",
        )
        return

    if not rest:
        await update.message.reply_text(
            _backend_menu_text(config),
            parse_mode="HTML",
            reply_markup=_backend_menu_keyboard(config),
        )
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

    text = _set_backend(config, target)
    await update.message.reply_text(text, parse_mode="HTML")


async def llm_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update, context):
        return
    if update.message is None:
        return
    config: Config = context.application.bot_data["config"]
    args = context.args or []

    if not args:
        await update.message.reply_text(
            _format_state(config),
            parse_mode="HTML",
            reply_markup=_panel_keyboard(config),
        )
        return

    arg = args[0].lower()

    if arg == "test":
        await _run_test_command(update.message, config)
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
    await update.message.reply_text(
        _format_state(config),
        parse_mode="HTML",
        reply_markup=_panel_keyboard(config),
    )


# ---- callback router --------------------------------------------------------

async def llm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single entrypoint for every llm:* inline-button callback. Patterns
    in bot.py route ^llm: here; we parse the suffix to decide.
    """
    if await reject_unauthorized(update, context):
        return
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return
    config: Config = context.application.bot_data["config"]
    data = query.data

    # Always ack the query so Telegram stops the spinner — we'll edit the
    # message separately when there's a state change.
    await query.answer()

    if data == _CB_TEST:
        await _run_test_in_panel(query.message, config)
        return

    if data == _CB_BACK:
        await query.message.edit_text(
            _format_state(config),
            parse_mode="HTML",
            reply_markup=_panel_keyboard(config),
        )
        return

    if data == _CB_TOGGLE:
        if not config.llm.enabled:
            await query.answer(
                "LLM disabled in config; runtime toggle inert.", show_alert=True
            )
            return
        await _do_toggle(config)
        await query.message.edit_text(
            _format_state(config),
            parse_mode="HTML",
            reply_markup=_panel_keyboard(config),
        )
        return

    if data == _CB_BACKEND_MENU:
        await query.message.edit_text(
            _backend_menu_text(config),
            parse_mode="HTML",
            reply_markup=_backend_menu_keyboard(config),
        )
        return

    if data.startswith(_CB_BACKEND_SET):
        target = data[len(_CB_BACKEND_SET):]
        if target not in BACKENDS:
            await query.answer(f"Unknown backend: {target}", show_alert=True)
            return
        if not config.llm.enabled:
            await query.answer(
                "LLM disabled in config; backend swap inert.", show_alert=True
            )
            return
        _set_backend(config, target)
        # Refresh the menu so the ✓ moves to the just-selected backend.
        await query.message.edit_text(
            _backend_menu_text(config),
            parse_mode="HTML",
            reply_markup=_backend_menu_keyboard(config),
        )
        return

    log.warning("llm_callback: unrecognised callback_data %r", data)
