# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Single-user Telegram bot (`naarad`) that runs one long-lived process: daily brief at 06:00, escalating water reminders during the day, market open/close snapshots on weekdays. Built for a Raspberry Pi but runs anywhere. State in SQLite; schedules via `python-telegram-bot`'s `JobQueue`. Targets Python 3.12+.

## Commands

```bash
uv sync                                  # install deps (incl. dev group)
uv run python -m naarad.bot              # run the bot (needs config.json)
uv run pytest                            # full test suite
uv run pytest tests/test_water_scheduler.py::test_X   # single test
uv run ruff check src tests scripts      # lint
uv run python scripts/gen_reference.py   # regenerate REFERENCE.md
uv run python deploy/configure.py        # interactively fill config.json
NAARAD_SMOKE_TEST=1 uv run python -m naarad.bot  # validate config + exit before polling
```

`tests/test_reference_drift.py` fails CI if `REFERENCE.md` is out of sync with `src/naarad/config.py` + `src/naarad/commands.py`. Re-run `gen_reference.py` after touching either.

## Architecture

### Single source of truth for state

- **`config.json`** (gitignored, chmod 600) — secrets + tunables, validated by pydantic in `src/naarad/config.py`. `load_config()` raises on a missing file or invalid shape.
- **`state.db`** (SQLite, autocommit + WAL) — `tickers`, `water_state` (single-row state machine), and a generic `settings` k/v table. Schema migrations are versioned (`SCHEMA_VERSION` in `src/naarad/db.py`) and wrapped in an explicit transaction.

### Layered feature gates (`src/naarad/runtime.py`)

LLM and tickers each have **three layers** that must all be on:
1. **Config floor** (`llm.enabled` / `tickers.enabled`) — compile-time disable; the runtime toggle is inert when off.
2. **Resource check** — tickers also require a non-empty EODHD API key.
3. **Runtime DB flag** (`settings.llm_enabled` / `settings.tickers_enabled`) — flipped by `/llm` and `/ticker on|off`; survives restart.

When any layer is off, consumers route to a fallback (LLM → plain renderer / hardcoded tones; tickers → silent skip). `tickers_off_reason()` returns *why* tickers are off so `/status` can explain it. Never bypass `is_llm_enabled` / `is_tickers_enabled` — they're the single gate.

### LLM dispatch (`src/naarad/llm/`)

Features describe a call via `LLMTask(prompt_builder, post_process, fallback, timeout, log_label)` and call `render(task, config)`. `dispatch.py` enforces the kill-switch, runs the subprocess in `asyncio.to_thread`, and **always returns a string, never raises** — failures (LLM disabled, subprocess crash, empty output, post-process exception, even a crash inside the fallback) are logged and degraded silently. Backends are CLIs (`copilot`, `claude`) shelled out via `runner.py`; binary path overridable with `COPILOT_BIN` / `CLAUDE_BIN`. `NAARAD_LLM_DEBUG=1` writes per-call traces to `logs/llm-debug/`.

### Water reminder state machine (`src/naarad/water/state.py`)

Pure logic, no I/O. `next_action(state, now, config)` → `Idle | Sleep | Reminder(level)`. The scheduler in `water/scheduler.py` loops: read state → next_action → dispatch → `apply_*` → persist. **Lock discipline**: the `water_lock` (in `app.bot_data`) is held during state transitions only — *not* across the LLM subprocess. After rendering a reminder, the scheduler re-acquires the lock and re-checks state, so a `/water` confirm during the ~45s render correctly discards the now-stale reminder. The chain is gated by `day_started_on` (set by the Start-day button tap or the 11:00 fallback); before that it's `Idle`. Intervals are pace-adjusted against `daily_target_glasses` (see `docs/water.md`).

### Wiring (`src/naarad/bot.py`)

`build_application()`:
1. Runs `validate_startup(config)` — fatal on bad token/chat_id/db_path; warns on missing LLM CLI or EODHD key.
2. Initializes DB (with schema migrations + seed tickers).
3. Builds the PTB `Application`, registers command + `CallbackQueryHandler`s for inline-button panels (`/water`, `/llm`, `/ticker`, `/status`, welcome, Start-day).
4. In `post_init`: calls `set_my_commands`, then kicks off the water / morning / ticker schedulers (each owns its named `JobQueue` job).

`main()` honors `NAARAD_SMOKE_TEST=1` so install scripts can validate config without firing the welcome message.

### Brief rendering (`src/naarad/brief/`)

Sources (RSS, weather, sunrise, Wikipedia) are pre-fetched and passed both to the LLM prompt (`prompt.py`) and to the deterministic `plain_renderer.py` fallback. `sanitizer.py` cleans LLM output. Weather is a *signal* for the LLM, not canonical text it's allowed to copy verbatim (see recent commit `be1ae19`).

### Per-exchange market clock (`src/naarad/jobs/`, `src/naarad/tickers/eodhd.py`)

Open/close jobs fire on `tickers.market_timezone` (default `America/New_York`), independent of the user-facing `timezone` used for water/morning. EODHD provides per-exchange holiday calendars so US and TSX holidays are handled separately.

## Conventions

- **Datetimes in SQLite are stored as ISO8601 TEXT (with offset)**, dates as ISO TEXT — the built-in `TIMESTAMP` adapter loses tzinfo. Helpers in `db.py` (`_to_iso_dt` etc.) enforce that aware datetimes are required.
- **Late imports** inside `naarad.runtime` and `naarad.llm.dispatch` are intentional — they break a real module-import cycle between `runtime` ↔ `llm`. Don't hoist them.
- **Ruff** is configured for `E/W/F/I/UP/B` with `E501` (line length) and `B008` (Field default_factory) ignored.
- **Pytest** uses `asyncio_mode = "auto"`; `freezegun` handles time-travel in state-machine tests.
- Recent UX direction (commits `0fe643f`, `1ff1444`, `63c3fd3`): commands surface inline-button panels rather than chatty replies; water confirm is single-message with an overwrite reminder.

## Where to look first

- Touching a feature gate or runtime flag → `src/naarad/runtime.py`.
- Adding a command → register in `bot.py`, add a handler under `src/naarad/handlers/`, add a row to `COMMAND_REFERENCE` in `src/naarad/commands.py`, regenerate `REFERENCE.md`. Also add to `BOT_COMMANDS` in `bot.py` if it should appear in Telegram's `/` autocomplete.
- Adding a config field → `src/naarad/config.py` (pydantic model + validator), regenerate `REFERENCE.md`, update `config.example.json`.
- Changing schedule timing → `src/naarad/water/scheduler.py`, `src/naarad/morning/scheduler.py`, or `src/naarad/jobs/scheduler.py`; each owns its named JobQueue job.
- Deep dives: `docs/water.md`, `docs/llm.md`, `docs/tickers.md`, `docs/deploy.md`. Schema/commands table: `REFERENCE.md` (auto-generated).
