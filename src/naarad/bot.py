"""Bot entrypoint. Wires handlers, JobQueue, and runs polling."""
from __future__ import annotations

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import BotCommand
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
from naarad.handlers import llm as llm_handlers
from naarad.handlers import morning as morning_handlers
from naarad.handlers import quote as quote_handlers
from naarad.handlers import status as status_handlers
from naarad.handlers import tickers as ticker_handlers
from naarad.handlers import water as water_handlers
from naarad.handlers import welcome as welcome_handlers
from naarad.jobs import scheduler as ticker_scheduler
from naarad.morning import scheduler as morning_scheduler
from naarad.startup import validate_startup
from naarad.tickers.eodhd import EODHDClient
from naarad.water import scheduler as water_scheduler
from naarad.water.scheduler import CONFIRM_CALLBACK

log = logging.getLogger(__name__)

# Surfaced in Telegram's "/" autocomplete menu via setMyCommands.
BOT_COMMANDS: list[BotCommand] = [
    BotCommand("water", "water status panel with a [💧 Log glass] button"),
    BotCommand("brief", "re-run today's morning brief"),
    BotCommand("llm", "toggle LLM features (on|off)"),
    BotCommand("status", "bot health: water, day, LLM"),
    BotCommand("ticker", "manage watchlist (add|remove|list|on|off)"),
    BotCommand("quote", "real-time quote for one symbol"),
    BotCommand("help", "show command reference"),
]


def build_application(config: Config) -> Application:
    validate_startup(config)
    db.init_db(config.db_path, seed_tickers=config.tickers_default)

    app = ApplicationBuilder().token(config.telegram.token).build()
    app.bot_data["config"] = config
    app.bot_data["water_cfg"] = water_scheduler.water_config_from(config)
    app.bot_data["water_lock"] = asyncio.Lock()
    app.bot_data["eodhd_client"] = EODHDClient(config.eodhd.api_key)

    app.add_handler(CommandHandler("water", water_handlers.water_command))
    app.add_handler(CommandHandler("brief", brief_handlers.brief_command))
    app.add_handler(CommandHandler("llm", llm_handlers.llm_command))
    app.add_handler(CommandHandler("ticker", ticker_handlers.ticker_command))
    app.add_handler(CommandHandler("quote", quote_handlers.quote_command))
    app.add_handler(CommandHandler("status", status_handlers.status_command))
    app.add_handler(CommandHandler(["help", "start"], status_handlers.help_command))
    app.add_handler(CallbackQueryHandler(
        water_handlers.water_button,
        pattern=f"^{CONFIRM_CALLBACK}$",
    ))
    app.add_handler(CallbackQueryHandler(
        water_handlers.water_panel_log,
        pattern=f"^{water_handlers.PANEL_LOG_CALLBACK}$",
    ))
    app.add_handler(CallbackQueryHandler(
        morning_handlers.start_day_button,
        pattern=f"^{morning_handlers.START_DAY_CALLBACK}$",
    ))
    app.add_handler(CallbackQueryHandler(
        welcome_handlers.welcome_button,
        pattern=f"^{welcome_handlers.WELCOME_BUTTON_CALLBACK}$",
    ))
    # Panel-button callbacks for /llm and /ticker. One handler per
    # command, routed by the verb suffix inside the callback itself.
    app.add_handler(CallbackQueryHandler(
        llm_handlers.llm_callback,
        pattern=f"^{llm_handlers.LLM_CALLBACK_PREFIX}",
    ))
    app.add_handler(CallbackQueryHandler(
        ticker_handlers.ticker_callback,
        pattern=f"^{ticker_handlers.TICKER_CALLBACK_PREFIX}",
    ))
    app.add_handler(CallbackQueryHandler(
        status_handlers.status_callback,
        pattern=f"^{status_handlers.STATUS_CALLBACK_PREFIX}",
    ))
    # Reply-to-reminder confirm. Exclude commands so /water doesn't double-fire.
    app.add_handler(MessageHandler(filters.REPLY & ~filters.COMMAND, water_handlers.water_reply))

    async def _post_init(app: Application) -> None:
        try:
            await app.bot.set_my_commands(BOT_COMMANDS)
        except Exception:
            log.exception("set_my_commands failed; / autocomplete may be stale")
        await water_scheduler.kickoff(app)
        await morning_scheduler.kickoff(app)
        await ticker_scheduler.kickoff(app)

    app.post_init = _post_init  # type: ignore[assignment]
    return app


def _configure_logging() -> None:
    """Console + rotating file handler at logs/naarad.log (5 MB × 3 backups)."""
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(
        log_dir / "naarad.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Drop any handlers a previous basicConfig may have installed (idempotent
    # on reload).
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # python-telegram-bot is chatty at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def main() -> None:
    _configure_logging()
    config = load_config()
    # build_application runs validate_startup before returning, so the
    # "startup validation passed" log line is emitted here. Install
    # scripts grep for it to confirm config is healthy.
    app = build_application(config)
    # Smoke-test mode: exit cleanly after validation, before any
    # Telegram contact happens. Install scripts set NAARAD_SMOKE_TEST=1
    # so the bot doesn't actually start polling (which would otherwise
    # fire the welcome message during install and cause the user to see
    # duplicate [Start day] buttons when systemd later starts the bot
    # for real).
    if os.environ.get("NAARAD_SMOKE_TEST") == "1":
        log.info("smoke test mode: exiting cleanly without polling")
        return
    log.info("naarad starting; chat_id=%s tz=%s", config.telegram.chat_id, config.timezone)
    app.run_polling()


if __name__ == "__main__":
    main()
