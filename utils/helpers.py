# utils/helpers.py
"""
Shared helper functions and lookup tables used across all panels.
No panel-specific logic lives here — only pure utility functions.

Active panels: A1, A2, S3, S4 Shark
"""

import re
import time


# ══════════════════════════════════════════════════════════
#                  COUNTRY LOOKUP TABLES
# ══════════════════════════════════════════════════════════

# Dial-code → flag emoji
COUNTRY_FLAGS_CODE: dict[str, str] = {
    "1": "🇺🇸", "7": "🇷🇺", "20": "🇪🇬", "27": "🇿🇦", "30": "🇬🇷", "31": "🇳🇱",
    "32": "🇧🇪", "33": "🇫🇷", "34": "🇪🇸", "36": "🇭🇺", "39": "🇮🇹", "40": "🇷🇴",
    "41": "🇨🇭", "43": "🇦🇹", "44": "🇬🇧", "45": "🇩🇰", "46": "🇸🇪", "47": "🇳🇴",
    "48": "🇵🇱", "49": "🇩🇪", "51": "🇵🇪", "52": "🇲🇽", "53": "🇨🇺", "54": "🇦🇷",
    "55": "🇧🇷", "56": "🇨🇱", "57": "🇨🇴", "58": "🇻🇪", "60": "🇲🇾", "61": "🇦🇺",
    "62": "🇮🇩", "63": "🇵🇭", "64": "🇳🇿", "65": "🇸🇬", "66": "🇹🇭", "81": "🇯🇵",
    "82": "🇰🇷", "84": "🇻🇳", "86": "🇨🇳", "90": "🇹🇷", "91": "🇮🇳", "92": "🇵🇰",
    "93": "🇦🇫", "94": "🇱🇰", "95": "🇲🇲", "98": "🇮🇷", "212": "🇲🇦", "213": "🇩🇿",
    "216": "🇹🇳", "218": "🇱🇾", "220": "🇬🇲", "221": "🇸🇳", "222": "🇲🇷", "223": "🇲🇱",
    "224": "🇬🇳", "225": "🇨🇮", "226": "🇧🇫", "227": "🇳🇪", "228": "🇹🇬", "229": "🇧🇯",
    "230": "🇲🇺", "231": "🇱🇷", "232": "🇸🇱", "233": "🇬🇭", "234": "🇳🇬", "235": "🇹🇩",
    "236": "🇨🇫", "237": "🇨🇲", "238": "🇨🇻", "239": "🇸🇹", "240": "🇬🇶", "241": "🇬🇦",
    "242": "🇨🇬", "243": "🇨🇩", "244": "🇦🇴", "245": "🇬🇼", "248": "🇸🇨", "249": "🇸🇩",
    "250": "🇷🇼", "251": "🇪🇹", "252": "🇸🇴", "253": "🇩🇯", "254": "🇰🇪", "255": "🇹🇿",
    "256": "🇺🇬", "257": "🇧🇮", "258": "🇲🇿", "260": "🇿🇲", "261": "🇲🇬", "263": "🇿🇼",
    "264": "🇳🇦", "265": "🇲🇼", "266": "🇱🇸", "267": "🇧🇼", "268": "🇸🇿", "269": "🇰🇲",
    "291": "🇪🇷", "297": "🇦🇼", "350": "🇬🇮", "351": "🇵🇹", "352": "🇱🇺", "353": "🇮🇪",
    "354": "🇮🇸", "355": "🇦🇱", "356": "🇲🇹", "357": "🇨🇾", "358": "🇫🇮", "359": "🇧🇬",
    "370": "🇱🇹", "371": "🇱🇻", "372": "🇪🇪", "373": "🇲🇩", "374": "🇦🇲", "375": "🇧🇾",
    "376": "🇦🇩", "377": "🇲🇨", "380": "🇺🇦", "381": "🇷🇸", "382": "🇲🇪", "385": "🇭🇷",
    "386": "🇸🇮", "387": "🇧🇦", "389": "🇲🇰", "420": "🇨🇿", "421": "🇸🇰", "501": "🇧🇿",
    "502": "🇬🇹", "503": "🇸🇻", "504": "🇭🇳", "505": "🇳🇮", "506": "🇨🇷", "507": "🇵🇦",
    "509": "🇭🇹", "591": "🇧🇴", "592": "🇬🇾", "593": "🇪🇨", "595": "🇵🇾", "597": "🇸🇷",
    "598": "🇺🇾", "670": "🇹🇱", "673": "🇧🇳", "675": "🇵🇬", "676": "🇹🇴", "677": "🇸🇧",
    "678": "🇻🇺", "679": "🇫🇯", "685": "🇼🇸", "686": "🇰🇮", "688": "🇹🇻", "850": "🇰🇵",
    "852": "🇭🇰", "853": "🇲🇴", "855": "🇰🇭", "856": "🇱🇦", "880": "🇧🇩", "886": "🇹🇼",
    "960": "🇲🇻", "961": "🇱🇧", "962": "🇯🇴", "963": "🇸🇾", "964": "🇮🇶", "965": "🇰🇼",
    "966": "🇸🇦", "967": "🇾🇪", "968": "🇴🇲", "970": "🇵🇸", "971": "🇦🇪", "972": "🇮🇱",
    "973": "🇧🇭", "974": "🇶🇦", "975": "🇧🇹", "976": "🇲🇳", "977": "🇳🇵", "992": "🇹🇯",
    "993": "🇹🇲", "994": "🇦🇿", "995": "🇬🇪", "996": "🇰🇬", "998": "🇺🇿",
}

# Dial-code → country name
COUNTRY_NAMES_CODE: dict[str, str] = {
    "1": "USA", "7": "Russia", "20": "Egypt", "27": "South Africa", "30": "Greece",
    "31": "Netherlands", "32": "Belgium", "33": "France", "34": "Spain", "36": "Hungary",
    "39": "Italy", "40": "Romania", "41": "Switzerland", "43": "Austria", "44": "UK",
    "45": "Denmark", "46": "Sweden", "47": "Norway", "48": "Poland", "49": "Germany",
    "51": "Peru", "52": "Mexico", "53": "Cuba", "54": "Argentina", "55": "Brazil",
    "56": "Chile", "57": "Colombia", "58": "Venezuela", "60": "Malaysia", "61": "Australia",
    "62": "Indonesia", "63": "Philippines", "64": "New Zealand", "65": "Singapore",
    "66": "Thailand", "81": "Japan", "82": "South Korea", "84": "Vietnam", "86": "China",
    "90": "Turkey", "91": "India", "92": "Pakistan", "93": "Afghanistan", "94": "Sri Lanka",
    "95": "Myanmar", "98": "Iran", "212": "Morocco", "213": "Algeria", "216": "Tunisia",
    "218": "Libya", "220": "Gambia", "221": "Senegal", "222": "Mauritania", "223": "Mali",
    "224": "Guinea", "225": "Ivory Coast", "226": "Burkina Faso", "227": "Niger",
    "228": "Togo", "229": "Benin", "230": "Mauritius", "231": "Liberia", "232": "Sierra Leone",
    "233": "Ghana", "234": "Nigeria", "235": "Chad", "236": "CAR", "237": "Cameroon",
    "238": "Cape Verde", "239": "Sao Tome", "240": "Eq. Guinea", "241": "Gabon",
    "242": "Congo", "243": "DR Congo", "244": "Angola", "245": "Guinea-Bissau",
    "248": "Seychelles", "249": "Sudan", "250": "Rwanda", "251": "Ethiopia",
    "252": "Somalia", "253": "Djibouti", "254": "Kenya", "255": "Tanzania",
    "256": "Uganda", "257": "Burundi", "258": "Mozambique", "260": "Zambia",
    "261": "Madagascar", "263": "Zimbabwe", "264": "Namibia", "265": "Malawi",
    "266": "Lesotho", "267": "Botswana", "268": "Eswatini", "269": "Comoros",
    "291": "Eritrea", "351": "Portugal", "352": "Luxembourg", "353": "Ireland",
    "358": "Finland", "359": "Bulgaria", "370": "Lithuania", "371": "Latvia",
    "372": "Estonia", "373": "Moldova", "374": "Armenia", "375": "Belarus",
    "380": "Ukraine", "381": "Serbia", "385": "Croatia", "386": "Slovenia",
    "387": "Bosnia", "389": "N. Macedonia", "420": "Czech Republic", "421": "Slovakia",
    "880": "Bangladesh", "960": "Maldives", "961": "Lebanon", "962": "Jordan",
    "963": "Syria", "964": "Iraq", "965": "Kuwait", "966": "Saudi Arabia",
    "967": "Yemen", "968": "Oman", "970": "Palestine", "971": "UAE", "972": "Israel",
    "973": "Bahrain", "974": "Qatar", "975": "Bhutan", "976": "Mongolia",
    "977": "Nepal", "992": "Tajikistan", "993": "Turkmenistan", "994": "Azerbaijan",
    "995": "Georgia", "996": "Kyrgyzstan", "998": "Uzbekistan",
    # ── Previously missing (present in COUNTRY_FLAGS_CODE but not here) ──
    "297": "Aruba", "350": "Gibraltar", "354": "Iceland", "355": "Albania",
    "356": "Malta", "357": "Cyprus", "376": "Andorra", "377": "Monaco",
    "382": "Montenegro",
    "501": "Belize", "502": "Guatemala", "503": "El Salvador", "504": "Honduras",
    "505": "Nicaragua", "506": "Costa Rica", "507": "Panama", "509": "Haiti",
    "591": "Bolivia", "592": "Guyana", "593": "Ecuador", "595": "Paraguay",
    "597": "Suriname", "598": "Uruguay",
    "670": "East Timor", "673": "Brunei", "675": "Papua New Guinea",
    "676": "Tonga", "677": "Solomon Islands", "678": "Vanuatu", "679": "Fiji",
    "685": "Samoa", "686": "Kiribati", "688": "Tuvalu",
    "850": "North Korea", "852": "Hong Kong", "853": "Macau",
    "855": "Cambodia", "856": "Laos", "886": "Taiwan",
}

# ISO-2 code → flag emoji
COUNTRY_FLAGS_ISO: dict[str, str] = {
    "CM": "🇨🇲", "VN": "🇻🇳", "PK": "🇵🇰", "TZ": "🇹🇿", "TJ": "🇹🇯", "TG": "🇹🇬",
    "NG": "🇳🇬", "GH": "🇬🇭", "KE": "🇰🇪", "BD": "🇧🇩", "IN": "🇮🇳", "PH": "🇵🇭",
    "ID": "🇮🇩", "MM": "🇲🇲", "KH": "🇰🇭", "ET": "🇪🇹", "CD": "🇨🇩", "MZ": "🇲🇿",
    "MG": "🇲🇬", "CI": "🇨🇮", "SN": "🇸🇳", "ML": "🇲🇱", "BF": "🇧🇫", "GN": "🇬🇳",
    "ZM": "🇿🇲", "ZW": "🇿🇼", "RW": "🇷🇼", "UG": "🇺🇬", "AO": "🇦🇴", "SD": "🇸🇩",
    "MR": "🇲🇷", "NE": "🇳🇪", "TD": "🇹🇩", "SO": "🇸🇴", "BI": "🇧🇮", "BJ": "🇧🇯",
    "MW": "🇲🇼", "SL": "🇸🇱", "LR": "🇱🇷", "CF": "🇨🇫", "GQ": "🇬🇶", "GA": "🇬🇦",
    "CG": "🇨🇬", "DJ": "🇩🇯", "ER": "🇪🇷", "GM": "🇬🇲", "GW": "🇬🇼", "CV": "🇨🇻",
    "ST": "🇸🇹", "KM": "🇰🇲", "SC": "🇸🇨", "MU": "🇲🇺", "ZA": "🇿🇦", "NA": "🇳🇦",
    "BW": "🇧🇼", "LS": "🇱🇸", "SZ": "🇸🇿", "EG": "🇪🇬", "LY": "🇱🇾", "TN": "🇹🇳",
    "DZ": "🇩🇿", "MA": "🇲🇦", "MX": "🇲🇽", "BR": "🇧🇷", "CO": "🇨🇴", "PE": "🇵🇪",
    "VE": "🇻🇪", "AR": "🇦🇷", "CL": "🇨🇱", "EC": "🇪🇨", "BO": "🇧🇴", "PY": "🇵🇾",
    "UY": "🇺🇾", "GY": "🇬🇾", "SR": "🇸🇷", "GT": "🇬🇹", "HN": "🇭🇳", "SV": "🇸🇻",
    "NI": "🇳🇮", "CR": "🇨🇷", "PA": "🇵🇦", "CU": "🇨🇺", "DO": "🇩🇴", "HT": "🇭🇹",
    "TH": "🇹🇭", "LA": "🇱🇦", "MY": "🇲🇾", "SG": "🇸🇬", "TL": "🇹🇱", "NP": "🇳🇵",
    "LK": "🇱🇰", "AF": "🇦🇫", "IR": "🇮🇷", "IQ": "🇮🇶", "SY": "🇸🇾", "YE": "🇾🇪",
    "SA": "🇸🇦", "AE": "🇦🇪", "QA": "🇶🇦", "KW": "🇰🇼", "BH": "🇧🇭", "OM": "🇴🇲",
    "JO": "🇯🇴", "LB": "🇱🇧", "PS": "🇵🇸", "AM": "🇦🇲", "AZ": "🇦🇿", "GE": "🇬🇪",
    "KZ": "🇰🇿", "UZ": "🇺🇿", "TM": "🇹🇲", "KG": "🇰🇬", "MN": "🇲🇳", "RU": "🇷🇺",
    "UA": "🇺🇦", "BY": "🇧🇾", "MD": "🇲🇩", "RO": "🇷🇴", "BG": "🇧🇬", "RS": "🇷🇸",
    "HR": "🇭🇷", "BA": "🇧🇦", "MK": "🇲🇰", "AL": "🇦🇱", "ME": "🇲🇪", "SI": "🇸🇮",
    "SK": "🇸🇰", "CZ": "🇨🇿", "PL": "🇵🇱", "HU": "🇭🇺", "AT": "🇦🇹", "CH": "🇨🇭",
    "DE": "🇩🇪", "FR": "🇫🇷", "ES": "🇪🇸", "IT": "🇮🇹", "PT": "🇵🇹", "GB": "🇬🇧",
    "IE": "🇮🇪", "NL": "🇳🇱", "BE": "🇧🇪", "LU": "🇱🇺", "DK": "🇩🇰", "SE": "🇸🇪",
    "NO": "🇳🇴", "FI": "🇫🇮", "IS": "🇮🇸", "US": "🇺🇸", "CA": "🇨🇦", "AU": "🇦🇺",
    "NZ": "🇳🇿", "JP": "🇯🇵", "KR": "🇰🇷", "CN": "🇨🇳", "TW": "🇹🇼", "HK": "🇭🇰",
}

# Country name (lowercase) → ISO-2
COUNTRY_NAME_TO_ISO: dict[str, str] = {
    "cameroon": "CM", "vietnam": "VN", "pakistan": "PK", "tanzania": "TZ",
    "tajikistan": "TJ", "togo": "TG", "nigeria": "NG", "ghana": "GH",
    "kenya": "KE", "bangladesh": "BD", "india": "IN", "philippines": "PH",
    "indonesia": "ID", "myanmar": "MM", "cambodia": "KH", "ethiopia": "ET",
    "congo": "CD", "dr congo": "CD", "mozambique": "MZ", "madagascar": "MG",
    "ivory coast": "CI", "senegal": "SN", "mali": "ML", "burkina faso": "BF",
    "guinea": "GN", "zambia": "ZM", "zimbabwe": "ZW", "rwanda": "RW",
    "uganda": "UG", "angola": "AO", "sudan": "SD", "mauritania": "MR",
    "niger": "NE", "chad": "TD", "somalia": "SO", "burundi": "BI",
    "benin": "BJ", "malawi": "MW", "sierra leone": "SL", "liberia": "LR",
    "car": "CF", "central african republic": "CF", "central africa": "CF",
    "eq. guinea": "GQ", "equatorial guinea": "GQ",
    "sao tome": "ST", "são tomé": "ST", "sao tome and principe": "ST",
    "guinea-bissau": "GW", "guinea bissau": "GW",
    "n. macedonia": "MK", "north macedonia": "MK",
    "democratic republic of congo": "CD", "drc": "CD",
    "cote d'ivoire": "CI", "côte d'ivoire": "CI",
    "gabon": "GA", "djibouti": "DJ", "eritrea": "ER",
    "gambia": "GM", "cape verde": "CV", "comoros": "KM", "seychelles": "SC",
    "mauritius": "MU", "south africa": "ZA", "namibia": "NA", "botswana": "BW",
    "lesotho": "LS", "eswatini": "SZ", "egypt": "EG", "libya": "LY",
    "tunisia": "TN", "algeria": "DZ", "morocco": "MA", "mexico": "MX",
    "brazil": "BR", "colombia": "CO", "peru": "PE", "venezuela": "VE",
    "argentina": "AR", "chile": "CL", "ecuador": "EC", "bolivia": "BO",
    "paraguay": "PY", "uruguay": "UY", "guyana": "GY", "suriname": "SR",
    "guatemala": "GT", "honduras": "HN", "el salvador": "SV", "nicaragua": "NI",
    "costa rica": "CR", "panama": "PA", "cuba": "CU", "haiti": "HT",
    "usa": "US", "united states": "US", "canada": "CA", "thailand": "TH",
    "laos": "LA", "malaysia": "MY", "singapore": "SG", "nepal": "NP",
    "sri lanka": "LK", "afghanistan": "AF", "iran": "IR", "iraq": "IQ",
    "syria": "SY", "yemen": "YE", "saudi arabia": "SA", "uae": "AE",
    "qatar": "QA", "kuwait": "KW", "bahrain": "BH", "oman": "OM",
    "jordan": "JO", "lebanon": "LB", "palestine": "PS", "armenia": "AM",
    "azerbaijan": "AZ", "georgia": "GE", "kazakhstan": "KZ", "uzbekistan": "UZ",
    "turkmenistan": "TM", "kyrgyzstan": "KG", "mongolia": "MN", "russia": "RU",
    "ukraine": "UA", "belarus": "BY", "moldova": "MD", "romania": "RO",
    "bulgaria": "BG", "serbia": "RS", "croatia": "HR", "bosnia": "BA",
    "albania": "AL", "montenegro": "ME", "slovenia": "SI", "slovakia": "SK",
    "czech republic": "CZ", "poland": "PL", "hungary": "HU", "austria": "AT",
    "switzerland": "CH", "germany": "DE", "france": "FR", "spain": "ES",
    "italy": "IT", "portugal": "PT", "uk": "GB", "ireland": "IE",
    "netherlands": "NL", "belgium": "BE", "luxembourg": "LU", "denmark": "DK",
    "sweden": "SE", "norway": "NO", "finland": "FI", "iceland": "IS",
    "australia": "AU", "new zealand": "NZ", "japan": "JP", "south korea": "KR",
    "china": "CN", "taiwan": "TW", "hong kong": "HK",
}

# ISO-2 code → country name (reverse of COUNTRY_NAME_TO_ISO)
COUNTRY_NAMES_ISO: dict[str, str] = {
    iso: name.title() for name, iso in COUNTRY_NAME_TO_ISO.items()
}

# App name → emoji
APP_EMOJIS: dict[str, str] = {
    "FACEBOOK":   "📘",
    "INSTAGRAM":  "📸",
    "TIKTOK":     "🎵",
    "SNAPCHAT":   "👻",
    "TWITTER":    "🐦",
    "GOOGLE":     "🔍",
    "WHATSAPP":   "💬",
    "TELEGRAM":   "✈️",
    "CHATGPT":    "🤖",
    "SHEIN":      "👗",
    "VERIFY":     "🔐",
    "WORLDFIRST": "🌏",
}

# OTP wait progress texts (cycled while polling)
LOADING_TEXTS: list[str] = [
    "⏳ Waiting for OTP...",
    "📡 Checking server...",
    "🔄 Scanning inbox...",
    "⌛ Please wait...",
    "🔍 Looking for OTP...",
]


# ══════════════════════════════════════════════════════════
#                  PURE UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════

def escape_mdv2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', str(text))


def get_flag_by_iso(code: str) -> str:
    """
    Return a flag emoji for a country name or ISO-2 code.
    Falls back to 🌍 when nothing matches.
    """
    if not code:
        return "🌍"
    name_key = code.lower().strip()
    if name_key in COUNTRY_NAME_TO_ISO:
        return COUNTRY_FLAGS_ISO.get(COUNTRY_NAME_TO_ISO[name_key], "🌍")
    short = code.upper().strip()[:2]
    return COUNTRY_FLAGS_ISO.get(short, "🌍")


def extract_otp(message: str | None) -> str | None:
    """
    Extract a numeric OTP from an SMS body.
    Tries longest matches first (8-digit → 6 → 5 → 4+).
    Returns None when no credible code is found.
    """
    if not message:
        return None
    patterns = [
        r'\b(\d{8}|\d{6}|\d{5})\b',
        r'\b(\d{3} \d{3})\b',
        r'\b(\d{2} \d{3})\b',
        r'\b(\d{4,8})\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            otp = match.group(1).replace(" ", "")
            if not re.fullmatch(r'0+', otp):
                return otp
    return None


def extract_country_code_from_number(number: str) -> str:
    """
    Given a raw phone number string (no +), return the dial-code prefix
    that matches a known country, trying 3-, 2-, then 1-digit prefixes.
    Returns "Unknown" if nothing matches.
    """
    for length in (3, 2, 1):
        code = number[:length]
        if code in COUNTRY_NAMES_CODE:
            return code
    return "Unknown"


def hide_number(number: str) -> str:
    """Partially mask a phone number for privacy: 1234xxxx567."""
    if len(number) <= 5:
        return number
    return number[:4] + "xxxx" + number[-3:]


def detect_app_from_message(message: str | None, default_app: str = "FACEBOOK") -> str:
    """
    Heuristically detect which app an OTP SMS belongs to.
    Returns an uppercase app name like "INSTAGRAM" or the default.
    """
    if not message:
        return default_app
    msg_lower = message.lower()
    for app in ("instagram", "facebook", "tiktok", "snapchat", "twitter",
                "google", "whatsapp", "telegram"):
        if app in msg_lower:
            return app.upper()
    ig_patterns = ("insta", " ig ", "ig code", "ig-", "siy",
                   "don't share it. siy", "your instagram",
                   "instagram code", "ig account")
    for pat in ig_patterns:
        if pat in msg_lower:
            return "INSTAGRAM"
    return default_app


# ══════════════════════════════════════════════════════════
#                      RATE LIMITING
# ══════════════════════════════════════════════════════════

_user_last_action: dict[int, float] = {}
_RATE_LIMIT_SECONDS = 2


def is_rate_limited(user_id: int) -> bool:
    """
    Return True (and do NOT update the timestamp) if the user triggered
    an action within the last _RATE_LIMIT_SECONDS seconds.
    Return False and record the current time otherwise.
    """
    now = time.time()
    if now - _user_last_action.get(user_id, 0) < _RATE_LIMIT_SECONDS:
        return True
    _user_last_action[user_id] = now
    return False
