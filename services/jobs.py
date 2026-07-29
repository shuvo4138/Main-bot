# services/jobs.py
"""
APScheduler background jobs — registered once in main.py.

Jobs:
  job_poll_s3_and_shark   — poll CR API (S3) + Supabase shark_otps (S3 V1) every 30s
  job_a1_range_post       — broadcast live A1 ranges to channel every 5 min
  job_a2_range_post       — broadcast live A2 ranges to channel every 5 min
  job_cleanup_sessions    — expire stale S3 user sessions every 10 min
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone

from utils.logger import get_logger
from utils.state import s3_user_sessions

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════
#              S3 + S3V1 OTP POLL (every 30s)
# ══════════════════════════════════════════════════════════

async def job_poll_s3_and_shark(context) -> None:
    from panels.s3 import poll_otps_s3
    from panels.s4_shark import poll_shark_otps
    await asyncio.gather(
        poll_otps_s3(context),
        poll_shark_otps(context),
        return_exceptions=True,
    )


# ══════════════════════════════════════════════════════════
#              A1 RANGE POST (every 5 min)
# ══════════════════════════════════════════════════════════

async def job_a1_range_post(context) -> None:
    from config import ZENEX_API_KEY, RANGE_CHANNEL_ID, MAIN_CHANNEL_LINK, NUMBER_BOT_LINK
    if not ZENEX_API_KEY or not RANGE_CHANNEL_ID:
        return

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    from panels.a1 import zenex_get_active_ranges, zenex_poll_otp
    from utils.helpers import extract_otp, escape_mdv2, COUNTRY_FLAGS_CODE, COUNTRY_NAMES_CODE

    try:
        ranges   = await zenex_get_active_ranges()
        bot      = context.bot
        now_bd   = datetime.now(timezone(timedelta(hours=6)))
        posted   = 0
        seen_ids: set[str] = set()

        for r in ranges:
            if posted >= 3:
                break
            rng = r.get("range", "").upper().strip()
            if not rng:
                continue

            slot      = now_bd.strftime('%Y-%m-%d %H:') + str(now_bd.minute // 5 * 5).zfill(2)
            unique_id = f"a1_{rng}_{slot}"
            if unique_id in seen_ids:
                continue

            clean = re.sub(r'X+$', '', rng).strip()
            code  = clean[:3] if clean[:3] in COUNTRY_NAMES_CODE else clean[:2]
            flag  = COUNTRY_FLAGS_CODE.get(code, "🌍")
            name  = COUNTRY_NAMES_CODE.get(code, code)


            # Get raw SMS from ZENEX
            raw_sms = ""
            otp     = ""
            try:
                entry = await zenex_poll_otp(rng)
                if entry:
                    raw_sms = entry.get("message", "") or entry.get("sms", "") or ""
                    otp     = extract_otp(raw_sms) or str(r.get("hits", "")) or ""
            except Exception:
                otp = str(r.get("hits", ""))

            if not otp or otp == "0":
                continue

            clean_sms = raw_sms.replace("<#>", "").strip() if raw_sms else ""

            text = (
                f"{flag} {escape_mdv2(name)}\n\n"
                f"📞 `{escape_mdv2(rng)}`\n"
                f"🔐 `{escape_mdv2(otp)}`\n"
                f"📘 Service: Facebook \\| A1\n"
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
            from telegram import CopyTextButton
            kb_rows = []
            if otp:
                kb_rows.append([InlineKeyboardButton(
                    "📋 🔑 COPY OTP",
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
                seen_ids.add(unique_id)
                posted += 1
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"A1 range post error for {rng}: {e}")

    except Exception as e:
        logger.error(f"job_a1_range_post error: {e}")


# ══════════════════════════════════════════════════════════
#              A2 RANGE POST (every 5 min)
# ══════════════════════════════════════════════════════════

async def job_a2_range_post(context) -> None:
    """A2 range post — delegates to panels/a2.py (has full logic + buttons)."""
    from panels.a2 import job_a2_range_post as _a2_job
    await _a2_job(context)


# ══════════════════════════════════════════════════════════
#              A3 RANGE POST (every 2 min)
# ══════════════════════════════════════════════════════════

async def job_a3_range_post(context) -> None:
    """
    Fetch live A3 (YesSMS) console data and post to channel every 5 minutes.
    Only posts ranges that have a real OTP in the console. Max 2 posts per run.
    """
    try:
        from config import YESMS_API_KEY, RANGE_CHANNEL_ID, MAIN_CHANNEL_LINK, NUMBER_BOT_LINK
    except ImportError:
        return
    if not YESMS_API_KEY or not RANGE_CHANNEL_ID:
        return

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton
    from panels.a3 import a3_get_active_ranges
    from utils.helpers import escape_mdv2

    try:
        ranges = await a3_get_active_ranges(force=True)
        bot    = context.bot
        now_bd = datetime.now(timezone(timedelta(hours=6)))

        posted     = 0
        seen_ids: set[str] = set()

        for r in ranges:
            if posted >= 4:
                break

            rid     = r.get("range_id", "")
            country = r.get("country", "Unknown")
            flag    = r.get("flag", "🌍")
            message = r.get("message", "")
            otp     = r.get("otp", "")
            service = r.get("service", "Facebook")

            if not rid or not otp:
                continue

            slot      = now_bd.strftime('%Y-%m-%d %H:') + str(now_bd.minute // 5 * 5).zfill(2)
            unique_id = f"a3_{rid}_{slot}"
            if unique_id in seen_ids:
                continue

            clean_sms = re.sub(r'<#>\s*', '', message).strip()

            text = (
                f"{flag} {escape_mdv2(country)}\n\n"
                f"📞 `{escape_mdv2(rid)}`\n"
                f"🔐 `{escape_mdv2(otp)}`\n"
                f"📘 Service: {escape_mdv2(service)} \\| A3\n"
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

            # ── Keyboard ──
            kb_rows = [[InlineKeyboardButton(
                "📋 🔑 COPY OTP",
                copy_text=CopyTextButton(text=otp),
                api_kwargs={"style": "success"},
            )]]
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

            try:
                await bot.send_message(
                    chat_id=RANGE_CHANNEL_ID,
                    text=text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(kb_rows),
                )
                seen_ids.add(unique_id)
                posted += 1
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"A3 range post error for {rid}: {e}")

    except Exception as e:
        logger.error(f"job_a3_range_post error: {e}", exc_info=True)


# ══════════════════════════════════════════════════════════
#              SESSION CLEANUP (every 10 min)
# ══════════════════════════════════════════════════════════

async def job_cleanup_sessions(context) -> None:
    cutoff = datetime.now() - timedelta(minutes=30)
    expired = []
    for uid, session in list(s3_user_sessions.items()):
        try:
            t = datetime.fromisoformat(session.get("assigned_time", ""))
            if t < cutoff:
                expired.append(uid)
        except Exception:
            expired.append(uid)
    for uid in expired:
        s3_user_sessions.pop(uid, None)
    if expired:
        logger.info(f"Session cleanup: removed {len(expired)} expired sessions")


# ══════════════════════════════════════════════════════════
#              REGISTRATION
# ══════════════════════════════════════════════════════════

def register_jobs(app) -> None:
    jq = app.job_queue
    jq.run_repeating(job_poll_s3_and_shark, interval=15,  first=5,   name="poll_s3_shark")
    jq.run_repeating(job_a1_range_post,     interval=300, first=60,  name="a1_range_post")
    jq.run_repeating(job_a2_range_post,     interval=120, first=90,  name="a2_range_post")
    jq.run_repeating(job_cleanup_sessions,  interval=600, first=120, name="cleanup_sessions")
    jq.run_repeating(job_a3_range_post,     interval=120, first=30,  name="a3_range_post")
    logger.info("✅ All background jobs registered")
