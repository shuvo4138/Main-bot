# main.py
"""
Bot entry point.

Startup sequence:
  1. Validate required config
  2. Build PTB Application
  3. Load S3 number pool from Supabase
  4. Register all handlers
  5. Register background jobs
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
#                  HANDLER REGISTRATION
# ══════════════════════════════════════════════════════════

def register_handlers(app: Application) -> None:
    from handlers.start import start_handler, message_handler
    from handlers.callbacks import callback_handler
    from handlers.admin import admin_command, stats_command

    # ── Commands ──
    app.add_handler(CommandHandler("start",  start_handler))
    app.add_handler(CommandHandler("admin",  admin_command))
    app.add_handler(CommandHandler("stats",  stats_command))
    app.add_handler(CommandHandler("stop",   _stop_handler))

    # ── Callbacks ──
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── TXT file upload (S3 number pool) ──
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("txt") & filters.ChatType.PRIVATE,
        _txt_file_handler,
    ))

    # ── Text messages ──
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE,
        message_handler,
    ))

    logger.info("✅ All handlers registered")


# ══════════════════════════════════════════════════════════
#                  EXTRA COMMAND HANDLERS
# ══════════════════════════════════════════════════════════

async def _stop_handler(update, context):
    """User /stop — cancel all their OTP tasks."""
    from handlers.helpers import cancel_all_otp_tasks, init_user
    from keyboards.menus import main_keyboard
    user_id = update.effective_user.id
    init_user(user_id)
    cancel_all_otp_tasks(user_id)
    await update.message.reply_text(
        "🛑 Auto OTP বন্ধ হয়েছে.",
        reply_markup=main_keyboard(user_id),
    )


async def _txt_file_handler(update, context):
    """Admin uploads a .txt file to add numbers to the S3 pool."""
    import re
    from config import ADMIN_ID
    from panels.s3 import add_numbers_to_pool, count_numbers, is_shark_pool
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return

    doc      = update.message.document
    filename = doc.file_name or ""
    file     = await doc.get_file()
    content  = await file.download_as_bytearray()
    text     = content.decode("utf-8", errors="ignore")

    # Filename: 91.txt | 91_v1.txt | 224_fb.txt
    match = re.match(r'([\d]+(?:[_-](?:v1|s\d+))?)', filename, re.IGNORECASE)
    if not match:
        await update.message.reply_text(
            "❌ *Invalid filename!*\n\nFormat:\n`91.txt` — S3 normal\n`91_v1.txt` — Shark (S4)",
            parse_mode="Markdown",
        )
        return

    base_pool_key = match.group(1).lower().replace("-", "_")
    new_numbers   = [
        line.strip().lstrip("+")
        for line in text.splitlines()
        if line.strip() and len(line.strip()) >= 7
    ]
    if not new_numbers:
        await update.message.reply_text("❌ File empty or invalid format!")
        return

    is_shark = "_v1" in base_pool_key
    context.user_data["pending_numbers"]  = new_numbers
    context.user_data["pending_pool_key"] = base_pool_key

    await update.message.reply_text(
        f"📁 *File:* `{filename}`\n"
        f"📊 *Numbers:* `{len(new_numbers)}`\n"
        f"{'🦈 Shark Panel (S4)' if is_shark else '🔴 S3 (CR API)'}\n\n"
        f"কোন service এর জন্য add করবো?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📘 Facebook",           callback_data="pool_service_fb",
                                   api_kwargs={"style": "primary"})],
            [InlineKeyboardButton("📸 Instagram",          callback_data="pool_service_ig",
                                   api_kwargs={"style": "primary"})],
            [InlineKeyboardButton("📘📸 Facebook + Instagram", callback_data="pool_service_both",
                                   api_kwargs={"style": "success"})],
        ]),
    )


# ══════════════════════════════════════════════════════════
#                  POST-INIT STARTUP
# ══════════════════════════════════════════════════════════

async def post_init(app: Application) -> None:
    """Load S3 pool from Supabase after the bot connects."""
    logger.info("⏳ Loading S3 number pool from Supabase...")
    await tg_load_all(app.bot)
    logger.info("✅ S3 pool loaded. Bot is ready.")


# ══════════════════════════════════════════════════════════
#                  MAIN
# ══════════════════════════════════════════════════════════

def main() -> None:
    validate_required_config()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    register_handlers(app)
    register_jobs(app)

    logger.info("🚀 Bot starting...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
