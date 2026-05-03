"""Daily brief cron / scheduler entry point.

Fetches sources, asks Copilot to render the brief, posts it silently to
Telegram with a [☀️ Start day] button, and persists the message_id so the
bot can edit it later (when the user taps Start, or when the 11 AM fallback
runs).

`run_brief()` is the reusable entry point — call it from the cron job
(`main`) or from the bot's in-process scheduler (`morning.scheduler`).
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime

from naarad import db
from naarad.brief.copilot import get_daily_brief
from naarad.config import load_config
from naarad.handlers.morning import START_DAY_CALLBACK
from naarad.telegram_api import send_message

log = logging.getLogger(__name__)


def _start_day_keyboard() -> dict:
    return {
        "inline_keyboard": [[
            {"text": "☀️ Start day", "callback_data": START_DAY_CALLBACK}
        ]]
    }


def run_brief() -> int:
    """Build, send, and persist today's brief. Safe to call from any process."""
    config = load_config()
    today = datetime.now(config.tz).date()

    body = get_daily_brief(today, config)

    try:
        result = send_message(
            config.telegram.token,
            config.telegram.chat_id,
            body,
            disable_notification=True,   # silent — user wakes ~8:30
            reply_markup=_start_day_keyboard(),
        )
    except Exception:
        log.exception("daily brief send failed")
        return 1

    msg_id = result.get("message_id")
    if msg_id is not None:
        try:
            db.update_water_state(config.db_path, start_button_message_id=msg_id)
        except Exception:
            log.exception("failed to persist start_button_message_id")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    return run_brief()


if __name__ == "__main__":
    sys.exit(main())
