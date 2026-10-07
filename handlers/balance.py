# handlers/balance.py
"""
Balance, Withdraw and Referral system.

Withdraw flow:
  1. User clicks Withdraw
  2. User selects payment method (bKash / Nagad / Binance UID)
  3. User sends number / UID  (validated)
  4. Full balance deducted -> pending request created
  5. Admin notified with Approve / Reject buttons
  6. Approve      -> status "approved", user notified, admin gets "Mark as Paid"
  7. Mark as Paid -> status "paid", user notified
  8. Reject       -> balance refunded to user, user notified

Callback data only carries the request ID; everything else is read from the DB.
"""

import html
import re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import ADMIN_ID, BOT_USERNAME, _get_int
WITHDRAW_CHANNEL_ID = _get_int("WITHDRAW_CHANNEL_ID")
from utils.logger import get_logger
from database.supabase import (
    db_get_balance,
    db_create_withdraw_request,
    db_get_pending_withdraw,
    db_get_withdrawal,
    db_approve_withdraw,
    db_mark_withdraw_paid,
    db_reject_withdraw,
    db_get_referral_count,
)

logger = get_logger(__name__)

MIN_WITHDRAW = 50.0
PER_OTP      = 0.20
PER_REFERRAL = 10.0

# key -> display info
METHODS = {
    "bkash":   {"emoji": "🟣", "label": "bKash",   "user_label": "bKash",
                "prompt": "📱 Enter your bKash number:"},
    "nagad":   {"emoji": "🟢", "label": "Nagad",   "user_label": "Nagad",
                "prompt": "📱 Enter your Nagad number:"},
    "binance": {"emoji": "🟡", "label": "Binance", "user_label": "Binance UID",
                "prompt": "🆔 Enter your Binance UID:"},
}

MOBILE_RE      = re.compile(r"^01[3-9]\d{8}$")   # same rule as before: 01XXXXXXXXX
BINANCE_UID_RE = re.compile(r"^\d{5,20}$")

STATUS_LINE = {
    "pending":  "⏳ Status: Pending",
    "approved": "✅ Status: Approved (payment pending)",
    "paid":     "💵 Status: Paid",
    "rejected": "❌ Status: Rejected (refunded)",
}


# ══════════════════════════════════════════════════════════
#                       HELPERS
# ══════════════════════════════════════════════════════════

def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _method_key(wd: dict) -> str:
    key = (wd.get("payment_method") or "bkash").lower()   # old rows = bKash
    return key if key in METHODS else "bkash"


def _account(wd: dict) -> str:
    return str(wd.get("payment_account") or wd.get("bkash") or "N/A")


async def _alert(query, text: str) -> None:
    """Popup alert; falls back to a normal message if the query was already answered."""
    try:
        await query.answer(text, show_alert=True)
    except Exception:
        try:
            await query.message.reply_text(text)
        except Exception as e:
            logger.error(f"alert fallback error: {e}")


def _method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{m['emoji']} {m['label']}", callback_data=f"withdraw_m:{key}")]
        for key, m in METHODS.items()
    ])


def _admin_text(wd: dict) -> str:
    key = _method_key(wd)
    uid = int(wd["user_id"])
    acc = _esc(_account(wd))
    lines = [
        "🏧 <b>NEW WITHDRAWAL REQUEST</b>",
        "",
        f"👤 User: <a href='tg://user?id={uid}'>{_esc(wd.get('user_name') or uid)}</a>",
        f"🆔 User ID: <code>{uid}</code>",
        "",
        f"💰 Amount: <b>৳{float(wd['amount']):.2f}</b>",
        f"💳 Method: {METHODS[key]['label']}",
    ]
    if key == "binance":
        lines.append(f"🆔 Binance UID: <code>{acc}</code>")
    else:
        lines.append(f"📱 Account: <code>{acc}</code>")
    lines += ["", f"📋 Request ID: #{wd['id']}", STATUS_LINE.get(wd.get("status"), "")]
    return "\n".join(lines)


def _admin_keyboard(wd: dict):
    wd_id  = wd["id"]
    status = wd.get("status")
    if status == "pending":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve:{wd_id}",
                                 api_kwargs={"style": "success"}),
            InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject:{wd_id}",
                                 api_kwargs={"style": "danger"}),
        ]])
    if status == "approved":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("💵 Mark as Paid", callback_data=f"wd_paid:{wd_id}",
                                 api_kwargs={"style": "success"}),
        ]])
    return None   # paid / rejected -> no buttons


async def _show_admin_message(query, wd: dict) -> None:
    try:
        await query.edit_message_text(
            _admin_text(wd), parse_mode="HTML", reply_markup=_admin_keyboard(wd),
        )
    except Exception as e:
        logger.warning(f"admin message edit skipped: {e}")


async def _notify_user(context, uid: int, text: str) -> None:
    try:
        await context.bot.send_message(uid, text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"user notify failed ({uid}): {e}")


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

    # ── Step 1: User clicks Withdraw -> choose payment method ──
    if data == "withdraw_start":
        balance = await db_get_balance(user_id) or 0.0

        if balance < MIN_WITHDRAW:
            await _alert(query, f"Minimum withdraw {MIN_WITHDRAW} Tk. Your balance: {balance:.2f} Tk")
            return

        if await db_get_pending_withdraw(user_id):
            await _alert(query, "You already have a pending withdraw request.")
            return

        context.user_data["withdraw_balance"] = balance
        await query.message.reply_text(
            f"💸 <b>Withdraw Request</b>\n\n"
            f"Balance: <b>{balance:.2f} Tk</b>\n\n"
            f"💳 <b>Select Payment Method</b>",
            parse_mode="HTML",
            reply_markup=_method_keyboard(),
        )
        return

    # ── Step 2: Method chosen -> ask for number / UID ──
    if data.startswith("withdraw_m:"):
        method = data.split(":", 1)[1]
        if method not in METHODS:
            await _alert(query, "Invalid payment method.")
            return

        balance = await db_get_balance(user_id) or 0.0
        if balance < MIN_WITHDRAW:
            await _alert(query, f"Minimum withdraw {MIN_WITHDRAW} Tk. Your balance: {balance:.2f} Tk")
            return
        if await db_get_pending_withdraw(user_id):
            await _alert(query, "You already have a pending withdraw request.")
            return

        context.user_data["waiting_bkash"]    = True      # same flag -> existing message_handler keeps working
        context.user_data["withdraw_method"]  = method
        context.user_data["withdraw_balance"] = balance
        await query.message.reply_text(METHODS[method]["prompt"])
        return

    # ── Admin actions: Approve / Reject / Mark as Paid ──
    for prefix, action in (("wd_approve:", "approve"), ("wd_reject:", "reject"), ("wd_paid:", "paid")):
        if data.startswith(prefix):
            await _handle_admin_action(query, user_id, context, action, data)
            return


async def _handle_admin_action(query, user_id: int, context, action: str, data: str) -> None:
    # Only the configured admin
    if user_id != ADMIN_ID:
        await _alert(query, "⛔ You are not authorized.")
        return

    # Request ID only (old-style buttons "wd_approve:id:uid:amount:bkash" still work: we use parts[1])
    try:
        wd_id = int(data.split(":")[1])
    except (IndexError, ValueError):
        await _alert(query, "Invalid request.")
        return

    # Everything else comes from the database, never from the button
    wd = await db_get_withdrawal(wd_id)
    if not wd:
        await _alert(query, f"Request #{wd_id} not found.")
        return

    uid    = int(wd["user_id"])
    amount = float(wd["amount"])

    if action == "approve":
        ok, new_status, expected = await db_approve_withdraw(wd_id, uid, amount, ADMIN_ID), "approved", "pending"
    elif action == "reject":
        ok, new_status, expected = await db_reject_withdraw(wd_id, ADMIN_ID), "rejected", "pending"
    else:
        ok, new_status, expected = await db_mark_withdraw_paid(wd_id, ADMIN_ID), "paid", "approved"

    if not ok:
        # Either already processed (duplicate click) or a DB error — find out which
        fresh = await db_get_withdrawal(wd_id)
        if fresh and fresh.get("status") != expected:
            current = fresh.get("status")
            if action == "paid" and current == "pending":
                await _alert(query, "Approve this request first.")
            else:
                await _alert(query, f"Request #{wd_id} is already {current}.")
            await _show_admin_message(query, fresh)   # refresh stale buttons
        else:
            await _alert(query, "⚠️ Database error. Nothing was changed, please try again.")
        return

    wd = {**wd, "status": new_status}
    await _show_admin_message(query, wd)

    key   = _method_key(wd)
    acc   = _esc(_account(wd))
    label = METHODS[key]["user_label"]

    if action == "approve":
        await _notify_user(
            context, uid,
            f"✅ <b>Withdrawal Approved!</b>\n\n"
            f"💰 Amount: <b>৳{amount:.2f}</b>\n"
            f"💳 Method: {label}\n"
            f"📱 Account: <code>{acc}</code>\n"
            f"🆔 Request ID: #{wd_id}\n\n"
            f"⏳ Payment within 1-7 business days.",
        )
    elif action == "paid":
        await _notify_user(
            context, uid,
            f"💵 <b>Payment Sent!</b>\n\n"
            f"💰 Amount: <b>৳{amount:.2f}</b>\n"
            f"💳 Method: {label}\n"
            f"📱 Account: <code>{acc}</code>\n"
            f"🆔 Request ID: #{wd_id}\n\n"
            f"Thank you!",
        )
    else:
        await _notify_user(
            context, uid,
            f"❌ <b>Withdrawal Rejected</b>\n\n"
            f"💰 Amount: <b>৳{amount:.2f}</b>\n"
            f"🆔 Request ID: #{wd_id}\n\n"
            f"💰 Balance has been refunded to your account.",
        )


# ══════════════════════════════════════════════════════════
#           ACCOUNT NUMBER / UID INPUT HANDLER
# ══════════════════════════════════════════════════════════

async def process_bkash_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Called from message_handler when user is in withdraw-input state
    (name kept so message_handler needs no change).
    Returns True if handled.
    """
    if not context.user_data.get("waiting_bkash"):
        return False

    user_id = update.effective_user.id
    method  = context.user_data.get("withdraw_method", "bkash")
    if method not in METHODS:
        method = "bkash"
    account = (update.message.text or "").strip()

    # Validate
    if method == "binance":
        if not BINANCE_UID_RE.match(account):
            await update.message.reply_text("Invalid Binance UID. Please send numbers only.")
            return True
    elif not MOBILE_RE.match(account):
        await update.message.reply_text(
            f"Invalid {METHODS[method]['label']} number. Valid format: 01XXXXXXXXX"
        )
        return True

    balance = context.user_data.get("withdraw_balance", 0.0)
    if not balance:
        balance = await db_get_balance(user_id) or 0.0

    context.user_data["waiting_bkash"]    = False
    context.user_data["withdraw_balance"] = 0.0
    context.user_data.pop("withdraw_method", None)

    if balance < MIN_WITHDRAW:
        await update.message.reply_text(
            f"Minimum withdraw {MIN_WITHDRAW} Tk. Your balance: {balance:.2f} Tk"
        )
        return True

    if await db_get_pending_withdraw(user_id):
        await update.message.reply_text("You already have a pending withdraw request.")
        return True

    user = update.effective_user
    name = user.full_name or str(user_id)

    # Create request (full balance)
    wd_id = await db_create_withdraw_request(
        user_id, balance,
        payment_method=method, payment_account=account, user_name=name,
    )
    if not wd_id:
        await update.message.reply_text("Error! Please try again.")
        return True

    # User confirmation
    account_line = (
        f"🆔 Binance UID: <code>{_esc(account)}</code>" if method == "binance"
        else f"📱 Account: <code>{_esc(account)}</code>"
    )
    await update.message.reply_text(
        f"🏧 <b>WITHDRAWAL REQUEST</b>\n\n"
        f"💰 Amount: <b>৳{balance:.2f}</b>\n"
        f"💳 Method: {METHODS[method]['user_label']}\n"
        f"{account_line}\n"
        f"🆔 Request ID: #{wd_id}\n\n"
        f"⏳ Status: Pending\n\n"
        f"Your withdrawal request has been submitted successfully.\n"
        f"Please wait for admin approval.",
        parse_mode="HTML",
    )

    # Notify admin + channel
    wd = {
        "id": wd_id, "user_id": user_id, "user_name": name, "amount": balance,
        "payment_method": method, "payment_account": account, "status": "pending",
    }
    notify_text = _admin_text(wd)
    notify_kb   = _admin_keyboard(wd)

    try:
        await context.bot.send_message(
            ADMIN_ID, notify_text, parse_mode="HTML", reply_markup=notify_kb,
        )
    except Exception as e:
        logger.error(f"Withdraw admin notify error: {e}")

    if WITHDRAW_CHANNEL_ID:
        try:
            await context.bot.send_message(
                WITHDRAW_CHANNEL_ID, notify_text, parse_mode="HTML", reply_markup=notify_kb,
            )
        except Exception as e:
            logger.error(f"Withdraw channel notify error: {e}")

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
