import asyncio
# services/otp.py
"""
Unified OTP sender — used by ALL panels (A1, A2, S3, S4 Shark).
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
from utils.helpers import escape_mdv2

logger = get_logger(__name__)

_posted_sms_ids: set[str] = set()


async def _safe_send(bot, chat_id: int, text: str, **kwargs) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as e:
        logger.error(f"_safe_send error chat_id={chat_id}: {e}")


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
    if not OTP_CHANNEL_ID:
        logger.warning("OTP_CHANNEL_ID not set — skipping channel post")
        return

    try:
        app_cap   = app.capitalize()
        clean_num = str(number).replace("+", "").strip()
        if len(clean_num) > 7:
            hidden = "+" + clean_num[:4] + "******" + clean_num[-3:]
        else:
            hidden = clean_num

        # Dedup
        uid = f"ch_{clean_num}_{otp}"
        if uid in _posted_sms_ids:
            return
        _posted_sms_ids.add(uid)
        if len(_posted_sms_ids) > 10_000:
            for k in list(_posted_sms_ids)[:5000]:
                _posted_sms_ids.discard(k)

        clean_sms   = raw_sms.replace("<#>", "").strip() if raw_sms else ""
        panel_label = str(panel).upper()

        # Detect language
        try:
            from utils.helpers import detect_language_from_sms
            lang = detect_language_from_sms(clean_sms)
        except Exception:
            lang = "English"

        msg = (
            f"{flag} {escape_mdv2(country)}\n\n"
            f"\U0001f4f1 Number : {escape_mdv2(hidden)}\n"
            f"\U0001f510 Code : {escape_mdv2(otp)}\n"
            f"\U0001f4cc Panel : {escape_mdv2(panel_label)} \\| {escape_mdv2(app_cap)}\n"
            f"\U0001f5e3 Language : {escape_mdv2(lang)}"
        )
        if clean_sms:
            quoted = "\n".join(
                f">{escape_mdv2(line)}"
                for line in clean_sms.splitlines()
                if line.strip()
            )
            msg += f"\n\n{quoted}"

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

        try:
            await _safe_send(
                bot, OTP_CHANNEL_ID, msg,
                parse_mode="MarkdownV2", reply_markup=keyboard,
            )
            logger.info(f"OTP channel post OK: {clean_num} | {otp}")
        except Exception as e:
            logger.warning(f"MarkdownV2 failed, trying plain: {e}")
            plain = f"{flag} {country}\n📱 {hidden}\n🔐 {otp}\n📌 {panel_label} | {app_cap}"
            if clean_sms:
                plain += f"\n\n{clean_sms[:150]}"
            await _safe_send(bot, OTP_CHANNEL_ID, plain, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"send_otp_to_channel error: {e}", exc_info=True)


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
    try:
        clean_num   = str(number).replace("+", "").strip()
        app_cap     = app.capitalize()
        panel_label = panel.upper()

        header = f"{flag} {country} {app_cap} ({panel_label}) \u2192 \U0001f7e2\n\U0001f4b0 +0.20 Tk credited!"

        buttons = []
        buttons.append([InlineKeyboardButton(
            f"📱 Phone : {clean_num}",
            copy_text=CopyTextButton(text=clean_num),
            api_kwargs={"style": "primary"},
        )])

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

        # Award balance
        try:
            from database.supabase import db_award_otp_balance
            asyncio.create_task(db_award_otp_balance(chat_id))
        except Exception:
            pass

    except Exception as e:
        logger.error(f"send_otp_to_inbox error chat_id={chat_id}: {e}", exc_info=True)
