# handlers/balance.py
"""
Balance & Withdrawal system.

  • Every OTP delivered to a user's inbox (A1, A2, S3, S4) awards
    OTP_REWARD_TK (see database/supabase.py) to that user's balance.
  • /balance shows the current balance + a Withdraw button.
  • Withdraw requests need MIN_WITHDRAW_TK balance, are capped at one
    pending request per user, and are approved/rejected manually by
    ADMIN_ID via inline buttons (no auto-payout).
"""

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import ADMIN_ID
from utils.logger import get_logger
from database.supabase import (
    OTP_REWARD_TK,
    MIN_WITHDRAW_TK,
    db_get_balance,
    db_has_pending_withdrawal,
    db_create_withdraw_request,
    db_get_withdrawal,
    db_set_withdrawal_status,
    db_deduct_balance,
)

logger = get_logger(__name__)


def _balance_text(balance: float) -> str:
    return (
        f"💰 <b>Your Balance</b>\n\n"
        f"Balance : <b>{balance:.2f} Tk</b>\n"
        f"Per OTP : {OTP_REWARD_TK:.2f} Tk\n"
        f"Minimum Withdraw : {MIN_WITHDRAW_TK:.0f} Tk"
    )


def _balance_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏧 Withdraw", callback_data="wd_request",
                              api_kwargs={"style": "primary"}),
    ]])


async def balance_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    balance = await db_get_balance(user_id)
    await update.message.reply_text(
        _balance_text(balance),
        parse_mode="HTML",
        reply_markup=_balance_keyboard(),
    )


async def balance_callback(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles wd_request / wd_approve:<id> / wd_reject:<id>."""
    query   = update.callback_query
    data    = query.data
    user_id = query.from_user.id
    bot     = context.bot

    # ── User requests a withdrawal ──
    if data == "wd_request":
        await query.answer()
        balance = await db_get_balance(user_id)

        if balance < MIN_WITHDRAW_TK:
            await query.answer(
                f"⚠️ Minimum withdraw {MIN_WITHDRAW_TK:.0f} Tk প্রয়োজন। "
                f"আপনার balance: {balance:.2f} Tk",
                show_alert=True,
            )
            return

        if await db_has_pending_withdrawal(user_id):
            await query.answer(
                "⏳ আপনার আগের withdraw request এখনো pending।",
                show_alert=True,
            )
            return

        wd_id = await db_create_withdraw_request(user_id, balance)
        if not wd_id:
            await query.answer("❌ Request পাঠাতে সমস্যা হয়েছে, আবার চেষ্টা করুন।", show_alert=True)
            return

        await query.edit_message_text(
            f"✅ Withdraw request পাঠানো হয়েছে!\n\n"
            f"Amount : {balance:.2f} Tk\n"
            f"Status : ⏳ Pending (admin review)",
            parse_mode="HTML",
        )

        # ── Notify admin ──
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"🏧 <b>New Withdraw Request</b>\n\n"
                    f"User ID : <code>{user_id}</code>\n"
                    f"Amount  : {balance:.2f} Tk\n"
                    f"Request ID : #{wd_id}"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve:{wd_id}",
                                          api_kwargs={"style": "success"}),
                    InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject:{wd_id}",
                                          api_kwargs={"style": "danger"}),
                ]]),
            )
        except Exception as e:
            logger.error(f"withdraw admin notify error: {e}")
        return

    # ── Admin approves/rejects ──
    if data.startswith("wd_approve:") or data.startswith("wd_reject:"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ শুধু admin এই কাজ করতে পারবে।", show_alert=True)
            return

        approve = data.startswith("wd_approve:")
        wd_id   = int(data.split(":", 1)[1])

        row = await db_get_withdrawal(wd_id)
        if not row:
            await query.answer("❌ Request পাওয়া যায়নি (হয়তো আগেই process হয়ে গেছে)।", show_alert=True)
            return
        if row.get("status") != "pending":
            await query.answer(f"⚠️ ইতিমধ্যে {row.get('status')} করা হয়ে গেছে।", show_alert=True)
            return

        await query.answer()
        target_user = row["user_id"]
        amount      = float(row["amount"])
        new_status  = "approved" if approve else "rejected"

        await db_set_withdrawal_status(wd_id, new_status, admin_id=user_id)

        if approve:
            await db_deduct_balance(target_user, amount)
            admin_note = f"✅ Approved — {amount:.2f} Tk deducted from user's balance."
            user_note  = f"✅ আপনার withdraw request ({amount:.2f} Tk) approve হয়েছে। Payment পাঠানো হবে।"
        else:
            admin_note = f"❌ Rejected — balance অপরিবর্তিত।"
            user_note  = f"❌ আপনার withdraw request ({amount:.2f} Tk) reject হয়েছে। বিস্তারিত জানতে Support-এ যোগাযোগ করুন।"

        try:
            await query.edit_message_text(
                query.message.text_html + f"\n\n{admin_note}",
                parse_mode="HTML",
            )
        except Exception:
            pass

        try:
            await bot.send_message(chat_id=target_user, text=user_note)
        except Exception as e:
            logger.error(f"withdraw user notify error: {e}")
        return
