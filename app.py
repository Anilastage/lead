import hashlib
import hmac
import json
import random
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from templates.default import default_email_template, default_whatsapp_template
from utils import ai, storage
from utils import leads as lead_utils
from utils.config import load_profile, save_profile, setting
from utils.emailer import EmailSender, decorate, text_to_html
from utils.whatsapp import build_wa_link, display_phone, only_digits

st.set_page_config(page_title="TrioCodeLab Outreach", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1450px;}
    div[data-testid="stMetricValue"] {font-size: 1.45rem;}
    .stButton button, .stDownloadButton button, .stLinkButton a {border-radius: 8px;}
    div[data-testid="stExpander"] details {border-radius: 10px;}
    </style>
    """,
    unsafe_allow_html=True,
)

storage.init_db()

if "profile" not in st.session_state:
    st.session_state.profile = load_profile()

if "leads" not in st.session_state:
    st.session_state.leads = None
    st.session_state.messages = {}
    saved = storage.load_state("leads")
    if saved:
        try:
            loaded = lead_utils.ensure_schema(pd.DataFrame(saved))
            if not loaded.empty:
                st.session_state.leads = loaded
                st.session_state.messages = storage.get_messages(loaded["lead_id"].tolist())
                st.session_state.loaded_from_db = True
        except Exception:
            st.session_state.leads = None

if "messages" not in st.session_state:
    st.session_state.messages = {}

profile = st.session_state.profile

widget_defaults = {
    "default_country": setting("DEFAULT_COUNTRY_CODE", "91"),
    "gemini_model": setting("GEMINI_MODEL", "gemini-2.5-flash"),
    "smtp_host": setting("SMTP_SERVER", "smtp.gmail.com"),
    "smtp_user": setting("EMAIL_USER", ""),
    "smtp_from_name": setting("FROM_NAME", "") or profile.get("company", ""),
    "smtp_from_email": setting("FROM_EMAIL", "") or setting("EMAIL_USER", ""),
    "smtp_reply_to": setting("REPLY_TO", "") or setting("EMAIL_USER", ""),
    "smtp_custom_port": int(setting("SMTP_PORT", "587") or 587),
    "email_template": default_email_template,
    "wa_template": default_whatsapp_template,
    "editor_version": 0,
}
smtp_saved_port = int(setting("SMTP_PORT", "587") or 587)
if smtp_saved_port == 465:
    widget_defaults["smtp_security"] = "SSL (465)"
elif smtp_saved_port == 587:
    widget_defaults["smtp_security"] = "STARTTLS (587)"
else:
    widget_defaults["smtp_security"] = "Custom port"
for key, value in widget_defaults.items():
    st.session_state.setdefault(key, value)

profile_defaults = {
    "profile_company": profile.get("company", ""),
    "profile_owner_name": profile.get("owner_name", ""),
    "profile_website": profile.get("website", ""),
    "profile_phone": profile.get("phone", ""),
    "profile_portfolio": profile.get("portfolio", ""),
    "profile_booking_link": profile.get("booking_link", ""),
    "profile_city": profile.get("city", ""),
    "profile_tone": profile.get("tone", "Friendly and professional"),
    "profile_language": profile.get("language", "English"),
    "profile_services": profile.get("services", ""),
    "profile_offer": profile.get("offer", ""),
    "uploader_version": 0,
}
for key, value in profile_defaults.items():
    st.session_state.setdefault(key, value)


def authenticate():
    password = setting("APP_PASSWORD")
    if not password or st.session_state.get("authed"):
        return True
    st.title("TrioCodeLab Outreach")
    st.caption("Private outreach console. Enter the app password to continue.")
    with st.form("login_form"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        if hmac.compare_digest(str(entered), str(password)):
            st.session_state.authed = True
            st.rerun()
        else:
            st.session_state.login_attempts = st.session_state.get("login_attempts", 0) + 1
            st.error("Wrong password.")
    if st.session_state.get("login_attempts", 0) >= 5:
        st.warning("Too many attempts. Restart the app and try again.")
    return False


if not authenticate():
    st.stop()


def set_flash(message, kind="success"):
    st.session_state["_flash"] = (kind, message)


def show_flash():
    flash = st.session_state.pop("_flash", None)
    if not flash:
        return
    kind, message = flash
    getattr(st, kind, st.info)(message)


def save_leads_state():
    df = st.session_state.get("leads")
    if df is None:
        storage.save_state("leads", [])
        return
    records = json.loads(df.to_json(orient="records"))
    storage.save_state("leads", records)


def get_leads():
    df = st.session_state.get("leads")
    if isinstance(df, pd.DataFrame) and not df.empty:
        return df
    return None


def contact_counts(df):
    if df is None or df.empty:
        return 0, 0
    emails = int((df["email"].str.len() > 0).sum())
    phones = int((df["phone"].str.len() > 0).sum())
    return emails, phones


def suppressed_for_row(row, suppressed):
    email = str(row.get("email", "")).strip().lower()
    phone = only_digits(row.get("phone", ""))
    return bool((email and email in suppressed) or (phone and phone in suppressed))


def missing_message_rows(df):
    messages = st.session_state.messages
    return df[~df["lead_id"].astype(str).isin(messages.keys())]


def save_message(message):
    st.session_state.messages[str(message["lead_id"])] = message
    storage.save_message(message)


def make_messages_from_templates(df):
    created = 0
    for _, row in df.iterrows():
        lead_id = str(row["lead_id"])
        message = ai.fallback_outreach(
            row.to_dict(),
            st.session_state.profile,
            st.session_state.email_template,
            st.session_state.wa_template,
        )
        message["lead_id"] = lead_id
        save_message(message)
        created += 1
    return created


def make_sender():
    security = st.session_state.get("smtp_security", "STARTTLS (587)")
    if security.startswith("SSL"):
        port = 465
    elif security.startswith("STARTTLS"):
        port = 587
    else:
        port = int(st.session_state.get("smtp_custom_port", 587) or 587)
    user = st.session_state.get("smtp_user", "").strip()
    password = st.session_state.get("smtp_password", "").strip() or str(setting("EMAIL_PASS", "") or "")
    return EmailSender(
        host=st.session_state.get("smtp_host", ""),
        port=port,
        user=user,
        password=password,
        from_name=st.session_state.get("smtp_from_name", "") or st.session_state.profile.get("company", ""),
        from_email=st.session_state.get("smtp_from_email", "") or user,
        reply_to=st.session_state.get("smtp_reply_to", "") or user,
        use_ssl=security.startswith("SSL"),
    )


def final_email_body(message):
    return decorate(message.get("email_body", ""))


def sidebar():
    with st.sidebar:
        st.markdown("### TrioCodeLab Outreach")
        st.caption("Websites for growing businesses")
        page = st.radio(
            "Navigate",
            ["Setup", "Leads", "AI Personalize", "Email Outreach", "WhatsApp Outreach", "History"],
            label_visibility="collapsed",
            key="nav",
        )
        st.divider()
        df = get_leads()
        if df is not None:
            st.caption(
                f"Leads: {len(df)} | Emails: {int((df['email'] != '').sum())} | Phones: {int((df['phone'] != '').sum())}"
            )
            st.caption(f"Selected: {int(df['selected'].sum())} | Messages: {len(st.session_state.messages)}")
        else:
            st.caption("No leads loaded")
        st.caption(f"Contacted: {storage.count_sends()}")
        st.divider()
        st.caption(f"Gemini key: {'saved' if setting('GEMINI_API_KEY') else 'not set'}")
        st.caption(f"Email login: {'saved' if setting('EMAIL_USER') else 'not set'}")
        st.caption(f"App login: {'set' if setting('APP_PASSWORD') else 'NOT SET'}")
        if setting("APP_PASSWORD") and st.session_state.get("authed"):
            if st.button("Log out", width="stretch"):
                st.session_state.authed = False
                st.rerun()
        st.caption("Use small daily batches. Quality beats volume.")
        return page


def page_setup():
    st.header("Setup")
    st.caption("Company details, credentials and deployment checklist.")
    show_flash()
    df = get_leads()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Leads loaded", len(df) if df is not None else 0)
    c2.metric("With email", int((df["email"] != "").sum()) if df is not None else 0)
    c3.metric("With phone", int((df["phone"] != "").sum()) if df is not None else 0)
    c4.metric("Total sent", storage.count_sends())

    if df is None:
        st.info("Start on the Leads page: import a CSV or Excel file. It can contain emails, phone numbers, or both.")
    elif not profile.get("owner_name") or not profile.get("website"):
        st.warning("Add your name and website below before sending. They are used in every message.")

    st.subheader("Company profile")
    st.caption("Used by the AI and by template placeholders.")
    with st.form("profile_form"):
        a, b = st.columns(2)
        company = a.text_input("Company name", key="profile_company")
        owner_name = b.text_input("Your name", key="profile_owner_name")
        website = a.text_input("Website", key="profile_website", placeholder="https://your-site.com")
        phone = b.text_input("Phone / WhatsApp", key="profile_phone")
        portfolio = a.text_input("Portfolio or samples link", key="profile_portfolio")
        booking_link = b.text_input("Booking / calendar link", key="profile_booking_link")
        city = a.text_input("Your city", key="profile_city")
        tone = b.selectbox(
            "Default AI tone",
            ["Friendly and professional", "Consultative", "Direct and concise", "Warm and casual", "Formal"],
            key="profile_tone",
        )
        language = st.selectbox("Default AI language", ["English", "Hindi", "Hinglish"], key="profile_language")
        services = st.text_area("What you offer", key="profile_services", height=90)
        offer = st.text_area("Your offer / call to action", key="profile_offer", height=80)
        saved = st.form_submit_button("Save profile", type="primary")
    if saved:
        new_profile = {
            "company": company.strip(),
            "owner_name": owner_name.strip(),
            "website": website.strip(),
            "phone": phone.strip(),
            "portfolio": portfolio.strip(),
            "booking_link": booking_link.strip(),
            "city": city.strip(),
            "tone": tone,
            "language": language,
            "services": services.strip(),
            "offer": offer.strip(),
        }
        st.session_state.profile = save_profile(new_profile)
        set_flash("Company profile saved.")
        st.rerun()

    st.subheader("Credentials")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Gemini API key", "Saved" if setting("GEMINI_API_KEY") else "Missing")
    c2.metric("Email account", "Saved" if setting("EMAIL_USER") else "Missing")
    c3.metric("Gmail app password", "Saved" if setting("EMAIL_PASS") else "Missing")
    c4.metric("App login password", "Saved" if setting("APP_PASSWORD") else "Missing")
    st.caption(
        "Put credentials in a local .env file or in Streamlit secrets. You can also enter the Gemini key on the "
        "AI Personalize page and the email login on the Email Outreach page for the current session."
    )

    with st.expander("Deployment checklist (Streamlit Community Cloud)"):
        st.markdown(
            """
1. Push this folder to a private GitHub repository.
2. Create an app on share.streamlit.io with `app.py` as the entry point.
3. Add these secrets in the app settings:

```toml
APP_PASSWORD = "choose-a-strong-password"
EMAIL_USER = "you@gmail.com"
EMAIL_PASS = "your-16-character-gmail-app-password"
FROM_NAME = "TrioCodeLab"
FROM_EMAIL = "you@gmail.com"
GEMINI_API_KEY = "your-gemini-key"
GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_COUNTRY_CODE = "91"
```

4. Turn on Gmail 2-step verification, then create an App Password (Google Account > Security > App passwords).
5. Keep the app private. Lead data and generated messages stay in the app's local SQLite database, which can reset when the app is rebuilt. Download your CSV exports regularly.
"""
        )

    with st.expander("Reset imported leads"):
        st.caption("History and suppression list are kept. This clears the current lead list and generated messages.")
        confirm = st.checkbox("I understand this clears the imported leads", key="confirm_reset_leads")
        if st.button("Clear leads and messages", disabled=not confirm):
            if df is not None and "lead_id" in df.columns:
                for lead_id in df["lead_id"].astype(str).tolist():
                    storage.delete_message(lead_id)
            st.session_state.leads = None
            st.session_state.messages = {}
            st.session_state.raw_df = None
            st.session_state.handled_upload_sig = None
            st.session_state.uploader_version += 1
            st.session_state.pop("loaded_from_db", None)
            save_leads_state()
            set_flash("Lead list cleared.", "info")
            st.rerun()


def set_raw_data(raw, source_label=""):
    raw = raw.dropna(how="all").reset_index(drop=True)
    if raw.empty:
        raise ValueError("No rows found in that file.")
    st.session_state.raw_df = raw
    st.session_state.raw_source = source_label
    st.session_state.mapping = lead_utils.detect_mapping(raw)
    for key in lead_utils.ALIASES:
        st.session_state.pop(f"map_{key}", None)
    st.session_state.leads = None
    st.session_state.messages = {}


def page_leads():
    st.header("Leads")
    st.caption("Import any list. Emails and phone numbers are both optional, so the app adapts to each file.")
    show_flash()

    with st.expander("Import leads", expanded=get_leads() is None):
        source = st.radio("Source", ["Upload file", "Paste text", "Google Sheet link"], horizontal=True, key="lead_source")
        if source == "Upload file":
            uploaded = st.file_uploader(
                "CSV, XLSX, XLS, DOCX, TSV or TXT",
                type=["csv", "xlsx", "xls", "docx", "tsv", "txt"],
                key=f"uploader_{st.session_state.uploader_version}",
            )
            if uploaded is not None:
                signature = f"{uploaded.name}:{uploaded.size}"
                if st.session_state.get("handled_upload_sig") != signature:
                    try:
                        set_raw_data(lead_utils.load_data(uploaded), uploaded.name)
                        st.session_state.handled_upload_sig = signature
                    except Exception as exc:
                        st.error(f"Could not read that file: {exc}")
            if st.session_state.get("raw_df") is not None:
                st.caption(f"Loaded source: {st.session_state.get('raw_source', 'uploaded file')}")
        elif source == "Paste text":
            with st.form("paste_form"):
                pasted = st.text_area("Paste CSV / tab-separated rows", height=150, placeholder="Name, Email, Phone\nAcme Ltd, hello@acme.com, 9876543210")
                submitted = st.form_submit_button("Load pasted data")
            if submitted and pasted.strip():
                try:
                    set_raw_data(lead_utils.load_data(pasted), "pasted text")
                except Exception as exc:
                    st.error(f"Could not read the pasted data: {exc}")
        else:
            with st.form("link_form"):
                url = st.text_input("Public Google Sheet or CSV URL", placeholder="https://docs.google.com/spreadsheets/d/.../edit")
                submitted = st.form_submit_button("Load link")
            if submitted and url.strip():
                try:
                    set_raw_data(lead_utils.load_data(url.strip()), url.strip())
                except Exception as exc:
                    st.error(
                        "Could not load that link. Share the sheet as 'Anyone with the link can view' "
                        f"and try the published CSV link. Details: {exc}"
                    )

    raw = st.session_state.get("raw_df")
    if raw is not None:
        st.subheader("Map columns")
        st.caption(f"{len(raw)} rows and {len(raw.columns)} columns found. Adjust the mapping if needed.")
        with st.expander("Preview imported rows", expanded=False):
            st.dataframe(raw.head(10), width="stretch", hide_index=True)

        mapping = st.session_state.get("mapping", {})
        labels = {
            "name": "Business / name",
            "first_name": "First name",
            "email": "Email",
            "phone": "Phone / WhatsApp",
            "business_type": "Business type",
            "website": "Website",
            "city": "City / area",
            "rating": "Rating",
            "reviews": "Reviews",
            "notes": "Notes",
        }
        options = ["-"] + [str(column) for column in raw.columns]
        columns = st.columns(2)
        new_mapping = {}
        for index, (key, label) in enumerate(labels.items()):
            current = mapping.get(key)
            selected_index = options.index(str(current)) if current is not None and str(current) in options else 0
            column = columns[index % 2]
            chosen = column.selectbox(label, options, index=selected_index, key=f"map_{key}")
            new_mapping[key] = None if chosen == "-" else chosen
        st.session_state.mapping = new_mapping

        a, b = st.columns([1, 3])
        country = a.text_input("Default country code", key="default_country", help="Used for numbers without a country code, e.g. 91 for India.")
        if not new_mapping.get("email") and not new_mapping.get("phone"):
            st.warning("No Email or Phone column is mapped. Select the correct columns above before building.")
        if b.button("Build lead list", type="primary"):
            try:
                result, stats = lead_utils.process_leads(raw, new_mapping, country)
            except Exception as exc:
                st.error(f"Could not build the list: {exc}")
            else:
                emails, phones = contact_counts(result)
                if result.empty:
                    st.error("No usable rows found. Map at least an email or phone column.")
                elif emails == 0 and phones == 0:
                    st.error(
                        "The list was built, but no valid emails or phone numbers were found. "
                        "Change the Email or Phone / WhatsApp mapping above and click Build lead list again. "
                        "Your saved list has not been changed."
                    )
                    if new_mapping.get("phone") and new_mapping["phone"] in raw.columns:
                        column = new_mapping["phone"]
                        samples = [str(value) for value in raw[column].dropna().head(5).tolist()]
                        with st.expander("Contact detection details", expanded=True):
                            st.write(f"Phone column: {column}")
                            st.write("Sample raw values:", samples)
                    elif new_mapping.get("email") and new_mapping["email"] in raw.columns:
                        column = new_mapping["email"]
                        samples = [str(value) for value in raw[column].dropna().head(5).tolist()]
                        with st.expander("Contact detection details", expanded=True):
                            st.write(f"Email column: {column}")
                            st.write("Sample raw values:", samples)
                else:
                    st.session_state.leads = result
                    st.session_state.messages = storage.get_messages(result["lead_id"].tolist())
                    st.session_state.import_stats = stats
                    save_leads_state()
                    set_flash(
                        f"Built {stats['kept']} leads ({emails} emails, {phones} phones). "
                        f"Dropped {stats['dropped']} empty rows and {stats['duplicates']} duplicates."
                    )
                    st.rerun()

    df = get_leads()
    if df is None:
        st.info("Required: a name, an email, or a phone number. Everything else is optional.")
        return

    if st.session_state.get("import_stats"):
        stats = st.session_state.import_stats
        emails, phones = contact_counts(df)
        st.caption(
            f"Last import: {stats['rows_read']} rows read, {stats['kept']} kept, "
            f"{stats['duplicates']} duplicates removed, {stats['dropped']} empty rows. "
            f"Contactable: {emails} emails, {phones} phones."
        )
    if st.session_state.get("loaded_from_db"):
        st.info("Loaded the last saved lead list from the app database.")

    st.subheader("Your list")
    with st.expander("Filters", expanded=False):
        a, b, c = st.columns(3)
        search = a.text_input("Search", placeholder="Name, email, phone, type", key="lead_search")
        types = sorted([value for value in df["business_type"].unique() if value])
        selected_types = b.multiselect("Business type", types, key="lead_types")
        min_score = c.slider("Minimum score", 0, 100, 0, key="lead_min_score")
        a, b, c = st.columns(3)
        contact_filter = a.radio(
            "Contact details",
            ["Any", "Has email", "Has phone", "Both", "Missing contact"],
            horizontal=False,
            key="lead_contact_filter",
        )
        hide_contacted = b.checkbox("Hide already contacted", key="lead_hide_contacted")
        hide_suppressed = c.checkbox("Hide suppressed", value=True, key="lead_hide_suppressed")

    contacted_ids = storage.sent_lead_ids()
    suppressed = storage.suppressed_values()

    filtered = df.copy()
    filtered["contacted"] = filtered["lead_id"].astype(str).isin(contacted_ids)
    filtered["suppressed"] = filtered.apply(lambda row: suppressed_for_row(row, suppressed), axis=1).astype(bool)

    if search:
        needle = search.lower()
        mask = (
            filtered["name"].str.lower().str.contains(needle, na=False, regex=False)
            | filtered["email"].str.lower().str.contains(needle, na=False, regex=False)
            | filtered["phone"].str.lower().str.contains(needle, na=False, regex=False)
            | filtered["business_type"].str.lower().str.contains(needle, na=False, regex=False)
            | filtered["city"].str.lower().str.contains(needle, na=False, regex=False)
        )
        filtered = filtered[mask]
    if selected_types:
        filtered = filtered[filtered["business_type"].isin(selected_types)]
    filtered = filtered[filtered["score"] >= min_score]
    has_email = filtered["email"].str.len() > 0
    has_phone = filtered["phone"].str.len() > 0
    if contact_filter == "Has email":
        filtered = filtered[has_email]
    elif contact_filter == "Has phone":
        filtered = filtered[has_phone]
    elif contact_filter == "Both":
        filtered = filtered[has_email & has_phone]
    elif contact_filter == "Missing contact":
        filtered = filtered[~has_email & ~has_phone]
    if hide_contacted:
        filtered = filtered[~filtered["contacted"]]
    if hide_suppressed:
        filtered = filtered[~filtered["suppressed"]]

    st.caption(
        f"Showing {len(filtered)} of {len(df)} leads. Selected: {int(df['selected'].sum())}. "
        "Tick rows to choose who to contact, then apply changes."
    )

    if filtered.empty:
        st.info("No leads match these filters.")
    else:
        display_columns = [
            "selected", "name", "business_type", "email", "phone", "score", "priority", "channel",
            "city", "rating", "reviews", "website", "contacted", "suppressed", "reason", "notes",
        ]
        for column in display_columns:
            if column not in filtered.columns:
                filtered[column] = ""
        view = filtered[display_columns].copy()
        filter_sig = hashlib.md5(
            f"{search}|{selected_types}|{min_score}|{contact_filter}|{hide_contacted}|{hide_suppressed}".encode()
        ).hexdigest()[:8]
        editor_key = f"leads_editor_{st.session_state.editor_version}_{filter_sig}"
        edited = st.data_editor(
            view,
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            disabled=[column for column in display_columns if column not in (
                "selected", "name", "email", "phone", "business_type", "website", "notes"
            )],
            column_config={
                "selected": st.column_config.CheckboxColumn("Select", width="small"),
                "score": st.column_config.NumberColumn("Score", width="small"),
                "rating": st.column_config.NumberColumn("Rating", width="small"),
                "reviews": st.column_config.NumberColumn("Reviews", width="small"),
                "contacted": st.column_config.CheckboxColumn("Contacted", width="small"),
                "suppressed": st.column_config.CheckboxColumn("DNC", width="small"),
                "reason": st.column_config.TextColumn("Why this lead", width="large"),
                "notes": st.column_config.TextColumn("Notes"),
            },
            key=editor_key,
        )

        a, b, c, d = st.columns(4)
        if a.button("Apply edits and selection", type="primary"):
            full = st.session_state.leads.copy()
            for column in ("selected", "name", "email", "phone", "business_type", "website", "notes"):
                full.loc[edited.index, column] = edited[column]
            for index in edited.index:
                full.at[index, "email"] = lead_utils.normalize_email(full.at[index, "email"])
                full.at[index, "phone"] = lead_utils.normalize_phone(
                    full.at[index, "phone"], st.session_state.get("default_country", "91")
                )
                full.at[index, "name"] = lead_utils.prettify_name(full.at[index, "name"])
                if not str(full.at[index, "first_name"]).strip() and str(full.at[index, "name"]).strip():
                    full.at[index, "first_name"] = str(full.at[index, "name"]).split()[0]
            full = lead_utils.recalculate_scores(full)
            st.session_state.leads = full
            st.session_state.editor_version += 1
            save_leads_state()
            set_flash("Lead list updated.")
            st.rerun()
        if b.button("Select all shown"):
            full = st.session_state.leads.copy()
            full.loc[filtered.index, "selected"] = True
            st.session_state.leads = full
            st.session_state.editor_version += 1
            save_leads_state()
            st.rerun()
        if c.button("Deselect all shown"):
            full = st.session_state.leads.copy()
            full.loc[filtered.index, "selected"] = False
            st.session_state.leads = full
            st.session_state.editor_version += 1
            save_leads_state()
            st.rerun()
        d.download_button(
            "Download list (CSV)",
            data=view.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"leads_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )

    if st.session_state.get("import_stats"):
        st.caption(
            "Tip: use the Filters and score to start with the top 10-20 leads. Sending small, relevant batches protects "
            "your email reputation and WhatsApp number."
        )


def page_ai():
    st.header("AI Personalize")
    st.caption("Write a unique email and WhatsApp message for each lead, plus a short reason to prioritize them.")
    show_flash()
    df = get_leads()
    if df is None:
        st.warning("Import leads first on the Leads page.")
        return

    a, b = st.columns([2, 1])
    a.caption(
        f"Writing as {profile.get('owner_name') or 'your team'} at {profile.get('company') or 'your company'} | "
        f"tone: {profile.get('tone')} | language: {profile.get('language')}"
    )
    b.caption("Edit these in Setup.")

    suppressed = storage.suppressed_values()
    working = df.copy()
    working["suppressed"] = working.apply(lambda row: suppressed_for_row(row, suppressed), axis=1).astype(bool)
    working = working[~working["suppressed"]]
    working = working[(working["email"].str.len() > 0) | (working["phone"].str.len() > 0)]
    if working.empty:
        st.info("Every lead is on the do-not-contact list or has no usable contact details.")
        return
    selected = working[working["selected"]]

    if selected.empty:
        st.info("No leads are selected. Tick leads on the Leads page. You can also generate for the highest-scoring leads below.")
        top_n = st.slider("Use top leads by score", 1, min(100, len(working)), min(25, len(working)))
        scope = working.sort_values(["score", "reviews"], ascending=[False, False]).head(top_n)
    else:
        limit = st.slider("Max leads this run", 1, min(100, len(selected)), min(25, len(selected)))
        scope = selected.sort_values(["score", "reviews"], ascending=[False, False]).head(limit)
        st.caption(f"{len(selected)} selected leads. This run will use {len(scope)}.")

    with st.expander("AI settings", expanded=not bool(setting("GEMINI_API_KEY"))):
        a, b = st.columns([2, 1])
        saved_key = bool(setting("GEMINI_API_KEY"))
        a.text_input(
            "Gemini API key",
            type="password",
            key="gemini_key",
            placeholder="Saved key is in use. Type here to override." if saved_key else "Paste your Gemini API key",
        )
        b.text_input("Model", key="gemini_model")
        st.caption("Free key: aistudio.google.com. If the key is already in .env or secrets, leave this blank.")
        if st.button("Test AI connection"):
            key = st.session_state.get("gemini_key", "").strip() or setting("GEMINI_API_KEY", "")
            try:
                client = ai.GeminiClient(key, st.session_state.get("gemini_model", "gemini-2.5-flash"))
                ok, message = client.test_connection()
                st.success(message) if ok else st.error(message)
            except Exception as exc:
                st.error(str(exc))

    with st.expander("Email and WhatsApp templates", expanded=False):
        st.caption(
            "Placeholders: {name} {company_name} {first_name} {business_type} {business_phrase} {location_phrase} "
            "{city} {rating} {reviews} {your_name} {your_company} {company_phrase} {website} {your_website} "
            "{services} {offer} {portfolio} {booking_link} {phone}"
        )
        st.text_area("Email template", key="email_template", height=240)
        st.text_area("WhatsApp template", key="wa_template", height=120)

    overwrite = st.checkbox("Overwrite messages that already exist", value=False, key="ai_overwrite")
    fallback = st.checkbox("Use the template if the AI call fails", value=True, key="ai_fallback")

    preview = scope[["name", "business_type", "city", "score", "priority", "channel"]].copy()
    st.dataframe(preview, width="stretch", hide_index=True)

    a, b = st.columns([1, 1])
    ai_clicked = a.button("Generate with AI", type="primary", width="stretch")
    template_clicked = b.button("Generate from templates", width="stretch", help="No API key needed. Fast and consistent.")

    todo = scope if overwrite else missing_message_rows(scope)
    if (ai_clicked or template_clicked) and todo.empty:
        st.info("Every lead in this scope already has a message. Tick overwrite to regenerate them.")

    if template_clicked and not todo.empty:
        created = make_messages_from_templates(todo)
        set_flash(f"Created {created} messages from templates.")
        st.rerun()

    if ai_clicked and not todo.empty:
        key = st.session_state.get("gemini_key", "").strip() or setting("GEMINI_API_KEY", "")
        if not key:
            st.error("Add a Gemini API key in the AI settings above or in Setup.")
        else:
            try:
                client = ai.GeminiClient(key, st.session_state.get("gemini_model", "gemini-2.5-flash"))
            except Exception as exc:
                st.error(str(exc))
            else:
                progress = st.progress(0)
                status = st.empty()
                errors = []
                created = 0
                used_fallback = 0
                for position, (_, row) in enumerate(todo.iterrows(), start=1):
                    status.caption(f"Writing for {row['name']} ({position}/{len(todo)})")
                    message = None
                    try:
                        message = client.generate_outreach(
                            row.to_dict(),
                            st.session_state.profile,
                            st.session_state.email_template,
                            st.session_state.wa_template,
                        )
                    except Exception as exc:
                        if fallback:
                            message = ai.fallback_outreach(
                                row.to_dict(),
                                st.session_state.profile,
                                st.session_state.email_template,
                                st.session_state.wa_template,
                            )
                            used_fallback += 1
                            errors.append(f"{row['name']}: {exc} (used template)")
                        else:
                            errors.append(f"{row['name']}: {exc}")
                    if message:
                        message["lead_id"] = str(row["lead_id"])
                        message["model"] = message.get("model") or st.session_state.get("gemini_model", "")
                        save_message(message)
                        created += 1
                    progress.progress(position / len(todo))
                    time.sleep(0.2)
                status.empty()
                st.session_state.ai_errors = errors[:25]
                set_flash(
                    f"Generated {created} messages ({used_fallback} from templates, {len(errors)} errors).",
                    "success" if created else "error",
                )
                st.rerun()

    if st.session_state.get("ai_errors"):
        with st.expander("Last generation errors"):
            for error in st.session_state.ai_errors:
                st.caption(error)

    st.subheader("Generated messages")
    current_ids = set(df["lead_id"].astype(str))
    messages = {key: value for key, value in st.session_state.messages.items() if key in current_ids}
    if not messages:
        st.info("No messages yet. Generate with AI or from templates above.")
        return

    label_map = {}
    for _, row in df.iterrows():
        lead_id = str(row["lead_id"])
        if lead_id in messages:
            label_map[lead_id] = f"{row['name']} | {row['email'] or row['phone'] or 'no contact'} | score {row['score']}"
    chosen = st.selectbox(
        "Preview and edit",
        list(label_map.keys()),
        format_func=lambda lead_id: label_map.get(lead_id, lead_id),
        key="message_preview_select",
    )
    message = messages[chosen]
    st.caption(f"AI angle: {message.get('angle') or 'add one'} | Suggested channel: {message.get('channel_advice') or 'n/a'}")
    subject = st.text_input("Subject", value=message.get("subject", ""), key=f"subject_{chosen}")
    email_body = st.text_area("Email body", value=message.get("email_body", ""), height=250, key=f"body_{chosen}")
    whatsapp_message = st.text_area(
        "WhatsApp message", value=message.get("whatsapp_message", ""), height=100, key=f"wa_{chosen}"
    )
    angle = st.text_input("Lead angle", value=message.get("angle", ""), key=f"angle_{chosen}")
    a, b = st.columns([1, 1])
    if a.button("Save this message", type="primary"):
        updated = dict(message)
        updated.update(
            {
                "subject": subject.strip(),
                "email_body": email_body.strip(),
                "whatsapp_message": whatsapp_message.strip(),
                "angle": angle.strip(),
            }
        )
        save_message(updated)
        set_flash("Message saved.")
        st.rerun()
    if b.button("Delete this message"):
        st.session_state.messages.pop(chosen, None)
        storage.delete_message(chosen)
        set_flash("Message deleted.", "info")
        st.rerun()


def page_email():
    st.header("Email Outreach")
    st.caption("Send personalized emails through Gmail SMTP, with delays, caps and a full send log.")
    show_flash()
    df = get_leads()
    if df is None:
        st.warning("Import leads first on the Leads page.")
        return

    selected = df[df["selected"]]
    if selected.empty:
        st.warning("No leads selected. Tick leads on the Leads page.")
        if st.button("Select top 10 leads by score"):
            full = st.session_state.leads.copy()
            top = full.sort_values(["score", "reviews"], ascending=[False, False]).head(10).index
            full.loc[top, "selected"] = True
            st.session_state.leads = full
            save_leads_state()
            set_flash("Selected the top 10 leads. Review them on the Leads page, then come back.")
            st.rerun()
        return

    messages = st.session_state.messages
    missing = missing_message_rows(selected)
    if not missing.empty:
        st.info(f"{len(missing)} selected leads do not have a message yet.")
        if st.button("Create messages from templates for these leads"):
            created = make_messages_from_templates(missing)
            set_flash(f"Created {created} messages from templates.")
            st.rerun()

    contacted_ids = storage.sent_lead_ids("email")
    sent_emails = storage.sent_recipients("email")
    suppressed = storage.suppressed_values()
    eligible_rows = []
    already = 0
    blocked = 0
    no_email = 0
    for _, row in selected.iterrows():
        lead_id = str(row["lead_id"])
        email = str(row.get("email", "")).strip()
        if not email:
            no_email += 1
            continue
        if lead_id in contacted_ids or email.lower() in sent_emails:
            already += 1
            continue
        if suppressed_for_row(row, suppressed):
            blocked += 1
            continue
        if lead_id not in messages:
            continue
        eligible_rows.append(row)
    eligible = pd.DataFrame(eligible_rows)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Selected", len(selected))
    c2.metric("Ready to email", len(eligible))
    c3.metric("Already emailed", already)
    c4.metric("Suppressed / no email", blocked + no_email)

    with st.expander("Sender settings", expanded=not bool(setting("EMAIL_USER"))):
        a, b = st.columns(2)
        a.text_input("SMTP host", key="smtp_host")
        b.selectbox("Security", ["STARTTLS (587)", "SSL (465)", "Custom port"], key="smtp_security")
        if st.session_state.get("smtp_security") == "Custom port":
            st.number_input("Port", min_value=1, max_value=65535, key="smtp_custom_port")
        a, b = st.columns(2)
        a.text_input("Email address", key="smtp_user")
        b.text_input(
            "App password",
            type="password",
            key="smtp_password",
            placeholder="Saved password is in use. Type to override." if setting("EMAIL_PASS") else "Gmail app password",
        )
        a, b = st.columns(2)
        a.text_input("From name", key="smtp_from_name")
        b.text_input("From email", key="smtp_from_email")
        st.text_input("Reply-to", key="smtp_reply_to")
        if st.button("Verify email login"):
            ok, message = make_sender().verify()
            st.success(message) if ok else st.error(message)
        st.caption(
            "Gmail needs 2-step verification and an App Password. Normal Gmail limits are around 500 recipients per day, "
            "but start much smaller (20-50) and send to relevant businesses only."
        )

    if eligible.empty:
        st.info("Nothing is ready to send. Check that selected leads have emails and generated messages.")
        return

    st.subheader("Ready to send")
    st.dataframe(
        eligible[["name", "email", "score", "priority", "channel"]].head(200),
        width="stretch",
        hide_index=True,
    )

    first_message = messages[str(eligible.iloc[0]["lead_id"])]
    with st.expander("Preview first email"):
        st.text_input("Subject", value=first_message.get("subject", ""), disabled=True, key="email_preview_subject")
        st.html(text_to_html(final_email_body(first_message)))

    st.subheader("Send batch")
    a, b, c = st.columns(3)
    max_send = a.number_input("Max emails this run", min_value=1, max_value=200, value=min(20, len(eligible)), key="email_max")
    delay = b.slider("Delay between emails (seconds)", 5, 180, 30, key="email_delay")
    dry_run = c.checkbox("Dry run (preview only)", value=True, key="email_dry_run")
    confirm = st.checkbox(
        "I confirm these are relevant business contacts and I will honour any opt-out request.",
        key="email_confirm",
    )

    with st.expander("Send a test email"):
        st.caption("Tests use the first ready lead's message and are not logged as contacts.")
        test = st.text_input("Send a test to (comma separated)", key="email_test_recipients")
        if st.button("Send test email"):
            recipients = [item.strip() for item in test.split(",") if item.strip()]
            if not recipients:
                st.warning("Add a test email address first.")
            else:
                sender = make_sender()
                try:
                    sender.connect()
                    for recipient in recipients:
                        sender.send(
                            recipient,
                            f"[TEST] {first_message.get('subject', 'Website idea')}",
                            final_email_body(first_message),
                            sender.user,
                        )
                    st.success(f"Test sent to {', '.join(recipients)}.")
                except Exception as exc:
                    st.error(f"Test failed: {exc}")
                finally:
                    sender.close()

    if dry_run:
        st.info("Dry run is on. Nothing will be sent.")
        if st.button("Run dry run"):
            st.dataframe(
                eligible[["name", "email"]].head(int(max_send)),
                width="stretch",
                hide_index=True,
            )
            st.success(f"Dry run checked {min(int(max_send), len(eligible))} emails.")
        return

    if not confirm:
        st.warning("Tick the confirmation box to enable sending.")
        return

    count = min(int(max_send), len(eligible))
    if not st.button(f"Send {count} emails now", type="primary"):
        return

    batch = eligible.head(count)
    sender = make_sender()
    if not sender.from_email:
        st.error("Add the sender email address first.")
        return
    try:
        sender.connect()
    except Exception as exc:
        st.error(f"Could not connect to the mail server: {exc}")
        return

    progress = st.progress(0)
    status = st.empty()
    results = []
    sent_count = 0
    consecutive_failures = 0
    try:
        for position, (_, row) in enumerate(batch.iterrows(), start=1):
            message = messages[str(row["lead_id"])]
            subject = message.get("subject", "Website idea")
            body = final_email_body(message)
            status.caption(f"Sending {position}/{len(batch)} to {row['email']}")
            try:
                sender.send(row["email"], subject, body, sender.user)
                sent_count += 1
                consecutive_failures = 0
                storage.log_send(
                    str(row["lead_id"]), row["name"], "email", str(row["email"]).lower(), subject, "sent"
                )
                results.append({"name": row["name"], "email": row["email"], "status": "sent", "error": ""})
            except Exception as exc:
                consecutive_failures += 1
                error = str(exc)
                storage.log_send(
                    str(row["lead_id"]), row["name"], "email", str(row["email"]).lower(), subject, "failed", error
                )
                results.append({"name": row["name"], "email": row["email"], "status": "failed", "error": error[:200]})
                if consecutive_failures >= 3 or "authentication" in error.lower() or "username and password" in error.lower():
                    st.warning("Stopped after repeated send errors. Check your email login and daily limits.")
                    break
            progress.progress(position / len(batch))
            if position < len(batch):
                time.sleep(random.uniform(max(1.0, delay * 0.8), delay * 1.2))
    finally:
        sender.close()
        status.empty()

    if sent_count and sent_count == len(batch):
        st.success(f"Sent all {sent_count} emails.")
    elif sent_count:
        st.warning(f"Sent {sent_count} of {len(batch)} emails. Failed: {len(batch) - sent_count}.")
    else:
        st.error("No emails were sent. Check the errors, your email login and your daily limit.")
    results_df = pd.DataFrame(results)
    st.dataframe(results_df, width="stretch", hide_index=True)
    st.download_button(
        "Download these results (CSV)",
        data=results_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"email_results_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )


def page_whatsapp():
    st.header("WhatsApp Outreach")
    st.caption("Open a pre-filled WhatsApp chat for each lead, one at a time. No automation, no ban risk from bulk blasting.")
    show_flash()
    df = get_leads()
    if df is None:
        st.warning("Import leads first on the Leads page.")
        return

    selected = df[df["selected"]]
    if selected.empty:
        st.warning("No leads selected. Tick leads on the Leads page.")
        if st.button("Select top 10 leads by score"):
            full = st.session_state.leads.copy()
            top = full.sort_values(["score", "reviews"], ascending=[False, False]).head(10).index
            full.loc[top, "selected"] = True
            st.session_state.leads = full
            save_leads_state()
            set_flash("Selected the top 10 leads. Review them on the Leads page, then come back.")
            st.rerun()
        return

    missing = missing_message_rows(selected[selected["phone"].str.len() > 0])
    if not missing.empty:
        st.info(f"{len(missing)} selected leads with a phone number do not have a message yet.")
        if st.button("Create messages from templates for these leads"):
            created = make_messages_from_templates(missing)
            set_flash(f"Created {created} messages from templates.")
            st.rerun()

    messages = st.session_state.messages
    contacted_ids = storage.sent_lead_ids("whatsapp")
    sent_phones = storage.sent_recipients("whatsapp")
    suppressed = storage.suppressed_values()
    rows = []
    for _, row in selected.iterrows():
        lead_id = str(row["lead_id"])
        phone = only_digits(row.get("phone", ""))
        if not phone:
            continue
        if lead_id in contacted_ids or phone in sent_phones:
            continue
        if suppressed_for_row(row, suppressed):
            continue
        if lead_id not in messages:
            continue
        rows.append(row)

    st.warning(
        "WhatsApp is sensitive to bulk messaging. Send to relevant businesses only, personalize the first message, "
        "and keep new chats to roughly 20-30 per day. Clicking a link opens WhatsApp with the message pre-filled; "
        "you still press send yourself."
    )
    st.caption(f"{len(rows)} leads ready for WhatsApp. Each chat opens with the message pre-filled.")

    if not rows:
        st.info("Nothing ready. Check that selected leads have phone numbers and generated messages.")
        return

    export_rows = []
    for row in rows:
        message = messages[str(row["lead_id"])]
        text = message.get("whatsapp_message", "")
        export_rows.append(
            {
                "name": row["name"],
                "phone": row["phone"],
                "business_type": row["business_type"],
                "score": row["score"],
                "message": text,
                "whatsapp_link": build_wa_link(row["phone"], text),
            }
        )
    st.download_button(
        "Download WhatsApp links (CSV)",
        data=pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8-sig"),
        file_name=f"whatsapp_links_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

    page_size = 10
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = st.selectbox("Page", list(range(1, total_pages + 1)), key="wa_page")
    start = (page - 1) * page_size
    for row in rows[start : start + page_size]:
        lead_id = str(row["lead_id"])
        message = messages[lead_id]
        heading = f"{row['name']} | {display_phone(row['phone'])} | {row['business_type'] or 'business'} | score {row['score']}"
        with st.expander(heading, expanded=False):
            a, b = st.columns([2, 1])
            with a:
                text = st.text_area(
                    "Message",
                    value=message.get("whatsapp_message", ""),
                    height=120,
                    key=f"wa_text_{lead_id}_{page}",
                )
                st.caption(f"{len(text)} characters")
            with b:
                st.caption(f"Phone: {display_phone(row['phone'])}")
                st.caption(f"Priority: {row['priority']}")
                st.caption(f"AI angle: {message.get('angle') or 'n/a'}")
                if text != message.get("whatsapp_message", ""):
                    if st.button("Save message edit", key=f"wa_save_{lead_id}_{page}"):
                        updated = dict(message)
                        updated["whatsapp_message"] = text.strip()
                        save_message(updated)
                        set_flash("WhatsApp message saved.")
                        st.rerun()
            st.link_button("Open WhatsApp with this message", build_wa_link(row["phone"], text), width="stretch")
            if st.button("Mark as sent", key=f"wa_mark_{lead_id}_{page}"):
                updated = dict(message)
                updated["whatsapp_message"] = text.strip()
                save_message(updated)
                storage.log_send(
                    lead_id,
                    row["name"],
                    "whatsapp",
                    only_digits(row["phone"]),
                    text,
                    "sent",
                )
                set_flash(f"Marked {row['name']} as contacted on WhatsApp.")
                st.rerun()


def page_history():
    st.header("History")
    st.caption("Every email and WhatsApp contact attempt, plus your do-not-contact list.")
    show_flash()
    history = storage.load_history()
    if history.empty:
        st.info("Nothing sent yet.")
    else:
        sent = history[history["status"] == "sent"]
        email_sent = sent[sent["channel"] == "email"]
        wa_sent = sent[sent["channel"] == "whatsapp"]
        failed = history[history["status"] == "failed"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total sent", len(sent))
        c2.metric("Emails", len(email_sent))
        c3.metric("WhatsApp", len(wa_sent))
        c4.metric("Failed", len(failed))
        st.dataframe(history, width="stretch", hide_index=True)
        st.download_button(
            "Download history (CSV)",
            data=history.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"outreach_history_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )

    st.subheader("Do-not-contact list")
    st.caption("Add an email or phone number here when someone opts out. Suppressed leads are skipped everywhere.")
    a, b = st.columns([2, 2])
    value = a.text_input("Email or phone", key="suppress_value")
    reason = b.text_input("Reason", placeholder="Replied STOP / asked not to contact", key="suppress_reason")
    if st.button("Add to do-not-contact"):
        if value.strip():
            storage.add_suppression(value.strip(), reason.strip())
            set_flash("Added to the do-not-contact list.", "info")
            st.rerun()
        else:
            st.warning("Enter an email or phone number.")
    suppression = storage.suppression_frame()
    if suppression.empty:
        st.caption("The list is empty.")
    else:
        st.dataframe(suppression, width="stretch", hide_index=True)
        remove = st.multiselect("Remove from the list", suppression["value"].tolist(), key="suppress_remove")
        if st.button("Remove selected", disabled=not remove):
            for item in remove:
                storage.remove_suppression(item)
            set_flash("Removed from the do-not-contact list.", "info")
            st.rerun()


page = sidebar()
if page == "Setup":
    page_setup()
elif page == "Leads":
    page_leads()
elif page == "AI Personalize":
    page_ai()
elif page == "Email Outreach":
    page_email()
elif page == "WhatsApp Outreach":
    page_whatsapp()
elif page == "History":
    page_history()
