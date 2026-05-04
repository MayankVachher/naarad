# naarad

> **नारद** — the divine messenger-sage who travels between realms delivering news (and famously stirring the pot).

A single-user Telegram bot. Runs on a Raspberry Pi (or your laptop):

- 🌅 **Daily brief** at 06:00 — silent send with a `[☀️ Start day]` button. Tapping it greets you and starts the water reminder chain. If you sleep in, an auto-fallback fires at 11:00. The brief itself is rendered by the GitHub Copilot CLI from pre-fetched RSS / weather / sun-times / Wikipedia data (see `src/naarad/brief/`).
- 💧 **Water reminders** during waking hours, escalating in interval and tone if ignored. Each reminder line is freshly written by Copilot CLI with a hardcoded fallback. Tap the button (or reply, or `/water`) to confirm — the reminder rewrites itself to "✅ Logged at HH:MM".
- 📈 **Market open / close snapshots** — scaffolded but disabled. Pending migration from EODHD to yfinance.

## Architecture

A single long-running bot process. Daily brief + water reminders + the 11:00 fallback are all scheduled in-process via python-telegram-bot's `JobQueue`. SQLite holds state (water chain, day rollover, message ids).

The `deploy/crontab.txt` entries exist as a Pi-friendly alternative for hosts that run only the long-poll loop with cron handling the brief separately, but they're commented out by default.

See [`docs/plans/2026-05-02-naarad-design.md`](docs/plans/2026-05-02-naarad-design.md) for the original design (some details have evolved — see `plan.md` in the session for current state).

## Prerequisites

- Python **3.12+**
- [`uv`](https://github.com/astral-sh/uv) for dependency management
- The **GitHub Copilot CLI** (`copilot`) on `PATH`, signed in. Used by the brief and water reminder generators. Override the binary path via `COPILOT_BIN` env var if it isn't on `PATH`.

## Setup

```bash
# 1. Clone
git clone <repo> naarad
cd naarad

# 2. Install deps with uv
uv sync

# 3. Create config (treat as a secret)
cp config.example.json config.json
chmod 600 config.json
$EDITOR config.json   # fill in token + chat_id

# 4. Run
uv run python -m naarad.bot
```

For Pi deployment with systemd:

```bash
sudo cp deploy/naarad.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now naarad
```

## Configuration

Everything lives in `config.json` (gitignored). See `config.example.json` for the full shape.

| Key | Purpose |
|-----|---------|
| `telegram.token` | BotFather token |
| `telegram.chat_id` | Your personal chat ID with the bot |
| `eodhd.api_key` | EODHD API key (unused while market jobs are disabled — keep for now) |
| `timezone` | IANA timezone for all schedules |
| `water.active_end` | After this, no reminders until next morning |
| `water.intervals_minutes` | Escalation curve |
| `brief.location_*` | City / lat / lon for the weather + sunrise lookup |
| `morning.start_time` | When the daily brief is generated (default 06:00) |
| `morning.fallback_time` | Auto-start the water chain by this time if you haven't tapped Start (default 11:00) |
| `tickers_default` | Seed tickers (currently dormant) |
| `db_path` | SQLite file path |

## Commands

| Command | What it does |
|---------|--------------|
| `/water` | Confirm you drank water (resets the chain) |
| `/ticker add SYMBOL` | Track a new ticker (dormant until yfinance lands) |
| `/ticker remove SYMBOL` | Stop tracking |
| `/ticker list` | List tracked tickers |
| `/status` | Bot health + last-drink time |
| `/help` | Command reference |

You can also confirm water by tapping the **💧 Drank water** button on any reminder, or by replying to a reminder with anything.

## Development

```bash
uv sync
uv run pytest
```

Tests cover the water state machine, water scheduler integration, and DB layer (32 tests across `tests/`).

## License

Personal project, no license.
