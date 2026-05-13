"""Single source of truth for the command reference.

`scripts/gen_reference.py` reads ``COMMAND_REFERENCE`` to render the
commands table in ``REFERENCE.md``. Adding a new bot command? Wire the
handler in ``bot.py``, then add a row here — the generator + drift test
will keep the docs honest.

The Telegram ``/`` autocomplete menu (``BOT_COMMANDS`` in ``bot.py``)
stays separate: it's limited to top-level commands with short labels,
which is a different shape than the README table.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandDoc:
    usage: str          # how to invoke, e.g. ``"/water"`` or ``"/ticker add SYMBOL"``
    description: str    # one-or-two sentence description for README + docs


COMMAND_REFERENCE: tuple[CommandDoc, ...] = (
    CommandDoc("/water", "Confirm you drank water (resets the chain and bumps the day's glass count)."),
    CommandDoc("/brief", "Re-run today's morning brief on demand. Useful for prompt iteration."),
    CommandDoc("/llm on|off", "Toggle LLM-generated brief + water lines at runtime."),
    CommandDoc("/llm test", "Fire a one-shot prompt at the configured backend; reports ✓ + the model's line or ✗ + the reason."),
    CommandDoc("/ticker add SYMBOL", "Track a new ticker (US bare, `.TO` suffix for TSX; symbol is validated)."),
    CommandDoc("/ticker remove SYMBOL", "Stop tracking a ticker."),
    CommandDoc("/ticker list", "List tracked tickers."),
    CommandDoc("/ticker on|off", "Runtime kill switch for market jobs + `/quote`."),
    CommandDoc("/quote SYMBOL", "On-demand real-time quote for a single symbol."),
    CommandDoc("/status", "Bot health, grouped into Water / LLM / Tickers / System sections."),
    CommandDoc("/help", "Command reference."),
)
