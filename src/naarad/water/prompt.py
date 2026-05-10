"""Water reminder prompt template.

Single small module so ``water/scheduler.py`` can compose its LLMTask
inline without dragging in the rest of the LLM machinery.
"""
from __future__ import annotations

PROMPT_TEMPLATE = """\
Write ONE single-line water reminder for Mayank (warm tone, dry wit, Toronto-based engineer).

Escalation level {level} (out of 4):
  0 = gentle, casual nudge
  1 = friendly check-in with mild concern
  2 = firm reminder, slightly impatient
  3 = strong demand, almost annoyed
  4 = alarm / emergency tone, all caps allowed

Hard rules:
- Output EXACTLY one line. Plain text. No markdown, no HTML.
- Lead with droplet emojis: 1 droplet at level 0, 2 at level 1, 3 at level 2, 4 at level 3, 🚨 + 💧 at level 4.
- ≤10 words after the emojis.
- Vary the wording — surprise him, don't just say "Time for water".
- No quotes, no preamble, no explanation. Just the line itself.
"""


def build_water_prompt(level: int) -> str:
    """Render the prompt for an escalation level (clamped to 0..4)."""
    return PROMPT_TEMPLATE.format(level=max(0, min(level, 4)))


def first_nonempty_line(text: str) -> str:
    """Take the first non-empty line of ``text``. The model occasionally
    drifts to multi-line; this keeps the reminder a single line.
    """
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""
