"""Backend-agnostic LLM CLI subprocess runner.

Both Copilot and Claude expose the same shape:
``<bin> -p <prompt> [<flags>...]`` → text on stdout, non-zero exit on failure.
This module owns the binary lookup, the subprocess invocation, the timeout,
and the failure taxonomy. Per-backend specifics (binary name, env-var
override, the exact flag set) live in ``copilot.py`` / ``claude.py`` as
``LLMBackend`` instances.

Synchronous on purpose. Callers that need to avoid blocking the event
loop should wrap with ``asyncio.to_thread(run_llm, ...)``.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMBackend:
    """Description of a CLI backend.

    ``flags`` are appended after ``-p <prompt>`` on every invocation. Edit
    the flag set in the backend module, not at call sites.
    """

    name: str           # human-readable backend label, also config key
    env_var: str        # env override for the binary path (e.g. COPILOT_BIN)
    default_bin: str    # PATH-resolved name when env_var is unset
    flags: tuple[str, ...]


@dataclass(frozen=True)
class LLMResult:
    """Outcome of an LLM CLI invocation.

    On success: ``ok=True`` and ``stdout`` contains the (already stripped)
    body. On failure: ``ok=False`` and ``error_reason`` is a short
    human-readable description suitable for embedding in a fallback or
    log line.
    """

    ok: bool
    stdout: str = ""
    error_reason: str = ""


def resolve_bin(backend: LLMBackend) -> str:
    """Resolve the binary path. ``backend.env_var`` wins, then PATH, else
    the default name (subprocess will then fail with FileNotFoundError so
    callers see a clear error reason).
    """
    explicit = os.environ.get(backend.env_var)
    if explicit:
        return explicit
    found = shutil.which(backend.default_bin)
    if found:
        return found
    return backend.default_bin


def run_llm(
    backend: LLMBackend,
    prompt: str,
    timeout: int,
    log_label: str,
) -> LLMResult:
    """Invoke ``<bin> -p <prompt> <flags>`` non-interactively. Never raises
    — every failure path is captured into a ``LLMResult(ok=False, ...)``.
    """
    cmd = [resolve_bin(backend), "-p", prompt, *backend.flags]

    log.info("invoking %s/%s (timeout=%ds)", backend.name, log_label, timeout)
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
        log.warning("%s: %s CLI not found on PATH", log_label, backend.name)
        return LLMResult(
            ok=False,
            error_reason=f"{backend.name} CLI not found on PATH",
        )
    except subprocess.TimeoutExpired:
        log.warning("%s: timed out after %ds", log_label, timeout)
        return LLMResult(
            ok=False,
            error_reason=f"{backend.name} timed out after {timeout}s",
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("%s: subprocess crashed", log_label)
        return LLMResult(ok=False, error_reason=f"{type(exc).__name__}: {exc}")

    elapsed = (datetime.now() - started).total_seconds()
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    log.info(
        "%s/%s: exit=%d in %.1fs (stdout=%dB)",
        backend.name, log_label, result.returncode, elapsed, len(stdout),
    )

    if result.returncode != 0:
        snippet = " / ".join(stderr.splitlines()[-3:]) if stderr else "no stderr"
        return LLMResult(
            ok=False,
            error_reason=f"{backend.name} exit {result.returncode}: {snippet}",
        )
    if not stdout:
        return LLMResult(
            ok=False,
            error_reason=f"{backend.name} returned empty output",
        )
    return LLMResult(ok=True, stdout=stdout)
