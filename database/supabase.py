# database/supabase.py
"""
All Supabase interactions for the bot.

Tables used:
  users        — user session log (app, panel, country, range, last_number)
  posted_sms   — dedup guard so the same OTP is never posted twice
  shark_otps   — S4 Shark OTP rows written by the external CR API webhook

Active panels: A1, A2, S3, S4 Shark
S1 / S2 tables (s2_token_cache) are intentionally excluded.
"""

import time
from datetime import datetime, timedelta

import httpx

from config import SUPABASE_URL, SUPABASE_KEY
from utils.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════
#                  INTERNAL HELPERS
# ══════════════════════════════════════════════════════════

def _sb_headers(extra: dict | None = None) -> dict:
    """Return standard Supabase REST API headers."""
    headers = {
        "apikey": SUPABASE_URL and SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    # Fix: apikey should just be the key, not the conditional above
    headers["apikey"] = SUPABASE_KEY
    if extra:
        headers.update(extra)
    return headers


def _url(table: str) -> str:
    """Build the REST URL for a given Supabase table."""
    return f"{SUPABASE_URL}/rest/v1/{table}"


# ══════════════════════════════════════════════════════════
#                   USERS TABLE
# ══════════════════════════════════════════════════════════

async def db_save_user_async(user_id: int, data: dict) -> None:
    """
    Upsert a user row into the `users` table.
    Called after every number assignment so we have a usage log.
    """
    try:
        payload = {
            "user_id":     user_id,
            "name":        data.get("name", "User"),
            "joined":      data.get("joined", datetime.now().strftime("%Y-%m-%d %H:%M")),
            "app":         data.get("app", "FACEBOOK"),
            "panel":       data.get("panel", "A1"),
            "country":     data.get("country"),
            "range":       data.get("range"),
            "last_number": data.get("last_number"),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(_url("users"), json=payload, headers=_sb_headers())
    except Exception as e:
        logger.error(f"db_save_user_async error: {e}")


# ══════════════════════════════════════════════════════════
#                  POSTED SMS TABLE
# ══════════════════════════════════════════════════════════

async def db_is_posted(unique_id: str) -> bool:
    """
    Return True if this OTP unique_id has already been posted to the channel.
    Used to prevent duplicate OTP broadcasts.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                _url("posted_sms"),
                params={"unique_id": f"eq.{unique_id}", "select": "unique_id"},
                headers=_sb_headers(),
            )
        return len(res.json()) > 0
    except Exception:
        return False


async def db_mark_posted(unique_id: str) -> None:
    """Mark an OTP unique_id as posted so it is never broadcast again."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                _url("posted_sms"),
                json={"unique_id": unique_id},
                headers=_sb_headers(),
            )
    except Exception as e:
        logger.error(f"db_mark_posted error: {e}")


# ══════════════════════════════════════════════════════════
#               S4 SHARK OTP TABLE
# ══════════════════════════════════════════════════════════

async def fetch_shark_otps_from_supabase() -> list:
    """
    Fetch OTP rows from the `shark_otps` table written by the CR API webhook.
    Returns rows from the last 30 minutes, newest first.
    Each row: {unique_id, number, otp, message, app, dt, created_at}
    """
    try:
        cutoff = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                _url("shark_otps"),
                headers=_sb_headers(),
                params={
                    "select":     "unique_id,number,otp,message,app,dt,created_at",
                    "created_at": f"gte.{cutoff}",
                    "order":      "created_at.desc",
                    "limit":      "200",
                },
            )
        if res.status_code != 200:
            logger.error(f"fetch_shark_otps status={res.status_code}")
            return []
        rows = res.json()
        return rows if isinstance(rows, list) else []
    except Exception as e:
        logger.error(f"fetch_shark_otps_from_supabase error: {e}")
        return []


async def db_save_shark_otp(
    unique_id: str,
    number: str,
    otp: str | None,
    message: str,
    app: str = "UNKNOWN",
) -> None:
    """
    Upsert a single Shark OTP row.
    Called by the CR API inbound webhook handler.
    """
    try:
        payload = {
            "unique_id": unique_id,
            "number":    number,
            "otp":       otp,
            "message":   message,
            "app":       app,
            "dt":        datetime.utcnow().isoformat(),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(_url("shark_otps"), json=payload, headers=_sb_headers())
    except Exception as e:
        logger.error(f"db_save_shark_otp error: {e}")


# ══════════════════════════════════════════════════════════
#                  S3 NUMBER POOL (Supabase)
# ══════════════════════════════════════════════════════════

async def db_load_s3_pool() -> list[dict]:
    """
    Load all available (unassigned) S3 numbers from Supabase.
    Returns a list of pool row dicts.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                _url("s3_numbers"),
                params={"assigned": "eq.false", "select": "*", "order": "added_at.asc"},
                headers=_sb_headers(),
            )
        if res.status_code != 200:
            logger.error(f"db_load_s3_pool status={res.status_code}")
            return []
        rows = res.json()
        return rows if isinstance(rows, list) else []
    except Exception as e:
        logger.error(f"db_load_s3_pool error: {e}")
        return []


async def db_s3_assign_number(number: str, user_id: int) -> bool:
    """
    Mark a number as assigned in Supabase.
    Returns True on success.
    """
    try:
        payload = {
            "assigned":    True,
            "assigned_to": user_id,
            "assigned_at": datetime.utcnow().isoformat(),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.patch(
                _url("s3_numbers"),
                json=payload,
                params={"number": f"eq.{number}"},
                headers=_sb_headers(),
            )
        return res.status_code in (200, 204)
    except Exception as e:
        logger.error(f"db_s3_assign_number error: {e}")
        return False


async def db_s3_release_number(number: str) -> None:
    """Release a number back to the pool (unassign it)."""
    try:
        payload = {"assigned": False, "assigned_to": None, "assigned_at": None}
        async with httpx.AsyncClient(timeout=10) as client:
            await client.patch(
                _url("s3_numbers"),
                json=payload,
                params={"number": f"eq.{number}"},
                headers=_sb_headers(),
            )
    except Exception as e:
        logger.error(f"db_s3_release_number error: {e}")


async def db_s3_add_number(
    number: str,
    service: str,
    country: str,
    added_by: int,
    pool_key: str = "",
) -> bool:
    """Insert a new number into the S3 pool."""
    try:
        payload = {
            "number":   number,
            "service":  service,
            "country":  country,
            "pool_key": pool_key,
            "added_by": added_by,
            "added_at": datetime.utcnow().isoformat(),
            "assigned": False,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.post(_url("s3_numbers"), json=payload, headers=_sb_headers())
        return res.status_code in (200, 201)
    except Exception as e:
        logger.error(f"db_s3_add_number error: {e}")
        return False


async def db_s3_delete_number(number: str) -> None:
    """Permanently remove a number from the S3 pool."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.delete(
                _url("s3_numbers"),
                params={"number": f"eq.{number}"},
                headers=_sb_headers(),
            )
    except Exception as e:
        logger.error(f"db_s3_delete_number error: {e}")
