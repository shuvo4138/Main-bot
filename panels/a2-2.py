# panels/a2.py
"""
A2 Panel — VOLTX SMS API
─────────────────────────
Responsibilities:
  • Fetch active ranges from VOLTX /liveaccess
  • Provision numbers via /getnum
  • Poll OTPs via /success-otp
  • Background auto_otp_a2 task (10s interval, 20 min max)
  • job_a2_range_post — broadcast live ranges to channel every 5 min
"""

import asyncio
import re
import time
from datetime import datetime, timezone, timedelta

import httpx

from config import (
    A2_API_KEY,
    A2_BASE_URL,
    A2_CACHE_TTL,
    A2_OTP_BASE_TIMEOUT_SECONDS,
    OTP_CHANNEL_LINK,
    OTP_CHANNEL_JOIN_LINK,
    RANGE_CHANNEL_ID,
)
from utils.logger import get_logger
from utils.helpers import (
    extract_otp,
    extract_country_code_from_number,
    detect_app_from_message,
    escape_mdv2,
    COUNTRY_FLAGS_CODE,
    COUNTRY_NAMES_CODE,
)
from utils.state import user_data, user_msg, a2_poll_tasks, _a2_range_cache

logger = get_logger(__name__)

# ── Module-level range cache (re-exported via state._a2_range_cache) ──
_ranges_cache: dict = {"data": [], "time": 0}


# ══════════════════════════════════════════════════════════
#                  API HELPERS
# ══════════════════════════════════════════════════════════

def _a2_headers() -> dict:
    return {
        "mauthapi":     A2_API_KEY,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }


def a2_extract_country_code(rng: str) -> str:
    """
    Extract the dial-code prefix from a range string like "22465XXX".
    Strips trailing X's then tries 3 → 2 → 1 digit match.
    """
    clean = re.sub(r'X+$', '', str(rng).upper().strip())
    for length in (3, 2, 1):
        if len(clean) >= length:
            c = clean[:length]
            if c in COUNTRY_NAMES_CODE:
                return c
    return clean[:3] if len(clean) >= 3 else clean


# ══════════════════════════════════════════════════════════
#                  RANGE FUNCTIONS
# ══════════════════════════════════════════════════════════

def a2_get_cached_ranges() -> list:
    """Return the last cached A2 range list (may be empty on cold start)."""
    return _ranges_cache.get("data", [])


async def a2_get_active_ranges(force: bool = False) -> list[dict]:
    """
    Fetch live ranges from VOLTX /liveaccess.
    Cached for A2_CACHE_TTL seconds unless force=True.
    Returns list of {range, rid, service, last_at}.
    """
    global _ranges_cache
    if (
        not force
        and (time.time() - _ranges_cache["time"]) < A2_CACHE_TTL
        and _ranges_cache["data"]
    ):
        return _ranges_cache["data"]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.get(f"{A2_BASE_URL}/liveaccess", headers=_a2_headers())

        logger.info(f"A2 liveaccess status: {res.status_code}")
        if res.status_code != 200:
            logger.warning(f"A2 liveaccess failed: {res.status_code}")
            return _ranges_cache["data"]

        data = res.json()
        if data.get("meta", {}).get("code") != 200:
            logger.warning(f"A2 meta check failed: {data.get('meta')}")
            return _ranges_cache["data"]

        raw_data   = data.get("data", {})
        ranges_out: list[dict] = []

        # ── Case 1: {"services": [...]} ──
        services = raw_data.get("services", []) if isinstance(raw_data, dict) else []
        if services:
            for svc in services:
                sid = svc.get("sid", "").upper()
                for rng in svc.get("ranges", []):
                    rng_u = rng.upper().strip()
                    ranges_out.append({"range": rng_u, "rid": rng_u,
                                       "service": sid, "last_at": svc.get("last_at", 0)})

        # ── Case 2: {"ranges": [...]} ──
        elif isinstance(raw_data, dict) and raw_data.get("ranges"):
            for rng in raw_data["ranges"]:
                if isinstance(rng, dict):
                    rng_u = (rng.get("range") or rng.get("rid") or rng.get("id", "")).upper().strip()
                    ranges_out.append({"range": rng_u, "rid": rng_u,
                                       "service": rng.get("service", rng.get("sid", "")),
                                       "last_at": rng.get("last_at", 0)})
                else:
                    rng_u = str(rng).upper().strip()
                    ranges_out.append({"range": rng_u, "rid": rng_u, "service": "", "last_at": 0})

        # ── Case 3: data is a list ──
        elif isinstance(raw_data, list):
            for item in raw_data:
                if isinstance(item, dict):
                    sid    = item.get("sid", item.get("service", "")).upper()
                    nested = item.get("ranges", [])
                    if nested:
                        for rng in nested:
                            rng_u = str(rng).upper().strip()
                            if rng_u:
                                ranges_out.append({"range": rng_u, "rid": rng_u,
                                                   "service": sid, "last_at": item.get("last_at", 0)})
                    else:
                        rng_u = (item.get("range") or item.get("rid") or item.get("id", "")).upper().strip()
                        if rng_u:
                            ranges_out.append({"range": rng_u, "rid": rng_u,
                                               "service": sid, "last_at": item.get("last_at", 0)})
                else:
                    rng_u = str(item).upper().strip()
                    if rng_u:
                        ranges_out.append({"range": rng_u, "rid": rng_u, "service": "", "last_at": 0})

        logger.info(f"A2 parsed {len(ranges_out)} ranges | samples: {ranges_out[:3]}")
        _ranges_cache = {"data": ranges_out, "time": time.time()}
        return ranges_out

    except Exception as e:
        logger.error(f"A2 active-ranges error: {e}")
        return _ranges_cache["data"]


# ══════════════════════════════════════════════════════════
#                  NUMBER PROVISION
# ══════════════════════════════════════════════════════════

async def a2_get_number(rid: str) -> dict | None:
    """
    Provision a number via VOLTX POST /getnum.
    Returns the data dict (includes _rid) on success, None on failure.
    """
    clean_rid = re.sub(r'X+$', '', str(rid).upper().strip())
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.post(
                f"{A2_BASE_URL}/getnum",
                headers=_a2_headers(),
                json={"rid": clean_rid},
            )
        logger.info(f"A2 getnum status: {res.status_code} | rid: {clean_rid}")
        if res.status_code == 200:
            data = res.json()
            if data.get("meta", {}).get("code") == 200:
                result = data.get("data", {})
                result["_rid"] = data.get("rid", "")
                return result
        logger.warning(f"A2 getnum failed: {res.status_code} {res.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"A2 getnum error: {e}")
        return None


# ══════════════════════════════════════════════════════════
#                  OTP POLLING
# ══════════════════════════════════════════════════════════

async def a2_fetch_all_otps(rid: str | None = None) -> list[dict]:
    """
    Fetch last 50 OTPs from VOLTX GET /success-otp.
    rid param is accepted for logging only — API returns all allocated OTPs.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.get(f"{A2_BASE_URL}/success-otp", headers=_a2_headers())
        if res.status_code != 200:
            logger.warning(f"A2 success-otp HTTP {res.status_code} | rid={rid}")
            return []
        data = res.json()
        if data.get("meta", {}).get("code") != 200:
            logger.warning(f"A2 success-otp meta error: {data.get('meta')} | rid={rid}")
            return []
        raw = data.get("data", {})
        otps = raw.get("otps", []) if isinstance(raw, dict) else []
        logger.info(f"A2 success-otp fetched {len(otps)} otps | rid={rid}")
        return otps
    except Exception as e:
        logger.error(f"A2 fetch_all_otps error: {e}")
        return []


async def a2_get_console_hits() -> list[dict]:
    """Fetch VOLTX GET /console hits for range post job."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.get(f"{A2_BASE_URL}/console", headers=_a2_headers())
        if res.status_code == 200:
            data = res.json()
            if data.get("meta", {}).get("code") == 200:
                return data.get("data", {}).get("hits", [])
        return []
    except Exception as e:
        logger.error(f"A2 console error: {e}")
        return []


# ══════════════════════════════════════════════════════════
#              NUMBER ASSIGNMENT + OTP FLOW
# ══════════════════════════════════════════════════════════

async def do_get_number_a2(message, user_id: int, bot=None) -> None:
    """
    Main entry point for A2 number assignment.

    Flow:
      1. Provision 2 numbers via /getnum
      2. Show number card with copy buttons
      3. Start auto_otp_a2 background task
    """
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton
    from database.supabase import db_save_user_async
    from handlers.helpers import cancel_all_otp_tasks, add_otp_task

    _bot    = bot or message.bot
    chat_id = message.chat.id
    existing_msg_id = user_msg.get(chat_id)

    range_val = user_data[user_id].get("range", "")
    app_name  = user_data[user_id].get("app", "FACEBOOK")

    if not range_val:
        err = "❌ Range পাওয়া যায়নি। আবার শুরু করুন।"
        if existing_msg_id:
            try:
                await _bot.edit_message_text(err, chat_id=chat_id, message_id=existing_msg_id)
            except Exception:
                await _bot.send_message(chat_id, err)
        else:
            await _bot.send_message(chat_id, err)
        return

    rid_clean = range_val.upper().strip()

    # ── Fetch 2 numbers ──
    nums_got: list[str] = []
    rids_got: list[str] = []
    for _ in range(2):
        result = await a2_get_number(rid_clean)
        if result:
            full_num = (
                result.get("no_plus_number")
                or result.get("full_number")
                or result.get("phone")
                or result.get("number")
                or result.get("msisdn")
                or result.get("phoneNumber")
                or result.get("phone_number")
                or ""
            )
            full_num = re.sub(r'\D', '', str(full_num)).strip()
            _rid_val = result.get("_rid", "")
            if full_num and full_num not in nums_got:
                nums_got.append(full_num)
                rids_got.append(_rid_val)
        await asyncio.sleep(0.5)

    if not nums_got:
        err = "❌ এই range এ এখন number পাওয়া যাচ্ছে না।\n\nকিছুক্ষণ পর আবার try করুন।"
        if existing_msg_id:
            try:
                await _bot.edit_message_text(err, chat_id=chat_id, message_id=existing_msg_id)
            except Exception:
                await _bot.send_message(chat_id, err)
        else:
            await _bot.send_message(chat_id, err)
        return

    user_data[user_id]["numbers"]         = nums_got
    user_data[user_id]["last_number"]     = nums_got[0]
    user_data[user_id]["panel"]           = "A2"
    user_data[user_id]["otp_active"]      = True
    user_data[user_id]["otp_running"]     = True
    user_data[user_id]["auto_otp_cancel"] = False

    asyncio.create_task(db_save_user_async(user_id, {
        "name":        user_data[user_id].get("name", "User"),
        "joined":      user_data[user_id].get("joined", ""),
        "app":         app_name,
        "panel":       "A2",
        "last_number": nums_got[0],
        "range":       range_val,
    }))

    # ── Build card ──
    country_code = a2_extract_country_code(rid_clean)
    country_name = COUNTRY_NAMES_CODE.get(country_code, user_data[user_id].get("country", "Unknown"))
    flag         = COUNTRY_FLAGS_CODE.get(country_code, "🌍")

    card_text = (
        f"✅ <b>Numbers Assigned!</b>\n\n"
        f"<b>Service:</b> {app_name.title()} [A2]\n"
        f"🌍 <b>Country:</b> {flag} {country_name}\n"
        f"⏳ <b>Reserved:</b> 20 min\n\n"
        f"📩 OTPs forwarded automatically."
    )

    colors  = ["success", "primary"]
    kb_rows = []
    for i, num in enumerate(nums_got):
        kb_rows.append([InlineKeyboardButton(
            f"📋 {num}",
            copy_text=CopyTextButton(text=num),
            api_kwargs={"style": colors[i % len(colors)]},
        )])
    kb_rows.append([InlineKeyboardButton("🔄 Change Numbers", callback_data="a2_change_numbers", api_kwargs={"style": "success"})])
    kb_rows.append([InlineKeyboardButton("🌍 Change Region",  callback_data="back_app",           api_kwargs={"style": "primary"})])
    otp_link = OTP_CHANNEL_LINK or OTP_CHANNEL_JOIN_LINK or ""
    if otp_link:
        kb_rows.append([InlineKeyboardButton("📢 OTP Channel", url=otp_link, api_kwargs={"style": "primary"})])
    kb = InlineKeyboardMarkup(kb_rows)

    if existing_msg_id:
        try:
            await _bot.edit_message_text(
                card_text, chat_id=chat_id, message_id=existing_msg_id,
                parse_mode="HTML", reply_markup=kb,
            )
            user_msg[chat_id] = existing_msg_id
        except Exception:
            sent = await _bot.send_message(chat_id, card_text, parse_mode="HTML", reply_markup=kb)
            user_msg[chat_id] = sent.message_id
    else:
        sent = await _bot.send_message(chat_id, card_text, parse_mode="HTML", reply_markup=kb)
        user_msg[chat_id] = sent.message_id

    # ── Cancel previous OTP tasks, start new one ──
    cancel_all_otp_tasks(user_id)
    user_data[user_id]["auto_otp_cancel"] = False
    user_data[user_id]["otp_active"]      = True
    user_data[user_id]["otp_running"]     = True
    task = asyncio.create_task(
        auto_otp_a2(user_id, nums_got, rids_got, rid_clean, _bot, chat_id)
    )
    add_otp_task(user_id, task)


async def auto_otp_a2(
    user_id: int,
    numbers: list[str],
    rids: list[str],
    display_range: str,
    bot,
    chat_id: int,
) -> None:
    """
    Background OTP poller for A2.
    Polls every 10 seconds for up to 20 minutes.
    One /success-otp call per number per cycle to avoid double-request bugs.
    """
    from services.otp import send_otp_to_inbox, send_otp_to_channel

    # Force correct state at task start (create_task runs on next event loop tick)
    user_data[user_id]["auto_otp_cancel"] = False
    user_data[user_id]["otp_active"]      = True
    user_data[user_id]["otp_running"]     = True

    TIMEOUT = A2_OTP_BASE_TIMEOUT_SECONDS
    start        = time.time()
    received:    set[str] = set()
    seen_otp_ids: set     = set()

    while True:
        if user_data.get(user_id, {}).get("auto_otp_cancel"):
            break
        if not user_data.get(user_id, {}).get("otp_active"):
            break
        if time.time() - start > TIMEOUT:
            break

        for idx, number in enumerate(list(numbers)):
            if number in received:
                continue

            _rid = rids[idx] if idx < len(rids) else None
            try:
                all_otps = await a2_fetch_all_otps(rid=_rid)
            except Exception as e:
                logger.error(f"auto_otp_a2 fetch error for {number}: {e}")
                continue

            clean_target = re.sub(r'\D', '', str(number))
            matched      = None
            for entry in all_otps:
                otp_id = entry.get("otp_id")
                if otp_id and otp_id in seen_otp_ids:
                    continue
                num = re.sub(r'\D', '', str(entry.get("number", "")))
                if num and clean_target and (
                    num == clean_target
                    or num.endswith(clean_target[-8:])
                    or clean_target.endswith(num[-8:])
                ):
                    matched = entry
                    break

            if not matched:
                continue

            message_text = matched.get("message", "")
            otp_code     = extract_otp(message_text)
            if not otp_code:
                continue

            received.add(number)
            otp_uid = matched.get("otp_id") or f"{number}_{otp_code}_{message_text[:30]}"
            seen_otp_ids.add(otp_uid)

            country_code  = extract_country_code_from_number(number)
            country_name  = COUNTRY_NAMES_CODE.get(country_code, "Unknown")
            flag          = COUNTRY_FLAGS_CODE.get(country_code, "🌍")
            _user_app     = user_data.get(user_id, {}).get("app", "FACEBOOK")
            detected_app  = detect_app_from_message(message_text, _user_app)
            if detected_app == "FACEBOOK" and _user_app in (
                "WHATSAPP", "TELEGRAM", "INSTAGRAM", "TIKTOK", "SNAPCHAT"
            ):
                detected_app = _user_app

            asyncio.create_task(
                send_otp_to_channel(bot, number, otp_code, detected_app,
                                    country_name, flag, message_text, "A2")
            )
            try:
                await send_otp_to_inbox(bot, chat_id, number, otp_code, detected_app,
                                        country_name, flag, message_text, "A2")
                logger.info(f"A2 OTP → user {user_id}: {otp_code} | {number} [{detected_app}]")
            except Exception as e:
                logger.error(f"auto_otp_a2 send error for {number}: {e}")

        if len(received) >= len(numbers):
            user_data[user_id]["otp_active"]  = False
            user_data[user_id]["otp_running"] = False
            break

        await asyncio.sleep(10)

    a2_poll_tasks.pop(user_id, None)


# ══════════════════════════════════════════════════════════
#              JOB — RANGE POST TO CHANNEL
# ══════════════════════════════════════════════════════════

async def job_a2_range_post(context) -> None:
    """
    APScheduler job — posts live A2 ranges to the range channel every 5 min.
    Only posts ranges that have a real OTP in the console.
    """
    if not A2_API_KEY or not RANGE_CHANNEL_ID:
        return
    try:
        ranges        = await a2_get_active_ranges()
        bot           = context.bot
        now_bd        = datetime.now(timezone(timedelta(hours=6)))
        console_hits  = await a2_get_console_hits()
        range_sms_map: dict[str, str] = {}
        for hit in (console_hits or []):
            h_range = hit.get("range", "").upper().strip()
            if h_range and h_range not in range_sms_map:
                range_sms_map[h_range] = hit.get("message", "")

        post_count    = 0
        posted_ids:   set[str] = set()

        for r in ranges:
            if post_count >= 3:
                break
            rng = r.get("range", "").upper().strip()
            if not rng:
                continue
            slot      = now_bd.strftime('%Y-%m-%d %H:') + str(now_bd.minute // 5 * 5).zfill(2)
            unique_id = f"a2_{rng}_{slot}"
            if unique_id in posted_ids:
                continue

            clean_base   = re.sub(r'X+$', '', rng).strip()
            code         = a2_extract_country_code(rng)
            range_flag   = COUNTRY_FLAGS_CODE.get(code, "🌍")
            country_name = COUNTRY_NAMES_CODE.get(code, code)

            raw_sms = range_sms_map.get(rng, range_sms_map.get(clean_base, ""))
            if not raw_sms:
                continue
            otp = extract_otp(raw_sms) or ""
            if not otp:
                continue

            clean_sms = re.sub(r'<#>\s*', '', raw_sms).strip()
            lines     = [
                l for l in clean_sms.splitlines()
                if l.strip() and not re.fullmatch(r'[A-Za-z0-9+/]{10,}', l.strip())
            ]
            clean_sms = "\n".join(lines).strip()

            text = (
                f"{range_flag} {escape_mdv2(country_name)}\n\n"
                f"📞 `{escape_mdv2(rng)}`\n"
                f"🔐 `{escape_mdv2(otp)}`\n"
                f"📘 Service: Facebook \\| A2\n"
                    f"🗣️ Language : {escape_mdv2(detect_language_from_sms(clean_sms))}\n"
                f"🗣️ Language : {escape_mdv2(lang_a2)}\n"
                f"{escape_mdv2('────────────')}\n"
                f"📩"
            )
            if clean_sms:
                quoted = "\n".join(
                    f">{escape_mdv2(line)}"
                    for line in clean_sms.splitlines()
                    if line.strip()
                )
                text += f"\n{quoted}"

            # ── Keyboard: COPY OTP + Main Channel + Number Bot ──
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton
            from config import MAIN_CHANNEL_LINK, NUMBER_BOT_LINK
            kb_rows = []
            if otp:
                kb_rows.append([InlineKeyboardButton(
                    f"📋 🔑 COPY OTP",
                    copy_text=CopyTextButton(text=otp),
                    api_kwargs={"style": "success"},
                )])
            btn_row = []
            if MAIN_CHANNEL_LINK:
                btn_row.append(InlineKeyboardButton(
                    "📢 Main Channel", url=MAIN_CHANNEL_LINK,
                    api_kwargs={"style": "primary"},
                ))
            if NUMBER_BOT_LINK:
                btn_row.append(InlineKeyboardButton(
                    "🤖 Number Bot", url=NUMBER_BOT_LINK,
                    api_kwargs={"style": "danger"},
                ))
            if btn_row:
                kb_rows.append(btn_row)
            keyboard = InlineKeyboardMarkup(kb_rows) if kb_rows else None

            try:
                await bot.send_message(
                    chat_id=RANGE_CHANNEL_ID,
                    text=text,
                    parse_mode="MarkdownV2",
                    reply_markup=keyboard,
                )
                posted_ids.add(unique_id)
                post_count += 1
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"A2 range post error for {rng}: {e}")

    except Exception as e:
        logger.error(f"job_a2_range_post error: {e}")
