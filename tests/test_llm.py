"""Tests for the LLM call orchestrator + backend registry.

Pure-logic coverage; never spawns a real ``copilot``/``claude`` subprocess.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from naarad import db
from naarad.config import (
    BriefConfig,
    Config,
    EodhdConfig,
    LLMConfig,
    MorningConfig,
    SchedulesConfig,
    TelegramConfig,
    WaterConfig,
)
from naarad.llm import (
    BACKENDS,
    CLAUDE,
    COPILOT,
    LLMBackend,
    LLMResult,
    LLMTask,
    get_backend,
    render,
    resolve_bin,
    run_llm,
)


def make_config(
    tmp_path: Path, *, llm_enabled: bool = True, backend: str = "copilot"
) -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        llm=LLMConfig(enabled=llm_enabled, backend=backend),
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


# ---- Backend registry --------------------------------------------------------

def test_backends_includes_both_copilot_and_claude():
    assert "copilot" in BACKENDS and "claude" in BACKENDS
    assert BACKENDS["copilot"] is COPILOT
    assert BACKENDS["claude"] is CLAUDE


def test_get_backend_returns_correct_instance():
    assert get_backend("copilot") is COPILOT
    assert get_backend("claude") is CLAUDE


def test_get_backend_raises_on_unknown():
    with pytest.raises(ValueError, match="unknown LLM backend"):
        get_backend("openai")


def test_copilot_flags_disable_tools_and_prompts():
    """Sanity check on the canonical Copilot flag set; if any of these
    drift, an LLM call could open a shell or write files."""
    flags = COPILOT.flags
    assert "--deny-tool=shell" in flags
    assert "--deny-tool=write" in flags
    assert "--no-ask-user" in flags


def test_claude_flags_lock_to_single_turn_no_tools():
    """Claude Code CLI flag naming is mixed upstream: --max-turns and
    --output-format are kebab-case but --disallowedTools is camelCase.
    See https://code.claude.com/docs/en/cli-reference.
    """
    flags = CLAUDE.flags
    assert "--max-turns" in flags
    assert "--output-format" in flags
    assert "--disallowedTools" in flags
    # Disallowed list should at least include the dangerous ones.
    idx = flags.index("--disallowedTools")
    disallowed = flags[idx + 1]
    for t in ("Bash", "Edit", "Write"):
        assert t in disallowed


# ---- resolve_bin -------------------------------------------------------------

def test_resolve_bin_honours_env_var(monkeypatch):
    monkeypatch.setenv("COPILOT_BIN", "/custom/path/copilot")
    assert resolve_bin(COPILOT) == "/custom/path/copilot"


def test_resolve_bin_falls_back_to_default_when_missing(monkeypatch):
    """If env var is unset and the binary isn't on PATH, return the
    default name so subprocess fails with a clear FileNotFoundError."""
    monkeypatch.delenv("COPILOT_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert resolve_bin(COPILOT) == "copilot"


# ---- render() orchestrator --------------------------------------------------

@pytest.mark.asyncio
async def test_render_takes_fallback_when_llm_disabled(tmp_path):
    """LLM disabled at runtime → fallback path runs, prompt builder doesn't."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)
    db.set_setting(config.db_path, "llm_enabled", "0")

    prompt_called = []
    fallback_called = []
    task = LLMTask(
        prompt_builder=lambda: (prompt_called.append(1), "PROMPT")[1],
        post_process=lambda raw: f"processed:{raw}",
        fallback=lambda: (fallback_called.append(1), "FALLBACK")[1],
        timeout=10,
        log_label="t",
    )

    out = await render(task, config)

    assert out == "FALLBACK"
    assert prompt_called == []   # disabled means we never even build the prompt
    assert fallback_called == [1]


@pytest.mark.asyncio
async def test_render_runs_post_process_on_success(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    monkeypatch.setattr(
        "naarad.llm.dispatch.run_llm",
        lambda backend, prompt, timeout, log_label: LLMResult(
            ok=True, stdout="raw output"
        ),
    )

    task = LLMTask(
        prompt_builder=lambda: "PROMPT",
        post_process=lambda raw: f"processed:{raw}",
        fallback=lambda: "should-not-be-used",
        timeout=10,
        log_label="t",
    )
    out = await render(task, config)
    assert out == "processed:raw output"


@pytest.mark.asyncio
async def test_render_uses_fallback_on_run_llm_failure(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    monkeypatch.setattr(
        "naarad.llm.dispatch.run_llm",
        lambda backend, prompt, timeout, log_label: LLMResult(
            ok=False, error_reason="boom"
        ),
    )

    task = LLMTask(
        prompt_builder=lambda: "PROMPT",
        post_process=lambda raw: f"processed:{raw}",
        fallback=lambda: "FALLBACK",
        timeout=10,
        log_label="t",
    )
    out = await render(task, config)
    assert out == "FALLBACK"


@pytest.mark.asyncio
async def test_render_uses_fallback_when_post_process_raises(tmp_path, monkeypatch):
    """A post_process that crashes must not bubble up to the caller."""
    config = make_config(tmp_path)
    db.init_db(config.db_path)

    monkeypatch.setattr(
        "naarad.llm.dispatch.run_llm",
        lambda backend, prompt, timeout, log_label: LLMResult(
            ok=True, stdout="raw"
        ),
    )

    def _crash(raw: str) -> str:
        raise RuntimeError("post-process exploded")

    task = LLMTask(
        prompt_builder=lambda: "PROMPT",
        post_process=_crash,
        fallback=lambda: "FALLBACK",
        timeout=10,
        log_label="t",
    )
    out = await render(task, config)
    assert out == "FALLBACK"


@pytest.mark.asyncio
async def test_render_returns_empty_when_fallback_also_crashes(tmp_path, monkeypatch):
    """Last-resort guard so a buggy fallback can't take down the caller."""
    config = make_config(tmp_path, llm_enabled=False)  # forces fallback path
    db.init_db(config.db_path)

    def _crash() -> str:
        raise RuntimeError("fallback exploded")

    task = LLMTask(
        prompt_builder=lambda: "PROMPT",
        post_process=lambda raw: raw,
        fallback=_crash,
        timeout=10,
        log_label="t",
    )
    out = await render(task, config)
    assert out == ""


@pytest.mark.asyncio
async def test_render_uses_fallback_when_llm_check_raises(tmp_path, monkeypatch):
    """A corrupt/locked SQLite making is_llm_enabled raise must not
    propagate; render is documented to never raise."""
    config = make_config(tmp_path)

    def _boom(config, db_path=None):
        raise RuntimeError("DB read crashed")

    monkeypatch.setattr("naarad.llm.dispatch.is_llm_enabled", _boom)

    task = LLMTask(
        prompt_builder=lambda: "P",
        post_process=lambda r: r,
        fallback=lambda: "FALLBACK",
        timeout=10,
        log_label="t",
    )
    out = await render(task, config)
    assert out == "FALLBACK"


@pytest.mark.asyncio
async def test_render_picks_backend_from_config(tmp_path, monkeypatch):
    """``config.llm.backend`` selects which LLMBackend run_llm is called with."""
    config = make_config(tmp_path, backend="claude")
    db.init_db(config.db_path)

    seen_backends: list[LLMBackend] = []

    def _capture(backend, prompt, timeout, log_label):
        seen_backends.append(backend)
        return LLMResult(ok=True, stdout="ok")

    monkeypatch.setattr("naarad.llm.dispatch.run_llm", _capture)

    task = LLMTask(
        prompt_builder=lambda: "P",
        post_process=lambda r: r,
        fallback=lambda: "F",
        timeout=10,
        log_label="t",
    )
    await render(task, config)
    assert seen_backends == [CLAUDE]


# ---- run_llm --------------------------------------------------------------

def test_run_llm_returns_failure_on_filenotfound(monkeypatch):
    """If the binary doesn't exist, run_llm should return a clean
    LLMResult instead of raising."""
    def _missing(*args, **kwargs):
        raise FileNotFoundError(args[0][0] if args else "bin")
    monkeypatch.setattr("subprocess.run", _missing)

    fake = LLMBackend(
        name="fake", env_var="FAKE_BIN", default_bin="fake", flags=()
    )
    result = run_llm(fake, "prompt", timeout=1, log_label="t")
    assert result.ok is False
    assert "not found" in result.error_reason


def test_run_llm_returns_failure_on_empty_stdout(monkeypatch):
    """Empty output is treated as failure so the fallback fires."""
    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _Result())

    fake = LLMBackend(
        name="fake", env_var="FAKE_BIN", default_bin="fake", flags=()
    )
    result = run_llm(fake, "prompt", timeout=1, log_label="t")
    assert result.ok is False
    assert "empty" in result.error_reason
