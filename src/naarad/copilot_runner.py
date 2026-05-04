"""Shared GitHub Copilot CLI subprocess invoker.

Both the daily brief and water reminder generators shell out to the
`copilot` CLI with identical flags. This module owns the binary lookup,
the flag set, the subprocess call, and the failure taxonomy so callers
only deal with a small typed result.

Synchronous on purpose. Callers that need to avoid blocking the event
loop should wrap with `asyncio.to_thread(run_copilot, ...)`.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

# Flags applied to every invocation. Edit here, not at the call sites.
DEFAULT_FLAGS: tuple[str, ...] = (
    "--no-color",
    "--log-level", "none",
    "--deny-tool=shell",
    "--deny-tool=write",
    "--disable-builtin-mcps",
    "--no-ask-user",
    "--no-auto-update",
)


@dataclass(frozen=True)
class CopilotResult:
    """Outcome of a copilot CLI invocation.

    On success: ok=True and stdout contains the (already stripped) body.
    On failure: ok=False and error_reason is a short human-readable
    description suitable for embedding in a fallback message.
    """
    ok: bool
    stdout: str = ""
    error_reason: str = ""


def copilot_bin() -> str:
    """Resolve the copilot binary path. COPILOT_BIN env var wins; else PATH."""
    explicit = os.environ.get("COPILOT_BIN")
    if explicit:
        return explicit
    found = shutil.which("copilot")
    if found:
        return found
    return "copilot"  # let subprocess fail with a clear FileNotFoundError


def run_copilot(prompt: str, timeout: int, log_label: str = "copilot") -> CopilotResult:
    """Invoke `copilot -p <prompt>` non-interactively, return a CopilotResult.

    Never raises — every failure path is captured into a CopilotResult
    with ok=False so callers can handle fallbacks uniformly.
    """
    cmd = [copilot_bin(), "-p", prompt, *DEFAULT_FLAGS]

    log.info("invoking %s (timeout=%ds)", log_label, timeout)
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
        log.warning("%s: copilot CLI not found on PATH", log_label)
        return CopilotResult(ok=False, error_reason="copilot CLI not found on PATH")
    except subprocess.TimeoutExpired:
        log.warning("%s: timed out after %ds", log_label, timeout)
        return CopilotResult(ok=False, error_reason=f"copilot timed out after {timeout}s")
    except Exception as exc:  # noqa: BLE001
        log.exception("%s: subprocess crashed", log_label)
        return CopilotResult(ok=False, error_reason=f"{type(exc).__name__}: {exc}")

    elapsed = (datetime.now() - started).total_seconds()
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    log.info(
        "%s: exit=%d in %.1fs (stdout=%dB)",
        log_label, result.returncode, elapsed, len(stdout),
    )

    if result.returncode != 0:
        snippet = " / ".join(stderr.splitlines()[-3:]) if stderr else "no stderr"
        return CopilotResult(
            ok=False,
            error_reason=f"copilot exit {result.returncode}: {snippet}",
        )
    if not stdout:
        return CopilotResult(ok=False, error_reason="copilot returned empty output")
    return CopilotResult(ok=True, stdout=stdout)
