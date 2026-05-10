"""LLM call orchestrator — what every LLM-using feature does the same way.

A feature describes itself via an ``LLMTask``: how to build the prompt,
how to post-process the raw stdout, what to fall back to on any failure
(LLM disabled, subprocess crash, empty output, exception in
post-process). ``render(task, config)`` runs the pipeline.

Rules:
- Lock-step layered kill-switch: ``is_llm_enabled(config)`` is checked
  first; ``False`` skips the subprocess and returns the fallback.
- Subprocess runs in ``asyncio.to_thread`` so the event loop stays
  responsive during a 30-90s Copilot/Claude call.
- Failures are silent at this layer (logged with ``log_label`` for
  diagnosis) — the caller controls what "failure" means by choosing the
  fallback.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from naarad.config import Config
from naarad.llm.runner import LLMBackend, LLMResult, run_llm
from naarad.runtime import is_llm_enabled

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMTask:
    """Per-feature spec for a single LLM call.

    ``prompt_builder`` is invoked lazily so a slow build (RSS fetch for
    the brief) doesn't run when the LLM is disabled. ``post_process`` may
    raise — render() catches and routes to fallback so the user is never
    shown a partially-formed response.
    """

    prompt_builder: Callable[[], str]
    post_process: Callable[[str], str]
    fallback: Callable[[], str]
    timeout: int
    log_label: str


def _resolve_backend(config: Config) -> LLMBackend:
    # Local import keeps the module-import graph acyclic: __init__.py
    # imports render, render imports get_backend.
    from naarad.llm import get_backend
    return get_backend(config.llm.backend)


async def render(task: LLMTask, config: Config) -> str:
    """Run ``task`` through the configured LLM backend, or take the
    fallback path if the LLM isn't usable. Always returns a string;
    never raises.
    """
    if not is_llm_enabled(config):
        log.info("%s: LLM disabled; using fallback", task.log_label)
        return _safe_fallback(task)

    backend = _resolve_backend(config)
    prompt = task.prompt_builder()
    result: LLMResult = await asyncio.to_thread(
        run_llm, backend, prompt, task.timeout, task.log_label
    )
    if not result.ok:
        log.warning(
            "%s: LLM call failed (%s); using fallback",
            task.log_label, result.error_reason,
        )
        return _safe_fallback(task)

    try:
        return task.post_process(result.stdout)
    except Exception:
        log.exception("%s: post_process crashed; using fallback", task.log_label)
        return _safe_fallback(task)


def _safe_fallback(task: LLMTask) -> str:
    """Run the caller-supplied fallback with one final guard so a crash
    in the fallback can't take down the calling job/handler.
    """
    try:
        return task.fallback()
    except Exception:
        log.exception("%s: fallback crashed; emitting empty string", task.log_label)
        return ""
