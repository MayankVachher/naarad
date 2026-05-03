"""Daily brief generator powered by the GitHub Copilot CLI (non-interactive).

We pre-fetch news (RSS), weather (Open-Meteo), sun times (astral), and
"on this day" (Wikipedia API) in Python — see brief/sources.py — and inject
that as raw material into the Copilot prompt. Copilot's job is to summarize
and rewrite, not to browse, so we run it without --allow-all-urls.

If the subprocess fails or times out, returns a safe fallback message so the
morning brief is never silently missing.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import date, datetime

from naarad import db
from naarad.brief import sources
from naarad.config import Config

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 600  # seconds; copilot can take a while


# Edit this template freely — it's not in config.json on purpose, so you can
# iterate on it as code (with diff/PR review) rather than as a secret blob.
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


def _build_prompt(today: date, config: Config) -> str:
    return PROMPT_TEMPLATE.format(
        date_str=today.strftime("%A, %B %d, %Y"),
        location_name=config.brief.location_name,
        sources_block=_build_sources_block(today, config),
    )


def _copilot_bin() -> str:
    """Resolve the copilot binary. COPILOT_BIN env var wins; otherwise PATH lookup."""
    explicit = os.environ.get("COPILOT_BIN")
    if explicit:
        return explicit
    found = shutil.which("copilot")
    if found:
        return found
    return "copilot"  # let subprocess fail with a clear message


def _fallback_brief(today: date, reason: str) -> str:
    return (
        f"<b>☀️ {today.strftime('%a %b %-d, %Y') if hasattr(today, 'strftime') else today}</b>\n"
        "\n"
        "(Copilot brief unavailable today — falling back to a placeholder.)\n"
        f"<i>{reason}</i>"
    )


def get_daily_brief(today: date, config: Config, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Generate today's brief by invoking `copilot -p <prompt>` non-interactively.

    Returns the brief body (already formatted for Telegram HTML parse mode).
    On failure, returns a fallback string — never raises.
    """
    prompt = _build_prompt(today, config)
    cmd = [
        _copilot_bin(),
        "-p", prompt,
        "--no-color",
        "--log-level", "none",
        "--deny-tool=shell",
        "--deny-tool=write",
        "--disable-builtin-mcps",
        "--no-ask-user",
        "--no-auto-update",
    ]

    log.info("invoking copilot for daily brief (timeout=%ds)", timeout)
    started = datetime.now()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return _fallback_brief(today, "copilot CLI not found on PATH")
    except subprocess.TimeoutExpired:
        return _fallback_brief(today, f"copilot timed out after {timeout}s")
    except Exception as exc:  # noqa: BLE001
        log.exception("copilot invocation crashed")
        return _fallback_brief(today, f"{type(exc).__name__}: {exc}")

    elapsed = (datetime.now() - started).total_seconds()
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    log.info("copilot exit=%d in %.1fs (stdout=%dB)", result.returncode, elapsed, len(stdout))

    if result.returncode != 0:
        snippet = stderr.strip().splitlines()[-3:]
        return _fallback_brief(today, f"copilot exit {result.returncode}: {' / '.join(snippet)}")

    body = stdout.strip()
    if not body:
        return _fallback_brief(today, "copilot returned empty output")

    body = _sanitize_html(body)

    # Header line at top — date in the user's preferred format ("Fri May 1, 2026").
    header = today.strftime("%a %b ") + str(today.day) + today.strftime(", %Y")
    return f"<b>☀️ {header}</b>\n\n{body}"


# --- Sanitization ---------------------------------------------------------

import re as _re

_ALLOWED_TAGS = ("b", "/b", "i", "/i", "u", "/u", "s", "/s",
                 "code", "/code", "pre", "/pre", "a", "/a")


def _sanitize_html(text: str) -> str:
    """Make body safe for Telegram HTML parse mode.

    - Escape stray '&' that isn't already part of a known entity.
    - Escape '<' and '>' that aren't part of a whitelisted tag.
    - Strip Markdown bold (**foo**) and italic (*foo*) — replace with <b>/<i>.
    """
    # 1) Markdown -> HTML.  ** before * so we don't eat the bold ones.
    text = _re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)
    text = _re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)

    # 2) Escape any remaining '&' that isn't already an entity.
    text = _re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+);)", "&amp;", text)

    # 3) Escape '<' / '>' that aren't around a whitelisted tag.
    def _esc_tag(m: "_re.Match[str]") -> str:
        tag = m.group(1).strip().lower().split()[0] if m.group(1).strip() else ""
        if tag in _ALLOWED_TAGS:
            return m.group(0)
        return m.group(0).replace("<", "&lt;").replace(">", "&gt;")

    text = _re.sub(r"<([^<>]*)>", _esc_tag, text)
    return text
