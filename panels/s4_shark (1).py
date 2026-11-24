# panels/s4_shark.py
"""
S4 Panel — Shark (REST API polling)
───────────────────────────────────
Responsibilities:
  • Poll the SharkSMS REST API (GET /api/v1/messages) for new messages
  • Send OTPs to OTP channel + matching user inboxes
  • Only notifies users whose pool_key is a Shark (_v1) pool
  • Dedup via _shark_otp_seen set (in-memory, max 5000 entries)

No S1/S2 code. Fully independent of X.Mint/StexSMS.

Uses its own config.py values (set them in Railway Variables).
These are separate from CR_API_URL / CR_API_TOKEN, which S3 uses:
  SHARK_API_URL   = https://sharksms.org   (panel address only, no /api/v1)
  SHARK_API_TOKEN = your SharkSMS API token
"""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import httpx

from config import (
    OTP_CHANNEL_LINK,
    MAIN_CHANNEL_LINK,
    JOIN_CHANNEL_LINK,
    NUMBER_BOT_LINK,
    SHARK_API_URL,
    SHARK_API_TOKEN,
)
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

# ── API settings ──
SHARK_API_BASE_URL = (SHARK_API_URL or "https://sharksms.org").rstrip("/")

# Only look back a few minutes each poll. Must be larger than the poll
# interval (30s) so nothing is missed between two polls.
SHARK_API_WINDOW_MIN = 3
# /messages returns only 10 records by default, so always ask for more.
SHARK_API_LIMIT = 200


# ══════════════════════════════════════════════════════════
#              FETCH — SharkSMS REST API
# ══════════════════════════════════════════════════════════

def _make_unique_id(rec: dict) -> str:
    """The API has no message id, so build a stable one from the record."""
    raw = "|".join(
        str(rec.get(k, "")) for k in ("time", "number", "cli", "content")
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


async def fetch_shark_otps_from_api() -> list[dict]:
    """
    Fetch recent messages from GET /api/v1/messages.

    Returns rows shaped like the old Supabase rows
    (unique_id / number / message / otp), oldest first, so OTPs
    are delivered in the order they arrived.
    """
    if not SHARK_API_TOKEN:
        logger.error("S4: SHARK_API_TOKEN is not set")
        return []

    since = datetime.now(timezone.utc) - timedelta(minutes=SHARK_API_WINDOW_MIN)
    params = {
        "from": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": SHARK_API_LIMIT,
    }
    # Token goes in the header (not the URL) so it never leaks into logs.
    headers = {"Authorization": f"Bearer {SHARK_API_TOKEN}"}
    url = f"{SHARK_API_BASE_URL}/api/v1/messages"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as e:
        logger.error(f"S4 API request failed: {type(e).__name__}")
        return []

    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After", "?")
        logger.warning(f"S4 API rate limited, retry after {retry}s")
        return []

    if resp.status_code != 200:
        try:
            code = resp.json().get("error", "unknown")
        except ValueError:
            code = "unknown"
        logger.error(f"S4 API error {resp.status_code}: {code}")
        return []

    try:
        records = resp.json().get("records", [])
    except ValueError:
        logger.error("S4 API returned invalid JSON")
        return []

    rows = []
    # API returns newest first -> reverse so oldest is processed first.
    for rec in reversed(records):
        rows.append({
            "unique_id": _make_unique_id(rec),
            "number":    rec.get("number", ""),
            "message":   rec.get("content", ""),
            "otp":       "",  # API has no otp field; extracted from message below
            "cli":       rec.get("cli", ""),
            "status":    rec.get("status", ""),
        })
    return rows


# ══════════════════════════════════════════════════════════
#              JOB — POLL SHARK OTPs (S4)
# ══════════════════════════════════════════════════════════

async def poll_shark_otps(context) -> None:
    """
    APScheduler job — polls the SharkSMS API for new messages.
    Called from services/jobs.py every 30s (alongside S3 poll).

    Flow:
      1. Fetch recent messages from /api/v1/messages
      2. Skip already-seen messages
      3. Post OTP to channel
      4. Notify matching user inboxes (v1 pool only)
    """
    global _shark_otp_seen

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton
    from services.otp import send_otp_to_channel

    try:
        shark_rows = await fetch_shark_otps_from_api()
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

            # API has no otp field -> extract OTP from message
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
