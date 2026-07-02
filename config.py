# config.py
"""
Centralized configuration for the bot.
Loads all environment variables once at import time and exposes them
as typed module-level constants. No other module should call os.getenv()
directly — everything reads from here to keep config in one place.
"""

import os
import warnings
from dotenv import load_dotenv

# Load .env file for local development. On Railway, real env vars are
# injected directly and this call is a harmless no-op.
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

# Telegram bot token from @BotFather (required)
BOT_TOKEN: str = _get_str("BOT_TOKEN")

# Telegram numeric user ID of the bot owner/admin
ADMIN_ID: int = _get_int("ADMIN_ID")

# Public @username of the bot (used to build deep links). Warn, don't crash,
# if missing — bot can still run without it, just with broken link buttons.
BOT_USERNAME: str = _get_str("BOT_USERNAME")
if not BOT_USERNAME:
    warnings.warn(
        "BOT_USERNAME is not set! Deep-link buttons will be broken.",
        stacklevel=2,
    )

# Support contact shown to users who need help
SUPPORT_ADMIN_LINK: str = _get_str("SUPPORT_ADMIN_LINK", "https://t.me/admin")


# ══════════════════════════════════════════════════════════
#                    CHANNEL CONFIG
# ══════════════════════════════════════════════════════════

# Channel where live OTPs get broadcast for public viewing
OTP_CHANNEL_ID: int = _get_int("OTP_CHANNEL_ID")
OTP_CHANNEL_LINK: str = _get_str("OTP_CHANNEL_LINK")

# Channel where active number ranges get posted periodically (used by A1/A2 jobs)
RANGE_CHANNEL_ID: int = _get_int("RANGE_CHANNEL_ID", os.getenv("OTP_CHANNEL_ID", "0"))


# ══════════════════════════════════════════════════════════
#                    A1 — ZENEX NETWORK
# ══════════════════════════════════════════════════════════

# API key for the ZENEX Network provider (A1 panel)
ZENEX_API_KEY: str = _get_str("ZENEX_API_KEY")

# Base URL for ZENEX Network API — fixed, not user-configurable
ZENEX_BASE_URL: str = "https://api.zenexnetwork.com"

# Cache TTL (seconds) for ZENEX active-ranges responses, to avoid hammering
# the provider API on every user interaction
ZENEX_CACHE_TTL: int = 60

# OTP wait window for A1 numbers: base timeout + extension when an OTP lands
A1_OTP_BASE_TIMEOUT_SECONDS: int = 20 * 60
A1_OTP_EXTEND_SECONDS: int = 5 * 60

# How many numbers to hand out per A1 request, keyed by service type
A1_INSTAGRAM_NUMBER_COUNT: int = 1
A1_DEFAULT_NUMBER_COUNT: int = 2


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
    Raise a clear error at startup if a hard-required setting is missing,
    rather than failing later with a cryptic Telegram API error.
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
