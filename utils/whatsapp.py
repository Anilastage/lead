import re
from urllib.parse import quote


def only_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def display_phone(value):
    digits = only_digits(value)
    if len(digits) == 12 and digits.startswith("91"):
        return f"+91 {digits[2:7]} {digits[7:]}"
    if len(digits) > 4:
        return f"+{digits}"
    return digits


def build_wa_link(phone, message):
    digits = only_digits(phone)
    if not digits:
        return ""
    text = quote(str(message or "").strip())
    return f"https://wa.me/{digits}?text={text}"


def build_web_link(phone, message):
    digits = only_digits(phone)
    if not digits:
        return ""
    text = quote(str(message or "").strip())
    return f"https://web.whatsapp.com/send?phone={digits}&text={text}"
