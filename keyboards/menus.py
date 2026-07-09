# keyboards/menus.py
"""
All Telegram keyboard builders for the bot.

Active panels: A1, A2, S3, S4 Shark
S1 / S2 keyboards removed.
"""

from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    CopyTextButton,
)

from config import OTP_CHANNEL_LINK, OTP_CHANNEL_JOIN_LINK, NUMBER_BOT_LINK
from utils.helpers import get_flag_by_iso


# ══════════════════════════════════════════════════════════
#                  WELCOME / START TEXT
# ══════════════════════════════════════════════════════════

def get_welcome_text(first_name: str = "User") -> str:
    """
    Personalized welcome message — matches screenshot design:
      👋 Welcome •.,♡ •.,♡ SHUVO ♡,·• ♡,·•!
      Use the menu below to get started.
    """
    return (
        f"👋 Welcome •.,♡ •.,♡ <b>{first_name}</b> ♡,·• ♡,·•!\n\n"
        "Use the menu below to get started."
    )

# Service select header — shown above inline buttons
SERVICE_SELECT_TEXT = "📱 Please select your service:"
FB_PANEL_SELECT_TEXT = "📘 Facebook — Select a panel:"


# ══════════════════════════════════════════════════════════
#                  MAIN REPLY KEYBOARD
# ══════════════════════════════════════════════════════════

def main_keyboard(user_id: int | None = None) -> ReplyKeyboardMarkup:
    """
    Bottom reply keyboard — matches screenshot:
      📲 Get Number   📦 My Numbers
      📡 Custom Range 🚦 Live Traffic
      ✈️ Telegram     👤 Profile
      🆘 Support
    """
    buttons = [
        [KeyboardButton("📲 Get Number"),   KeyboardButton("📦 My Numbers")],
        [KeyboardButton("📡 Custom Range"), KeyboardButton("🚦 Live Traffic")],
        [KeyboardButton("✈️ Telegram"),     KeyboardButton("👤 Profile")],
        [KeyboardButton("🆘 Support")],
    ]
    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )


# ══════════════════════════════════════════════════════════
#                  JOIN CHANNEL
# ══════════════════════════════════════════════════════════

def join_channel_keyboard(
    join_link: str,
    otp_link: str    = "",
    backup_link: str = "",
) -> InlineKeyboardMarkup:
    """Ask user to join required channels before using the bot."""
    rows = [[InlineKeyboardButton(
        "📢 Join Main Channel", url=join_link,
        api_kwargs={"style": "success"},
    )]]
    if otp_link:
        rows.append([InlineKeyboardButton(
            "📡 Join OTP Channel", url=otp_link,
            api_kwargs={"style": "primary"},
        )])
    if backup_link:
        rows.append([InlineKeyboardButton(
            "💾 Join Backup Channel", url=backup_link,
            api_kwargs={"style": "primary"},
        )])
    rows.append([InlineKeyboardButton(
        "✅ I've Joined", callback_data="check_join",
        api_kwargs={"style": "success"},
    )])
    return InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════
#                  SERVICE / PANEL SELECT
# ══════════════════════════════════════════════════════════

async def panel_select_inline() -> InlineKeyboardMarkup:
    """
    Top-level app selection — 2x2 grid:

      [📘 Facebook]   [📸 Instagram]
      [💬 WhatsApp]   [✈️ Telegram]

    Facebook opens a submenu (A1 / A2 / A3 / S3).
    Instagram goes straight to its range/S3 flow.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📘 Facebook",
                callback_data="app_fb",
                api_kwargs={"style": "primary"},
            ),
            InlineKeyboardButton(
                "📸 Instagram",
                callback_data="s3app:ig",
                api_kwargs={"style": "danger"},
            ),
        ],
        [
            InlineKeyboardButton(
                "💬 WhatsApp",
                callback_data="select_panel_WA",
                api_kwargs={"style": "success"},
            ),
            InlineKeyboardButton(
                "✈️ Telegram",
                callback_data="select_panel_TG",
                api_kwargs={"style": "success"},
            ),
        ],
    ])


async def fb_panel_select_inline() -> InlineKeyboardMarkup:
    """
    Facebook panel submenu — shown after tapping "📘 Facebook":

      [🆕 Facebook A1]        ← green
      [⚡ Facebook A2]        ← green
      [🌐 Facebook A3]        ← green
      [🔴 Facebook S3 (N)]   ← red
      [◀️ Back]
    """
    from panels.s3 import get_numbers_pool
    pool     = get_numbers_pool()
    fb_count = sum(len(v) for k, v in pool.items() if k.endswith("_fb"))

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🆕 Facebook A1",
            callback_data="select_panel_A1_fb",
            api_kwargs={"style": "success"},
        )],
        [InlineKeyboardButton(
            "⚡ Facebook A2",
            callback_data="select_panel_A2_fb",
            api_kwargs={"style": "success"},
        )],
        [InlineKeyboardButton(
            "🌐 Facebook A3",
            callback_data="select_panel_A3_fb",
            api_kwargs={"style": "success"},
        )],
        [InlineKeyboardButton(
            f"🔴 Facebook S3 ({fb_count})",
            callback_data="s3app:fb",
            api_kwargs={"style": "danger"},
        )],
        [InlineKeyboardButton(
            "◀️ Back", callback_data="back_app",
            api_kwargs={"style": "primary"},
        )],
    ])


# ══════════════════════════════════════════════════════════
#                  COUNTRY / RANGE SELECT
# ══════════════════════════════════════════════════════════

def country_select_inline(
    countries: list,
    app_name: str,
    back_cb: str      = "back_app",
    show_ig_btn: bool = True,
) -> InlineKeyboardMarkup:
    """2-column country list keyboard."""
    buttons: list[list] = []
    row: list = []
    for c in countries[:20]:
        country_name = c if isinstance(c, str) else c.get("country", "")
        panel        = c.get("panel", "A1") if isinstance(c, dict) else "A1"
        flag         = get_flag_by_iso(country_name)
        row.append(InlineKeyboardButton(
            f"{flag} {country_name}",
            callback_data=f"country_{panel}_{country_name}",
            api_kwargs={"style": "primary"},
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if show_ig_btn:
        first_panel = (
            countries[0].get("panel", "A1")
            if countries and isinstance(countries[0], dict)
            else "A1"
        )
        buttons.append([InlineKeyboardButton(
            "📸 Instagram",
            callback_data=f"ig_panel_{first_panel}",
            api_kwargs={"style": "success"},
        )])
    buttons.append([InlineKeyboardButton(
        "◀️ Back", callback_data=back_cb,
        api_kwargs={"style": "primary"},
    )])
    return InlineKeyboardMarkup(buttons)


def range_select_inline(
    ranges: list,
    app_name: str,
    country: str,
    back_cb: str = "back_app",
) -> InlineKeyboardMarkup:
    """Range list keyboard (up to 15 ranges)."""
    buttons: list[list] = []
    for r in ranges[:15]:
        rv      = r["range"] if isinstance(r, dict) else r
        display = rv if rv.upper().endswith("X") else rv + "XXX"
        buttons.append([InlineKeyboardButton(
            f"📡 {display}",
            callback_data=f"range_{rv}",
            api_kwargs={"style": "primary"},
        )])
    buttons.append([InlineKeyboardButton(
        "◀️ Back",
        callback_data=f"back_country_{app_name}",
        api_kwargs={"style": "primary"},
    )])
    return InlineKeyboardMarkup(buttons)


# ══════════════════════════════════════════════════════════
#                  NUMBER CARD — A1
# ══════════════════════════════════════════════════════════

def after_number_inline_a1(
    numbers: list[str] | str,
    service_name: str,
    otp_status: dict | None = None,
    range_val: str = "",
) -> InlineKeyboardMarkup:
    """Number card keyboard for A1. ✅ appears once OTP received."""
    if isinstance(numbers, str):
        numbers = [numbers]
    if otp_status is None:
        otp_status = {}

    buttons: list[list] = []
    colors = ["success", "primary"]
    for i, num in enumerate(numbers):
        clean   = str(num).replace("+", "").strip()
        has_otp = otp_status.get(clean, False)
        label   = f"📋 {clean} ✅" if has_otp else f"📋 {clean}"
        buttons.append([InlineKeyboardButton(
            label,
            copy_text=CopyTextButton(text=clean),
            api_kwargs={"style": colors[i % len(colors)]},
        )])

    buttons.append([InlineKeyboardButton(
        "🔄 Change Numbers", callback_data="a1_change_numbers",
        api_kwargs={"style": "success"},
    )])
    buttons.append([InlineKeyboardButton(
        "🌍 Change Region", callback_data="select_panel_A1_fb",
        api_kwargs={"style": "primary"},
    )])
    ch_link = OTP_CHANNEL_LINK or OTP_CHANNEL_JOIN_LINK or ""
    if ch_link:
        buttons.append([InlineKeyboardButton(
            "📢 OTP Channel", url=ch_link,
            api_kwargs={"style": "primary"},
        )])
    return InlineKeyboardMarkup(buttons)


# ══════════════════════════════════════════════════════════
#                  NUMBER CARD — A2
# ══════════════════════════════════════════════════════════

def after_number_inline_a2(
    numbers: list[str],
    range_val: str = "",
) -> InlineKeyboardMarkup:
    """Number card keyboard for A2 panel."""
    buttons: list[list] = []
    colors = ["success", "primary"]
    for i, num in enumerate(numbers):
        clean = str(num).replace("+", "").strip()
        buttons.append([InlineKeyboardButton(
            f"📋 {clean}",
            copy_text=CopyTextButton(text=clean),
            api_kwargs={"style": colors[i % len(colors)]},
        )])
    buttons.append([InlineKeyboardButton(
        "🔄 Change Numbers", callback_data="a2_change_numbers",
        api_kwargs={"style": "success"},
    )])
    buttons.append([InlineKeyboardButton(
        "🌍 Change Region", callback_data="select_panel_A2_fb",
        api_kwargs={"style": "primary"},
    )])
    ch_link = OTP_CHANNEL_LINK or OTP_CHANNEL_JOIN_LINK or ""
    if ch_link:
        buttons.append([InlineKeyboardButton(
            "📢 OTP Channel", url=ch_link,
            api_kwargs={"style": "primary"},
        )])
    return InlineKeyboardMarkup(buttons)


# ══════════════════════════════════════════════════════════
#                  NUMBER CARD — S3
# ══════════════════════════════════════════════════════════

def after_number_inline_s3(
    pool_key: str,
    numbers: list[str] | None = None,
) -> InlineKeyboardMarkup:
    """Number card keyboard for S3 pool panel."""
    rows: list[list] = []
    if numbers:
        colors = ["success", "primary"]
        for i, num in enumerate(numbers):
            rows.append([InlineKeyboardButton(
                f"📋 {num}",
                copy_text=CopyTextButton(text=str(num)),
                api_kwargs={"style": colors[i % len(colors)]},
            )])
    rows.append([InlineKeyboardButton(
        "🔄 Change Numbers", callback_data=f"s3change:{pool_key}",
        api_kwargs={"style": "success"},
    )])
    rows.append([InlineKeyboardButton(
        "🌍 Change Country", callback_data="s3changecountry",
        api_kwargs={"style": "primary"},
    )])
    ch_link = OTP_CHANNEL_LINK or OTP_CHANNEL_JOIN_LINK or ""
    if ch_link:
        rows.append([InlineKeyboardButton(
            "📢 OTP Channel", url=ch_link,
            api_kwargs={"style": "primary"},
        )])
    return InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════
#                  ADMIN KEYBOARDS
# ══════════════════════════════════════════════════════════

def admin_keyboard() -> InlineKeyboardMarkup:
    """Unified admin panel — A1/A2/S3/S4 only."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats",          callback_data="admin_stats",        api_kwargs={"style": "primary"}),
            InlineKeyboardButton("👥 Users",           callback_data="admin_allusers",     api_kwargs={"style": "primary"}),
        ],
        [
            InlineKeyboardButton("📡 API Status",     callback_data="admin_apistatus",    api_kwargs={"style": "primary"}),
            InlineKeyboardButton("🚦 Live Traffic",   callback_data="admin_livetraffic",  api_kwargs={"style": "primary"}),
        ],
        [
            InlineKeyboardButton("🆕 A1 Panel",       callback_data="admin_a1panel",      api_kwargs={"style": "success"}),
            InlineKeyboardButton("⚡ A2 Panel",       callback_data="admin_a2panel",      api_kwargs={"style": "success"}),
        ],
        [
            InlineKeyboardButton("🔴 S3 Panel",       callback_data="s3admin_stats",      api_kwargs={"style": "danger"}),
            InlineKeyboardButton("🦈 S4 Shark",       callback_data="admin_s4panel",      api_kwargs={"style": "danger"}),
        ],
        [
            InlineKeyboardButton("📢 Broadcast",      callback_data="admin_broadcast",    api_kwargs={"style": "primary"}),
            InlineKeyboardButton("🚨 Send Alert",     callback_data="s3admin_broadcast",  api_kwargs={"style": "danger"}),
        ],
        [
            InlineKeyboardButton("📈 Analytics",      callback_data="s3admin_analytics",  api_kwargs={"style": "primary"}),
            InlineKeyboardButton("🔄 Refresh",        callback_data="admin_refresh",      api_kwargs={"style": "primary"}),
        ],
        [
            InlineKeyboardButton("📤 Upload Numbers", callback_data="s3admin_addnumbers", api_kwargs={"style": "success"}),
            InlineKeyboardButton("🗑 Delete Numbers", callback_data="s3admin_delete",     api_kwargs={"style": "danger"}),
        ],
        [
            InlineKeyboardButton("⚙️ Settings",       callback_data="s3admin_settings",   api_kwargs={"style": "primary"}),
            InlineKeyboardButton("🧹 Clear Cache",    callback_data="admin_clearcache",   api_kwargs={"style": "primary"}),
        ],
        [
            InlineKeyboardButton("♻️ Restart Bot",    callback_data="admin_restart",      api_kwargs={"style": "danger"}),
            InlineKeyboardButton("⛔ Stop Bot",       callback_data="admin_stop",         api_kwargs={"style": "danger"}),
        ],
    ])


def admin_keyboard_s3() -> InlineKeyboardMarkup:
    """S3-specific admin sub-panel."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Statistics",     callback_data="s3admin_stats",      api_kwargs={"style": "primary"}),
            InlineKeyboardButton("📈 Analytics",      callback_data="s3admin_analytics",  api_kwargs={"style": "primary"}),
        ],
        [
            InlineKeyboardButton("📢 Broadcast",      callback_data="s3admin_broadcast",  api_kwargs={"style": "primary"}),
            InlineKeyboardButton("📤 Upload Numbers", callback_data="s3admin_addnumbers", api_kwargs={"style": "success"}),
        ],
        [
            InlineKeyboardButton("🗑️ Delete Numbers", callback_data="s3admin_delete",     api_kwargs={"style": "danger"}),
            InlineKeyboardButton("⚙️ Settings",       callback_data="s3admin_settings",   api_kwargs={"style": "primary"}),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="admin_main",                      api_kwargs={"style": "primary"})],
    ])
