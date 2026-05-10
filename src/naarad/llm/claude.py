"""Anthropic Claude Code CLI backend definition.

Flags lock the CLI into a single-shot text-only response (no agentic
loops, no tool use). Tune via ``CLAUDE_BIN`` env var if the binary
isn't on PATH.

Auth is via ``claude login`` or ``ANTHROPIC_API_KEY`` — set up before
flipping ``config.llm.backend`` to ``"claude"``.
"""
from __future__ import annotations

from naarad.llm.runner import LLMBackend

# Tools the CLI knows about; we disallow them all so a brief or reminder
# request can't accidentally trigger a tool call. ``-p`` already implies
# non-interactive mode but doesn't itself block tools.
_DISALLOWED = ",".join((
    "Bash", "Edit", "Write", "Read",
    "Glob", "Grep", "Task",
    "WebFetch", "WebSearch",
    "NotebookEdit",
))

CLAUDE = LLMBackend(
    name="claude",
    env_var="CLAUDE_BIN",
    default_bin="claude",
    flags=(
        "--max-turns", "1",
        "--disallowed-tools", _DISALLOWED,
        "--output-format", "text",
    ),
)
