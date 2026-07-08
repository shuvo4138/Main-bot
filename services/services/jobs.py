# services/jobs.py
"""
APScheduler background jobs — registered once in main.py.

Jobs:
  job_poll_s3_and_shark   — poll CR API (S3) + Supabase shark_otps (S4) every 30s
  job_a1_range_post       — broadcast live A1 ranges to channel every 5 min
  job_a2_range_post       — broadcast live A2 ranges to channel every 5 min
  job_cleanup_sessions    — expire stale S3 user sessions every 10 min

All jobs are registered via register_jobs(app) from main.py.
"""

import asyncio
from datetime import datetime, timedelta

from utils.logger import get_logger
from utils.state import s3_user_sessions

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════
#              S3 + S4 OTP POLL (every 30s)
# ══════════════════════════════════════════════════════════

async def job_poll_s3_and_shark(context) -> None:
    """
    Poll CR API for S3 OTPs and Supabase for S4 Shark OTPs.
    Runs every 30 seconds.
    """
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
    """
    Fetch live A1 (ZENEX) ranges and post to the range channel.
    Runs every 5 minutes.
    """
    from config import ZENEX_API_KEY, RANGE_CHANNEL_ID
    if not ZENEX_API_KEY or not RANGE_CHANNEL_ID:
        return
    try:
        from panels.a1 import zenex_get_active_ranges
        from utils.helpers import (
            extract_otp, escape_mdv2,
            COUNTRY_FLAGS_CODE, COUNTRY_NAMES_CODE,
        )
        import re
        from datetime import timezone, timedelta as _td

        ranges   = await zenex_get_active_ranges()
        bot      = context.bot
        now_bd   = datetime.now(timezone(_td(hours=6)))
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
            otp   = str(r.get("hits", "------"))
            if not otp or otp == "0":
                continue

            text = (
                f"{flag} {escape_mdv2(name)}\n\n"
                f"📞 `{escape_mdv2(rng)}`\n"
                f"🔐 `{escape_mdv2(otp)}`\n"
                f"📘 Service: Facebook \\| A1\n"
                f"{escape_mdv2('────────────')}\n"
            )
            try:
                await bot.send_message(
                    chat_id=RANGE_CHANNEL_ID,
                    text=text,
                    parse_mode="MarkdownV2",
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
    """
    Delegate to panels/a2.py job which has full range+OTP logic.
    Runs every 5 minutes.
    """
    from panels.a2 import job_a2_range_post as _a2_job
    await _a2_job(context)


# ══════════════════════════════════════════════════════════
#              SESSION CLEANUP (every 10 min)
# ══════════════════════════════════════════════════════════

async def job_cleanup_sessions(context) -> None:
    """
    Remove expired S3 user sessions from memory.
    Sessions older than 30 minutes are dropped.
    """
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
    """
    Register all background jobs with the PTB JobQueue.
    Call once from main.py after building the Application.
    """
    jq = app.job_queue

    # S3 + S4 OTP poll — every 30 seconds
    jq.run_repeating(job_poll_s3_and_shark, interval=10, first=10,
                     name="poll_s3_shark")

    # A1 range post — every 5 minutes
    jq.run_repeating(job_a1_range_post, interval=300, first=60,
                     name="a1_range_post")

    # A2 range post — every 5 minutes
    jq.run_repeating(job_a2_range_post, interval=300, first=90,
                     name="a2_range_post")

    # Session cleanup — every 10 minutes
    jq.run_repeating(job_cleanup_sessions, interval=600, first=120,
                     name="cleanup_sessions")

    logger.info("✅ All background jobs registered")
