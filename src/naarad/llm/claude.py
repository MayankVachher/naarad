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

import logging
import os
from datetime import datetime
from pathlib import Path

from naarad.llm.runner import LLMBackend

log = logging.getLogger(__name__)

# Set NAARAD_LLM_DEBUG=1 to capture each Claude invocation's full
# agentic trace (tool calls, intermediate responses, timings) into a
# per-call file. Files land under ``logs/llm-debug/`` relative to the
# bot's CWD — same dir naarad.log lives in, so rotation/operator
# expectations stay consistent. Override the directory with
# ``NAARAD_LLM_DEBUG_DIR``.
_DEBUG_ENV = "NAARAD_LLM_DEBUG"
_DEBUG_DIR_ENV = "NAARAD_LLM_DEBUG_DIR"


def _debug_file_flags(log_label: str) -> tuple[str, ...]:
    """If ``NAARAD_LLM_DEBUG`` is truthy, return ``--debug-file <path>``
    pointing at a fresh per-call log. Otherwise empty.

    The flag implicitly enables debug mode upstream (per the Claude CLI
    reference) so the file captures the full agentic trace — exactly
    what you need to see *what* Claude searched / fetched / decided.
    """
    if not os.environ.get(_DEBUG_ENV):
        return ()
    base = Path(os.environ.get(_DEBUG_DIR_ENV) or "logs/llm-debug")
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.exception("can't create LLM debug dir %s; skipping --debug-file", base)
        return ()
    # Sortable name: ISO-ish timestamp + label + pid for uniqueness when
    # two jobs fire in the same second (rare but possible).
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = base / f"{stamp}-{log_label}-{os.getpid()}.log"
    log.info("claude debug log: %s", path)
    return ("--debug-file", str(path))

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
    extra_flags=_debug_file_flags,
)
