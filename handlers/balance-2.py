# handlers/balance.py
"""
Balance, Withdraw and Referral system.
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
    db_save_user_async,
)

logger = get_logger(__name__)

MIN_WITHDRAW = 50.0
PER_OTP      = 0.20
PER_REFERRAL = 10.0


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show balance + referral info."""
    user    = update.effective_user
    user_id = user.id

    balance  = await db_get_balance(user_id) or 0.0
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


async def handle_withdraw_callback(query, user_id: int, context) -> None:
    """Handle withdraw flow callbacks."""
    data = query.data

    if data == "withdraw_start":
        balance = await db_get_balance(user_id) or 0.0

        if balance < MIN_WITHDRAW:
            await query.answer(
                f"❌ Minimum withdraw {MIN_WITHDRAW} Tk। আপনার balance: {balance:.2f} Tk",
                show_alert=True
            )
            return

        # Check pending request
        pending = await db_get_pending_withdraw(user_id)
        if pending:
            await query.answer("⏳ আপনার একটি pending withdraw request আছে।", show_alert=True)
            return

        # Ask for amount
        context.user_data["waiting_withdraw"] = True
        await query.message.reply_text(
            f"💸 <b>Withdraw Request</b>\n\n"
            f"আপনার balance: <b>{balance:.2f} Tk</b>\n"
            f"Minimum: {MIN_WITHDRAW} Tk\n\n"
            f"কত টাকা withdraw করতে চান? Amount লিখুন:",
            parse_mode="HTML",
        )
        return

    # Admin approve/reject
    if data.startswith("wd_approve:") and user_id == ADMIN_ID:
        wd_id   = int(data.split(":")[1])
        uid     = int(data.split(":")[2])
        amount  = float(data.split(":")[3])
        success = await db_approve_withdraw(wd_id, uid, amount, ADMIN_ID)
        if success:
            await query.edit_message_text(f"✅ Withdraw #{wd_id} approved — {amount:.2f} Tk")
            try:
                await context.bot.send_message(
                    uid,
                    f"✅ <b>Withdraw Approved!</b>\n\n"
                    f"Amount: <b>{amount:.2f} Tk</b>\n"
                    f"Request #{wd_id} processed.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return

    if data.startswith("wd_reject:") and user_id == ADMIN_ID:
        wd_id   = int(data.split(":")[1])
        uid     = int(data.split(":")[2])
        amount  = float(data.split(":")[3])
        await db_reject_withdraw(wd_id, ADMIN_ID)
        await query.edit_message_text(f"❌ Withdraw #{wd_id} rejected")
        try:
            await context.bot.send_message(
                uid,
                f"❌ <b>Withdraw Rejected</b>\n\n"
                f"Amount: {amount:.2f} Tk\n"
                f"Request #{wd_id} rejected by admin.\n"
                f"Balance refunded.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return


async def process_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Called from message_handler when user is in withdraw flow.
    Returns True if handled.
    """
    if not context.user_data.get("waiting_withdraw"):
        return False

    user_id = update.effective_user.id
    text    = update.message.text.strip()

    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text("❌ Valid amount লিখুন। যেমন: 50")
        return True

    balance = await db_get_balance(user_id) or 0.0

    if amount < MIN_WITHDRAW:
        await update.message.reply_text(f"❌ Minimum withdraw {MIN_WITHDRAW} Tk।")
        return True

    if amount > balance:
        await update.message.reply_text(f"❌ আপনার balance কম। Balance: {balance:.2f} Tk")
        return True

    context.user_data["waiting_withdraw"] = False

    # Create request
    wd_id = await db_create_withdraw_request(user_id, amount)
    if not wd_id:
        await update.message.reply_text("❌ Error! আবার try করুন।")
        return True

    await update.message.reply_text(
        f"✅ <b>Withdraw Request Submitted!</b>\n\n"
        f"Amount: <b>{amount:.2f} Tk</b>\n"
        f"Request ID: #{wd_id}\n\n"
        f"Admin review করবে শীঘ্রই।",
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
            f"💰 Amount: <b>{amount:.2f} Tk</b>\n"
            f"📋 Request: #{wd_id}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve",
                    callback_data=f"wd_approve:{wd_id}:{user_id}:{amount:.2f}",
                    api_kwargs={"style": "success"}),
                InlineKeyboardButton("❌ Reject",
                    callback_data=f"wd_reject:{wd_id}:{user_id}:{amount:.2f}",
                    api_kwargs={"style": "danger"}),
            ]]),
        )
    except Exception as e:
        logger.error(f"Withdraw admin notify error: {e}")

    return True


async def handle_referral(bot, referrer_id: int, new_user_id: int) -> None:
    """Award referral bonus when new user joins via referral link."""
    try:
        from database.supabase import db_award_referral_bonus
        await db_award_referral_bonus(referrer_id, new_user_id)
        await bot.send_message(
            referrer_id,
            f"🎉 <b>Referral Bonus!</b>\n\n"
            f"+{PER_REFERRAL:.0f} Tk credited to your balance!\n"
            f"A new user joined via your referral link.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"handle_referral error: {e}")
