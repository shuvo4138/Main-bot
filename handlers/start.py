# handlers/start.py
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    ADMIN_ID,
    SUPPORT_ADMIN_LINK,
    JOIN_CHANNEL_LINK,
    OTP_CHANNEL_JOIN_LINK,
    BACKUP_CHANNEL_LINK,
)
from utils.logger import get_logger
from utils.state import user_data, user_msg
from utils.helpers import COUNTRY_FLAGS_CODE, COUNTRY_NAMES_CODE, APP_EMOJIS
from keyboards.menus import (
    main_keyboard,
    join_channel_keyboard,
    panel_select_inline,
    get_welcome_text,
    SERVICE_SELECT_TEXT,
    after_number_inline_s3,
)
from handlers.helpers import (
    init_user,
    cancel_all_otp_tasks,
    check_user_joined,
    processing_users,
)
from panels.s3 import (
    s3_add_user,
    s3_get_session,
    s3_get_user_count,
    get_numbers_pool,
    parse_pool_key,
    otp_cache,
)

logger = get_logger(__name__)


def main_keyboard(user_id=None):
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    buttons = [
        [KeyboardButton("📲 Get Number"),  KeyboardButton("📦 My Numbers")],
        [KeyboardButton("💰 Balance"),     KeyboardButton("🏧 Withdraw")],
        [KeyboardButton("🆘 Support"),     KeyboardButton("📊 Status")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, is_persistent=True)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    user_id = user.id

    init_user(user_id)

    first  = user.first_name or "User"
    last   = user.last_name  or ""
    uname  = user.username   or str(user_id)
    joined = datetime.now().strftime("%Y-%m-%d")
    user_data[user_id].update({
        "name":     f"{first} {last}".strip(),
        "username": uname,
        "joined":   joined,
    })
    s3_add_user(user_id, uname)

    # Referral check
    args = context.args
    if args and str(args[0]).startswith("ref"):
        try:
            referrer_id = int(str(args[0])[3:])
            if referrer_id != user_id:
                from handlers.balance import handle_referral
                asyncio.create_task(handle_referral(context.bot, referrer_id, user_id))
        except Exception:
            pass

    joined_all = await check_user_joined(context.bot, user_id)
    if not joined_all:
        await update.message.reply_text(
            "Bot use korte sob channel join korun:",
            reply_markup=join_channel_keyboard(
                join_link=JOIN_CHANNEL_LINK,
                otp_link=OTP_CHANNEL_JOIN_LINK,
                backup_link=BACKUP_CHANNEL_LINK,
            ),
        )
        return

    welcome = get_welcome_text(first)
    await update.message.reply_text(
        welcome,
        parse_mode="HTML",
        reply_markup=main_keyboard(user_id),
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    text    = (update.message.text or "").strip()

    init_user(user_id)

    if text == "/panel" and user_id == ADMIN_ID:
        from keyboards.menus import admin_keyboard
        await update.message.reply_text(
            "Admin Panel",
            reply_markup=admin_keyboard(),
        )
        return

    joined_all = await check_user_joined(context.bot, user_id)
    if not joined_all:
        await update.message.reply_text(
            "Bot use korte sob channel join korun:",
            reply_markup=join_channel_keyboard(
                join_link=JOIN_CHANNEL_LINK,
                otp_link=OTP_CHANNEL_JOIN_LINK,
                backup_link=BACKUP_CHANNEL_LINK,
            ),
        )
        return

    # bKash number input
    if context.user_data.get("waiting_bkash"):
        from handlers.balance import process_bkash_input
        handled = await process_bkash_input(update, context)
        if handled:
            return

    waiting = user_data[user_id].get("waiting_for")

    if waiting == "custom_range":
        user_data[user_id]["waiting_for"] = None
        raw   = text.strip().upper().replace(" ", "").replace("+", "")
        panel = user_data[user_id].get("panel", "A1")
        user_data[user_id]["range"] = raw
        await update.message.delete()
        if panel == "A1":
            from panels.a1 import do_get_number_a1
            asyncio.create_task(do_get_number_a1(update.message, user_id, bot=context.bot))
        elif panel == "A2":
            from panels.a2 import do_get_number_a2
            asyncio.create_task(do_get_number_a2(update.message, user_id, bot=context.bot))
        return

    if waiting == "broadcast" and user_id == ADMIN_ID:
        user_data[user_id]["waiting_for"] = None
        from handlers.admin import do_broadcast
        await do_broadcast(context.bot, message=update.message)
        await update.message.reply_text("Broadcast sent!", reply_markup=main_keyboard(user_id))
        return

    if context.bot_data.get("pending_delete_number") and user_id == ADMIN_ID:
        context.bot_data["pending_delete_number"] = False
        number = text.strip().lstrip("+")
        pool   = get_numbers_pool()
        deleted = False
        for pk, nums in pool.items():
            if number in nums:
                nums.remove(number)
                deleted = True
                await update.message.reply_text(f"Deleted: {number}")
                break
        if not deleted:
            await update.message.reply_text(f"Not found: {number}")
        return

    # ── 📲 Get Number ──
    if text in ("📲 Get Number", "Get Number"):
        if user_id in processing_users:
            return
        processing_users.add(user_id)
        try:
            cancel_all_otp_tasks(user_id)
            user_data[user_id]["waiting_for"] = None
            user_data[user_id]["otp_active"]  = False
            kb = await panel_select_inline()
            msg = await context.bot.send_message(
                chat_id, SERVICE_SELECT_TEXT, reply_markup=kb,
            )
            user_msg[chat_id] = msg.message_id
        finally:
            processing_users.discard(user_id)
        return

    # ── 📦 My Numbers ──
    if text in ("📦 My Numbers",):
        panel = user_data[user_id].get("panel", "A1")
        if panel == "S3":
            session = s3_get_session(user_id)
            if session:
                nums     = session.get("numbers", [session.get("number")] if session.get("number") else [])
                pool_key = session["pool_key"]
                code, service, _ = parse_pool_key(pool_key)
                flag  = COUNTRY_FLAGS_CODE.get(code, "🌍")
                cname = COUNTRY_NAMES_CODE.get(code, "Unknown")
                svc   = "FACEBOOK" if service == "fb" else "INSTAGRAM"
                card  = (
                    f"Numbers Assigned!\n\nService: {svc}\nCountry: {flag} {cname}\n"
                    f"Reserved: 30 min\n\nOTPs forwarded automatically."
                )
                await update.message.reply_text(
                    card, reply_markup=after_number_inline_s3(pool_key, nums),
                )
            else:
                await update.message.reply_text("No active S3 session.")
        else:
            last = str(user_data[user_id].get("last_number", "")).replace("+", "").strip()
            if not last:
                await update.message.reply_text("No number taken yet.")
            else:
                await update.message.reply_text(f"Last number: `{last}`", parse_mode="Markdown")
        return

    # ── 💰 Balance ──
    if text in ("💰 Balance", "Balance"):
        from handlers.balance import balance_command
        await balance_command(update, context)
        return

    # ── 🏧 Withdraw ──
    if text in ("🏧 Withdraw", "Withdraw"):
        balance = 0.0
        try:
            from database.supabase import db_get_balance
            balance = await db_get_balance(user_id) or 0.0
        except Exception:
            pass
        if balance < 50:
            await update.message.reply_text(
                f"Minimum withdraw 50 Tk. Your balance: {balance:.2f} Tk",
                reply_markup=main_keyboard(user_id),
            )
        else:
            context.user_data["waiting_bkash"] = True
            context.user_data["withdraw_balance"] = balance
            await update.message.reply_text(
                f"Withdraw Request\n\nBalance: {balance:.2f} Tk\n\nbKash number dun (01XXXXXXXXX):",
            )
        return

    # ── 📊 Status ──
    if text in ("📊 Status", "Status"):
        pool     = get_numbers_pool()
        fb_count = sum(len(v) for k, v in pool.items() if k.endswith("_fb"))
        ig_count = sum(len(v) for k, v in pool.items() if k.endswith("_ig"))
        s3_otp   = sum(1 for k in otp_cache if k.startswith("s3:"))
        now      = datetime.now().strftime("%I:%M %p")
        msg = (
            f"Bot Status\n\nS3 OTPs: {s3_otp}\n"
            f"FB Pool: {fb_count}\nIG Pool: {ig_count}\n\n{now}"
        )
        await update.message.reply_text(msg, reply_markup=main_keyboard(user_id))
        return

    # ── 🆘 Support ──
    if text in ("🆘 Support", "Support"):
        await update.message.reply_text(
            f"Support: {SUPPORT_ADMIN_LINK}",
            reply_markup=main_keyboard(user_id),
        )
        return

    # ── ✈️ Telegram ──
    if text in ("✈️ Telegram", "Telegram"):
        await update.message.reply_text(
            "Telegram Panel:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("A2 Telegram", callback_data="select_panel_TG",
                                      api_kwargs={"style": "primary"})],
            ]),
        )
        return
