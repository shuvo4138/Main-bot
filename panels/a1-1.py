# panels/a1.py
"""
A1 Panel — ZENEX Network API
─────────────────────────────
Responsibilities:
  • Fetch active ranges from ZENEX
  • Provision numbers (Facebook=2, Instagram=1)
  • Poll OTPs and forward to user inbox + channel
  • No S1/S2 code — clean A1-only module
"""

import asyncio
import re
import time

import httpx

from config import (
    ZENEX_API_KEY,
    ZENEX_BASE_URL,
    ZENEX_CACHE_TTL,
    A1_OTP_BASE_TIMEOUT_SECONDS,
    A1_OTP_EXTEND_SECONDS,
    A1_INSTAGRAM_NUMBER_COUNT,
    A1_DEFAULT_NUMBER_COUNT,
)
from utils.logger import get_logger
from utils.helpers import extract_otp, get_flag_by_iso
from utils.state import user_data, user_msg, a1_poll_tasks

logger = get_logger(__name__)

# ── Range cache (module-level, reset on restart) ──
_zenex_ranges_cache: dict = {"data": [], "time": 0}


# ══════════════════════════════════════════════════════════
#                  ZENEX API HELPERS
# ══════════════════════════════════════════════════════════

def _zenex_headers() -> dict:
    return {
        "mapikey": ZENEX_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ══════════════════════════════════════════════════════════
#                  RANGE FUNCTIONS
# ══════════════════════════════════════════════════════════

async def zenex_get_active_ranges(service: str | None = None) -> list[dict]:
    """
    Fetch active ranges from ZENEX /v1/active-ranges.
    Results are cached for ZENEX_CACHE_TTL seconds.
    Pass service= to filter (e.g. "Facebook", "Whatsapp").
    """
    global _zenex_ranges_cache
    if (
        (time.time() - _zenex_ranges_cache["time"]) < ZENEX_CACHE_TTL
        and _zenex_ranges_cache["data"]
    ):
        ranges = _zenex_ranges_cache["data"]
    else:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.get(
                    f"{ZENEX_BASE_URL}/v1/active-ranges",
                    headers=_zenex_headers(),
                )
            if res.status_code == 200:
                data = res.json()
                ranges = data.get("data", {}).get("active_ranges", [])
                _zenex_ranges_cache = {"data": ranges, "time": time.time()}
            else:
                logger.warning(f"ZENEX active-ranges HTTP {res.status_code}")
                ranges = _zenex_ranges_cache["data"]
        except Exception as e:
            logger.error(f"ZENEX active-ranges error: {e}")
            ranges = _zenex_ranges_cache["data"]

    if service:
        ranges = [r for r in ranges if r.get("service", "").upper() == service.upper()]
    return ranges


# ══════════════════════════════════════════════════════════
#                  NUMBER PROVISION
# ══════════════════════════════════════════════════════════

async def zenex_get_number(range_val: str) -> dict | None:
    """
    Provision a single number from ZENEX for the given range.
    Returns the data dict on success, None on failure.
    """
    clean = re.sub(r'X+$', '', range_val.upper().strip())
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.post(
                f"{ZENEX_BASE_URL}/v1/getnum",
                headers=_zenex_headers(),
                json={
                    "range": clean + "XXX",
                    "is_national": False,
                    "remove_plus": False,
                },
            )
        if res.status_code == 200:
            data = res.json()
            if data.get("meta", {}).get("code") == 200:
                return data.get("data", {})
        logger.warning(f"ZENEX getnum failed: {res.status_code} {res.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"ZENEX getnum error: {e}")
        return None


# ══════════════════════════════════════════════════════════
#                  OTP POLLING
# ══════════════════════════════════════════════════════════

async def zenex_poll_otp(target_number: str) -> dict | None:
    """
    Poll ZENEX /v1/numsuccess/info and return the OTP entry for target_number.
    Returns None if no match found.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"{ZENEX_BASE_URL}/v1/numsuccess/info",
                headers=_zenex_headers(),
            )
        if res.status_code == 200:
            data = res.json()
            otps = data.get("data", {}).get("otps", [])
            clean_target = target_number.lstrip("+")
            for entry in otps:
                num = entry.get("number", "").lstrip("+")
                if num == clean_target or num.endswith(clean_target[-7:]):
                    return entry
        return None
    except Exception as e:
        logger.error(f"ZENEX poll_otp error: {e}")
        return None


# ══════════════════════════════════════════════════════════
#              NUMBER ASSIGNMENT + OTP FLOW
# ══════════════════════════════════════════════════════════

async def do_get_number_a1(message, user_id: int, bot) -> None:
    """
    Main entry point for A1 number assignment.

    Flow:
      1. Fetch 1 or 2 numbers from ZENEX in parallel
      2. Show number card to user
      3. Start background OTP poll (20 min window)
      4. On OTP: notify user inbox + broadcast to channel
    """
    from utils.state import user_data, user_msg  # avoid circular at module level

    # Late imports for OTP sender (defined in services/)
    from services.otp import send_otp_to_inbox, send_otp_to_channel
    from keyboards.menus import after_number_inline_a1
    from handlers.helpers import init_user

    init_user(user_id)

    range_val    = user_data[user_id].get("range", "")
    service_name = user_data[user_id].get("a1_service", "Facebook New Account")
    zenex_svc    = user_data[user_id].get("a1_zenex_service", "Facebook")
    chat_id      = message.chat.id

    if not range_val:
        await bot.send_message(chat_id, "❌ Range select করা হয়নি!")
        return
    if not ZENEX_API_KEY:
        await bot.send_message(
            chat_id,
            "❌ ZENEX API key সেট করা নেই! Railway ENV এ ZENEX_API_KEY add করুন।",
        )
        return

    is_instagram = "instagram" in zenex_svc.lower() or "instagram" in service_name.lower()
    fetch_count  = A1_INSTAGRAM_NUMBER_COUNT if is_instagram else A1_DEFAULT_NUMBER_COUNT

    # ── Loading message ──
    existing_msg_id = user_msg.get(chat_id)
    if existing_msg_id:
        class _FakeMsg:
            def __init__(self, mid): self.message_id = mid
        loading_msg = _FakeMsg(existing_msg_id)
    else:
        loading_msg = await bot.send_message(chat_id, "⏳ Searching Number...")
        await asyncio.sleep(1)
        try:
            await bot.edit_message_text(
                "📡 Connecting Server...",
                chat_id=chat_id,
                message_id=loading_msg.message_id,
            )
            await asyncio.sleep(1)
        except Exception:
            pass
        user_msg[chat_id] = loading_msg.message_id

    # ── Fetch numbers in parallel ──
    results = await asyncio.gather(
        *[zenex_get_number(range_val) for _ in range(fetch_count)],
        return_exceptions=True,
    )

    numbers = []
    seen_nums: set[str] = set()
    for r in results:
        if isinstance(r, dict) and r.get("full_number"):
            full = r["full_number"]
            if full not in seen_nums:
                seen_nums.add(full)
                numbers.append(r)

    if not numbers:
        try:
            await bot.edit_message_text(
                "❌ Number পাওয়া যায়নি! আবার try করুন।",
                chat_id=chat_id,
                message_id=loading_msg.message_id,
            )
        except Exception:
            pass
        return

    country  = numbers[0].get("country", "Unknown")
    flag     = get_flag_by_iso(country)
    num_list = [n["full_number"] for n in numbers]

    user_data[user_id]["a1_numbers"]      = num_list
    user_data[user_id]["a1_otp_received"] = {}

    # ── Build card ──
    card_text = (
        f"✅ <b>Numbers Assigned!</b>\n\n"
        f"<b>Service:</b> {service_name} [A1]\n"
        f"🌍 <b>Country:</b> {flag} {country}\n"
        f"⏳ <b>Reserved:</b> 20 min\n\n"
        f"📩 OTPs forwarded automatically."
    )
    otp_status: dict[str, bool] = {}

    card_msg_id = loading_msg.message_id
    try:
        await bot.edit_message_text(
            card_text,
            chat_id=chat_id,
            message_id=card_msg_id,
            parse_mode="HTML",
            reply_markup=after_number_inline_a1(num_list, service_name, otp_status),
        )
    except Exception as e:
        logger.warning(f"A1 card edit failed: {e}")

    # ── OTP poll task ──
    async def _poll_a1():
        deadline = {num: time.time() + A1_OTP_BASE_TIMEOUT_SECONDS for num in num_list}
        otp_received: dict[str, list] = {}

        async def _refresh_card():
            try:
                await bot.edit_message_text(
                    card_text,
                    chat_id=chat_id,
                    message_id=card_msg_id,
                    parse_mode="HTML",
                    reply_markup=after_number_inline_a1(num_list, service_name, otp_status),
                )
            except Exception:
                pass

        while True:
            now = time.time()
            all_expired = all(now >= deadline.get(n["full_number"], 0) for n in numbers)
            if all_expired:
                break

            for num_data in numbers:
                full  = num_data.get("full_number", "")
                clean = full.replace("+", "").strip()
                if now >= deadline.get(full, 0):
                    continue

                entry = await zenex_poll_otp(full)
                if not entry:
                    continue

                raw_otp  = entry.get("otp", "") or entry.get("message", "") or ""
                otp_code = extract_otp(raw_otp) or raw_otp[:10]

                if otp_code and otp_code not in otp_received.get(full, []):
                    otp_received.setdefault(full, []).append(otp_code)
                    otp_status[clean] = True
                    deadline[full] = max(
                        deadline.get(full, 0),
                        time.time() + A1_OTP_EXTEND_SECONDS,
                    )
                    c_flag = get_flag_by_iso(entry.get("country", country))
                    c_name = entry.get("country", country)

                    try:
                        await send_otp_to_inbox(
                            bot, chat_id, full, otp_code,
                            service_name, c_name, c_flag, raw_otp, "A1",
                        )
                    except Exception as e:
                        logger.error(f"A1 inbox send error: {e}")

                    try:
                        await send_otp_to_channel(
                            bot, full, otp_code,
                            service_name, c_name, c_flag, raw_otp, "A1",
                        )
                    except Exception as e:
                        logger.error(f"A1 channel send error: {e}")

                    await _refresh_card()

            await asyncio.sleep(5)

        # Task finished — remove from tracker
        a1_poll_tasks.pop(user_id, None)

    # Cancel any existing poll for this user
    old_task = a1_poll_tasks.pop(user_id, None)
    if old_task and not old_task.done():
        old_task.cancel()

    task = asyncio.create_task(_poll_a1())
    a1_poll_tasks[user_id] = task
