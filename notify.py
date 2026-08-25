"""
notify.py — Shared helpers for admin notifications and external data collection.

Two things live here:

1. Email helper — sends a plain-text email to the admin's personal inbox using
   the same SMTP env vars already used for the Pro Signals welcome email.

2. Google Sheets webhook helper — posts JSON data to a Google Apps Script Web
   App URL. That Apps Script lives inside a Google Sheet (which lives inside
   Google Drive), so every submission shows up as a new row automatically.
   This is the simplest reliable way to get form/signup data into Drive
   without managing OAuth service-account credentials.

All of this is OPTIONAL and silently disabled until the relevant environment
variables are configured — nothing breaks if they're missing.
"""

import os
import smtplib
import requests
from email.message import EmailMessage


# ── Admin email notifications ────────────────────────────────────────────────

def notify_admin(subject: str, body: str) -> bool:
    """Send a plain-text notification email to the admin's personal inbox.

    Requires SMTP_HOST, SMTP_USER, SMTP_PASSWORD to be set.
    Recipient is ADMIN_NOTIFY_EMAIL if set, otherwise falls back to SMTP_USER.
    Returns True on success, False if not configured or send failed
    (never raises — a broken notification should never break the user's request).
    """
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not all((host, user, password)):
        return False

    to_addr = os.environ.get("ADMIN_NOTIFY_EMAIL") or user

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ.get("SMTP_FROM", "support@skillshieldbtc.com")
    message["To"] = to_addr
    message.set_content(body)

    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=10) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(message)
        return True
    except Exception:
        return False


# ── Google Sheets / Drive data collection ────────────────────────────────────

def send_to_sheet(payload: dict, webhook_env_var: str) -> bool:
    """POST a JSON payload to a Google Apps Script Web App URL, which appends
    it as a new row in a Google Sheet stored in Google Drive.

    webhook_env_var is the name of the environment variable holding the URL,
    e.g. "SIGNUP_SHEETS_WEBHOOK_URL". Returns True only on a 2xx response,
    False if not configured or the request failed.
    """
    webhook = os.environ.get(webhook_env_var)
    if not webhook:
        return False
    try:
        response = requests.post(webhook, json=payload, timeout=10)
        return 200 <= response.status_code < 300
    except Exception:
        return False
