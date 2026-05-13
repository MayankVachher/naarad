# naarad

> **नारद** — the wandering sage of Hindu and Buddhist tradition. He travels between realms delivering news, parables, and the occasional pointed nudge (with a legendary fondness for stirring the pot).
>
> The fit is on the nose: this bot pings you each morning with a brief from the world, escalates its water reminders when ignored, and generally acts as the messenger-sage who's decided your inbox is his beat.

A single-user Telegram bot. Runs on a Raspberry Pi (or your laptop). One long-running process; SQLite for state; python-telegram-bot's `JobQueue` for schedules.

## Features

- 🌅 **Daily brief** at 06:00 — silent send with a `[☀️ Start day]` button. RSS / weather / sun-times / Wikipedia, rendered by the configured LLM CLI or a deterministic fallback.
- 💧 **Water reminders** — escalating cadence + tone if ignored. Pace-adjusted: intervals tighten when you fall behind the day's target, and the bot goes quiet once you hit it. See [docs/water.md](docs/water.md).
- 📈 **Market open / close snapshots** at 09:35 / 16:05 ET on weekdays + on-demand `/quote`. Per-exchange holiday handling (US + TSX). See [docs/tickers.md](docs/tickers.md).
- 🤖 **LLM backend** is pluggable (Copilot or Claude) and optional. See [docs/llm.md](docs/llm.md).
- 👋 **First-boot welcome** echoes your config and runs a live LLM smoke check, so misconfig surfaces immediately.

## Install

### Local dev

```bash
git clone <repo> naarad
cd naarad
uv sync
uv run python deploy/configure.py   # token + chat_id + EODHD (optional)
uv run python -m naarad.bot
```

### Raspberry Pi

```bash
git clone <repo> ~/naarad
cd ~/naarad
./deploy/install.sh        # or ./deploy/install.fish
```

Full walkthrough (standard + hardened/ai-agent account): [docs/deploy.md](docs/deploy.md).

## Reference

- **[REFERENCE.md](REFERENCE.md)** — auto-generated config schema + commands table.
- **[docs/](docs/)** — per-feature deep dives (water, llm, tickers, deploy).

Prerequisites: Python 3.12+, [`uv`](https://github.com/astral-sh/uv). Everything else is optional and surfaced by `/status` when off.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests scripts
uv run python scripts/gen_reference.py   # regen REFERENCE.md after touching config or commands
```

`tests/test_reference_drift.py` fails the build if `REFERENCE.md` is out of sync with the source of truth (`src/naarad/config.py` + `src/naarad/commands.py`).

## License

Personal project, no license.
