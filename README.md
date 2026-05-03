# naarad

> **नारद** — the divine messenger-sage who travels between realms delivering news (and famously stirring the pot).

A single-user Telegram bot that runs on a Raspberry Pi:

- 📰 **Daily brief** at 08:00 (currently a stub — slot for a future Claude run)
- 📈 **Market open** snapshot at 09:35 ET (weekdays) for configured tickers
- 📉 **Market close** snapshot at 16:05 ET (weekdays) for the same tickers
- 💧 **Water reminders** during waking hours, escalating in interval and tone if ignored

## Architecture

A long-running bot process (water reminders, command handlers) plus three cron-invoked one-shot scripts (daily brief, market open, market close), sharing a SQLite database.

See [`docs/plans/2026-05-02-naarad-design.md`](docs/plans/2026-05-02-naarad-design.md) for the full design.

## Setup (Raspberry Pi)

```bash
# 1. Clone
git clone <repo> naarad
cd naarad

# 2. Install deps with uv
uv sync

# 3. Create config (treat as a secret)
cp config.example.json config.json
chmod 600 config.json
$EDITOR config.json   # fill in tokens and chat_id

# 4. Install systemd service
sudo cp deploy/naarad.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now naarad

# 5. Install cron entries
crontab -e
# paste contents of deploy/crontab.txt

# 6. Verify
systemctl status naarad
# then in Telegram: /status
```

## Configuration

Everything lives in `config.json` (gitignored). See `config.example.json` for the full shape.

| Key | Purpose |
|-----|---------|
| `telegram.token` | BotFather token |
| `telegram.chat_id` | Your personal chat ID with the bot |
| `eodhd.api_key` | EODHD API key for market data |
| `anthropic.api_key` | Anthropic API key (used by the daily-brief stub later) |
| `timezone` | IANA timezone for all schedules |
| `water.morning_ping` | When the soft morning ping fires |
| `water.active_end` | After this, no reminders until next morning |
| `water.intervals_minutes` | Escalation curve |
| `tickers_default` | Seed tickers on first run |
| `schedules.*` | Cron times — must match `deploy/crontab.txt` |

## Commands

| Command | What it does |
|---------|--------------|
| `/water` | Confirm you drank water (resets the chain) |
| `/ticker add SYMBOL` | Track a new ticker |
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

Tests cover the water state machine logic only.

## License

Personal project, no license.
