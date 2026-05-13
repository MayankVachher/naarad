"""Tests for the brief prompt builder + per-backend sources routing.

The Claude path drops the RSS news headlines so Sonnet doesn't anchor
on the feed's choices — WebSearch fills those four sections instead.
The Copilot path keeps the headlines because it has no search tool.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from naarad import db
from naarad.brief import sources
from naarad.brief.prompt import build_prompt
from naarad.brief.sources import BriefContext, Headline
from naarad.config import (
    BriefConfig,
    Config,
    EodhdConfig,
    LLMConfig,
    MorningConfig,
    SchedulesConfig,
    TelegramConfig,
    TickersConfig,
    WaterConfig,
)
from naarad.runtime import set_llm_backend


def _ctx() -> BriefContext:
    return BriefContext(
        location_name="Toronto",
        weather_line="14°C partly cloudy",
        sunrise="06:01",
        sunset="20:34",
        world=[Headline(source="BBC", title="World A")],
        canada=[Headline(source="CBC", title="Canada A")],
        ai_tech=[Headline(source="Verge", title="AI A")],
        google=[Headline(source="Google", title="Google A")],
        notable=["On this day: thing happened"],
    )


def make_config(tmp_path: Path, *, backend: str = "copilot") -> Config:
    return Config(
        telegram=TelegramConfig(token="123:ABCDEFGHIJKLMNOPQRSTUVWXYZ", chat_id=42),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=WaterConfig(),
        brief=BriefConfig(),
        morning=MorningConfig(),
        llm=LLMConfig(enabled=True, backend=backend),
        tickers=TickersConfig(),
        schedules=SchedulesConfig(),
        db_path=str(tmp_path / "state.db"),
    )


# ---- format_for_prompt: news headlines flag ---------------------------------

def test_format_for_prompt_includes_news_headlines_by_default() -> None:
    text = sources.format_for_prompt(_ctx())
    for label in ("World headlines", "Canada headlines", "AI / Tech headlines", "Google-related headlines"):
        assert label in text
    assert "Weather" in text


def test_format_for_prompt_omits_news_headlines_when_disabled() -> None:
    text = sources.format_for_prompt(_ctx(), include_news_headlines=False)
    for label in ("World headlines", "Canada headlines", "AI / Tech headlines", "Google-related headlines"):
        assert label not in text
    # Canonical sections still present.
    assert "Weather" in text
    assert "On this day" in text
    # Header is rewritten to match the new contract.
    assert "REFERENCE DATA" in text
    assert "WebSearch" in text


# ---- build_prompt: per-backend routing --------------------------------------

def _stub_build_context(monkeypatch) -> None:
    """Skip the real RSS/weather/sunrise fetches — tests must stay offline."""
    monkeypatch.setattr(sources, "build_context", lambda **kw: _ctx())


def test_build_prompt_for_copilot_includes_news_headlines(tmp_path: Path, monkeypatch) -> None:
    _stub_build_context(monkeypatch)
    config = make_config(tmp_path, backend="copilot")
    db.init_db(config.db_path)
    out = build_prompt(date(2026, 5, 12), config)
    assert "RAW SOURCE DATA" in out
    assert "World headlines" in out


def test_build_prompt_for_claude_omits_news_headlines(tmp_path: Path, monkeypatch) -> None:
    _stub_build_context(monkeypatch)
    config = make_config(tmp_path, backend="claude")
    db.init_db(config.db_path)
    out = build_prompt(date(2026, 5, 12), config)
    assert "REFERENCE DATA" in out
    for label in ("World headlines", "Canada headlines", "AI / Tech headlines", "Google-related headlines"):
        assert label not in out


def test_build_prompt_runtime_backend_override_wins(tmp_path: Path, monkeypatch) -> None:
    """A live /llm backend swap should change which prompt shape we build."""
    _stub_build_context(monkeypatch)
    config = make_config(tmp_path, backend="copilot")
    db.init_db(config.db_path)
    out_before = build_prompt(date(2026, 5, 12), config)
    assert "REFERENCE DATA" not in out_before  # copilot path

    set_llm_backend(config.db_path, "claude")
    out_after = build_prompt(date(2026, 5, 12), config)
    assert "REFERENCE DATA" in out_after  # claude path now
