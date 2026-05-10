"""LLM CLI integration: subprocess plumbing + per-feature orchestrator.

Layout:
    runner.py    LLMBackend dataclass + run_llm() subprocess wrapper
    copilot.py   COPILOT backend: GitHub Copilot CLI flags
    claude.py    CLAUDE backend: Claude Code CLI flags
    dispatch.py  LLMTask + async render() — the call-site orchestrator

Call sites compose an ``LLMTask`` (prompt builder, post-processor, fallback,
timeout, label) and ``await render(task, config)``. The runtime picks the
backend from ``config.llm.backend`` and consults the kill-switch layers
inside ``render`` so call sites don't repeat that.
"""
from naarad.llm.claude import CLAUDE
from naarad.llm.copilot import COPILOT
from naarad.llm.dispatch import LLMTask, render
from naarad.llm.runner import LLMBackend, LLMResult, resolve_bin, run_llm

BACKENDS: dict[str, LLMBackend] = {
    COPILOT.name: COPILOT,
    CLAUDE.name: CLAUDE,
}


def get_backend(name: str) -> LLMBackend:
    """Return the LLMBackend for ``name`` (``"copilot"`` or ``"claude"``).

    Raises ValueError on an unknown name — config validation should catch
    this at boot, but the runtime check guards against bypassed config.
    """
    if name not in BACKENDS:
        raise ValueError(
            f"unknown LLM backend {name!r}; choose from {sorted(BACKENDS)}"
        )
    return BACKENDS[name]


__all__ = [
    "BACKENDS",
    "CLAUDE",
    "COPILOT",
    "LLMBackend",
    "LLMResult",
    "LLMTask",
    "get_backend",
    "render",
    "resolve_bin",
    "run_llm",
]
