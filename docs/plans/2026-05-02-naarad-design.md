# Naarad — Design

> **Naarad** (नारद): the divine messenger-sage who travels between realms delivering news and (famously) stirring the pot. Single-user Telegram bot that delivers a daily brief, market open/close updates for configured tickers, and escalating water reminders.

## Problem

Owner wants a personal Telegram bot running on a Raspberry Pi that:

1. Posts a daily brief at 08:00 (placeholder for a future Claude-powered run).
2. Posts market-open data (09:35 ET) and market-close data (16:05 ET) for a configurable list of tickers, sourced from EODHD.
3. Sends water reminders during waking hours, escalating in both interval and tone if ignored, resetting on confirmation.

Single user, runs on the Pi, written in Python.

## Architecture

Two cooperating processes sharing a SQLite database:

```
┌─────────────────────────────────────────────────────────┐
│  bot.py (systemd service, always running)               │
│  ─ python-telegram-bot (async)                          │
│  ─ APScheduler (in-process, water reminders only)       │
│  ─ Handlers: /water, /ticker add|remove|list, /status,  │
│              💧 button callback, reply detection         │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
            ┌─────────────┐
            │ state.db    │
            │ (SQLite)    │
            └─────────────┘
                   ▲
                   │
┌──────────────────┴────────────┐  ┌──────────────────────┐
│  cron jobs (separate scripts) │  │  config.json          │
│  ─ daily_brief.py  (08:00)    │  │  (secret, chmod 600)  │
│  ─ market_open.py  (09:35)    │  │  ─ tickers default    │
│  ─ market_close.py (16:05)    │  │  ─ schedules          │
└───────────────────────────────┘  │  ─ water settings     │
                                   └──────────────────────┘
```

**Why split:** Cron jobs are stateless one-shots — easy to debug, survive bot crashes, simple to test. The bot owns water reminders because they're stateful and need to react to user input in real time. Both processes share state via SQLite.

## Components

### Daily brief (08:00 daily, all 7 days)

Cron-invoked `naarad.jobs.daily_brief`. Calls `naarad.claude.brief.get_daily_brief() -> str` (stub returning a placeholder for now). Posts result via Telegram sendMessage. The stub will later be replaced by an actual Claude API call.

### Market data (weekdays only)

- `naarad.jobs.market_open` at 09:35 ET — for each tracked ticker: open price, previous close, % change vs prev close, pre-market price (if available).
- `naarad.jobs.market_close` at 16:05 ET — for each tracked ticker: close price, day's % change, day's high, day's low, volume.

EODHD is the data source. Slight offsets (09:35 / 16:05) avoid stale-data issues at the bell.

**Holiday handling:** Each market job hits EODHD's holiday-calendar endpoint first. If today is a US market holiday, post a single "📅 Market closed today — \<holiday name\>" message and exit.

**Error handling:** If EODHD fails, post "⚠️ Market data unavailable" with the error class. Always send something — silent failure is worse than visible failure.

### Tickers

Configurable via:

- `tickers_default` in `config.json` — used to seed the DB on first run.
- `/ticker add SYMBOL`, `/ticker remove SYMBOL`, `/ticker list` — bot commands that mutate the DB. After first run, the DB is the source of truth.

Stored in SQLite: `tickers(symbol TEXT PRIMARY KEY, added_at TIMESTAMP)`.

### Water reminders

State machine, single-row table:

```sql
water_state(
  last_drink_at      TIMESTAMP,  -- when user last confirmed
  last_reminder_at   TIMESTAMP,  -- when bot last sent a reminder
  level              INTEGER,    -- 0..4, escalation level
  last_msg_id        INTEGER,    -- Telegram message_id of last reminder
  morning_pinged_on  DATE        -- last date the soft morning ping was sent
)
```

**Intervals by level:** `[120, 60, 30, 15, 5]` minutes. After level 4, stays at 5min.

**Tone by level:**

| Level | Message |
|-------|---------|
| 0 | 💧 Time for water |
| 1 | 💧💧 Hey, hydrate |
| 2 | 💧💧💧 You really should drink water |
| 3 | 💧💧💧💧 DRINK. WATER. NOW. |
| 4+ | 🚨 HYDRATION EMERGENCY 🚨 |

Each reminder includes an inline button **"💧 Drank water"**.

**Active window:** 08:00–21:00 ET. Reminders outside this window are skipped.

**Wake-up behavior (Soft Morning, option C):**

- 08:00 daily — soft ping ("☀️ Good morning — water?"). Level stays at 0. No follow-up; level does not escalate from this ping. `morning_pinged_on = today`.
- The escalation chain begins only on the **first confirm of the day**: at that point, `last_drink_at = now`, level = 0, and the next reminder is scheduled at `now + 2h`.
- Subsequent reminders escalate normally if ignored.

**Triggers that reset to level 0 + reschedule for `now + 120min`:**

1. Inline button click on any water reminder.
2. `/water` command.
3. Any message that is a Telegram reply to a reminder (`reply_to_message_id == last_msg_id`).

**Bot startup recovery:** On boot, read `last_drink_at`, `level`, and `morning_pinged_on` from DB. Reschedule the next reminder based on `last_reminder_at + interval(level)`. If today's morning ping hasn't fired yet and we're past 08:00, fire it immediately. If a missed reminder is overdue, fire it immediately (within active window).

## Project layout

```
naarad/
├── pyproject.toml
├── README.md
├── .gitignore                  # ignores config.json, .env, *.db
├── config.example.json         # committed template
├── deploy/
│   ├── naarad.service          # systemd unit
│   └── crontab.txt             # cron entries to install
├── src/
│   └── naarad/
│       ├── __init__.py
│       ├── bot.py              # entrypoint: APScheduler + dispatcher
│       ├── config.py           # load config.json
│       ├── db.py               # SQLite schema + helpers
│       ├── telegram_api.py     # thin sendMessage wrapper for cron scripts
│       ├── water/
│       │   ├── state.py        # state machine logic (pure)
│       │   ├── scheduler.py    # APScheduler wiring
│       │   └── messages.py     # tone-by-level copy
│       ├── tickers/
│       │   ├── commands.py     # /ticker subcommand handlers
│       │   └── eodhd.py        # EODHD client
│       ├── handlers/
│       │   ├── water.py        # /water, button, reply detection
│       │   ├── tickers.py      # /ticker subcommand router
│       │   └── status.py       # /status, /help
│       ├── jobs/
│       │   ├── daily_brief.py  # cron-invokable
│       │   ├── market_open.py  # cron-invokable
│       │   └── market_close.py # cron-invokable
│       └── claude/
│           └── brief.py        # get_daily_brief() stub
└── tests/
    └── test_water_state.py     # pure-logic tests for escalation
```

Cron scripts run as `uv run python -m naarad.jobs.<name>`.

## Config & secrets

Single secrets surface: **`config.json`** (chmod 600, gitignored).

```json
{
  "telegram": {
    "token": "...",
    "chat_id": 123456789
  },
  "eodhd": {
    "api_key": "..."
  },
  "anthropic": {
    "api_key": "..."
  },
  "timezone": "America/New_York",
  "water": {
    "morning_ping": "08:00",
    "active_end": "21:00",
    "intervals_minutes": [120, 60, 30, 15, 5]
  },
  "tickers_default": ["SPY", "QQQ", "AAPL"],
  "schedules": {
    "daily_brief": "08:00",
    "market_open": "09:35",
    "market_close": "16:05"
  }
}
```

`config.example.json` is committed with placeholder values. Real `config.json` is created on the Pi only.

## Testing

Light, surgical:

- `tests/test_water_state.py` — pure-logic tests for the level/interval/active-window math, with mocked "now" times. Covers the soft-morning behavior, the first-confirm-starts-the-chain rule, escalation, ceiling at level 4, and reset triggers.
- Manual smoke testing for cron scripts via a `TEST_CHAT_ID` env var pointing to a throwaway channel.

No tests for: Telegram API wrapping, EODHD calls, config loading. Trust the libraries.

## Deployment

Pi setup:

1. `git clone` on the Pi, `uv sync`.
2. `cp config.example.json config.json`, fill in secrets, `chmod 600 config.json`.
3. `sudo cp deploy/naarad.service /etc/systemd/system/`.
4. `sudo systemctl enable --now naarad`.
5. `crontab -e` and paste from `deploy/crontab.txt`.
6. Verify: `systemctl status naarad`, send `/status` on Telegram.

The systemd unit runs as a non-root user, restarts on failure, logs to journald.

## Out of scope (v1)

- Multi-user support
- `/snooze` command
- Travel / multi-timezone handling
- Web dashboard
- Persistent conversation history with the Claude brief
