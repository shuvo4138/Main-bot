"""
main.py

Application entry point.
Responsible only for bootstrapping the Telegram bot.
"""

from telegram import Update
from telegram.ext import Application, ContextTypes

from config import BOT_TOKEN, validate_required_config
from utils.logger import get_logger

from handlers.start import register_start_handlers
from handlers.callback import register_callback_handlers

logger = get_logger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler."""
    logger.exception("Unhandled exception", exc_info=context.error)


def create_application() -> Application:
    validate_required_config()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    register_start_handlers(application)
    register_callback_handlers(application)

    application.add_error_handler(error_handler)
    return application


def main() -> None:
    logger.info("Starting Telegram bot...")
    app = create_application()
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
