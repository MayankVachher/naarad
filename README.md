# naarad

> **नारद** — the wandering sage of Hindu and Buddhist tradition. He travels between realms delivering news, parables, and the occasional pointed nudge (with a legendary fondness for stirring the pot).
>
> The fit is on the nose: this bot pings you each morning with a brief from the world, escalates its water reminders when ignored, and generally acts as the messenger-sage who's decided your inbox is his beat.

A single-user Telegram bot. Runs on a Raspberry Pi (or your laptop):

- 👋 **First-boot welcome** — on the very first run with a fresh `state.db`, the bot sends a single welcome message echoing your config (timezone, schedules, water intervals, LLM backend, watchlist) plus a `[👋 Start day]` button and an `LLM check` line. The check line updates a few seconds later with ✓ and a one-liner from the model, or ✗ + the reason if the LLM isn't reachable — so misconfig surfaces immediately instead of tomorrow morning. Subsequent boots skip the welcome.
- 🌅 **Daily brief** at 06:00 — silent send with a `[☀️ Start day]` button. Tapping it greets you and starts the water reminder chain. If you sleep in, an auto-fallback fires at 11:00. If the bot was offline at 06:00 and comes up before `water.active_end`, a catch-up brief fires shortly after boot. After bedtime there's no catch-up — tomorrow's normal schedule handles it. The brief itself is rendered by the configured LLM CLI (Copilot or Claude) from pre-fetched RSS / weather / sun-times / Wikipedia data (see `src/naarad/brief/`).
- 💧 **Water reminders** during waking hours, escalating in interval and tone if ignored. The first reminder of the day waits ~3 min after you tap the morning brief's [Start day] (configurable via `water.first_reminder_delay_minutes`) — that path assumes you're walking away to brush teeth. The welcome message's [Start day] tap fires the first reminder immediately, since you're actively at the bot. The first reminder uses a gentle, time-agnostic opener; subsequent reminders escalate normally. Tap the button (or reply, or `/water`) to confirm — the reminder rewrites itself to "✅ Glass #N logged at HH:MM" and the reply tells you your running glass count, your pace status (`🎯 target hit` / `🟢 on track` / `⚠️ at risk` / `🚨 behind by ~N glasses`), and the absolute time of the next reminder. Cadence is **pace-adjusted**: if you're behind the `water.daily_target_glasses` rate at any given hour, intervals tighten linearly down to `water.pace_floor` × the base (default 0.3, so a 120-min base becomes at most ~36 min when you're far behind). `/status` shows progress vs. target ("Glasses today: 3 / 8 — behind by ~1.4 (intervals tightened)"). Set `daily_target_glasses: 0` to disable pace adjustment entirely.
- 📈 **Market open / close snapshots** at 09:35 ET and 16:05 ET on weekdays — per-symbol bullet block with price, previous close, change %, and a 🟢/🔴/⚪ status dot. Per-exchange holiday handling (US + TSX); a closed exchange is acknowledged with a single line and its quotes are skipped. `/quote SYMBOL` for on-demand pulls.

## Architecture

A single long-running bot process. Daily brief + water reminders + the 11:00 fallback are all scheduled in-process via python-telegram-bot's `JobQueue`. SQLite holds state (water chain, day rollover, message ids).

## Prerequisites

- Python **3.12+**
- [`uv`](https://github.com/astral-sh/uv) for dependency management

Optional:

- An **LLM CLI** on `PATH`, signed in. Pick one via `config.llm.backend`:
  - `"copilot"` (default) — GitHub Copilot CLI, `copilot auth login`. Override the binary path via `COPILOT_BIN`.
  - `"claude"` — Anthropic [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) CLI, `claude login` or set `ANTHROPIC_API_KEY`. Override the binary path via `CLAUDE_BIN`.

  When present, the brief and water reminders are written by the LLM; without it the brief renders deterministically from the same RSS / weather / sun / Wikipedia data, and water reminders use a hardcoded escalating tone table. Set `config.llm.enabled: false` to permanently disable LLM features regardless of which CLI is installed.
- An **EODHD API key** for `/quote` and the market open/close briefs. Without it those features stay dormant; the rest of the bot is unaffected (see `/status` for the off-reason).

## Setup (local dev)

```bash
git clone <repo> naarad
cd naarad
uv sync
uv run python deploy/configure.py   # interactive: token + chat_id + EODHD
uv run python -m naarad.bot
```

For Pi deployment, see [Deploy on a Raspberry Pi](#deploy-on-a-raspberry-pi).

## Deploy on a Raspberry Pi

Fresh Raspberry Pi OS, Pi already on your network, Telegram bot already created via BotFather.

```bash
git clone <repo> ~/naarad
cd ~/naarad
./deploy/install.sh        # or ./deploy/install.fish if you use fish
```

The script is idempotent — re-run after editing config or pulling updates. It will:

1. install `uv` if missing
2. `uv sync`
3. run `deploy/configure.py` if there's no `config.json` yet — it asks for the BotFather token, auto-detects your `chat_id` by polling Telegram for a message you send to the bot, takes an optional EODHD key, and prompts for timezone (defaulting to `/etc/timezone` if available). Re-run it later with `uv run python deploy/configure.py` to change any of these — existing values appear as defaults so Enter keeps them. Location lat/lon stays at the example values; edit `config.json` afterwards if you live outside Toronto.
4. smoke-test the bot (looks for "startup validation passed")
5. `sed` the systemd unit template into `/etc/systemd/system/naarad.service`, then `daemon-reload` + `enable` + `restart`
6. print status + log paths

**Optional:** install an LLM CLI *before* running the script if you want LLM-written briefs and water reminders. Either:
- GitHub Copilot CLI (`copilot auth login`) — default; or
- Claude Code CLI (`claude login` or `ANTHROPIC_API_KEY`) + set `"backend": "claude"` in `config.json`'s `llm` block.

Without either, the bot uses the deterministic plain renderer and hardcoded reminder tones.

Logs go to **two places** by design: journald (`journalctl -u naarad`) and `logs/naarad.log` (rotating, 5 MB × 3) inside the install directory.

To pull updates: `git pull && uv sync && sudo systemctl restart naarad`.

To remove: `./deploy/uninstall.sh` (or `./deploy/uninstall.fish`) — stops, disables, and removes the systemd unit. Repo, config, DB, and logs are left in place.

**Running under a hardened account** (e.g. the `ai-agent` user from [pi-hardening](https://github.com/MayankVachher/pi-hardening)) — single command, no shell switching:

```bash
sudo bash deploy/install-all-hardened.sh
```

Prompts upfront for the AI user, repo URL, and LLM backend. Then runs the whole flow: clones into `/srv/<ai_user>/naarad`, installs `uv` + deps as the AI user, installs and auths Copilot or Claude CLI inside the sandbox, runs `configure.py` for your token + chat_id, smoke-tests, and writes the hardened systemd unit (`User=<ai_user>`, tight `ReadWritePaths`, sandbox preserved). The interactive bits (device-code LLM auth, chat_id detection by you sending a message) pass through to your terminal because each AI-account command runs via `sudo -u <ai_user> -H -i bash -c …`.

If you'd rather run the two phases manually (Phase 2 as the AI user, then Phase 3 to install the unit), `deploy/install-hardened.sh` does just Phase 3 — useful if you've already got the install dir set up and just want to (re)write the systemd unit.

<details>
<summary>Manual install (no script)</summary>

```bash
# 1. uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# 2. Deps + config
uv sync
cp config.example.json config.json
chmod 600 config.json
$EDITOR config.json   # token, chat_id, location, etc.

# 3. Smoke-test (expect "startup validation passed" within a second; Ctrl-C)
uv run python -m naarad.bot

# 4. systemd unit
sed \
  -e "s|@USER@|$USER|g" \
  -e "s|@HOME@|$HOME|g" \
  -e "s|@INSTALL_DIR@|$HOME/naarad|g" \
  deploy/naarad.service.template | sudo tee /etc/systemd/system/naarad.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now naarad
```
</details>

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
| `water.first_reminder_delay_minutes` | Grace between [Start day] and the first reminder (default 5) |
| `water.daily_target_glasses` | Target glass count per day; drives `/status` progress + pace-adjusted intervals (default 8; set 0 to disable) |
| `water.pace_floor` | Minimum interval multiplier when behind pace (default 0.3; 0–1 range) |
| `brief.location_*` | City / lat / lon for the weather + sunrise lookup |
| `morning.start_time` | When the daily brief is generated (default 06:00) |
| `morning.fallback_time` | Auto-start the water chain by this time if you haven't tapped Start (default 11:00) |
| `llm.enabled` | Compile-time floor for LLM features (default true) |
| `llm.backend` | Which CLI to shell out to: `"copilot"` (default) or `"claude"` |
| `tickers.enabled` | Compile-time floor for the market jobs + /quote (default true) |
| `tickers.market_timezone` | Timezone the market_open/market_close fire in (default America/New_York) |
| `tickers_default` | Seed tickers (default `GOOGL, NVDA, VFV.TO, VCN.TO`) |
| `db_path` | SQLite file path |

## Commands

| Command | What it does |
|---------|--------------|
| `/water` | Confirm you drank water (resets the chain) |
| `/brief` | Re-run today's morning brief on demand (good for prompt iteration) |
| `/llm on\|off` | Toggle LLM-generated brief + water lines at runtime |
| `/llm test` | Fire a one-shot prompt at the configured backend; reports ✓ + the model's line or ✗ + the reason |
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
