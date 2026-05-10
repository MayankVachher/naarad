"""Plain (non-LLM) brief renderer.

Used when the LLM kill switch is off (config.llm.enabled=false OR
runtime /llm off). Same data sources as the LLM brief — different render.

Layout matches the LLM brief's section structure so the user gets a
predictable shape regardless of which renderer ran:

    🌅 WEATHER
    🌎 WORLD
    🍁 CANADA
    🤖 AI&TECH
    🔵 AT GOOGLE
    ✨ NOTABLE TODAY
    💭 QUOTE OF THE DAY

Headlines: top 3 per section, formatted `• <b>Title</b> — <i>Source</i>`.
Quote: deterministic per-date pick from the curated list (see quotes.py).
"""
from __future__ import annotations

import html
import logging
from datetime import date

from naarad.brief import sources
from naarad.brief.quotes import pick_quote_for
from naarad.brief.sources import BriefContext, Headline
from naarad.config import Config

log = logging.getLogger(__name__)

DIVIDER = "─────────"
SECTION_LIMIT = 3


def _format_headlines(items: list[Headline]) -> list[str]:
    if not items:
        return ["• <i>(no items today)</i>"]
    out: list[str] = []
    for h in items[:SECTION_LIMIT]:
        title = html.escape(h.title.strip())
        source = html.escape(h.source.strip())
        out.append(f"• <b>{title}</b> — <i>{source}</i>")
    return out


def _format_notable(items: list[str]) -> list[str]:
    if not items:
        return ["• <i>(nothing notable surfaced today)</i>"]
    return [f"• {html.escape(n.strip())}" for n in items[:SECTION_LIMIT]]


def _format_weather(ctx: BriefContext) -> list[str]:
    bullets: list[str] = []
    if ctx.weather_line:
        bullets.append(f"• {html.escape(ctx.weather_line)}")
    if ctx.sunrise and ctx.sunset:
        bullets.append(
            f"• Sunrise <b>{html.escape(ctx.sunrise)}</b> · "
            f"Sunset <b>{html.escape(ctx.sunset)}</b>"
        )
    if not bullets:
        bullets.append("• <i>(weather unavailable today)</i>")
    return bullets


def _section(heading: str, body_lines: list[str]) -> str:
    return f"<b>{heading}</b>\n\n" + "\n".join(body_lines)


def render_from_context(today: date, ctx: BriefContext) -> str:
    """Pure renderer — caller provides the BriefContext. Easier to test."""
    header = today.strftime("%a %b ") + str(today.day) + today.strftime(", %Y")
    location = ctx.location_name or "your area"

    sections: list[str] = [
        f"<b>☀️ {html.escape(header)}</b>",
        _section(f"🌅 WEATHER — {html.escape(location)}", _format_weather(ctx)),
        _section("🌎 WORLD", _format_headlines(ctx.world)),
        _section("🍁 CANADA", _format_headlines(ctx.canada)),
        _section("🤖 AI&amp;TECH", _format_headlines(ctx.ai_tech)),
        _section("🔵 AT GOOGLE", _format_headlines(ctx.google)),
        _section("✨ NOTABLE TODAY", _format_notable(ctx.notable)),
    ]

    quote = pick_quote_for(today)
    quote_block = (
        "<b>💭 QUOTE OF THE DAY</b>\n\n"
        f"<i>{html.escape(quote.text)}</i>\n— <b>{html.escape(quote.author)}</b>"
    )
    sections.append(quote_block)

    return f"\n\n{DIVIDER}\n\n".join(sections)


def render_plain_brief(today: date, config: Config) -> str:
    """Fetch sources and render."""
    try:
        ctx = sources.build_context(
            today=today,
            location_name=config.brief.location_name,
            location_lat=config.brief.location_lat,
            location_lon=config.brief.location_lon,
            timezone=config.timezone,
        )
    except Exception:
        log.exception("plain renderer: source fetch failed; using empty context")
        ctx = BriefContext(location_name=config.brief.location_name)
    return render_from_context(today, ctx)


def safe_render_plain_brief(today: date, config: Config) -> str:
    """Wrap ``render_plain_brief`` with a final safety net so even a crash
    in the deterministic path can't leave the user with no morning post.
    Used as the fallback in the scheduled brief's LLMTask.
    """
    try:
        return render_plain_brief(today, config)
    except Exception:
        log.exception("plain renderer crashed; emitting placeholder")
        header = today.strftime("%a %b ") + str(today.day) + today.strftime(", %Y")
        return (
            f"<b>☀️ {header}</b>\n\n"
            "(Brief unavailable today — both the LLM and the plain renderer "
            "failed. See logs.)"
        )
