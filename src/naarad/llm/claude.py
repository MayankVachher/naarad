"""Anthropic Claude Code CLI backend definition.

Tune via ``CLAUDE_BIN`` env var if the binary isn't on PATH. Auth is via
``claude login`` or ``ANTHROPIC_API_KEY`` — set up before flipping
``config.llm.backend`` to ``"claude"``.

Tool policy
-----------
We *allow* WebSearch + WebFetch so the morning brief can supplement the
pre-fetched RSS with live lookups (verify a headline, find a more
recent update, fill in context). We *disallow* every code-touching
tool — Bash, Edit, Write, Read, Glob, Grep, Task, NotebookEdit — so a
prompt can't accidentally read or mutate the host. ``-p`` implies
non-interactive mode but doesn't itself block tools.

``--max-turns 5`` gives a tight budget for the agentic loop: one
initial response + up to four tool-use cycles. Plenty for "search +
incorporate" without rabbit holes.

CLI flag naming is *mixed* upstream: ``--max-turns`` and
``--output-format`` are kebab-case, but ``--disallowedTools`` /
``--allowedTools`` are still camelCase. See
https://code.claude.com/docs/en/cli-reference if a future version drifts.
"""
from __future__ import annotations

from naarad.llm.runner import LLMBackend

# Everything that could read or mutate the host. Note: WebSearch +
# WebFetch are deliberately NOT here — the brief prompt invites Claude
# to use them.
_DISALLOWED = " ".join((
    "Bash", "Edit", "Write", "Read",
    "Glob", "Grep", "Task",
    "NotebookEdit",
))

# Initial answer + up to (TURN_BUDGET - 1) tool-use cycles. The brief
# prompt also surfaces this number so the model can self-pace.
TURN_BUDGET = 5

CLAUDE = LLMBackend(
    name="claude",
    env_var="CLAUDE_BIN",
    default_bin="claude",
    flags=(
        "--max-turns", str(TURN_BUDGET),
        "--disallowedTools", _DISALLOWED,
        "--output-format", "text",
    ),
)
