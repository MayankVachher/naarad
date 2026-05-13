"""Anthropic Claude Code CLI backend definition.

Tune via ``CLAUDE_BIN`` env var if the binary isn't on PATH. Auth is via
``claude login`` or ``ANTHROPIC_API_KEY`` — set up before flipping
``config.llm.backend`` to ``"claude"``.

Tool policy
-----------
``--tools "WebSearch,WebFetch"`` whitelists exactly the read-only web
tools the brief prompt invites. Everything else (Bash, Edit, Write,
Read, Glob, Grep, Task, NotebookEdit, MCP tools) stays off, which is
both safer and clearer than a deny-list. ``-p`` implies non-interactive
mode but doesn't itself block tools.

``--strict-mcp-config --mcp-config '{}'`` loads zero MCP servers — the
user's Claude.ai-side ones (Gmail, Calendar, Drive, Playwright, etc.)
get silently auto-discovered otherwise, which costs ~500 ms per server
on startup, bloats Sonnet's tool list with unrelated entries, and (in
the EROFS-protected sandbox) generates write errors trying to persist
project state. ``--strict-mcp-config`` says "use only what's in
``--mcp-config``"; the empty JSON literal says "nothing".

Why not ``--bare``? ``--bare`` switches the CLI into the Agent SDK
codepath which expects ``ANTHROPIC_API_KEY`` (or similar explicit
auth). The default install authenticates via ``claude login`` (OAuth
subscription), and the Agent SDK path can't read that token — calls
fail with "Could not resolve authentication method". The ``--strict-mcp-config``
trick gets us most of bare-mode's startup-time win while leaving the
OAuth auth path intact.

``--max-turns 5`` gives a tight budget for the agentic loop: one
initial response + up to four tool-use cycles. Plenty for "search +
incorporate" without rabbit holes.

CLI flag naming is *mixed* upstream: ``--max-turns`` and
``--output-format`` are kebab-case, ``--tools`` takes a
comma-separated list (no spaces), and ``--allowedTools`` /
``--disallowedTools`` are camelCase. See
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

# Read-only web tools the brief prompt invites. Everything else is off
# by default under --tools.
_ALLOWED_TOOLS = "WebSearch,WebFetch"

# Initial answer + up to (TURN_BUDGET - 1) tool-use cycles. The brief
# prompt also surfaces this number so the model can self-pace.
TURN_BUDGET = 5

CLAUDE = LLMBackend(
    name="claude",
    env_var="CLAUDE_BIN",
    default_bin="claude",
    flags=(
        "--strict-mcp-config",
        # `--mcp-config` validates the JSON against the MCP schema, so a
        # bare `{}` is rejected with "mcpServers: expected record". The
        # explicit empty mcpServers object passes validation cleanly.
        "--mcp-config", '{"mcpServers":{}}',
        "--tools", _ALLOWED_TOOLS,
        "--max-turns", str(TURN_BUDGET),
        "--output-format", "text",
    ),
    extra_flags=_debug_file_flags,
)
