# naarad

> **नारद** — the wandering sage of Hindu and Buddhist tradition. He travels between realms delivering news, parables, and the occasional pointed nudge (with a legendary fondness for stirring the pot).
>
> The fit is on the nose: this bot pings you each morning with a brief from the world, escalates its water reminders when ignored, and generally acts as the messenger-sage who's decided your inbox is his beat.

A single-user Telegram bot. Runs on a Raspberry Pi (or your laptop):

- 🌅 **Daily brief** at 06:00 — silent send with a `[☀️ Start day]` button. Tapping it greets you and starts the water reminder chain. If you sleep in, an auto-fallback fires at 11:00. If the bot was offline at 06:00, a catch-up brief fires shortly after the next boot. The brief itself is rendered by the GitHub Copilot CLI from pre-fetched RSS / weather / sun-times / Wikipedia data (see `src/naarad/brief/`).
- 💧 **Water reminders** during waking hours, escalating in interval and tone if ignored. Each reminder line is freshly written by Copilot CLI with a hardcoded fallback. Tap the button (or reply, or `/water`) to confirm — the reminder rewrites itself to "✅ Logged at HH:MM".
- 📈 **Market open / close snapshots** at 09:35 ET and 16:05 ET on weekdays — per-symbol bullet block with price, previous close, change %, and a 🟢/🔴/⚪ status dot. Per-exchange holiday handling (US + TSX); a closed exchange is acknowledged with a single line and its quotes are skipped. `/quote SYMBOL` for on-demand pulls.

## Architecture

A single long-running bot process. Daily brief + water reminders + the 11:00 fallback are all scheduled in-process via python-telegram-bot's `JobQueue`. SQLite holds state (water chain, day rollover, message ids).

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

For Pi deployment with systemd, see the [Deploy on a Raspberry Pi](#deploy-on-a-raspberry-pi) section below.

## Deploy on a Raspberry Pi

These steps assume a fresh Raspberry Pi OS install on a Pi 4 or 5, the Pi already on your network, and a Telegram bot already created via BotFather.

```bash
# 1. Install uv (https://docs.astral.sh/uv/getting-started/installation/)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# 2. Install + sign in to the GitHub Copilot CLI (the brief and reminder
#    generators shell out to `copilot`). Follow the official install guide
#    for your platform; on a Pi you'll typically use the npm install:
#       sudo apt install -y nodejs npm
#       npm install -g @githubnext/github-copilot-cli   # or current package
#    Then sign in with `copilot auth login`. Override the binary path via
#    COPILOT_BIN if it isn't on PATH.

# 3. Clone + setup
git clone <repo> ~/naarad
cd ~/naarad
uv sync
cp config.example.json config.json
chmod 600 config.json
$EDITOR config.json   # token, chat_id, location, etc.

# 4. Smoke-test (you should see "startup validation passed" within a second)
uv run python -m naarad.bot
# Ctrl-C once you see the bot is happy.

# 5. Install the systemd service (substitute placeholders for your user
#    + install path).
sed \
  -e "s|@USER@|$USER|g" \
  -e "s|@HOME@|$HOME|g" \
  -e "s|@INSTALL_DIR@|$HOME/naarad|g" \
  deploy/naarad.service.template | sudo tee /etc/systemd/system/naarad.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now naarad

# 6. Check status + logs
systemctl status naarad
journalctl -u naarad -f              # live tail
tail -f ~/naarad/logs/naarad.log     # the rotating file logger's output
```

Logs go to **two places** by design: journald (via systemd, available through `journalctl -u naarad`) and `logs/naarad.log` (rotating, 5 MB × 3 backups) inside the install directory. Pick whichever is more convenient for the question you're asking.

To pull updates: `cd ~/naarad && git pull && uv sync && sudo systemctl restart naarad`.

## Configuration

Everything lives in `config.json` (gitignored). See `config.example.json` for the full shape.

| Key | Purpose |
|-----|---------|
| `telegram.token` | BotFather token |
| `telegram.chat_id` | Your personal chat ID with the bot |
| `eodhd.api_key` | EODHD API key (real-time quotes + per-exchange holiday calendar) |
| `timezone` | IANA timezone for water + morning schedules |
| `water.active_end` | After this, no reminders until next morning |
| `water.intervals_minutes` | Escalation curve |
| `brief.location_*` | City / lat / lon for the weather + sunrise lookup |
| `morning.start_time` | When the daily brief is generated (default 06:00) |
| `morning.fallback_time` | Auto-start the water chain by this time if you haven't tapped Start (default 11:00) |
| `tickers.enabled` | Compile-time floor for the market jobs + /quote (default true) |
| `tickers.market_timezone` | Timezone the market_open/market_close fire in (default America/New_York) |
| `tickers_default` | Seed tickers (default `GOOGL, NVDA, VFV.TO, VCN.TO`) |
| `db_path` | SQLite file path |

## Commands

| Command | What it does |
|---------|--------------|
| `/water` | Confirm you drank water (resets the chain) |
| `/brief` | Re-run today's morning brief on demand (good for prompt iteration) |
| `/llm on\|off` | Toggle Copilot-generated brief + water lines at runtime |
| `/ticker add SYMBOL` | Track a new ticker (US bare or `.TO` suffix; symbol is validated) |
| `/ticker remove SYMBOL` | Stop tracking |
| `/ticker list` | List tracked tickers |
| `/ticker on\|off` | Runtime kill switch for market jobs + /quote |
| `/quote SYMBOL` | On-demand real-time quote for a single symbol |
| `/status` | Bot health: day-started, next reminder, last drink, level, LLM, tickers |
| `/help` | Command reference |

You can also confirm water by tapping the **💧 Drank water** button on any reminder, or by replying to a reminder with anything.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

Tests cover the water state machine, water scheduler integration, DB layer, brief HTML sanitizer, plain renderer, runtime flag, startup validation, EODHD client + per-exchange holiday handling, in-process market_open / market_close jobs, and Telegram command handlers (`/water`, `/brief`, `/llm`, `/ticker`, `/quote`, `/status`). Ruff lints + sorts imports + catches common bugs.

## License

Personal project, no license.
