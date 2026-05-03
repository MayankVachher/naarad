"""Market-open cron job: posts open price + previous close + % change for all tickers."""
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
    unavailable_message,
)
from naarad.telegram_api import send_message
from naarad.tickers.eodhd import EODHDClient

log = logging.getLogger(__name__)


def _format_open(quotes) -> str:
    lines = ["📈 <b>Market open</b>"]
    for q in quotes:
        if q.is_empty:
            lines.append(f"  • <code>{q.symbol}</code> — data unavailable")
            continue
        lines.append(
            f"  • <code>{q.symbol}</code>  "
            f"open {fmt_price(q.open)}  "
            f"prev {fmt_price(q.previous_close)}  "
            f"({fmt_pct(q.change_pct)})"
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
            "📈 <b>Market open</b>\n  (no tickers tracked — try /ticker add SYMBOL)",
        )
        return 0

    try:
        quotes = fetch_quotes(client, symbols)
        body = _format_open(quotes)
    except Exception as exc:
        log.exception("market open failed")
        body = unavailable_message("Market open", type(exc).__name__)

    try:
        send_message(config.telegram.token, config.telegram.chat_id, body)
    except Exception:
        log.exception("market open send failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
