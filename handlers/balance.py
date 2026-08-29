# handlers/balance.py
"""
Balance, Withdraw and Referral system.

Withdraw flow:
  1. User clicks Withdraw
  2. Bot asks for bKash number
  3. User sends bKash number
  4. Full balance deducted → pending request created
  5. Admin notified with Approve/Reject buttons
  6. Approve → User notified (1-7 days)
  7. Reject → Balance refunded to user
"""

import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import ADMIN_ID, BOT_USERNAME
from utils.logger import get_logger
from database.supabase import (
    db_get_balance,
    db_create_withdraw_request,
    db_get_pending_withdraw,
    db_approve_withdraw,
    db_reject_withdraw,
    db_get_referral_count,
)

logger = get_logger(__name__)

MIN_WITHDRAW = 50.0
PER_OTP      = 0.20
PER_REFERRAL = 10.0


# ══════════════════════════════════════════════════════════
#                  BALANCE COMMAND
# ══════════════════════════════════════════════════════════

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    user_id = user.id

    balance   = await db_get_balance(user_id) or 0.0
    ref_count = await db_get_referral_count(user_id) or 0
    ref_link  = f"https://t.me/{BOT_USERNAME}?start=ref{user_id}"

    text = (
        f"💰 <b>Your Balance</b>\n\n"
        f"• Balance: <b>{balance:.2f} Tk</b>\n"
        f"• Per OTP: {PER_OTP} Tk\n"
        f"• Per Referral: {PER_REFERRAL} Tk\n"
        f"• Minimum Withdraw: {MIN_WITHDRAW} Tk\n\n"
        f"👥 Referrals: <b>{ref_count}</b>\n\n"
        f"🔗 <b>Your Referral Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"<i>Earn by receiving OTPs and inviting friends.</i>"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏧 Withdraw", callback_data="withdraw_start",
                              api_kwargs={"style": "success"})
    ]])

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ══════════════════════════════════════════════════════════
#                  WITHDRAW CALLBACKS
# ══════════════════════════════════════════════════════════

async def handle_withdraw_callback(query, user_id: int, context) -> None:
    data = query.data

    # ── Step 1: User clicks Withdraw ──
    if data == "withdraw_start":
        balance = await db_get_balance(user_id) or 0.0

        if balance < MIN_WITHDRAW:
            await query.answer(
                f"Minimum withdraw {MIN_WITHDRAW} Tk. Your balance: {balance:.2f} Tk",
                show_alert=True,
            )
            return

        pending = await db_get_pending_withdraw(user_id)
        if pending:
            await query.answer(
                "You already have a pending withdraw request.",
                show_alert=True,
            )
            return

        # Ask for bKash number
        context.user_data["waiting_bkash"]   = True
        context.user_data["withdraw_balance"] = balance
        await query.message.reply_text(
            f"💸 <b>Withdraw Request</b>\n\n"
            f"Balance: <b>{balance:.2f} Tk</b>\n\n"
            f"📱 Your bKash number dun:",
            parse_mode="HTML",
        )
        return

    # ── Admin Approve ──
    if data.startswith("wd_approve:") and user_id == ADMIN_ID:
        parts  = data.split(":")
        wd_id  = int(parts[1])
        uid    = int(parts[2])
        amount = float(parts[3])
        bkash  = parts[4] if len(parts) > 4 else "N/A"

        success = await db_approve_withdraw(wd_id, uid, amount, ADMIN_ID)
        if success:
            await query.edit_message_text(
                f"✅ Withdraw #{wd_id} approved\n"
                f"Amount: {amount:.2f} Tk\n"
                f"bKash: {bkash}"
            )
            try:
                await context.bot.send_message(
                    uid,
                    f"✅ <b>Withdraw Approved!</b>\n\n"
                    f"Amount: <b>{amount:.2f} Tk</b>\n"
                    f"bKash: <code>{bkash}</code>\n"
                    f"Request: #{wd_id}\n\n"
                    f"⏳ Payment within 1-7 business days.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return

    # ── Admin Reject ──
    if data.startswith("wd_reject:") and user_id == ADMIN_ID:
        parts  = data.split(":")
        wd_id  = int(parts[1])
        uid    = int(parts[2])
        amount = float(parts[3])
        bkash  = parts[4] if len(parts) > 4 else "N/A"

        await db_reject_withdraw(wd_id, ADMIN_ID)
        await query.edit_message_text(
            f"❌ Withdraw #{wd_id} rejected\n"
            f"Amount: {amount:.2f} Tk refunded"
        )
        try:
            await context.bot.send_message(
                uid,
                f"❌ <b>Withdraw Rejected</b>\n\n"
                f"Amount: {amount:.2f} Tk\n"
                f"Request: #{wd_id}\n\n"
                f"💰 Balance has been refunded to your account.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return


# ══════════════════════════════════════════════════════════
#              BKASH NUMBER INPUT HANDLER
# ══════════════════════════════════════════════════════════

async def process_bkash_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Called from message_handler when user is in bKash input state.
    Returns True if handled.
    """
    if not context.user_data.get("waiting_bkash"):
        return False

    user_id = update.effective_user.id
    bkash   = update.message.text.strip()

    # Validate bKash number
    import re
    if not re.match(r'^01[3-9]\d{8}$', bkash):
        await update.message.reply_text(
            "Invalid bKash number. Valid format: 01XXXXXXXXX"
        )
        return True

    balance = context.user_data.get("withdraw_balance", 0.0)
    if not balance:
        balance = await db_get_balance(user_id) or 0.0

    context.user_data["waiting_bkash"]   = False
    context.user_data["withdraw_balance"] = 0.0

    if balance < MIN_WITHDRAW:
        await update.message.reply_text(
            f"Minimum withdraw {MIN_WITHDRAW} Tk. Your balance: {balance:.2f} Tk"
        )
        return True

    # Create request (full balance)
    wd_id = await db_create_withdraw_request(user_id, balance, bkash)
    if not wd_id:
        await update.message.reply_text("Error! Please try again.")
        return True

    await update.message.reply_text(
        f"✅ <b>Withdraw Request Submitted!</b>\n\n"
        f"Amount: <b>{balance:.2f} Tk</b>\n"
        f"bKash: <code>{bkash}</code>\n"
        f"Request ID: #{wd_id}\n\n"
        f"⏳ Payment within 1-7 business days.",
        parse_mode="HTML",
    )

    # Notify admin
    user = update.effective_user
    name = user.full_name or str(user_id)
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"🏧 <b>New Withdraw Request</b>\n\n"
            f"👤 User: <a href='tg://user?id={user_id}'>{name}</a>\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"💰 Amount: <b>{balance:.2f} Tk</b>\n"
            f"📱 bKash: <code>{bkash}</code>\n"
            f"📋 Request: #{wd_id}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"wd_approve:{wd_id}:{user_id}:{balance:.2f}:{bkash}",
                    api_kwargs={"style": "success"},
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"wd_reject:{wd_id}:{user_id}:{balance:.2f}:{bkash}",
                    api_kwargs={"style": "danger"},
                ),
            ]]),
        )
    except Exception as e:
        logger.error(f"Withdraw admin notify error: {e}")

    return True


# ══════════════════════════════════════════════════════════
#                  REFERRAL HANDLER
# ══════════════════════════════════════════════════════════

async def handle_referral(bot, referrer_id: int, new_user_id: int) -> None:
    try:
        from database.supabase import db_award_referral_bonus
        await db_award_referral_bonus(referrer_id, new_user_id)
        await bot.send_message(
            referrer_id,
            f"🎉 <b>Referral Bonus!</b>\n\n"
            f"+{PER_REFERRAL:.0f} Tk credited!\n"
            f"A new user joined via your referral link.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"handle_referral error: {e}")


# ══════════════════════════════════════════════════════════
#                  CALLBACK ENTRY POINT
# ══════════════════════════════════════════════════════════

async def balance_callback(update, context) -> None:
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    await handle_withdraw_callback(query, user_id, context)
