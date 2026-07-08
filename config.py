# config.py
"""
Centralized configuration for the bot.
Loads all environment variables once at import time and exposes them
as typed module-level constants. No other module should call os.getenv()
directly — everything reads from here to keep config in one place.

Active panels: A1 (Zenex), A2 (VOLTX), A3 (YesSMS), S3 (Number Pool), S4 Shark (CR API)
Removed panels: S1, S2 (X.Mint/StexSMS)
"""

import os
import warnings
from dotenv import load_dotenv

load_dotenv()


def _get_str(key: str, default: str = "") -> str:
    """Fetch an environment variable as a stripped string."""
    return os.getenv(key, default).strip()


def _get_int(key: str, default: str = "0") -> int:
    """Fetch an environment variable and safely coerce it to int."""
    raw = os.getenv(key, default).strip()
    try:
        return int(raw)
    except ValueError:
        return int(default)


# ══════════════════════════════════════════════════════════
#                    CORE BOT CONFIG
# ══════════════════════════════════════════════════════════

BOT_TOKEN: str = _get_str("BOT_TOKEN")
ADMIN_ID: int  = _get_int("ADMIN_ID")

BOT_USERNAME: str = _get_str("BOT_USERNAME")
if not BOT_USERNAME:
    warnings.warn(
        "BOT_USERNAME is not set! Deep-link buttons will be broken.",
        stacklevel=2,
    )

SUPPORT_ADMIN_LINK: str = _get_str("SUPPORT_ADMIN_LINK", "https://t.me/admin")
NUMBER_BOT_LINK: str    = _get_str("NUMBER_BOT_LINK")


# ══════════════════════════════════════════════════════════
#                    CHANNEL CONFIG
# ══════════════════════════════════════════════════════════

# Main public channel — users must join before using the bot
JOIN_CHANNEL_USERNAME: str = _get_str("JOIN_CHANNEL_USERNAME", "alwaysrvice24hours")
JOIN_CHANNEL_LINK: str     = _get_str("JOIN_CHANNEL_LINK", "https://t.me/alwaysrvice24hours")
MAIN_CHANNEL_LINK: str     = _get_str("MAIN_CHANNEL_LINK")

# OTP channel — live OTPs get broadcast here
OTP_CHANNEL_ID: int        = _get_int("OTP_CHANNEL_ID")
OTP_CHANNEL_LINK: str      = _get_str("OTP_CHANNEL_LINK")
OTP_CHANNEL_JOIN_LINK: str = _get_str("OTP_CHANNEL_JOIN_LINK", "https://t.me/+SWraCXOQrWM4Mzg9")

# Backup channel
BACKUP_CHANNEL_LINK: str = _get_str("BACKUP_CHANNEL_LINK", "https://t.me/+dutZzSJv-FxhYTdl")

# Second/backup channel details (optional)
CHANNEL2_USERNAME: str = _get_str("CHANNEL2_USERNAME")
CHANNEL2_LINK: str     = _get_str("CHANNEL2_LINK")
CHANNEL2_NAME: str     = _get_str("CHANNEL2_NAME", "Backup Channel")

# Channel where active ranges get posted periodically (A1/A2 jobs)
RANGE_CHANNEL_ID: int = _get_int("RANGE_CHANNEL_ID", os.getenv("OTP_CHANNEL_ID", "0"))

# Storage channel — bot saves S3 number pool & user data here as messages
STORAGE_CHANNEL_ID: int = _get_int("STORAGE_CHANNEL_ID")

# Channel IDs used for membership check (numeric)
MAIN_CHANNEL_CHECK_ID: int   = _get_int("MAIN_CHANNEL_CHECK_ID",   "-1001792312528")
OTP_CHANNEL_CHECK_ID: int    = _get_int("OTP_CHANNEL_CHECK_ID",    "-1002625886518")
BACKUP_CHANNEL_CHECK_ID: int = _get_int("BACKUP_CHANNEL_CHECK_ID", "-1003803282073")


# ══════════════════════════════════════════════════════════
#                    A1 — ZENEX NETWORK
# ══════════════════════════════════════════════════════════

ZENEX_API_KEY: str  = _get_str("ZENEX_API_KEY")
ZENEX_BASE_URL: str = "https://api.zenexnetwork.com"
ZENEX_CACHE_TTL: int = 60

A1_OTP_BASE_TIMEOUT_SECONDS: int = 20 * 60
A1_OTP_EXTEND_SECONDS: int       = 5 * 60
A1_INSTAGRAM_NUMBER_COUNT: int   = 1
A1_DEFAULT_NUMBER_COUNT: int     = 2


# ══════════════════════════════════════════════════════════
#                    A2 — VOLTX / 2OO9
# ══════════════════════════════════════════════════════════

A2_API_KEY: str = _get_str("A2_API_KEY")
A2_BASE_URL: str = _get_str(
    "A2_BASE_URL",
    "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
)
A2_CACHE_TTL: int = 60

A2_OTP_BASE_TIMEOUT_SECONDS: int = 20 * 60
A2_OTP_EXTEND_SECONDS: int       = 5 * 60


# ══════════════════════════════════════════════════════════
#                    A3 — YESSMS (yesms.online)
# ══════════════════════════════════════════════════════════

YESMS_API_KEY: str  = _get_str("YESMS_API_KEY")
YESMS_BASE_URL: str = _get_str("YESMS_BASE_URL", "https://yesms.online")
YESMS_CACHE_TTL: int = 60


# ══════════════════════════════════════════════════════════
#                    S3 — NUMBER POOL
# ══════════════════════════════════════════════════════════

# S3 uses Telegram STORAGE_CHANNEL_ID + Supabase to persist the number pool.
# No extra API keys needed — controlled entirely by admin via bot commands.

# How long (seconds) a number stays reserved before it's released back to pool
S3_NUMBER_RESERVE_SECONDS: int = 20 * 60


# ══════════════════════════════════════════════════════════
#                    S4 — SHARK / CR API
# ══════════════════════════════════════════════════════════

CR_API_URL: str   = _get_str("CR_API_URL")
CR_API_TOKEN: str = _get_str("CR_API_TOKEN")


# ══════════════════════════════════════════════════════════
#                    SUPABASE
# ══════════════════════════════════════════════════════════

SUPABASE_URL: str = _get_str("SUPABASE_URL")
SUPABASE_KEY: str = _get_str("SUPABASE_KEY")


# ══════════════════════════════════════════════════════════
#                    STARTUP VALIDATION
# ══════════════════════════════════════════════════════════

def validate_required_config() -> None:
    """
    Raise a clear error at startup if a hard-required setting is missing.
    Call this once from main.py before building the Application.
    """
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_ID:
        missing.append("ADMIN_ID")
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_KEY:
        missing.append("SUPABASE_KEY")
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}"
        )
