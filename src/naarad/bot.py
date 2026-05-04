"""Bot entrypoint. Wires handlers, JobQueue, and runs polling."""
from __future__ import annotations

import asyncio
import logging

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from naarad import db
from naarad.config import Config, load_config
from naarad.handlers import brief as brief_handlers
from naarad.handlers import morning as morning_handlers
from naarad.handlers import status as status_handlers
from naarad.handlers import tickers as ticker_handlers
from naarad.handlers import water as water_handlers
from naarad.morning import scheduler as morning_scheduler
from naarad.water import scheduler as water_scheduler
from naarad.water.scheduler import CONFIRM_CALLBACK

log = logging.getLogger(__name__)


def build_application(config: Config) -> Application:
    db.init_db(config.db_path, seed_tickers=config.tickers_default)

    app = ApplicationBuilder().token(config.telegram.token).build()
    app.bot_data["config"] = config
    app.bot_data["water_cfg"] = water_scheduler.water_config_from(config)
    app.bot_data["water_lock"] = asyncio.Lock()

    app.add_handler(CommandHandler("water", water_handlers.water_command))
    app.add_handler(CommandHandler("brief", brief_handlers.brief_command))
    app.add_handler(CommandHandler("ticker", ticker_handlers.ticker_command))
    app.add_handler(CommandHandler("status", status_handlers.status_command))
    app.add_handler(CommandHandler(["help", "start"], status_handlers.help_command))
    app.add_handler(CallbackQueryHandler(
        water_handlers.water_button,
        pattern=f"^{CONFIRM_CALLBACK}$",
    ))
    app.add_handler(CallbackQueryHandler(
        morning_handlers.start_day_button,
        pattern=f"^{morning_handlers.START_DAY_CALLBACK}$",
    ))
    # Reply-to-reminder confirm. Exclude commands so /water doesn't double-fire.
    app.add_handler(MessageHandler(filters.REPLY & ~filters.COMMAND, water_handlers.water_reply))

    async def _post_init(app: Application) -> None:
        await water_scheduler.kickoff(app)
        await morning_scheduler.kickoff(app)

    app.post_init = _post_init  # type: ignore[assignment]
    return app


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # python-telegram-bot is chatty at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def main() -> None:
    _configure_logging()
    config = load_config()
    app = build_application(config)
    log.info("naarad starting; chat_id=%s tz=%s", config.telegram.chat_id, config.timezone)
    app.run_polling()


if __name__ == "__main__":
    main()
