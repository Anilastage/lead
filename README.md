# TrioCodeLab Outreach

A private outreach console for finding the best leads, writing personalized emails and WhatsApp messages, and sending from your own Gmail account.

It works with messy lead lists. A file can have emails only, phone numbers only, or both. Columns are detected automatically and can be remapped in the app.

## Features

- Import CSV, XLSX, XLS, DOCX, TSV, pasted text, or a public Google Sheet link
- Automatic column detection plus manual mapping
- Cleaning: valid emails, normalized phone numbers, duplicate removal
- Lead scoring and a recommended channel for each lead
- Gemini AI personalization: email subject, email body, WhatsApp message, and a short "why this lead" angle
- Template fallback that works without an API key
- Gmail SMTP sending with HTML + plain text, reply-to, list-unsubscribe header, delays, batch caps, dry run, and send log
- WhatsApp click-to-send links with pre-filled personalized messages, CSV export, and manual "mark as sent"
- Do-not-contact list, already-contacted detection, and history export
- Optional password gate for online deployment

## Quick start (Windows)

```powershell
cd C:\Users\Arjun\Desktop\AI-Email-Outreach
python -m pip install -r requirements.txt
copy .env.example .env
notepad .env
python -m streamlit run app.py
```

The app opens at http://localhost:8501.

### 1. Company profile

Open **Setup** and fill in your name, company, website, services and offer. These values are used in every message. Edit the tone and language here too.

### 2. Leads

On **Leads**, upload a file. Map the columns, choose the default country code, then click **Build lead list**.

Supported files: CSV, XLSX, XLS, DOCX (tables or delimited text), TSV, TXT, pasted text, and public Google Sheet links. PDFs should be exported to CSV or Excel first, because PDF text extraction is unreliable for contact tables.

The app scores every lead out of 100 using:

- available email and phone
- business type fit (real estate, clinics, salons, restaurants, builders, agencies, etc.)
- Google rating and review count
- website presence

Tick the rows you want to contact, then click **Apply edits and selection**. Emails and phones can be edited directly in the table, which is useful when a new file has no email column yet.

### 3. Personalize

On **AI Personalize**, choose a batch size (start with 10-25), add your Gemini API key, and click **Generate with AI**. Get a free key at https://aistudio.google.com.

Without a key, click **Generate from templates**. This still fills the placeholders and is good enough to start.

### 4. Email

On **Email Outreach**, open **Sender settings** and enter:

- SMTP host: `smtp.gmail.com`
- Security: `STARTTLS (587)`
- Your Gmail address
- A Gmail **App Password** (not your normal password)

Gmail requires 2-step verification before App Passwords can be created. Use **Verify email login** and **Send a test email** first. Dry run is on by default.

`EMAIL_PASS` is the Gmail App Password used to send mail. `APP_PASSWORD` is the separate password that protects the app itself when it is online. They are different values.

### 5. WhatsApp

On **WhatsApp Outreach**, each lead has an **Open WhatsApp with this message** button. WhatsApp opens with the message pre-filled; you press send. Then click **Mark as sent**.

Keep new WhatsApp chats to roughly 20-30 per day from a personal number. Bulk automated WhatsApp traffic gets numbers banned.

## Deploy online (Streamlit Community Cloud)

1. Create a private GitHub repository and push this folder.
2. Go to https://share.streamlit.io and create an app from the repository with `app.py` as the entry point.
3. In **App settings > Secrets**, paste the values from `.streamlit/secrets.toml.example` and fill them in.
4. Set a strong `APP_PASSWORD`. Without it, anyone with the link can use the app.
5. Open the app, sign in, and add your company profile.

### Data persistence

Leads, messages, send history and the do-not-contact list are stored in `data/outreach.db` (SQLite). On Streamlit Community Cloud the filesystem can reset when the app is rebuilt. Download your CSV exports regularly, and keep the original files. The app automatically reloads the last saved lead list when the database survives.

## Safety and deliverability

- Send to relevant businesses only. Random lists damage your domain and get accounts limited.
- Start with 20-30 emails per day, then increase slowly. Gmail's normal cap is around 500 recipients per day, but new accounts should stay far below that.
- Keep messages short, personalized and free of attachments.
- Honour every opt-out. Add the email or phone to the do-not-contact list.
- Do not use this for unsolicited bulk WhatsApp messages. The app intentionally creates click-to-send links rather than automating WhatsApp.
- For larger volumes later, use a business domain with SPF, DKIM and DMARC, and a transactional email provider.

## Credits and limits

- AI calls go to Gemini's free API. Free tiers have per-minute and per-day quotas; generate in small batches.
- The app does not track email opens or replies. Gmail replies land in your inbox; mark opted-out people in the do-not-contact list.
- `data/profile.json` stores only company details, never passwords or API keys.
- Credentials live in `.env` locally or Streamlit secrets online. Never commit them.

## Project layout

```
app.py                      Streamlit UI
utils/leads.py              import, mapping, cleaning, scoring
utils/ai.py                 Gemini prompts, JSON parsing, template fallback
utils/emailer.py            SMTP sender and HTML rendering
utils/whatsapp.py           phone normalization and wa.me links
utils/storage.py            SQLite messages, send log, suppression, saved leads
templates/default.py        default email and WhatsApp templates
data/                       local database and company profile
```
