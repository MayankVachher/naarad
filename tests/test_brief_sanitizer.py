"""Unit tests for brief.sanitizer.sanitize_html."""
from __future__ import annotations

import pytest

from naarad.brief.sanitizer import sanitize_html


def test_passes_whitelisted_tags_unchanged():
    text = "<b>bold</b> and <i>italic</i> and <a href=\"x\">link</a>"
    assert sanitize_html(text) == text


def test_escapes_unknown_tags():
    text = "Copilot leaked a <thinking>plan</thinking> here"
    out = sanitize_html(text)
    assert "<thinking>" not in out
    assert "&lt;thinking&gt;" in out
    assert "&lt;/thinking&gt;" in out


def test_escapes_stray_ampersand_but_keeps_existing_entities():
    text = "Tom &amp; Jerry & Friends &lt;script&gt;"
    out = sanitize_html(text)
    # The unescaped '&' between 'Jerry' and 'Friends' becomes &amp;,
    # but the pre-existing &amp; / &lt; / &gt; entities stay intact.
    assert out == "Tom &amp; Jerry &amp; Friends &lt;script&gt;"


def test_converts_markdown_bold_and_italic():
    text = "Here is **important** and *emphasis* together"
    out = sanitize_html(text)
    assert "<b>important</b>" in out
    assert "<i>emphasis</i>" in out
    assert "**" not in out


def test_does_not_eat_bold_when_only_italic_present():
    # `**foo**` should win over the italic regex (longer match first).
    out = sanitize_html("**only bold**")
    assert out == "<b>only bold</b>"


def test_is_idempotent():
    text = (
        "<b>News</b>: AT&T merger & <thinking>oops</thinking> "
        "with **markdown** and *italics*"
    )
    once = sanitize_html(text)
    twice = sanitize_html(once)
    assert once == twice


def test_handles_empty_string():
    assert sanitize_html("") == ""


@pytest.mark.parametrize(
    "raw",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=y>",
        "<style>body{}</style>",
    ],
)
def test_escapes_dangerous_tags(raw: str):
    out = sanitize_html(raw)
    assert "<script" not in out
    assert "<img" not in out
    assert "<style" not in out
    # Whatever was in the input shows up escaped, not as a live tag.
    assert "&lt;" in out and "&gt;" in out
