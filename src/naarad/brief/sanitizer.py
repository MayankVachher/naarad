"""HTML sanitization for the daily brief body.

The daily brief is sent with Telegram's ``parse_mode="HTML"``, which only
accepts a small whitelist of tags. Copilot output is mostly well-behaved,
but we get the occasional stray ``<thinking>`` block, raw ``&`` from a
news headline, or markdown ``**bold**`` despite the prompt forbidding it.
This module is the last line of defense before send_message rejects the
whole message and the morning brief silently fails.

Pure functions, no I/O — easy to unit-test.
"""
from __future__ import annotations

import re

# Tags Telegram accepts in HTML parse mode (plus their closers).
_ALLOWED_TAGS = (
    "b", "/b",
    "i", "/i",
    "u", "/u",
    "s", "/s",
    "code", "/code",
    "pre", "/pre",
    "a", "/a",
)


def sanitize_html(text: str) -> str:
    """Make body safe for Telegram HTML parse mode.

    - Convert leftover Markdown bold/italic to ``<b>``/``<i>``.
    - Escape stray ``&`` that isn't already part of a known entity.
    - Escape ``<`` / ``>`` that aren't around a whitelisted tag.

    Idempotent: running it twice yields the same output.
    """
    # 1) Markdown -> HTML.  ** before * so we don't eat the bold ones.
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)

    # 2) Escape any remaining '&' that isn't already an entity.
    text = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+);)", "&amp;", text)

    # 3) Escape '<' / '>' that aren't around a whitelisted tag.
    def _esc_tag(m: re.Match[str]) -> str:
        tag = m.group(1).strip().lower().split()[0] if m.group(1).strip() else ""
        if tag in _ALLOWED_TAGS:
            return m.group(0)
        return m.group(0).replace("<", "&lt;").replace(">", "&gt;")

    text = re.sub(r"<([^<>]*)>", _esc_tag, text)
    return text
