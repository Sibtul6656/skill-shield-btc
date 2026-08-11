# Skill Shield BTC

AI-Powered Institutional Analytics Engine — Flask dashboard for Bitcoin whale intelligence.

## Stack

- **Language:** Python 3.11
- **Web framework:** Flask 3.1
- **HTTP client:** requests
- **Database:** SQLite (`skillshield.db`, auto-created on first run)
- **Port:** 5000 (host 0.0.0.0)

## Layout

```
main.py             # Flask app: dashboard, blockchain.info stats, all routes
auth.py             # Auth blueprint: login, signup, session, trial logic
admin.py            # Admin blueprint: user management portal
requirements.txt    # Python dependencies (flask, requests)
skillshield.db      # SQLite user database (auto-created)
```

## Run

The `Start application` workflow runs:

```
python main.py
```

## Environment

- `SESSION_SECRET` — used as Flask session secret key (Replit Secret, already configured)
- `SECRET_KEY` — alternative env var for Flask session secret (optional override)
- `PRO_LEADS_WEBHOOK_URL` or `GOOGLE_SHEETS_WEBHOOK_URL` — optional HTTPS webhook that receives validated Pro Analysis Signals leads as JSON
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` — optional SMTP settings for the Pro welcome email
- `SMTP_FROM` — optional sender override (defaults to `support@skillshieldbtc.com`)

Pro Analysis Signals opens from the dashboard's VIP navigation button. Lead access is remembered in browser
`localStorage`; the live brief uses public Binance candle data and public crypto RSS feeds. External lead delivery
and welcome email are optional and remain disabled until their environment variables are configured.

## Default Admin Account

- **URL:** `/alpha-admin-portal`
- **Email:** `admin@skillshield.alpha`
- **Password:** `MasterAdmin123!`
- Change this password via `/alpha-admin-portal/reset-pass` after first login.

## Auth & Access Model

- New users get a **48-hour free trial** (full access)
- Paid users pay $99 lifetime via Bitcoin — they submit a TxID, admin verifies and activates
- Admin can activate/revoke accounts from the admin portal
