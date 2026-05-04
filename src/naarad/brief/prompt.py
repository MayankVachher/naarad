"""Prompt construction for the daily brief.

The Copilot CLI is given a fully-rendered prompt (no tools, no browsing).
Raw source data — RSS, weather, sun times, on-this-day — is gathered in
``brief/sources.py`` and inlined here so Copilot's job is pure
summarization/rewriting.

The template lives in code (not config.json) so it gets diff/PR review
when it changes, and so iterating on it is a normal commit.
"""
from __future__ import annotations

import logging
from datetime import date

from naarad.brief import sources
from naarad.config import Config

log = logging.getLogger(__name__)


PROMPT_TEMPLATE = """\
You are writing Mayank's morning brief for {date_str}. He lives in {location_name}, works at Google, reads this once over coffee. Tone: warm, dry-witted, plain-spoken. He's a sharp engineer — don't be cheesy.

Use the RAW SOURCE DATA below. Do not invent facts. Pick the highest-signal items; cut everything else. If a section is genuinely thin, say so in one warm line instead of padding.

OUTPUT FORMAT (rendered with Telegram HTML parse mode — only <b>, <i>, <a> are allowed tags):

🌅 <b>WEATHER — {location_name}</b>

• <b>Now</b> — direct: e.g. "<i>14°C, partly cloudy (feels 12°)</i>". No fluff.

• <b>High / Low</b> — direct: e.g. "<i>17° / 6° (feels 15°/3°)</i>". Append precip chance if ≥30%.

• <b>Heads-up</b> — one short sentence on wind, sunrise/sunset, or what to wear. Skip with "—" if nothing's notable.

─────────

🌎 <b>WORLD</b>

• <b>Headline (≤8 words)</b> — one short sentence of context with a touch of personality. Optional one emoji at end.

• <b>Headline</b> — same shape.

• <b>Headline</b> — same shape.

─────────

🍁 <b>CANADA</b>

• … exactly three, same shape (blank line between each bullet).

─────────

🤖 <b>AI &amp; TECH</b>

• … exactly three (blank line between each bullet).

─────────

🔵 <b>AT GOOGLE</b>

• … exactly three (blank line between each bullet).

─────────

✨ <b>NOTABLE TODAY</b>

• … exactly three (events, holidays, on-this-day from the raw data; blank line between each).

─────────

💭 <b>QUOTE OF THE DAY</b>
"<i>A genuine, thought-provoking quote (≤25 words).</i>" — <i>Author Name</i>
You choose. Pick something that fits the day's vibe — the news, the weather, the season, or just something Mayank would appreciate. Vary sources (philosophers, scientists, writers, technologists, athletes). Avoid clichés ("carpe diem", "live laugh love", overused Steve Jobs lines). Make sure the attribution is real.

Hard rules:
- The bullet character is "•" (U+2022). Not "-" or "*".
- Section headers MUST be exactly as shown: leading emoji + space + <b>ALL CAPS TITLE</b>. The emoji is OUTSIDE the <b>...</b>.
- Use &amp; in section titles (Telegram escaping for "&").
- Exactly 3 bullets per section (including WEATHER) — the top 3 most important from the raw pool. If the pool is too thin, drop to 2 + one short honest line.
- One blank line after every section heading (before the first bullet).
- Each bullet: bold headline + " — " (em dash with spaces) + one short sentence (≤22 words total). At most one emoji at the very end of the line.
- One blank line between every two bullets within a section (so each bullet stands alone).
- Between sections, output the literal divider "─────────" (nine U+2500 box-drawing chars) on its own line, with one blank line above and one blank line below it. Use it between every adjacent pair of sections, including before QUOTE OF THE DAY. Do NOT add a divider before the first section or after the last.
- No markdown, no other HTML tags besides <b> and <i>.
- No greeting, no sign-off, no meta-commentary about the brief.

{sources_block}"""


def _build_sources_block(today: date, config: Config) -> str:
    try:
        ctx = sources.build_context(
            today=today,
            location_name=config.brief.location_name,
            location_lat=config.brief.location_lat,
            location_lon=config.brief.location_lon,
            timezone=config.timezone,
        )
        return sources.format_for_prompt(ctx)
    except Exception:
        log.exception("sources block build failed; sending Copilot an empty block")
        return "RAW SOURCE DATA: (all sources failed to fetch)\n"


def build_prompt(today: date, config: Config) -> str:
    return PROMPT_TEMPLATE.format(
        date_str=today.strftime("%A, %B %d, %Y"),
        location_name=config.brief.location_name,
        sources_block=_build_sources_block(today, config),
    )
