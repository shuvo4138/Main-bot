# handlers/callbacks.py
"""
Callback query handler — routes all InlineKeyboard button presses.

Active panels: A1, A2, S3, S4 Shark
S1 / S2 removed.
"""

import asyncio
import re

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import (
    ADMIN_ID,
    ZENEX_API_KEY,
    A2_API_KEY,
    JOIN_CHANNEL_LINK,
    OTP_CHANNEL_JOIN_LINK,
    BACKUP_CHANNEL_LINK,
)
from utils.logger import get_logger
from utils.state import user_data, user_msg
from utils.helpers import (
    get_flag_by_iso,
    COUNTRY_FLAGS_CODE,
    COUNTRY_NAMES_CODE,
)
from handlers.helpers import (
    init_user,
    safe_edit,
    cancel_all_otp_tasks,
    check_user_joined,
    processing_users,
)
from keyboards.menus import (
    main_keyboard,
    panel_select_inline,
    join_channel_keyboard,
    after_number_inline_s3,
    SERVICE_SELECT_TEXT,
)
from panels.s3 import (
    get_numbers_pool,
    s3_get_session,
    s3_set_session,
    s3_get_all_users,
    add_numbers_to_pool,
    remove_number_from_pool,
    count_numbers,
    get_button_label,
    get_short_label,
    parse_pool_key,
    is_shark_pool,
    mark_number_assigned,
)
from panels.a2 import a2_extract_country_code, a2_get_active_ranges, a2_get_cached_ranges

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════
#                  MAIN CALLBACK ROUTER
# ══════════════════════════════════════════════════════════

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    await query.answer()
    data    = query.data or ""
    user_id = update.effective_user.id
    chat_id = query.message.chat.id
    init_user(user_id)

    # ── Join check ──
    if data not in ("check_join", "verify_join", "noop_channel"):
        joined = await check_user_joined(context.bot, user_id)
        if not joined:
            await safe_edit(query,
                "⚠️ Bot ব্যবহার করতে সব channel join করুন:",
                reply_markup=join_channel_keyboard(
                    join_link=JOIN_CHANNEL_LINK,
                    otp_link=OTP_CHANNEL_JOIN_LINK,
                    backup_link=BACKUP_CHANNEL_LINK,
                ),
            )
            return

    # ══════════════════════════════════════════════════════
    #  ADMIN callbacks
    # ══════════════════════════════════════════════════════
    if data.startswith("admin_") or data.startswith("s3admin_"):
        from handlers.admin import handle_admin_callback
        prefix = "admin_" if data.startswith("admin_") else "s3admin_"
        action = data.replace(prefix, "")
        await handle_admin_callback(query, user_id, action, context)
        return

    # ══════════════════════════════════════════════════════
    #  JOIN / VERIFY
    # ══════════════════════════════════════════════════════
    if data in ("check_join", "verify_join"):
        from handlers.helpers import _join_cache
        _join_cache.pop(user_id, None)
        joined = await check_user_joined(context.bot, user_id)
        if joined:
            try:
                await query.message.delete()
            except Exception:
                pass
            kb   = await panel_select_inline()
            msg  = await context.bot.send_message(
                chat_id, SERVICE_SELECT_TEXT, reply_markup=kb
            )
            user_msg[chat_id] = msg.message_id
        else:
            await safe_edit(query,
                "❌ এখনো সব channel join করা হয়নি। Join করে আবার try করুন।",
                reply_markup=join_channel_keyboard(
                    join_link=JOIN_CHANNEL_LINK,
                    otp_link=OTP_CHANNEL_JOIN_LINK,
                    backup_link=BACKUP_CHANNEL_LINK,
                ),
            )
        return

    if data == "noop_channel":
        await query.answer("⚠️ Channel link configured নেই।")
        return

    # ══════════════════════════════════════════════════════
    #  BACK / STOP
    # ══════════════════════════════════════════════════════
    if data == "back_app":
        cancel_all_otp_tasks(user_id)
        kb  = await panel_select_inline()
        await safe_edit(query, SERVICE_SELECT_TEXT, reply_markup=kb)
        return

    if data == "stop_auto":
        cancel_all_otp_tasks(user_id)
        await query.answer("🛑 Auto OTP বন্ধ করা হয়েছে!")
        return

    # ══════════════════════════════════════════════════════
    #  PANEL SELECT — A1 / A2 / WA / TG / S3
    # ══════════════════════════════════════════════════════

    # ── A1 Facebook ──
    if data == "select_panel_A1_fb":
        user_data[user_id].update({
            "panel": "A1", "app": "FACEBOOK",
            "a1_service": "Facebook New Account",
            "a1_zenex_service": "Facebook",
        })
        await safe_edit(query, "⏳ A1 ranges লোড হচ্ছে...")
        from panels.a1 import zenex_get_active_ranges
        ranges = await zenex_get_active_ranges(service="Facebook")
        if not ranges:
            await safe_edit(query,
                "❌ A1 Facebook এ এখন কোনো active range নেই।",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                          api_kwargs={"style": "primary"})
                ]]),
            )
            return
        # ── Country grouped ──
        countries: dict = {}
        for r in ranges:
            rng  = r.get("range", "")
            if not rng: continue
            code = rng[:3] if rng[:3] in COUNTRY_NAMES_CODE else rng[:2]
            name = COUNTRY_NAMES_CODE.get(code, code)
            countries.setdefault(name, []).append({"range": rng, "code": code})
        user_data[user_id]["a1_countries"] = countries
        buttons = []
        for name, rngs in list(countries.items())[:20]:
            code = rngs[0]["code"]
            flag = COUNTRY_FLAGS_CODE.get(code, "🌍")
            buttons.append([InlineKeyboardButton(
                f"{flag} {name} ({len(rngs)})",
                callback_data=f"a1_country:{name}",
                api_kwargs={"style": "primary"},
            )])
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                              api_kwargs={"style": "primary"})])
        await safe_edit(query, "🆕 *A1 Facebook*\n\n🌍 Country select করুন:",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ── A1 country → range list ──
    if data.startswith("a1_country:"):
        country_name = data.split("a1_country:", 1)[1]
        countries    = user_data[user_id].get("a1_countries", {})
        rngs         = countries.get(country_name, [])
        if not rngs:
            await safe_edit(query, "❌ Range পাওয়া যায়নি।")
            return
        code = rngs[0]["code"]
        flag = COUNTRY_FLAGS_CODE.get(code, "🌍")
        buttons = []
        for r in rngs[:15]:
            rng = r["range"]
            buttons.append([InlineKeyboardButton(
                f"📡 {rng}",
                callback_data=f"a1_range:{rng}",
                api_kwargs={"style": "primary"},
            )])
        buttons.append([InlineKeyboardButton("◀️ Back",
                        callback_data="select_panel_A1_fb",
                        api_kwargs={"style": "primary"})])
        await safe_edit(query,
            f"🆕 *A1 Facebook — {flag} {country_name}*\n\n📡 Range select করুন:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ── A1 range → number ──
    if data.startswith("a1_range:"):
        rng = data.split("a1_range:", 1)[1]
        user_data[user_id]["range"] = rng
        from panels.a1 import do_get_number_a1
        asyncio.create_task(do_get_number_a1(query.message, user_id, bot=context.bot))
        return

    # ── A1 change numbers ──
    if data == "a1_change_numbers":
        rng = user_data[user_id].get("range", "")
        if rng:
            cancel_all_otp_tasks(user_id)
            from panels.a1 import do_get_number_a1
            asyncio.create_task(do_get_number_a1(query.message, user_id, bot=context.bot))
        return

    # ── A2 Facebook ──
    if data == "select_panel_A2_fb":
        user_data[user_id].update({"panel": "A2", "app": "FACEBOOK"})
        await safe_edit(query, "⏳ A2 ranges লোড হচ্ছে...")
        ranges    = a2_get_cached_ranges() or await a2_get_active_ranges(force=True)
        fb_ranges = [r for r in ranges if r.get("service","").upper() in ("FACEBOOK","FB","")]
        if not fb_ranges: fb_ranges = ranges
        if not fb_ranges:
            await safe_edit(query,
                "❌ A2 Facebook এ এখন কোনো active range নেই।",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                          api_kwargs={"style": "primary"})
                ]]),
            )
            return
        # ── Country grouped ──
        countries: dict = {}
        for r in fb_ranges:
            rng  = r.get("range","")
            rid  = r.get("rid", rng)
            if not rng: continue
            code = a2_extract_country_code(rng)
            name = COUNTRY_NAMES_CODE.get(code, code)
            countries.setdefault(name, []).append({"range": rng, "rid": rid, "code": code})
        user_data[user_id]["a2_countries"] = countries
        buttons = []
        for name, rngs in list(countries.items())[:20]:
            code = rngs[0]["code"]
            flag = COUNTRY_FLAGS_CODE.get(code, "🌍")
            buttons.append([InlineKeyboardButton(
                f"{flag} {name} ({len(rngs)})",
                callback_data=f"a2_country:{name}",
                api_kwargs={"style": "primary"},
            )])
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                              api_kwargs={"style": "primary"})])
        await safe_edit(query, "⚡ *A2 Facebook*\n\n🌍 Country select করুন:",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ── A2 country → range list ──
    if data.startswith("a2_country:"):
        country_name = data.split("a2_country:", 1)[1]
        countries    = user_data[user_id].get("a2_countries", {})
        rngs         = countries.get(country_name, [])
        if not rngs:
            await safe_edit(query, "❌ Range পাওয়া যায়নি।")
            return
        code = rngs[0]["code"]
        flag = COUNTRY_FLAGS_CODE.get(code, "🌍")
        buttons = []
        for r in rngs[:15]:
            buttons.append([InlineKeyboardButton(
                f"📡 {r['range']}",
                callback_data=f"a2_range:{r['rid']}",
                api_kwargs={"style": "primary"},
            )])
        buttons.append([InlineKeyboardButton("◀️ Back",
                        callback_data="select_panel_A2_fb",
                        api_kwargs={"style": "primary"})])
        await safe_edit(query,
            f"⚡ *A2 Facebook — {flag} {country_name}*\n\n📡 Range select করুন:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ── A2 range → number ──
    if data.startswith("a2_range:"):
        rid = data.split("a2_range:", 1)[1]
        user_data[user_id]["range"] = rid
        user_data[user_id]["panel"] = "A2"
        user_msg[chat_id] = query.message.message_id
        from panels.a2 import do_get_number_a2
        asyncio.create_task(do_get_number_a2(query.message, user_id, bot=context.bot))
        return

    # ── A2 change numbers ──
    if data == "a2_change_numbers":
        cancel_all_otp_tasks(user_id)
        user_msg[chat_id] = query.message.message_id
        from panels.a2 import do_get_number_a2
        asyncio.create_task(do_get_number_a2(query.message, user_id, bot=context.bot))
        return

    # ── A3 Facebook ──
    if data == "select_panel_A3_fb":
        user_data[user_id].update({"panel": "A3", "app": "FACEBOOK"})
        await safe_edit(query, "⏳ A3 ranges লোড হচ্ছে...")
        from panels.a3 import a3_get_active_ranges
        from config import YESMS_API_KEY
        if not YESMS_API_KEY:
            await safe_edit(query,
                "❌ YESMS API Key সেট করা নেই! Railway ENV এ YESMS_API_KEY add করুন।",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                          api_kwargs={"style": "primary"})
                ]]),
            )
            return
        ranges = await a3_get_active_ranges()
        fb_ranges = [r for r in ranges if r.get("service","").upper() in ("FACEBOOK","FB","")]
        if not fb_ranges: fb_ranges = ranges
        if not fb_ranges:
            await safe_edit(query,
                "❌ A3 Facebook এ এখন কোনো active range নেই।",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                          api_kwargs={"style": "primary"})
                ]]),
            )
            return
        # Country grouped
        countries: dict = {}
        for r in fb_ranges:
            name = r.get("country", "Unknown")
            countries.setdefault(name, []).append(r)
        user_data[user_id]["a3_countries"] = countries
        buttons = []
        for name, rngs in list(countries.items())[:20]:
            flag = rngs[0].get("flag", "🌍")
            buttons.append([InlineKeyboardButton(
                f"{flag} {name} ({len(rngs)})",
                callback_data=f"a3_country:{name}",
                api_kwargs={"style": "primary"},
            )])
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                              api_kwargs={"style": "primary"})])
        await safe_edit(query, "🌐 *A3 Facebook*

🌍 Country select করুন:",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ── A3 country → range list ──
    if data.startswith("a3_country:"):
        country_name = data.split("a3_country:", 1)[1]
        countries    = user_data[user_id].get("a3_countries", {})
        rngs         = countries.get(country_name, [])
        if not rngs:
            await safe_edit(query, "❌ Range পাওয়া যায়নি।")
            return
        flag = rngs[0].get("flag", "🌍")
        buttons = []
        for r in rngs[:15]:
            rid = r["range_id"]
            buttons.append([InlineKeyboardButton(
                f"📡 {rid}",
                callback_data=f"a3_range:{rid}",
                api_kwargs={"style": "primary"},
            )])
        buttons.append([InlineKeyboardButton("◀️ Back",
                        callback_data="select_panel_A3_fb",
                        api_kwargs={"style": "primary"})])
        await safe_edit(query,
            f"🌐 *A3 Facebook — {flag} {country_name}*

📡 Range select করুন:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ── A3 range → number ──
    if data.startswith("a3_range:"):
        rid = data.split("a3_range:", 1)[1]
        # Find country/flag from cached countries
        a3_countries = user_data[user_id].get("a3_countries", {})
        for cname, rngs in a3_countries.items():
            for r in rngs:
                if r["range_id"] == rid:
                    user_data[user_id]["a3_country"] = cname
                    user_data[user_id]["a3_flag"]    = r.get("flag", "🌍")
                    break
        user_data[user_id]["range"] = rid
        user_data[user_id]["panel"] = "A3"
        user_msg[chat_id] = query.message.message_id
        from panels.a3 import do_get_number_a3
        asyncio.create_task(do_get_number_a3(query.message, user_id, bot=context.bot))
        return

    # ── A3 change numbers ──
    if data == "a3_change_numbers":
        cancel_all_otp_tasks(user_id)
        user_msg[chat_id] = query.message.message_id
        from panels.a3 import do_get_number_a3
        asyncio.create_task(do_get_number_a3(query.message, user_id, bot=context.bot))
        return

    # ── WhatsApp ──
    if data == "select_panel_WA":
        user_data[user_id]["app"] = "WHATSAPP"
        await safe_edit(query, "⏳ WhatsApp ranges লোড হচ্ছে...")
        # A1 + A2 combined country list
        a1_ranges, a2_all = await asyncio.gather(
            (panels_a1_get() if ZENEX_API_KEY else asyncio.sleep(0, result=[])),
            a2_get_active_ranges(force=False),
            return_exceptions=True,
        )
        if isinstance(a1_ranges, Exception): a1_ranges = []
        if isinstance(a2_all, Exception):    a2_all    = []
        wa_a1 = [r for r in (a1_ranges or []) if "whatsapp" in r.get("service","").lower()]
        wa_a2 = [r for r in (a2_all or []) if r.get("service","").upper() in ("WHATSAPP","WA")]

        wa_countries: dict[str, dict] = {}
        for r in wa_a1:
            rng  = r.get("range","")
            code = rng[:3] if rng[:3] in COUNTRY_NAMES_CODE else rng[:2]
            name = COUNTRY_NAMES_CODE.get(code, code)
            flag = COUNTRY_FLAGS_CODE.get(code, "🌍")
            if name not in wa_countries:
                wa_countries[name] = {"flag": flag, "sources": []}
            wa_countries[name]["sources"].append({"panel":"A1","range":rng,"rid":rng})
        for r in wa_a2:
            rng  = r.get("range","")
            rid  = r.get("rid", rng)
            code = a2_extract_country_code(rng)
            name = COUNTRY_NAMES_CODE.get(code, code)
            flag = COUNTRY_FLAGS_CODE.get(code, "🌍")
            if name not in wa_countries:
                wa_countries[name] = {"flag": flag, "sources": []}
            wa_countries[name]["sources"].append({"panel":"A2","range":rng,"rid":rid})

        if not wa_countries:
            await safe_edit(query,
                "❌ WhatsApp এ এখন কোনো active range নেই।",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                          api_kwargs={"style": "primary"})
                ]]),
            )
            return

        user_data[user_id]["wa_countries"] = wa_countries
        buttons = []
        for name, info in list(wa_countries.items())[:25]:
            buttons.append([InlineKeyboardButton(
                f"{info['flag']} {name}",
                callback_data=f"wa_country:{name}",
                api_kwargs={"style": "primary"},
            )])
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                              api_kwargs={"style": "primary"})])
        await safe_edit(query, "💬 *WhatsApp*\n\n🌍 Country select করুন:",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ── WhatsApp country → range ──
    if data.startswith("wa_country:"):
        country_name = data.split("wa_country:", 1)[1]
        wa_countries = user_data[user_id].get("wa_countries", {})
        info         = wa_countries.get(country_name, {})
        sources      = info.get("sources", [])
        flag         = info.get("flag", "🌍")
        buttons = []
        seen: set[str] = set()
        for src in sources:
            panel_src = src["panel"]
            rng       = src.get("range","")
            rid       = src.get("rid", rng)
            if not rng or rng in seen: continue
            seen.add(rng)
            tag = "🆕" if panel_src == "A1" else "⚡"
            cb  = f"wa_range_a1:{rng}" if panel_src == "A1" else f"wa_range_a2:{rid}"
            buttons.append([InlineKeyboardButton(
                f"{tag} {rng} [{panel_src}]",
                callback_data=cb,
                api_kwargs={"style": "primary"},
            )])
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="select_panel_WA",
                                              api_kwargs={"style": "primary"})])
        await safe_edit(query,
            f"💬 *WhatsApp — {flag} {country_name}*\n\n📡 Range select করুন:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("wa_range_a1:"):
        rng = data.split("wa_range_a1:", 1)[1]
        user_data[user_id].update({
            "range": rng, "panel": "A1", "app": "WHATSAPP",
            "a1_service": "WhatsApp", "a1_zenex_service": "Whatsapp",
        })
        from panels.a1 import do_get_number_a1
        asyncio.create_task(do_get_number_a1(query.message, user_id, bot=context.bot))
        return

    if data.startswith("wa_range_a2:"):
        rid = data.split("wa_range_a2:", 1)[1]
        user_data[user_id].update({"range": rid, "panel": "A2", "app": "WHATSAPP"})
        user_msg[chat_id] = query.message.message_id
        from panels.a2 import do_get_number_a2
        asyncio.create_task(do_get_number_a2(query.message, user_id, bot=context.bot))
        return

    # ── Telegram ──
    if data == "select_panel_TG":
        user_data[user_id].update({"panel": "A2", "app": "TELEGRAM"})
        await safe_edit(query, "⏳ Telegram ranges লোড হচ্ছে...")
        ranges  = a2_get_cached_ranges() or await a2_get_active_ranges(force=True)
        tg_rngs = [r for r in (ranges or []) if r.get("service","").upper() in ("TELEGRAM","TG")]
        if not tg_rngs: tg_rngs = ranges or []
        if not tg_rngs:
            await safe_edit(query,
                "❌ Telegram এ এখন কোনো active range নেই।",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                          api_kwargs={"style": "primary"})
                ]]),
            )
            return
        buttons = []
        seen: set[str] = set()
        for r in tg_rngs[:25]:
            rng = r.get("range","")
            rid = r.get("rid", rng)
            if not rng or rng in seen: continue
            seen.add(rng)
            code = a2_extract_country_code(rng)
            flag = COUNTRY_FLAGS_CODE.get(code, "🌍")
            buttons.append([InlineKeyboardButton(
                f"{flag} {rng}",
                callback_data=f"tg_range:{rid}",
                api_kwargs={"style": "primary"},
            )])
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                              api_kwargs={"style": "primary"})])
        await safe_edit(query, "✈️ *Telegram*\n\n📡 Range select করুন:",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("tg_range:"):
        rid = data.split("tg_range:", 1)[1]
        user_data[user_id].update({"range": rid, "panel": "A2", "app": "TELEGRAM"})
        user_msg[chat_id] = query.message.message_id
        from panels.a2 import do_get_number_a2
        asyncio.create_task(do_get_number_a2(query.message, user_id, bot=context.bot))
        return

    # ══════════════════════════════════════════════════════
    #  S3 POOL — Country / Number assign
    # ══════════════════════════════════════════════════════

    if data.startswith("s3app:"):
        service = data.split("s3app:", 1)[1]  # "fb" or "ig"
        pool    = get_numbers_pool()
        keys    = [k for k in pool if k.endswith(f"_{service}") and pool[k]]
        if not keys:
            await safe_edit(query,
                f"❌ {'Facebook' if service=='fb' else 'Instagram'} S3 তে এখন কোনো number নেই।",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                          api_kwargs={"style": "primary"})
                ]]),
            )
            return
        buttons = []
        for pk in keys[:20]:
            label = get_button_label(pk)
            cnt   = count_numbers(pk)
            buttons.append([InlineKeyboardButton(
                f"{label} ({cnt})",
                callback_data=f"s3pool:{pk}",
                api_kwargs={"style": "danger" if not is_shark_pool(pk) else "primary"},
            )])
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                              api_kwargs={"style": "primary"})])
        svc_label = "Facebook" if service == "fb" else "Instagram"
        await safe_edit(query,
            f"🔴 *{svc_label} S3*\n\n🌍 Country select করুন:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("s3pool:"):
        pool_key = data.split("s3pool:", 1)[1]
        pool     = get_numbers_pool()
        nums     = pool.get(pool_key, [])
        if not nums:
            await safe_edit(query,
                f"❌ `{pool_key}` pool এ এখন কোনো number নেই।",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_app",
                                          api_kwargs={"style": "primary"})
                ]]),
            )
            return

        # Assign numbers (max 2)
        assigned = []
        for num in nums[:2]:
            assigned.append(num)
        pool[pool_key] = [n for n in nums if n not in assigned]

        user_data[user_id]["panel"]   = "S3"
        user_data[user_id]["numbers"] = assigned
        s3_set_session(user_id, assigned, pool_key)

        code, service, _ = parse_pool_key(pool_key)
        flag  = COUNTRY_FLAGS_CODE.get(code, "🌍")
        cname = COUNTRY_NAMES_CODE.get(code, "Unknown")
        svc   = "FACEBOOK" if service == "fb" else "INSTAGRAM"

        card = (
            f"✅ <b>Numbers Assigned!</b>\n\n"
            f"<b>Service:</b> {svc} [S3]\n"
            f"🌍 <b>Country:</b> {flag} {cname}\n"
            f"⏳ <b>Reserved:</b> 20 min\n\n"
            f"📩 OTPs forwarded automatically."
        )
        asyncio.create_task(mark_number_assigned(assigned[0], user_id, pool_key))
        await safe_edit(query, card, parse_mode="HTML",
                        reply_markup=after_number_inline_s3(pool_key, assigned))
        return

    if data.startswith("s3change:"):
        pool_key = data.split("s3change:", 1)[1]
        pool     = get_numbers_pool()
        nums     = pool.get(pool_key, [])
        if not nums:
            await safe_edit(query, "❌ আর কোনো number নেই এই pool এ।")
            return
        new_num = nums.pop(0)
        pool[pool_key] = nums
        session = s3_get_session(user_id) or {}
        current = session.get("numbers", [])
        if current:
            current[0] = new_num
        else:
            current = [new_num]
        s3_set_session(user_id, current, pool_key)
        user_data[user_id]["numbers"] = current
        code, service, _ = parse_pool_key(pool_key)
        flag  = COUNTRY_FLAGS_CODE.get(code, "🌍")
        cname = COUNTRY_NAMES_CODE.get(code, "Unknown")
        svc   = "FACEBOOK" if service == "fb" else "INSTAGRAM"
        card  = (
            f"✅ <b>Numbers Changed!</b>\n\n"
            f"<b>Service:</b> {svc} [S3]\n"
            f"🌍 <b>Country:</b> {flag} {cname}\n"
            f"⏳ <b>Reserved:</b> 20 min\n\n"
            f"📩 OTPs forwarded automatically."
        )
        await safe_edit(query, card, parse_mode="HTML",
                        reply_markup=after_number_inline_s3(pool_key, current))
        return

    if data == "s3changecountry":
        kb = await panel_select_inline()
        await safe_edit(query, SERVICE_SELECT_TEXT, reply_markup=kb)
        return

    # ══════════════════════════════════════════════════════
    #  TXT FILE UPLOAD — pool_service_fb / ig / both
    # ══════════════════════════════════════════════════════

    if data.startswith("pool_service_"):
        if user_id != ADMIN_ID:
            return
        service_key  = data.replace("pool_service_", "")
        new_numbers  = context.user_data.get("pending_numbers", [])
        base_pool_key= context.user_data.get("pending_pool_key", "")
        if not new_numbers or not base_pool_key:
            await safe_edit(query, "❌ Session expired. File আবার পাঠান।")
            return

        svc_map = {
            "fb":   [("fb",  "📘 Facebook")],
            "ig":   [("ig",  "📸 Instagram")],
            "both": [("fb",  "📘 Facebook"), ("ig", "📸 Instagram")],
        }
        services    = svc_map.get(service_key, [("fb", "📘 Facebook")])
        result_text = "✅ *Upload Complete!*\n\n"
        bc_parts    = []

        for suffix, label in services:
            pool_key = f"{base_pool_key}_{suffix}"
            added, skipped = await add_numbers_to_pool(context.bot, pool_key, new_numbers)
            result_text += (
                f"{label} Pool: `{pool_key}`\n"
                f"✅ Added: `{added}` | ⏭ Skipped: `{skipped}`\n"
                f"📱 Total: `{count_numbers(pool_key)}`\n\n"
            )
            if added > 0:
                code  = base_pool_key.split("_")[0]
                cname = COUNTRY_NAMES_CODE.get(code, code)
                flag  = COUNTRY_FLAGS_CODE.get(code, "🌍")
                svc   = "Facebook" if suffix == "fb" else "Instagram"
                bc_parts.append((flag, cname, svc, added))

        context.user_data.pop("pending_numbers", None)
        context.user_data.pop("pending_pool_key", None)
        await safe_edit(query, result_text, parse_mode="Markdown")

        # Broadcast to all users
        if bc_parts:
            all_users = list(set(list(user_data.keys()) + [int(u) for u in s3_get_all_users()]))
            for flag, cname, svc, added in bc_parts:
                bc_msg = (
                    f"🆕 *New Numbers Available!*\n\n"
                    f"{flag} *{cname} {svc}*\n"
                    f"📱 `{added}` numbers added\n\n"
                    f"⚡ Get yours now → /start"
                )
                sent = failed = 0
                for uid in all_users:
                    try:
                        await context.bot.send_message(int(uid), bc_msg, parse_mode="Markdown")
                        sent += 1
                    except Exception:
                        failed += 1
                    await asyncio.sleep(0.05)
                await context.bot.send_message(
                    ADMIN_ID,
                    f"📢 *Broadcast Done!*\n✅ Sent: `{sent}` | ❌ Failed: `{failed}`",
                    parse_mode="Markdown",
                )
        return


# ── Helper: lazy import to avoid circular ──
async def panels_a1_get():
    from panels.a1 import zenex_get_active_ranges
    return await zenex_get_active_ranges(service="Whatsapp")
