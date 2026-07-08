# utils/state.py
"""
Global in-memory state shared across all handlers and panels.

All mutable dicts live here so every module imports from one place
instead of creating module-level globals that go out of sync.

Active panels: A1, A2, S3, S4 Shark
"""

from typing import Any

# ── Per-user session data ──────────────────────────────────────────────────
# Keyed by Telegram user_id (int).
# Typical keys set by handlers:
#   panel, app, country, range, number, numbers (list),
#   a1_service, a1_zenex_service,
#   s3_session_id,
#   shark_country, shark_range
user_data: dict[int, dict[str, Any]] = {}

# ── Per-chat last bot message ID ───────────────────────────────────────────
# Keyed by chat_id (int) → message_id (int).
# Used by safe_edit() to update the correct message.
user_msg: dict[int, int] = {}

# ── A1 active OTP poll tasks ───────────────────────────────────────────────
# Keyed by user_id → asyncio.Task
# Cancelled when user requests a new number or /cancel.
a1_poll_tasks: dict[int, Any] = {}

# ── A2 active OTP poll tasks ───────────────────────────────────────────────
a2_poll_tasks: dict[int, Any] = {}

# ── S3 number pool ─────────────────────────────────────────────────────────
# Keyed by number string → {service, country, added_by, added_at, ...}
# Populated by admin upload commands; persisted via Supabase.
numbers_pool: dict[str, dict[str, Any]] = {}

# ── S3 active user sessions ────────────────────────────────────────────────
# Keyed by user_id → {number, service, assigned_at, session_id, ...}
s3_user_sessions: dict[int, dict[str, Any]] = {}

# ── S4 Shark active OTP poll tasks ────────────────────────────────────────
shark_poll_tasks: dict[int, Any] = {}

# ── A2 range cache ─────────────────────────────────────────────────────────
# Stores the last fetched A2 range list to avoid hammering the API.
# Format: {"ranges": [...], "fetched_at": float}
_a2_range_cache: dict[str, Any] = {}

# ── A1 range cache ─────────────────────────────────────────────────────────
_a1_range_cache: dict[str, Any] = {}
