# panels/s3.py
"""
S3 Panel — Number Pool (Admin-managed)
───────────────────────────────────────
Responsibilities:
  • In-memory number pool backed by Supabase s3_numbers table
  • User session management (who has which number)
  • OTP polling via CR API + user inbox notification
  • Pool management helpers (add, remove, assign, release)

No S1/S2 code. No X.Mint/StexSMS dependency.
"""

import asyncio
import hashlib
import re
import time
from datetime import datetime, timedelta

import httpx

from config import (
    ADMIN_ID,
    CR_API_URL,
    CR_API_TOKEN,
    OTP_CHANNEL_LINK,
    MAIN_CHANNEL_LINK,
    JOIN_CHANNEL_LINK,
    NUMBER_BOT_LINK,
    SUPABASE_URL,
    SUPABASE_KEY,
    S3_NUMBER_RESERVE_SECONDS,
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
from utils.state import numbers_pool, s3_user_sessions

logger = get_logger(__name__)

# ── OTP dedup cache: cache_key → True ──
otp_cache: dict[str, bool] = {}

# ── Bot start time — OTPs before this are skipped ──
BOT_START_TIME = datetime.now() - timedelta(minutes=5)  # allow 5 min grace

# ── Supabase headers ──
def _sb_headers() -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates",
    }


# ══════════════════════════════════════════════════════════
#                  POOL KEY HELPERS
# ══════════════════════════════════════════════════════════

SERVICE_LABELS = {"fb": "Facebook", "ig": "Instagram"}

def parse_pool_key(pool_key: str) -> tuple[str, str, str | None]:
    """
    Parse pool_key formats:
      "95_fb"       → (95, fb, None)      → Myanmar Facebook
      "95_s1_fb"    → (95, fb, s1)        → Myanmar Facebook S1
      "95_v1_fb"    → (95, fb, v1)        → Myanmar Facebook v1 (Shark)
      "224_ig"      → (224, ig, None)     → Guinea Instagram
    Returns (dial_code, service_short, variant|None).
    """
    parts = pool_key.split("_")
    code  = parts[0]

    # Find service (last part that's fb or ig)
    service = "fb"
    for p in reversed(parts[1:]):
        if p.lower() in ("fb", "ig"):
            service = p.lower()
            break

    # Find variant (middle parts excluding code and service)
    middle = [p for p in parts[1:] if p.lower() not in ("fb", "ig")]
    variant = "_".join(middle) if middle else None

    return code, service, variant


def get_service_label(service: str) -> str:
    return SERVICE_LABELS.get(service, "Facebook")


def get_variant_label(variant: str | None) -> str:
    """Convert variant to display label: s1 → S1, v1 → v1, None → ""."""
    if not variant:
        return ""
    return f" {variant.upper()}"


def get_button_label(pool_key: str) -> str:
    code, service, variant = parse_pool_key(pool_key)
    flag    = COUNTRY_FLAGS_CODE.get(code, "🌍")
    name    = COUNTRY_NAMES_CODE.get(code, code)
    label   = get_service_label(service)
    var_lbl = get_variant_label(variant)
    return f"{flag} {name} {label}{var_lbl}"


def get_short_label(pool_key: str) -> str:
    code, service, variant = parse_pool_key(pool_key)
    flag    = COUNTRY_FLAGS_CODE.get(code, "🌍")
    name    = COUNTRY_NAMES_CODE.get(code, "Unknown")
    label   = get_service_label(service)
    var_lbl = get_variant_label(variant)
    return f"{flag} {name} {label}{var_lbl}"


def is_shark_pool(pool_key: str) -> bool:
    """Pool keys with _v1 variant use the Shark (S4) OTP source."""
    _, _, variant = parse_pool_key(pool_key)
    return variant == "v1"


# ══════════════════════════════════════════════════════════
#               POOL CRUD (in-memory + Supabase)
# ══════════════════════════════════════════════════════════

def get_numbers_pool() -> dict:
    return numbers_pool


def get_pool_numbers(pool_key: str) -> list:
    return numbers_pool.get(pool_key, [])


def count_numbers(pool_key: str) -> int:
    return len(numbers_pool.get(pool_key, []))


async def tg_load_all(bot=None) -> None:
    """
    Load all available S3 numbers from Supabase into memory on startup.
    Also loads s3_users table into an in-memory dict (s3_users_db).
    """
    from utils.state import numbers_pool  # re-import to mutate module-level dict

    # ── Numbers ──
    try:
        all_rows: list[dict] = []
        page_size = 1000
        offset    = 0
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                res = await client.get(
                    f"{SUPABASE_URL}/rest/v1/s3_numbers",
                    params={
                        "select":  "*",
                        "status":  "eq.available",
                        "limit":   str(page_size),
                        "offset":  str(offset),
                    },
                    headers=_sb_headers(),
                )
                rows = res.json()
                if not isinstance(rows, list) or not rows:
                    break
                all_rows.extend(rows)
                if len(rows) < page_size:
                    break
                offset += page_size

        for row in all_rows:
            pk  = row.get("pool_key", "")
            num = row.get("number", "")
            if pk and num:
                numbers_pool.setdefault(pk, [])
                if num not in numbers_pool[pk]:
                    numbers_pool[pk].append(num)

        total = sum(len(v) for v in numbers_pool.values())
        logger.info(f"✅ S3 loaded {len(numbers_pool)} pools, {total} numbers from Supabase")
    except Exception as e:
        logger.error(f"tg_load_all (numbers) error: {e}")

    # ── Users ──
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.get(
                f"{SUPABASE_URL}/rest/v1/users",
                params={"select": "user_id,name,joined", "limit": "5000"},
                headers=_sb_headers(),
            )
        rows = res.json()
        if isinstance(rows, list):
            for row in rows:
                uid    = str(row.get("user_id", ""))
                name   = row.get("name", uid)
                joined = row.get("joined", "")
                if uid and uid not in s3_users_db:
                    s3_users_db[uid] = {"username": name, "joined": joined}
            logger.info(f"✅ S3 loaded {len(s3_users_db)} users from Supabase")
    except Exception as e:
        logger.error(f"tg_load_all (users) error: {e}")


async def add_numbers_to_pool(bot, pool_key: str, new_numbers: list[str]) -> tuple[int, int]:
    """
    Add numbers to a pool. Skips duplicates.
    Returns (added_count, skipped_count).
    """
    existing = set(numbers_pool.get(pool_key, []))
    added = skipped = 0
    new_rows = []
    for n in new_numbers:
        if n not in existing:
            existing.add(n)
            added += 1
            new_rows.append({"number": n, "pool_key": pool_key, "status": "available"})
        else:
            skipped += 1
    numbers_pool[pool_key] = list(existing)
    if new_rows:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(
                    f"{SUPABASE_URL}/rest/v1/s3_numbers",
                    headers=_sb_headers(),
                    json=new_rows,
                )
        except Exception as e:
            logger.error(f"add_numbers_to_pool Supabase error: {e}")
    return added, skipped


async def remove_number_from_pool(bot, pool_key: str, number: str) -> None:
    """
    Permanently delete a number from memory + Supabase.
    If pool_key is empty, searches all pools.
    """
    # Remove from memory — search all pools if pool_key not specified
    if pool_key:
        pools_to_check = [pool_key]
    else:
        pools_to_check = [pk for pk, nums in numbers_pool.items() if number in nums]

    for pk in pools_to_check:
        nums = numbers_pool.get(pk, [])
        if number in nums:
            nums.remove(number)
            numbers_pool[pk] = nums
            logger.info(f"🗑 Removed from memory: {number} | pool={pk}")

    # Delete from Supabase
    try:
        params = {"number": f"eq.{number}"}
        if pool_key:
            params["pool_key"] = f"eq.{pool_key}"
        async with httpx.AsyncClient(timeout=15) as client:
            await client.delete(
                f"{SUPABASE_URL}/rest/v1/s3_numbers",
                params=params,
                headers=_sb_headers(),
            )
        logger.info(f"🗑 Deleted from Supabase: {number}")
    except Exception as e:
        logger.error(f"remove_number_from_pool error: {e}")


async def mark_number_assigned(number: str, user_id: int, pool_key: str = "") -> None:
    """Mark a number as assigned in Supabase (available → assigned)."""
    try:
        params = {"number": f"eq.{number}"}
        if pool_key:
            params["pool_key"] = f"eq.{pool_key}"
        async with httpx.AsyncClient(timeout=15) as client:
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/s3_numbers",
                params=params,
                headers=_sb_headers(),
                json={"status": "assigned", "assigned_to": str(user_id)},
            )
    except Exception as e:
        logger.error(f"mark_number_assigned error: {e}")


async def mark_number_used(number: str, pool_key: str = "") -> None:
    """Mark a number as used (assigned → used)."""
    try:
        params = {"number": f"eq.{number}"}
        if pool_key:
            params["pool_key"] = f"eq.{pool_key}"
        async with httpx.AsyncClient(timeout=15) as client:
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/s3_numbers",
                params=params,
                headers=_sb_headers(),
                json={"status": "used"},
            )
    except Exception as e:
        logger.error(f"mark_number_used error: {e}")


# ══════════════════════════════════════════════════════════
#                  SESSION HELPERS
# ══════════════════════════════════════════════════════════

# In-memory user DB {str(user_id): {username, joined}}
s3_users_db: dict[str, dict] = {}


def s3_add_user(user_id: int, username: str) -> bool:
    uid = str(user_id)
    if uid not in s3_users_db:
        s3_users_db[uid] = {
            "username": username or str(user_id),
            "joined":   datetime.now().strftime("%Y-%m-%d"),
        }
        return True
    return False


def s3_is_new_user(user_id: int) -> bool:
    return str(user_id) not in s3_users_db


def s3_get_all_users() -> list[str]:
    return list(s3_users_db.keys())


def s3_get_user_count() -> int:
    return len(s3_users_db)


def s3_set_session(user_id: int, numbers: list[str] | str, pool_key: str) -> None:
    if isinstance(numbers, str):
        numbers = [numbers]
    s3_user_sessions[str(user_id)] = {
        "number":        numbers[0] if numbers else "",
        "numbers":       numbers,
        "pool_key":      pool_key,
        "assigned_time": datetime.now().isoformat(),
    }


def s3_get_session(user_id: int) -> dict | None:
    return s3_user_sessions.get(str(user_id))


def s3_find_users_by_number(number: str) -> list[str]:
    """
    Return user_ids that currently hold this number within their session window.
    Normalizes numbers before comparing (strips +, spaces, leading zeros).
    """
    import re as _re
    def _norm(n: str) -> str:
        return _re.sub(r'\D', '', str(n)).lstrip('0')

    target = _norm(number)
    matched = []
    for uid, session in s3_user_sessions.items():
        session_numbers = session.get("numbers", [])
        if not session_numbers and session.get("number"):
            session_numbers = [session["number"]]
        # Normalize and compare last 9 digits for flexibility
        found = False
        for snum in session_numbers:
            snum_norm = _norm(snum)
            if snum_norm == target:
                found = True
                break
            # tail match — last 9 digits
            if len(target) >= 9 and len(snum_norm) >= 9:
                if snum_norm[-9:] == target[-9:]:
                    found = True
                    break
        if not found:
            continue
        try:
            session_time = datetime.fromisoformat(session["assigned_time"])
            limit = (
                timedelta(minutes=120)
                if int(uid) == ADMIN_ID
                else timedelta(seconds=S3_NUMBER_RESERVE_SECONDS)
            )
            if datetime.now() - session_time < limit:
                matched.append(uid)
        except Exception:
            matched.append(uid)
    return matched


# ══════════════════════════════════════════════════════════
#                  CR API — OTP FETCH
# ══════════════════════════════════════════════════════════

async def fetch_cr_api_otps() -> list[dict]:
    """
    Fetch recent OTPs from CR API.
    Token passed as query param (not Bearer header).
    Returns list of {num, message, dt, cli}.
    """
    if not CR_API_URL or not CR_API_TOKEN:
        logger.warning("CR API URL or TOKEN not set!")
        return []
    try:
        now  = datetime.now()
        dt2  = now.strftime("%Y-%m-%d %H:%M:%S")
        dt1  = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        params = {
            "token":   CR_API_TOKEN,
            "dt1":     dt1,
            "dt2":     dt2,
            "records": 200,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(CR_API_URL, params=params)

        logger.info(f"CR API status: {res.status_code}")
        if res.status_code != 200:
            logger.warning(f"CR API error: {res.text[:200]}")
            return []

        raw = res.text.strip()
        if not raw:
            logger.warning("CR API empty response")
            return []

        try:
            data = res.json()
        except Exception:
            # Rate limit returns plain text error
            if "too many" in raw.lower() or "try again" in raw.lower():
                logger.warning(f"CR API rate limited — waiting 5s then retry")
                await asyncio.sleep(5)
                try:
                    async with httpx.AsyncClient(timeout=15) as client2:
                        res2 = await client2.get(CR_API_URL, params=params)
                    data = res2.json()
                except Exception:
                    logger.warning(f"CR API retry also failed: {repr(raw[:100])}")
                    return []
            else:
                logger.warning(f"CR API invalid JSON: {repr(raw[:100])}")
                return []

        logger.info(f"CR API response status={data.get('status')} records={len(data.get('data', []))}")

        if data.get("status") != "success":
            logger.warning(f"CR API not success: {data}")
            return []

        result = []
        for row in data.get("data", []):
            try:
                entry = {
                    "dt":      str(row.get("dt",      "")).strip(),
                    "num":     str(row.get("num",     "")).strip().lstrip("+"),
                    "cli":     str(row.get("cli",     "")).strip().upper(),
                    "message": str(row.get("message", "")).strip(),
                }
                if entry["num"] and entry["message"]:
                    result.append(entry)
            except Exception:
                continue
        return result

    except Exception as e:
        logger.error(f"fetch_cr_api_otps error: {e}")
        return []


# ══════════════════════════════════════════════════════════
#             SEND HELPER (flood-safe)
# ══════════════════════════════════════════════════════════

async def _s3_send_with_retry(
    bot, chat_id: int, text: str,
    parse_mode=None, reply_markup=None, max_retries: int = 3,
) -> bool:
    for attempt in range(1, max_retries + 1):
        try:
            await bot.send_message(
                chat_id=chat_id, text=text,
                parse_mode=parse_mode, reply_markup=reply_markup,
            )
            return True
        except Exception as e:
            err = str(e).lower()
            if "retry after" in err or "flood" in err:
                wait = int(re.search(r'\d+', str(e)).group() or 5)
                logger.warning(f"S3 flood wait {wait}s (attempt {attempt})")
                await asyncio.sleep(wait + 1)
            elif attempt < max_retries:
                logger.warning(f"S3 send fail attempt {attempt}: {e}")
                await asyncio.sleep(2 * attempt)
            else:
                logger.error(f"S3 send FINAL FAIL after {max_retries} attempts: {e}")
                return False
    return False


# ══════════════════════════════════════════════════════════
#              JOB — POLL CR API OTPs (S3)
# ══════════════════════════════════════════════════════════

async def poll_otps_s3(context) -> None:
    """
    APScheduler job — polls CR API for new OTPs every 30s.
    Sends to OTP channel + matching user inboxes.
    Skips duplicates via otp_cache and BOT_START_TIME guard.
    """
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton
    from services.otp import send_otp_to_channel

    try:
        # ── Channel keyboard ──
        ch_link = OTP_CHANNEL_LINK or MAIN_CHANNEL_LINK or JOIN_CHANNEL_LINK or ""
        kb_btns = []
        if ch_link and len(ch_link) > 10:
            kb_btns.append(InlineKeyboardButton("📢 Main Channel", url=ch_link, api_kwargs={"style": "primary"}))
        if NUMBER_BOT_LINK:
            kb_btns.append(InlineKeyboardButton("🤖 Number Bot", url=NUMBER_BOT_LINK, api_kwargs={"style": "danger"}))
        otp_channel_keyboard = InlineKeyboardMarkup([kb_btns]) if kb_btns else None

        cr_otps = await fetch_cr_api_otps()
        logger.info(f"S3 poll: fetched {len(cr_otps)} OTPs from CR API | type={type(cr_otps)}")
        if cr_otps:
            sample = cr_otps[0]
            logger.info(f"S3 CR API sample keys: {list(sample.keys()) if isinstance(sample, dict) else sample} | sample: {str(sample)[:300]}")
        else:
            # Log raw response for debug
            logger.warning("S3 CR API returned empty list — check URL/token or response format")
        sent_count = skipped_count = 0

        for otp_data in cr_otps:
            try:
                number  = (
                    otp_data.get("num")
                    or otp_data.get("number")
                    or otp_data.get("phone")
                    or otp_data.get("msisdn")
                    or ""
                ).strip().lstrip("+")
                message = (
                    otp_data.get("message")
                    or otp_data.get("sms")
                    or otp_data.get("body")
                    or ""
                ).strip()
                dt      = (
                    otp_data.get("dt")
                    or otp_data.get("created_at")
                    or otp_data.get("time")
                    or ""
                ).strip()
                if not number or not message:
                    skipped_count += 1
                    continue
                if not dt:
                    dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                msg_hash  = hashlib.md5(message.encode()).hexdigest()[:12]
                cache_key = f"s3:{number}:{dt}:{msg_hash}"
                if cache_key in otp_cache:
                    skipped_count += 1
                    continue

                # Mark as seen (cache handles dedup, no time-based skip)
                otp_cache[cache_key] = True
                # Trim cache
                if len(otp_cache) > 10_000:
                    for k in list(otp_cache.keys())[:5000]:
                        otp_cache.pop(k, None)

                otp_code     = extract_otp(message)
                if not otp_code:
                    logger.info(f"S3 skip — no OTP extracted for {hide_number(number)}")
                    continue

                country      = extract_country_code_from_number(number)
                flag         = COUNTRY_FLAGS_CODE.get(country, "🌍")
                country_name = COUNTRY_NAMES_CODE.get(country, "Unknown")
                detected_app = detect_app_from_message(message, default_app="FACEBOOK")

                logger.info(f"S3 OTP: {hide_number(number)} | {otp_code} | {detected_app} | {dt}")

                await send_otp_to_channel(
                    context.bot, number, otp_code,
                    detected_app.capitalize(), country_name, flag, message, "S3",
                )
                sent_count += 1

                # ── User inbox notification ──
                matched_users = s3_find_users_by_number(number)
                for uid in matched_users:
                    try:
                        session  = s3_get_session(int(uid))
                        _app     = detected_app.capitalize()
                        header   = f"{flag} {country_name} {_app} (S3) → 🟢"

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

                        inbox_sent = await _s3_send_with_retry(
                            context.bot, int(uid), header,
                            reply_markup=InlineKeyboardMarkup(kb_inbox),
                        )
                        if not inbox_sent:
                            logger.error(f"S3 inbox FAILED [{uid}] for {hide_number(number)}")
                    except Exception as e:
                        logger.error(f"S3 inbox error [{uid}]: {e}")

            except Exception as e:
                logger.error(f"S3 OTP process error: {e}")

        logger.info(f"S3 poll done: sent={sent_count} skipped={skipped_count}")

    except Exception as e:
        logger.error(f"poll_otps_s3 error: {e}")
