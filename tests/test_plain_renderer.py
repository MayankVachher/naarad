"""Tests for plain (non-LLM) brief renderer + curated quote rotation."""
from __future__ import annotations

from datetime import date

from naarad.brief.plain_renderer import render_from_context
from naarad.brief.quotes import QUOTES, pick_quote_for
from naarad.brief.sources import BriefContext, Headline


def _full_ctx() -> BriefContext:
    return BriefContext(
        location_name="Toronto",
        weather_line="14°C now, partly cloudy, high 17°C / low 6°C",
        sunrise="06:14",
        sunset="19:42",
        world=[
            Headline(source="BBC", title="World story one"),
            Headline(source="Guardian", title="World story two"),
            Headline(source="Reuters", title="World story three"),
            Headline(source="AP", title="Should not appear"),
        ],
        canada=[Headline(source="CBC", title="Canada lead")],
        ai_tech=[
            Headline(source="HN", title="AI thing & stuff"),  # ampersand
            Headline(source="Verge", title="Tech thing two"),
        ],
        google=[Headline(source="blog.google", title="Gemini update")],
        notable=["1957: Sputnik launched", "1990: Hubble first light"],
    )


# ---- pick_quote_for ----------------------------------------------------------

def test_quotes_list_nonempty() -> None:
    assert len(QUOTES) >= 30


def test_pick_quote_is_deterministic() -> None:
    today = date(2026, 5, 4)
    assert pick_quote_for(today) == pick_quote_for(today)


def test_pick_quote_varies_across_days() -> None:
    picks = {pick_quote_for(date(2026, 1, d)) for d in range(1, 32)}
    # Across 31 days against ~40 quotes we expect significant variety.
    assert len(picks) >= 10


def test_quote_has_text_and_author() -> None:
    q = pick_quote_for(date(2026, 5, 4))
    assert q.text and q.author


# ---- render_from_context -----------------------------------------------------

def test_renders_all_sections() -> None:
    out = render_from_context(date(2026, 5, 4), _full_ctx())
    for heading in ("WEATHER", "WORLD", "CANADA", "AI&amp;TECH", "AT GOOGLE", "NOTABLE TODAY", "QUOTE OF THE DAY"):
        assert heading in out


def test_truncates_to_top_three_headlines() -> None:
    out = render_from_context(date(2026, 5, 4), _full_ctx())
    assert "Should not appear" not in out
    assert "World story one" in out
    assert "World story three" in out


def test_escapes_ampersand_in_headline() -> None:
    out = render_from_context(date(2026, 5, 4), _full_ctx())
    assert "AI thing &amp; stuff" in out
    assert "AI thing & stuff" not in out


def test_handles_empty_sections() -> None:
    ctx = BriefContext(location_name="Toronto")
    out = render_from_context(date(2026, 5, 4), ctx)
    assert "no items today" in out
    assert "nothing notable surfaced" in out
    assert "weather unavailable" in out


def test_includes_date_header() -> None:
    out = render_from_context(date(2026, 5, 4), _full_ctx())
    assert "Mon May 4, 2026" in out


def test_includes_quote_with_attribution() -> None:
    today = date(2026, 5, 4)
    q = pick_quote_for(today)
    out = render_from_context(today, _full_ctx())
    assert q.author in out


def test_uses_html_bold_and_italic_tags() -> None:
    out = render_from_context(date(2026, 5, 4), _full_ctx())
    # Sanity: telegram HTML parse mode requires these tags.
    assert "<b>" in out and "</b>" in out
    assert "<i>" in out and "</i>" in out


def test_dividers_separate_sections() -> None:
    out = render_from_context(date(2026, 5, 4), _full_ctx())
    # 8 sections (header + 6 content + quote) → 7 dividers.
    assert out.count("─────────") == 7
