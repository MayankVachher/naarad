"""Prompt construction + body formatting for the daily brief.

The configured LLM CLI is given a fully-rendered prompt (no tools, no
browsing). Raw source data — RSS, weather, sun times, on-this-day — is
gathered in ``brief/sources.py`` and inlined here so the LLM's job is
pure summarization/rewriting.

The template lives in code (not config.json) so it gets diff/PR review
when it changes, and so iterating on it is a normal commit.

``format_brief_body`` is the post-process step LLM call sites use to
turn raw stdout into Telegram-safe HTML with the date header.
"""
from __future__ import annotations

import logging
from datetime import date

from naarad.brief import sources
from naarad.brief.sanitizer import sanitize_html
from naarad.config import Config
from naarad.llm.claude import TURN_BUDGET

log = logging.getLogger(__name__)


def brief_header(today: date) -> str:
    """Plain-text date header, e.g. ``Fri May 1, 2026``."""
    return today.strftime("%a %b ") + str(today.day) + today.strftime(", %Y")


def format_brief_body(today: date, raw: str) -> str:
    """Sanitize the LLM's raw stdout and prepend the date header.

    Used as the ``post_process`` of every brief LLMTask so the scheduled
    job and the manual /brief produce identical formatting.
    """
    body = sanitize_html(raw)
    return f"<b>☀️ {brief_header(today)}</b>\n\n{body}"


PROMPT_TEMPLATE = """\
You are writing Mayank's morning brief for {date_str}. He lives in {location_name}, works at Google, reads this once over coffee. Tone: warm, dry-witted, plain-spoken. He's a sharp engineer — don't be cheesy.

Use the RAW SOURCE DATA below as your primary input. Do not invent facts. Pick the highest-signal items; cut everything else. If a section is genuinely thin, say so in one warm line instead of padding.

Research workflow (Claude Code only — Copilot ignores this):
1. Read the RAW SOURCE DATA below.
2. For each of WORLD, CANADA, AI &amp; TECH, and AT GOOGLE: pick the strongest seed headline from the raw pool, then run ONE WebSearch to (a) confirm it's still current, and (b) pull one concrete detail the raw RSS lacks — a name, figure, date, ruling, version number. If a section's raw pool is empty or stale (>24h), use the search result as the primary source for that section.
3. Write the brief.

Budget: {turn_budget} turns total — this answer plus up to {tool_turns} tool calls combined. That's one focused search per substantive section. Use WebFetch only when a search result has a critical URL worth reading in full; don't fetch arbitrarily.

WEATHER, NOTABLE TODAY, and QUOTE OF THE DAY don't need tool use — the raw weather block is canonical, NOTABLE TODAY comes from the on-this-day raw data, and QUOTE is your own pick. Save the tool budget for the four live sections.

Don't paste raw URLs into the output. Don't cite the searches — just incorporate the facts.

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
        turn_budget=TURN_BUDGET,
        tool_turns=TURN_BUDGET - 1,
    )
