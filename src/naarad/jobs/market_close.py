"""Market-close cron job: posts close price + day's % change + high/low + volume.

DEPRECATED: cron entry is disabled in deploy/crontab.txt and the in-process
scheduler does not invoke this. Pending replacement by a yfinance-backed
job (Phase 10).
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime

from naarad import db
from naarad.config import load_config
from naarad.jobs._common import (
    check_holiday_or_proceed,
    fetch_quotes,
    fmt_pct,
    fmt_price,
    fmt_volume,
    unavailable_message,
)
from naarad.telegram_api import send_message
from naarad.tickers.eodhd import EODHDClient

log = logging.getLogger(__name__)


def _format_close(quotes) -> str:
    lines = ["📉 <b>Market close</b>"]
    for q in quotes:
        if q.is_empty:
            lines.append(f"  • <code>{q.symbol}</code> — data unavailable")
            continue
        lines.append(
            f"  • <code>{q.symbol}</code>  "
            f"close {fmt_price(q.close)}  "
            f"({fmt_pct(q.change_pct)})  "
            f"hi {fmt_price(q.high)}  "
            f"lo {fmt_price(q.low)}  "
            f"vol {fmt_volume(q.volume)}"
        )
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    config = load_config()
    db.init_db(config.db_path, seed_tickers=config.tickers_default)
    today = datetime.now(config.tz).date()
    client = EODHDClient(config.eodhd.api_key)

    holiday_msg = check_holiday_or_proceed(client, config, today)
    if holiday_msg:
        send_message(config.telegram.token, config.telegram.chat_id, holiday_msg)
        return 0

    symbols = db.list_tickers(config.db_path)
    if not symbols:
        send_message(
            config.telegram.token,
            config.telegram.chat_id,
            "📉 <b>Market close</b>\n  (no tickers tracked — try /ticker add SYMBOL)",
        )
        return 0

    try:
        quotes = fetch_quotes(client, symbols)
        body = _format_close(quotes)
    except Exception as exc:
        log.exception("market close failed")
        body = unavailable_message("Market close", type(exc).__name__)

    try:
        send_message(config.telegram.token, config.telegram.chat_id, body)
    except Exception:
        log.exception("market close send failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
