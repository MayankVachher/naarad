# Tickers

Optional market-data feature. Off automatically when there's no EODHD key — the rest of the bot is unaffected (`/status` shows the off reason).

## Daily snapshots

Two scheduled jobs fire on weekdays in `tickers.market_timezone` (default America/New_York):

- **09:35** — market_open brief.
- **16:05** — market_close brief.

Each brief is a bullet block, one line per ticker: price, previous close, change %, and a 🟢/🔴/⚪ status dot.

Per-exchange holiday handling (US + TSX) is built in. A closed exchange is acknowledged with a single line and its quotes are skipped for that brief.

## Watchlist

- `/ticker add SYMBOL` — track a new ticker. US symbols are bare (`GOOGL`); TSX symbols use the `.TO` suffix (`VFV.TO`). The symbol is validated against EODHD before it's stored.
- `/ticker remove SYMBOL` — stop tracking.
- `/ticker list` — list everything currently tracked.

Seed tickers are set via `tickers_default` in `config.json` and planted into the DB on first init. After that, edits go through `/ticker`.

## Kill switches

- `/ticker on|off` — runtime toggle. Persists across restarts.
- `tickers.enabled: false` — compile-time floor. Permanently disables the market jobs + `/quote`; the runtime toggle is inert.
- Missing `eodhd.api_key` — auto-disables. `/status` reports `off (no EODHD key)`.

## On-demand

`/quote SYMBOL` — real-time quote for a single symbol. Same source as the briefs, no schedule.

## Related config

See [REFERENCE.md](../REFERENCE.md) — the `tickers.*`, `eodhd.*`, `schedules.*`, and `tickers_default` rows.
