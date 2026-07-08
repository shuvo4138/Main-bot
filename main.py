# main.py
"""
Bot entry point.

Startup sequence:
  1. Validate required environment variables
  2. Build PTB Application with BOT_TOKEN
  3. Load S3 number pool from Supabase
  4. Register command / callback / message handlers
  5. Register background jobs (APScheduler via job_queue)
  6. Start polling
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
#                  HANDLER IMPORTS
# ══════════════════════════════════════════════════════════

from handlers.start import start_handler, message_handler
from handlers.callbacks import callback_handler
from handlers.admin import admin_command, stats_command


# ══════════════════════════════════════════════════════════
#                  STOP HANDLER
# ══════════════════════════════════════════════════════════

async def _stop_handler(update: Update, context) -> None:
    """Allow admin to stop the bot via /stop command."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    await update.message.reply_text("🛑 Bot বন্ধ হচ্ছে...")
    asyncio.get_event_loop().stop()


# ══════════════════════════════════════════════════════════
#                  TXT FILE HANDLER
# ══════════════════════════════════════════════════════════

async def _txt_file_handler(update: Update, context) -> None:
    """Handle .txt file uploads from admin (number pool upload)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    waiting = context.user_data.get("waiting_for") or \
              __import__("utils.state", fromlist=["user_data"]).user_data.get(user_id, {}).get("waiting_for")

    if waiting != "upload_numbers":
        return

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ .txt file পাঠান।")
        return

    # Determine pool_key from caption or prompt
    caption = (update.message.caption or "").strip()
    if not caption:
        await update.message.reply_text(
            "❌ Caption এ pool_key দিন (e.g. `224_fb`).", parse_mode="Markdown"
        )
        return

    pool_key = caption.strip()

    try:
        file = await context.bot.get_file(doc.file_id)
        content_bytes = await file.download_as_bytearray()
        text = content_bytes.decode("utf-8", errors="ignore")
        numbers = [
            line.strip().lstrip("+")
            for line in text.splitlines()
            if line.strip()
        ]
    except Exception as e:
        logger.error(f"File download error: {e}")
        await update.message.reply_text("❌ File পড়তে সমস্যা হয়েছে।")
        return

    if not numbers:
        await update.message.reply_text("❌ File এ কোনো number নেই।")
        return

    from panels.s3 import add_numbers_to_pool
    added, skipped = await add_numbers_to_pool(context.bot, pool_key, numbers)

    from utils.state import user_data
    user_data[user_id]["waiting_for"] = None

    await update.message.reply_text(
        f"✅ *Numbers Uploaded!*\n\n"
        f"🗂 Pool: `{pool_key}`\n"
        f"➕ Added: `{added}`\n"
        f"⏭ Skipped (dup): `{skipped}`",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════
#                  REGISTER HANDLERS
# ══════════════════════════════════════════════════════════

def register_handlers(app: Application) -> None:
    """Register all PTB handlers on the Application."""
    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("stop",  _stop_handler))

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))

    # .txt file uploads (admin number pool)
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("txt"),
        _txt_file_handler,
    ))

    # Text messages (reply-keyboard buttons + free text)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        message_handler,
    ))

    logger.info("✅ All handlers registered")


# ══════════════════════════════════════════════════════════
#                  MAIN
# ══════════════════════════════════════════════════════════

async def main() -> None:
    # 1. Validate config
    validate_required_config()

    # 2. Build Application
    app = Application.builder().token(BOT_TOKEN).build()

    # 3. Load S3 number pool from Supabase
    logger.info("Loading S3 number pool from Supabase...")
    await tg_load_all(app.bot)

    # 4. Register handlers
    register_handlers(app)

    # 5. Register background jobs
    register_jobs(app)

    # 6. Start polling
    logger.info("🤖 Bot starting — polling for updates...")
    await app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.run(main())
