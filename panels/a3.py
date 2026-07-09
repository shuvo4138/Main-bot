# panels/a3.py
"""
A3 Panel — YesSMS (yesms.online)
──────────────────────────────────
Endpoints:
  GET  /api/live_access_data  → console data (ranges + OTPs)
  POST /api/allocate_number   → provision number
  GET  /api/user_numbers      → OTP success logs

Auth: Header "authkey: API_KEY"
"""

import asyncio
import re
import time

import httpx

from config import OTP_CHANNEL_LINK, OTP_CHANNEL_JOIN_LINK, YESMS_API_KEY
from utils.logger import get_logger
from utils.helpers import (
    extract_otp,
    get_flag_by_iso,
    COUNTRY_FLAGS_CODE,
    COUNTRY_NAMES_CODE,
)
from utils.state import user_data, user_msg

logger = get_logger(__name__)

# ── Config ──
YESMS_BASE_URL = "https://yesms.online"

# ── Cache ──
_ranges_cache: dict = {"data": [], "time": 0}
CACHE_TTL = 60

# ── OTP dedup ──
_seen_otps: set[str] = set()


def _headers() -> dict:
    return {
        "authkey":      YESMS_API_KEY,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }


# ══════════════════════════════════════════════════════════
#                  RANGES
# ══════════════════════════════════════════════════════════

async def a3_get_active_ranges(force: bool = False) -> list[dict]:
    """
    Fetch live console data from /api/console_data.
    Returns list of {range_id, country, flag, service, otp, message}.
    Each row: [range_id, "<flag emoji> <country>", message, service, timestamp]
    """
    global _ranges_cache
    if (
        not force
        and (time.time() - _ranges_cache["time"]) < CACHE_TTL
        and _ranges_cache["data"]
    ):
        return _ranges_cache["data"]

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"{YESMS_BASE_URL}/api/console_data",
                headers=_headers(),
            )
        if res.status_code != 200:
            logger.warning(f"A3 console_data HTTP {res.status_code}")
            return _ranges_cache["data"]

        data = res.json()
        table = data.get("table", [])
        if not isinstance(table, list):
            return _ranges_cache["data"]

        ranges_out: list[dict] = []
        seen: set[str] = set()

        for row in table:
            if not isinstance(row, list) or len(row) < 4:
                continue
            range_id = str(row[0]).strip()
            country  = str(row[1]).strip()
            message  = str(row[2]).strip()
            service  = str(row[3]).strip()

            if not range_id or range_id in seen:
                continue
            seen.add(range_id)

            # Extract flag + clean country name
            flag = "🌍"
            country_clean = country
            flag_match = re.match(r'([\U0001F1E0-\U0001F1FF]{2})\s*(.*)', country)
            if flag_match:
                flag          = flag_match.group(1)
                country_clean = flag_match.group(2).strip()
            country_clean = re.sub(
                r',?\s*(State of|Islamic Republic of|Republic of|Federation)',
                '', country_clean
            ).strip()

            otp = extract_otp(message) or ""

            ranges_out.append({
                "range_id":     range_id,
                "country":      country_clean,
                "flag":         flag,
                "service":      service,
                "otp":          otp,
                "message":      message,
            })

        _ranges_cache = {"data": ranges_out, "time": time.time()}
        logger.info(f"A3 ranges: {len(ranges_out)} loaded")
        return ranges_out

    except Exception as e:
        logger.error(f"a3_get_active_ranges error: {type(e).__name__}: {e!r}")
        return _ranges_cache["data"]


# ══════════════════════════════════════════════════════════
#                  NUMBER PROVISION
# ══════════════════════════════════════════════════════════

async def a3_allocate_number(range_id: str) -> dict | None:
    """
    POST /api/allocate_number with range_id.
    Returns data dict with full_number on success, None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.post(
                f"{YESMS_BASE_URL}/api/allocate_number",
                headers=_headers(),
                json={"range_id": range_id},
            )
        if res.status_code == 200:
            data = res.json()
            if data.get("success"):
                return data.get("data", {})
        logger.warning(f"A3 allocate_number failed: {res.status_code} {res.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"a3_allocate_number error: {type(e).__name__}: {e!r}")
        return None


# ══════════════════════════════════════════════════════════
#                  OTP FETCH
# ══════════════════════════════════════════════════════════

async def a3_fetch_otps() -> list[dict]:
    """
    GET /api/user_numbers — fetch recent OTP success logs.
    Returns list of {number, otp_code, full_message, time}.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"{YESMS_BASE_URL}/api/user_numbers",
                headers=_headers(),
            )
        if res.status_code != 200:
            logger.warning(f"A3 user_numbers HTTP {res.status_code}")
            return []
        data = res.json()
        if not data.get("success"):
            return []
        return data.get("logs", [])
    except Exception as e:
        logger.error(f"a3_fetch_otps error: {e}")
        return []


# ══════════════════════════════════════════════════════════
#              NUMBER ASSIGNMENT + OTP FLOW
# ══════════════════════════════════════════════════════════

async def do_get_number_a3(message, user_id: int, bot=None) -> None:
    """
    Main entry point for A3 number assignment.
    Flow: allocate → show card → poll OTP (60s interval, 20 min max)
    """
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton
    from database.supabase import db_save_user_async
    from handlers.helpers import cancel_all_otp_tasks, add_otp_task
    from services.otp import send_otp_to_inbox, send_otp_to_channel

    _bot    = bot or message.bot
    chat_id = message.chat.id

    range_id = user_data[user_id].get("range", "")
    app_name = user_data[user_id].get("app", "FACEBOOK")
    country  = user_data[user_id].get("a3_country", "Unknown")
    flag     = user_data[user_id].get("a3_flag", "🌍")

    existing_msg_id = user_msg.get(chat_id)

    if not range_id:
        err = "❌ Range পাওয়া যায়নি। আবার শুরু করুন।"
        if existing_msg_id:
            try:
                await _bot.edit_message_text(err, chat_id=chat_id, message_id=existing_msg_id)
            except Exception:
                await _bot.send_message(chat_id, err)
        else:
            await _bot.send_message(chat_id, err)
        return

    # ── Fetch 2 numbers ──
    nums: list[str] = []
    for _ in range(2):
        result = await a3_allocate_number(range_id)
        if result:
            full = (
                result.get("full_number")
                or result.get("national_number")
                or result.get("number")
                or ""
            )
            full = re.sub(r'\D', '', str(full)).strip()
            if full and full not in nums:
                nums.append(full)
        await asyncio.sleep(0.5)

    if not nums:
        err = "❌ এই range এ এখন number পাওয়া যাচ্ছে না।\n\nকিছুক্ষণ পর আবার try করুন।"
        if existing_msg_id:
            try:
                await _bot.edit_message_text(err, chat_id=chat_id, message_id=existing_msg_id)
            except Exception:
                await _bot.send_message(chat_id, err)
        else:
            await _bot.send_message(chat_id, err)
        return

    user_data[user_id].update({
        "numbers":         nums,
        "last_number":     nums[0],
        "panel":           "A3",
        "otp_active":      True,
        "auto_otp_cancel": False,
    })

    asyncio.create_task(db_save_user_async(user_id, {
        "name":        user_data[user_id].get("name", "User"),
        "joined":      user_data[user_id].get("joined", ""),
        "app":         app_name,
        "panel":       "A3",
        "last_number": nums[0],
        "range":       range_id,
    }))

    # ── Number card ──
    card = (
        f"✅ <b>Numbers Assigned!</b>\n\n"
        f"<b>Service:</b> {app_name.title()} [A3]\n"
        f"🌍 <b>Country:</b> {flag} {country}\n"
        f"⏳ <b>Reserved:</b> 20 min\n\n"
        f"📩 OTPs forwarded automatically."
    )
    colors  = ["success", "primary"]
    kb_rows = []
    for i, num in enumerate(nums):
        kb_rows.append([InlineKeyboardButton(
            f"📋 {num}",
            copy_text=CopyTextButton(text=num),
            api_kwargs={"style": colors[i % 2]},
        )])
    kb_rows.append([InlineKeyboardButton(
        "🔄 Change Numbers", callback_data="a3_change_numbers",
        api_kwargs={"style": "success"},
    )])
    kb_rows.append([InlineKeyboardButton(
        "🌍 Change Region", callback_data="back_app",
        api_kwargs={"style": "primary"},
    )])
    ch_link = OTP_CHANNEL_LINK or OTP_CHANNEL_JOIN_LINK or ""
    if ch_link:
        kb_rows.append([InlineKeyboardButton(
            "📢 OTP Channel", url=ch_link,
            api_kwargs={"style": "primary"},
        )])
    kb = InlineKeyboardMarkup(kb_rows)

    if existing_msg_id:
        try:
            await _bot.edit_message_text(
                card, chat_id=chat_id, message_id=existing_msg_id,
                parse_mode="HTML", reply_markup=kb,
            )
            user_msg[chat_id] = existing_msg_id
        except Exception:
            sent = await _bot.send_message(chat_id, card, parse_mode="HTML", reply_markup=kb)
            user_msg[chat_id] = sent.message_id
    else:
        sent = await _bot.send_message(chat_id, card, parse_mode="HTML", reply_markup=kb)
        user_msg[chat_id] = sent.message_id

    # ── OTP poll task ──
    cancel_all_otp_tasks(user_id)
    user_data[user_id]["auto_otp_cancel"] = False   # reset — cancel_all_otp_tasks() sets this True to kill the OLD task; it must not also kill this NEW one

    async def _poll_a3():
        deadline  = time.time() + 20 * 60
        received: set[str] = set()
        # Force correct state at task start (create_task runs on next event loop tick)
        user_data[user_id]["auto_otp_cancel"] = False
        logger.info(f"A3 DEBUG poll started for nums={nums}")

        while True:
            if user_data.get(user_id, {}).get("auto_otp_cancel"):
                break
            if time.time() > deadline:
                break

            try:
                logs = await a3_fetch_otps()
                logger.info(f"A3 DEBUG fetched {len(logs)} logs, numbers={[l.get('number') for l in logs]}")
            except Exception as e:
                logger.error(f"A3 poll error: {e}")
                await asyncio.sleep(10)
                continue

            for log in logs:
                raw_num  = re.sub(r'\D', '', str(log.get("number", ""))).strip()
                otp_code = log.get("otp_code") or extract_otp(log.get("full_message", "")) or ""
                msg_text = log.get("full_message", "")
                log_time = log.get("time", "")

                if not otp_code:
                    continue

                # Match against our numbers
                for num in nums:
                    clean_num = re.sub(r'\D', '', num)
                    if not (raw_num == clean_num or
                            raw_num[-9:] == clean_num[-9:]):
                        continue

                    uid_key = f"{num}_{otp_code}"
                    if uid_key in received:
                        continue
                    received.add(uid_key)

                    logger.info(f"A3 OTP → {num} | {otp_code}")

                    asyncio.create_task(send_otp_to_channel(
                        _bot, num, otp_code,
                        app_name.capitalize(), country, flag, msg_text, "A3",
                    ))
                    try:
                        await send_otp_to_inbox(
                            _bot, chat_id, num, otp_code,
                            app_name.capitalize(), country, flag, msg_text, "A3",
                        )
                    except Exception as e:
                        logger.error(f"A3 inbox error: {e}")

            await asyncio.sleep(10)

        user_data[user_id]["otp_active"] = False

    poll_task = asyncio.create_task(_poll_a3())
    add_otp_task(user_id, poll_task)
