import json
import re
import sqlite3
from datetime import datetime

import pandas as pd

from utils.config import DB_PATH


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                lead_id TEXT PRIMARY KEY,
                subject TEXT,
                email_body TEXT,
                whatsapp_message TEXT,
                angle TEXT,
                channel_advice TEXT,
                model TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS send_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id TEXT,
                lead_name TEXT,
                channel TEXT,
                recipient TEXT,
                subject TEXT,
                status TEXT,
                error TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS suppression (
                value TEXT PRIMARY KEY,
                reason TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_send_log_lead ON send_log(lead_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_send_log_channel ON send_log(channel)")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def save_message(message):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO messages (lead_id, subject, email_body, whatsapp_message, angle, channel_advice, model, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lead_id) DO UPDATE SET
                subject=excluded.subject,
                email_body=excluded.email_body,
                whatsapp_message=excluded.whatsapp_message,
                angle=excluded.angle,
                channel_advice=excluded.channel_advice,
                model=excluded.model,
                updated_at=excluded.updated_at
            """,
            (
                str(message.get("lead_id", "")),
                str(message.get("subject", "")),
                str(message.get("email_body", "")),
                str(message.get("whatsapp_message", "")),
                str(message.get("angle", "")),
                str(message.get("channel_advice", "")),
                str(message.get("model", "")),
                _now(),
            ),
        )


def delete_message(lead_id):
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE lead_id = ?", (str(lead_id),))


def get_messages(lead_ids=None):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM messages").fetchall()
    result = {}
    ids = {str(item) for item in lead_ids} if lead_ids is not None else None
    for row in rows:
        lead_id = str(row["lead_id"])
        if ids is not None and lead_id not in ids:
            continue
        result[lead_id] = {
            "lead_id": lead_id,
            "subject": row["subject"] or "",
            "email_body": row["email_body"] or "",
            "whatsapp_message": row["whatsapp_message"] or "",
            "angle": row["angle"] or "",
            "channel_advice": row["channel_advice"] or "",
            "model": row["model"] or "",
        }
    return result


def log_send(lead_id, lead_name, channel, recipient, subject, status, error=""):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO send_log (lead_id, lead_name, channel, recipient, subject, status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(lead_id or ""),
                str(lead_name or ""),
                str(channel or ""),
                str(recipient or ""),
                str(subject or ""),
                str(status or ""),
                str(error or "")[:500],
                _now(),
            ),
        )


def sent_lead_ids(channel=None):
    query = "SELECT DISTINCT lead_id FROM send_log WHERE status = 'sent'"
    params = []
    if channel:
        query += " AND channel = ?"
        params.append(channel)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return {str(row["lead_id"]) for row in rows if row["lead_id"]}


def sent_recipients(channel=None):
    query = "SELECT DISTINCT recipient FROM send_log WHERE status = 'sent'"
    params = []
    if channel:
        query += " AND channel = ?"
        params.append(channel)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return {str(row["recipient"]).lower() for row in rows if row["recipient"]}


def add_suppression(value, reason=""):
    text = str(value or "").strip()
    if not text:
        return
    normalized = text.lower() if "@" in text else re.sub(r"\D", "", text)
    if not normalized:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO suppression (value, reason, created_at) VALUES (?, ?, ?)",
            (normalized, str(reason or ""), _now()),
        )


def remove_suppression(value):
    with _connect() as conn:
        conn.execute("DELETE FROM suppression WHERE value = ?", (str(value),))


def suppressed_values():
    with _connect() as conn:
        rows = conn.execute("SELECT value FROM suppression").fetchall()
    return {str(row["value"]) for row in rows}


def suppression_frame():
    with _connect() as conn:
        return pd.read_sql_query("SELECT value, reason, created_at FROM suppression ORDER BY created_at DESC", conn)


def load_history():
    with _connect() as conn:
        return pd.read_sql_query("SELECT * FROM send_log ORDER BY id DESC", conn)


def count_sends():
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM send_log WHERE status = 'sent'").fetchone()
    return int(row["total"] if row else 0)


def save_state(key, value):
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_state (key, value, updated_at) VALUES (?, ?, ?)",
            (str(key), json.dumps(value, ensure_ascii=False, default=str), _now()),
        )


def load_state(key):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (str(key),)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except Exception:
        return None
