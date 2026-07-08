# panels/s4_shark.py
"""
S4 Panel — Shark (CR API Webhook)
───────────────────────────────────
Responsibilities:
  • Poll Supabase shark_otps table (written by external CR API webhook)
  • Send OTPs to OTP channel + matching user inboxes
  • Only notifies users whose pool_key is a Shark (_v1) pool
  • Dedup via _shark_otp_seen set (in-memory, max 5000 entries)

No S1/S2 code. Fully independent of X.Mint/StexSMS.
"""

import asyncio
import hashlib

from config import (
    OTP_CHANNEL_LINK,
    MAIN_CHANNEL_LINK,
    JOIN_CHANNEL_LINK,
    NUMBER_BOT_LINK,
)
from database.supabase import fetch_shark_otps_from_supabase
from panels.s3 import (
    s3_find_users_by_number,
    s3_get_session,
    is_shark_pool,
    _s3_send_with_retry,
)
from utils.logger import get_logger
from utils.helpers import (
    extract_otp,
    extract_country_code_from_number,
    detect_app_from_message,
    hide_number,
    COUNTRY_FLAGS_CODE,
    COUNTRY_NAMES_CODE,
)

logger = get_logger(__name__)

# ── Dedup seen set ──
_shark_otp_seen: set[str] = set()


# ══════════════════════════════════════════════════════════
#              JOB — POLL SHARK OTPs (S4)
# ══════════════════════════════════════════════════════════

async def poll_shark_otps(context) -> None:
    """
    APScheduler job — polls Supabase shark_otps for new rows.
    Called from services/jobs.py every 30s (alongside S3 poll).

    Flow:
      1. Fetch recent rows from shark_otps table
      2. Skip already-seen unique_ids
      3. Post OTP to channel
      4. Notify matching user inboxes (v1 pool only)
    """
    global _shark_otp_seen

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton
    from services.otp import send_otp_to_channel

    try:
        shark_rows = await fetch_shark_otps_from_supabase()
        if not shark_rows:
            return

        # ── Channel keyboard ──
        ch_link = OTP_CHANNEL_LINK or MAIN_CHANNEL_LINK or JOIN_CHANNEL_LINK or ""
        kb_btns = []
        if ch_link and len(ch_link) > 10:
            kb_btns.append(InlineKeyboardButton(
                "📢 Main Channel", url=ch_link,
                api_kwargs={"style": "primary"},
            ))
        if NUMBER_BOT_LINK:
            kb_btns.append(InlineKeyboardButton(
                "🤖 Number Bot", url=NUMBER_BOT_LINK,
                api_kwargs={"style": "danger"},
            ))
        otp_channel_keyboard = InlineKeyboardMarkup([kb_btns]) if kb_btns else None

        sent_count = 0

        for row in shark_rows:
            uid_key = row.get("unique_id", "")
            if not uid_key or uid_key in _shark_otp_seen:
                continue

            number   = str(row.get("number",  "")).strip()
            message  = str(row.get("message", "")).strip()
            otp_code = str(row.get("otp",     "")).strip()

            if not number or not message:
                continue

            # Mark seen
            _shark_otp_seen.add(uid_key)
            if len(_shark_otp_seen) > 5000:
                _shark_otp_seen = set(list(_shark_otp_seen)[-2500:])

            # Fallback: extract OTP from message if row.otp is empty
            if not otp_code:
                otp_code = extract_otp(message) or ""
            if not otp_code:
                logger.info(f"S4 skip — no OTP for {hide_number(number)}")
                continue

            country      = extract_country_code_from_number(number)
            flag         = COUNTRY_FLAGS_CODE.get(country, "🌍")
            country_name = COUNTRY_NAMES_CODE.get(country, "Unknown")
            detected_app = detect_app_from_message(message, default_app="FACEBOOK")
            app_cap      = detected_app.capitalize()

            logger.info(f"S4 OTP: {hide_number(number)} | {otp_code} | {app_cap}")

            # ── Channel post ──
            try:
                await send_otp_to_channel(
                    context.bot, number, otp_code,
                    app_cap, country_name, flag, message, "S3 V1",
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"S4 channel post error: {e}")

            # ── User inbox — only v1 (Shark) pool users ──
            matched_users = s3_find_users_by_number(number)
            for uid in matched_users:
                try:
                    session  = s3_get_session(int(uid))
                    pool_key = session.get("pool_key", "") if session else ""
                    if not is_shark_pool(pool_key):
                        continue

                    header = f"{flag} {country_name} {app_cap} (S3 V1) → 🟢"

                    kb_inbox = [[InlineKeyboardButton(
                        f"📱 Phone : {number}",
                        copy_text=CopyTextButton(text=str(number)),
                        api_kwargs={"style": "primary"},
                    )]]
                    btn_row = []
                    if message:
                        btn_row.append(InlineKeyboardButton(
                            "📋 Full SMS",
                            copy_text=CopyTextButton(text=message[:256]),
                            api_kwargs={"style": "success"},
                        ))
                    if otp_code:
                        btn_row.append(InlineKeyboardButton(
                            f"📋 {otp_code}",
                            copy_text=CopyTextButton(text=otp_code),
                            api_kwargs={"style": "success"},
                        ))
                    if btn_row:
                        kb_inbox.append(btn_row)

                    await _s3_send_with_retry(
                        context.bot, int(uid), header,
                        reply_markup=InlineKeyboardMarkup(kb_inbox),
                    )
                except Exception as e:
                    logger.error(f"S4 inbox error [{uid}]: {e}")

        if sent_count:
            logger.info(f"🦈 S4 Shark poll done: {sent_count} OTPs sent")

    except Exception as e:
        logger.error(f"poll_shark_otps error: {e}")
