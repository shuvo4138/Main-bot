# main.py
"""
Bot entry point.

Validates config, builds the PTB Application, loads the S3 number pool
from Supabase, registers all handlers and background jobs, then starts
Telegram long-polling.
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import BOT_TOKEN, ADMIN_ID, validate_required_config
from utils.logger import get_logger
from services.jobs import register_jobs
from panels.s3 import tg_load_all

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════
#                  STOP HANDLER
# ══════════════════════════════════════════════════════════

async def _stop_handler(update: Update, context) -> None:
    """Admin-only /stop command — gracefully shuts down the bot."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    await update.message.reply_text("🛑 Bot বন্ধ হচ্ছে...")
    asyncio.get_event_loop().stop()


# ══════════════════════════════════════════════════════════
#                  TXT FILE UPLOAD HANDLER
# ══════════════════════════════════════════════════════════

async def _txt_file_handler(update: Update, context) -> None:
    """
    Handle .txt file uploads from admin — used to bulk-add numbers to a pool.
    Delegates to the S3 admin upload flow.
    """
    from utils.state import user_data
    from config import ADMIN_ID as _ADMIN_ID

    user_id = update.effective_user.id
    if user_id != _ADMIN_ID:
        return

    waiting = user_data.get(user_id, {}).get("waiting_for")
    if waiting != "upload_numbers":
        return

    user_data[user_id]["waiting_for"] = None

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ .txt file পাঠান।")
        return

    # Determine pool_key from admin state or ask
    pool_key = user_data[user_id].get("s3_upload_pool_key", "")
    if not pool_key:
        await update.message.reply_text(
            "❌ Pool key সেট নেই। Admin panel থেকে আবার চেষ্টা করুন।"
        )
        return

    file = await context.bot.get_file(doc.file_id)
    raw = await file.download_as_bytearray()
    lines = raw.decode("utf-8", errors="ignore").splitlines()
    numbers = [ln.strip().lstrip("+") for ln in lines if ln.strip()]

    if not numbers:
        await update.message.reply_text("❌ File এ কোনো number নেই।")
        return

    from panels.s3 import add_numbers_to_pool
    added, skipped = await add_numbers_to_pool(context.bot, pool_key, numbers)
    await update.message.reply_text(
        f"✅ *Numbers Added!*\n\n"
        f"📦 Pool: `{pool_key}`\n"
        f"➕ Added: `{added}`\n"
        f"⏭ Skipped (dup): `{skipped}`",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════
#                  HANDLER REGISTRATION
# ══════════════════════════════════════════════════════════

def register_handlers(app: Application) -> None:
    """Register all command, callback, and message handlers."""
    from handlers.start import start_handler, message_handler
    from handlers.callbacks import callback_handler
    from handlers.admin import admin_command, stats_command

    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("stop", _stop_handler))

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))

    # .txt file uploads (admin number pool management)
    app.add_handler(
        MessageHandler(
            filters.Document.FileExtension("txt") & filters.ChatType.PRIVATE,
            _txt_file_handler,
        )
    )

    # Text messages (reply-keyboard buttons + free-text input)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
    )

    logger.info("✅ All handlers registered")


# ══════════════════════════════════════════════════════════
#                  MAIN
# ══════════════════════════════════════════════════════════

async def main() -> None:
    """Build and start the bot."""
    validate_required_config()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Load S3 number pool from Supabase into memory
    logger.info("Loading S3 number pool from Supabase...")
    await tg_load_all(app.bot)

    # Register handlers and background jobs
    register_handlers(app)
    register_jobs(app)

    logger.info("🚀 Bot starting — polling...")
    await app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.run(main())
