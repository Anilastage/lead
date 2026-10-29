import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
PROFILE_PATH = DATA_DIR / "profile.json"
DB_PATH = DATA_DIR / "outreach.db"

load_dotenv(ROOT / ".env")


def setting(key, default=None):
    value = os.getenv(key)
    if value is not None and str(value).strip() != "":
        return value
    try:
        import streamlit as st

        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


DEFAULT_PROFILE = {
    "company": "TrioCodeLab",
    "owner_name": "",
    "website": "",
    "phone": "",
    "portfolio": "",
    "services": "Website design, website development, e-commerce stores, SEO, landing pages and website maintenance",
    "offer": "Happy to share a free homepage mockup and a no-obligation quote",
    "booking_link": "",
    "city": "Mumbai",
    "tone": "Friendly and professional",
    "language": "English",
}


def load_profile():
    profile = dict(DEFAULT_PROFILE)
    if PROFILE_PATH.exists():
        try:
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                profile.update({k: v for k, v in data.items() if k in DEFAULT_PROFILE})
        except Exception:
            pass
    return profile


def save_profile(profile):
    clean = {k: str(profile.get(k, DEFAULT_PROFILE[k])) for k in DEFAULT_PROFILE}
    PROFILE_PATH.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    return clean
