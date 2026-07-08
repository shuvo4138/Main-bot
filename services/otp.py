# services/otp.py
"""
Unified OTP sender — used by ALL panels (A1, A2, S3, S4 Shark).

Two public functions:
  send_otp_to_channel()  — broadcast to OTP channel
  send_otp_to_inbox()    — send to individual user chat

Both are panel-agnostic; the `panel` param is just a label string.
"""

from telegram import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton

from config import (
    OTP_CHANNEL_ID,
    OTP_CHANNEL_LINK,
    MAIN_CHANNEL_LINK,
    JOIN_CHANNEL_LINK,
    NUMBER_BOT_LINK,
)
from utils.logger import get_logger
from utils.helpers import escape_mdv2, extract_country_code_from_number

logger = get_logger(__name__)

# ── In-memory dedup set: prevents the same OTP being posted twice ──
_posted_sms_ids: set[str] = set()


# ══════════════════════════════════════════════════════════
#                  SAFE SEND HELPER
# ══════════════════════════════════════════════════════════

async def _safe_send(bot, chat_id: int, text: str, **kwargs) -> None:
    """Send a message, silently logging any error instead of crashing."""
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as e:
        logger.error(f"_safe_send error chat_id={chat_id}: {e}")


# ══════════════════════════════════════════════════════════
#                  CHANNEL BROADCAST
# ══════════════════════════════════════════════════════════

async def send_otp_to_channel(
    bot,
    number: str,
    otp: str,
    app: str,
    country: str,
    flag: str,
    raw_sms: str = "",
    panel: str   = "A1",
) -> None:
    """
    Post an OTP to the OTP channel.

    Format (MarkdownV2):
      🇲🇿 Mozambique
      📱 Number : +258xxxx***855
      🔐 Code   : 123456
      📌 Panel  : A1 | Facebook
      > Full SMS line 1
      > Full SMS line 2

    Keyboard:
      [ 🔑 COPY OTP ]
      [ 📢 Main Channel ]  [ 🤖 Number Bot ]
    """
    if not OTP_CHANNEL_ID:
        return

    try:
        app_cap   = app.capitalize()
        clean_num = str(number).replace("+", "").strip()
        if len(clean_num) > 7:
            hidden = "+" + clean_num[:4] + "******" + clean_num[-3:]
        else:
            hidden = clean_num

        # ── Dedup guard ──
        uid = f"ch_{clean_num}_{otp}"
        if uid in _posted_sms_ids:
            logger.info(f"send_otp_to_channel: duplicate skip {uid}")
            return
        _posted_sms_ids.add(uid)
        # Trim set to avoid unbounded growth
        if len(_posted_sms_ids) > 10_000:
            for k in list(_posted_sms_ids)[:5000]:
                _posted_sms_ids.discard(k)

        clean_sms = raw_sms.replace("<#>", "").strip() if raw_sms else ""
        panel_label = str(panel).upper()

        msg = (
            f"{flag} {escape_mdv2(country)}\n\n"
            f"📱 Number : {escape_mdv2(hidden)}\n"
            f"🔐 Code : {escape_mdv2(otp)}\n"
            f"📌 Panel : {escape_mdv2(panel_label)} \\| {escape_mdv2(app_cap)}"
        )
        if clean_sms:
            quoted = "\n".join(
                f">{escape_mdv2(line)}"
                for line in clean_sms.splitlines()
                if line.strip()
            )
            msg += f"\n\n{quoted}"

        # ── Keyboard ──
        kb_rows = []
        if otp:
            kb_rows.append([InlineKeyboardButton(
                "🔑 COPY OTP",
                copy_text=CopyTextButton(text=otp),
                api_kwargs={"style": "success"},
            )])
        ch_link = OTP_CHANNEL_LINK or MAIN_CHANNEL_LINK or JOIN_CHANNEL_LINK or ""
        bottom  = []
        if ch_link and len(ch_link) > 10:
            bottom.append(InlineKeyboardButton(
                "📢 Main Channel", url=ch_link,
                api_kwargs={"style": "primary"},
            ))
        if NUMBER_BOT_LINK:
            bottom.append(InlineKeyboardButton(
                "🤖 Number Bot", url=NUMBER_BOT_LINK,
                api_kwargs={"style": "danger"},
            ))
        if bottom:
            kb_rows.append(bottom)
        keyboard = InlineKeyboardMarkup(kb_rows) if kb_rows else None

        # Try MarkdownV2, fall back to plain text
        try:
            await _safe_send(
                bot, OTP_CHANNEL_ID, msg,
                parse_mode="MarkdownV2", reply_markup=keyboard,
            )
        except Exception:
            plain = f"{flag} {country}\n📱 Number : {hidden}\n🔐 Code : {otp}"
            if clean_sms:
                plain += f"\n\n{clean_sms[:150]}"
            await _safe_send(bot, OTP_CHANNEL_ID, plain, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"send_otp_to_channel error: {e}")


# ══════════════════════════════════════════════════════════
#                  USER INBOX NOTIFICATION
# ══════════════════════════════════════════════════════════

async def send_otp_to_inbox(
    bot,
    chat_id: int,
    number: str,
    otp: str,
    app: str,
    country: str,
    flag: str,
    raw_sms: str = "",
    panel: str   = "A1",
) -> None:
    """
    Send an OTP notification directly to the user's chat.

    Design:
      🇲🇿 Mozambique Facebook (A1) → 🟢
      [ 📱 Phone : 258871116855 ]
      [ 📋 Full SMS ]  [ 📋 123456 ]
    """
    try:
        clean_num   = str(number).replace("+", "").strip()
        app_cap     = app.capitalize()
        panel_label = panel.upper()

        header = f"{flag} {country} {app_cap} ({panel_label}) → 🟢"

        buttons = []

        # Row 1 — phone copy
        buttons.append([InlineKeyboardButton(
            f"📱 Phone : {clean_num}",
            copy_text=CopyTextButton(text=clean_num),
            api_kwargs={"style": "primary"},
        )])

        # Row 2 — SMS + OTP copy
        row2 = []
        if raw_sms:
            row2.append(InlineKeyboardButton(
                "📋 Full SMS",
                copy_text=CopyTextButton(text=raw_sms[:256]),
                api_kwargs={"style": "success"},
            ))
        if otp:
            row2.append(InlineKeyboardButton(
                f"📋 {otp}",
                copy_text=CopyTextButton(text=otp),
                api_kwargs={"style": "success"},
            ))
        if row2:
            buttons.append(row2)

        await bot.send_message(
            chat_id,
            header,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )

    except Exception as e:
        logger.error(f"send_otp_to_inbox error chat_id={chat_id}: {e}")
