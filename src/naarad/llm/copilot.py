"""GitHub Copilot CLI backend definition.

Flags are tuned for non-interactive plain-text generation: no colour, no
log noise, no tool use, no prompts to the user, no auto-update.
"""
from __future__ import annotations

from naarad.llm.runner import LLMBackend

COPILOT = LLMBackend(
    name="copilot",
    env_var="COPILOT_BIN",
    default_bin="copilot",
    flags=(
        "--no-color",
        "--log-level", "none",
        "--deny-tool=shell",
        "--deny-tool=write",
        "--disable-builtin-mcps",
        "--no-ask-user",
        "--no-auto-update",
    ),
)
