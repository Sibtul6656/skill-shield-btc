import sqlite3
import hashlib
import os
import secrets
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, request, session, redirect, url_for, Response, render_template_string

from notify import notify_admin, send_to_sheet

auth_bp = Blueprint("auth", __name__)

DB_PATH = "skillshield.db"
TRIAL_HOURS = 48
ADMIN_EMAIL = "admin@skillshield.alpha"
ADMIN_PASSWORD = "MasterAdmin123!"


# ── DB bootstrap ──────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                email              TEXT    UNIQUE NOT NULL,
                password_hash      TEXT    NOT NULL,
                signup_ts          REAL    NOT NULL,
                payment_status     TEXT    NOT NULL DEFAULT 'Trial',
                tx_hash            TEXT,
                is_admin           INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS support_messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                email        TEXT    NOT NULL,
                subject      TEXT    NOT NULL,
                message      TEXT    NOT NULL,
                submitted_ts REAL    NOT NULL,
                read         INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pro_leads (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT,
                email        TEXT,
                platform     TEXT,
                handle       TEXT,
                submitted_ts TEXT
            );
        """)
        # Migrate: add new columns if they don't exist yet
        for col, definition in [
            ("subscription_end_ts", "REAL"),
            ("plan_type",          "TEXT"),
            ("referred_by",        "TEXT"),
            ("reset_token",        "TEXT"),
        ]:
            try:
                db.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
            except Exception:
                pass  # column already exists
        db.commit()
        existing = db.execute(
            "SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)
        ).fetchone()
        if not existing:
            db.execute(
                """INSERT INTO users (email, password_hash, signup_ts, payment_status, is_admin)
                   VALUES (?, ?, ?, 'Active', 1)""",
                (ADMIN_EMAIL, _hash(ADMIN_PASSWORD), datetime.utcnow().timestamp()),
            )
        db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_user_by_email(email: str):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()


def get_user_by_id(user_id: int):
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def trial_status(user) -> dict:
    if user["payment_status"] == "Active":
        # Check if a time-limited subscription has expired
        end_ts = user["subscription_end_ts"] if "subscription_end_ts" in user.keys() else None
        if end_ts and datetime.utcnow() > datetime.utcfromtimestamp(end_ts):
            # Subscription lapsed — revert to expired state
            with get_db() as db:
                db.execute("UPDATE users SET payment_status='Trial' WHERE id=?", (user["id"],))
                db.commit()
            return {"locked": True, "status": "expired", "hours_left": 0}
        return {"locked": False, "status": "active", "hours_left": None}
    if user["payment_status"] == "Pending":
        return {"locked": False, "status": "pending", "hours_left": None}
    expiry = datetime.utcfromtimestamp(user["signup_ts"]) + timedelta(hours=TRIAL_HOURS)
    now = datetime.utcnow()
    if now < expiry:
        hours_left = (expiry - now).total_seconds() / 3600
        return {"locked": False, "status": "trial", "hours_left": round(hours_left, 1)}
    return {"locked": True, "status": "expired", "hours_left": 0}


# ── Decorators ────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login_page"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login_page"))
        user = get_user_by_id(session["user_id"])
        if not user or not user["is_admin"]:
            return Response("Forbidden", 403)
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ───────────────────────────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SkillShield BTC — Real-Time Bitcoin Mempool Intelligence</title>
<meta name="description" content="Institutional-grade Bitcoin Mempool intelligence and live on-chain analytics. Track unconfirmed transactions, network congestion, and fee telemetry in real time.">
<meta name="keywords" content="bitcoin, mempool, on-chain analytics, btc mempool tracker, bitcoin transactions, crypto intelligence">
<meta name="robots" content="index, follow">

<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{
  background:#0d1117;
  font-family:'Inter','Segoe UI',sans-serif;
  min-height:100vh;
  display:flex;
  align-items:stretch;
  color:#c9d1d9;
  overflow-x:hidden;
}

/* ── Background grid ── */
body::before{
  content:'';
  position:fixed;inset:0;
  background-image:
    linear-gradient(rgba(88,166,255,0.04) 1px,transparent 1px),
    linear-gradient(90deg,rgba(88,166,255,0.04) 1px,transparent 1px);
  background-size:48px 48px;
  pointer-events:none;z-index:0;
}
body::after{
  content:'';
  position:fixed;inset:0;
  background:radial-gradient(ellipse 80% 60% at 20% 40%,rgba(31,111,235,0.08) 0%,transparent 70%),
             radial-gradient(ellipse 60% 50% at 80% 70%,rgba(88,166,255,0.05) 0%,transparent 70%);
  pointer-events:none;z-index:0;
}

/* ── Layout ── */
.auth-wrap{
  position:relative;z-index:1;
  display:flex;align-items:stretch;
  width:100%;min-height:100vh;
}
.auth-left{
  flex:1;
  display:flex;flex-direction:column;justify-content:center;
  padding:60px 56px;
  border-right:1px solid #21262d;
  background:rgba(13,17,23,0.6);
}
.auth-right{
  width:480px;flex-shrink:0;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:48px 40px;
}

/* ── Left panel ── */
.auth-brand{
  font-size:1.1em;font-weight:900;letter-spacing:3px;text-transform:uppercase;
  background:linear-gradient(135deg,#fff 0%,#79c0ff 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  margin-bottom:8px;
}
.auth-tagline{color:#58a6ff;font-size:0.72em;letter-spacing:2px;text-transform:uppercase;margin-bottom:48px}
.auth-headline{
  color:#fff;font-size:2.2em;font-weight:900;line-height:1.2;margin-bottom:16px;
  letter-spacing:-0.5px;
}
.auth-headline span{
  background:linear-gradient(135deg,#58a6ff,#79c0ff);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}
.auth-desc{color:#8b949e;font-size:0.9em;line-height:1.7;max-width:460px;margin-bottom:40px}
.feat-list{list-style:none;display:flex;flex-direction:column;gap:16px;margin-bottom:48px}
.feat-item{display:flex;align-items:flex-start;gap:14px}
.feat-icon{
  width:36px;height:36px;border-radius:9px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-size:1.1em;
  background:rgba(88,166,255,0.1);border:1px solid rgba(88,166,255,0.2);
}
.feat-text{flex:1}
.feat-title{color:#e6edf3;font-size:0.88em;font-weight:700;margin-bottom:2px}
.feat-desc{color:#6e7681;font-size:0.78em;line-height:1.5}
/* ── Live counter ── */
.counter-block{
  display:flex;align-items:center;justify-content:space-between;
  background:linear-gradient(135deg,rgba(88,166,255,0.07),rgba(63,185,80,0.05));
  border:1px solid rgba(88,166,255,0.18);
  border-radius:14px;padding:18px 22px;margin-bottom:28px;
  box-shadow:0 0 32px rgba(88,166,255,0.06);
}
.counter-inner{flex:1}
.counter-num{
  color:#fff;font-size:2.4em;font-weight:900;
  letter-spacing:-1px;line-height:1;
  background:linear-gradient(135deg,#e6edf3 0%,#79c0ff 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}
.counter-label{color:#8b949e;font-size:0.72em;font-weight:600;letter-spacing:0.5px;margin-top:4px}
.counter-pulse-wrap{display:flex;flex-direction:column;align-items:center;gap:6px;padding-left:20px}
.counter-pulse{
  display:block;width:10px;height:10px;border-radius:50%;background:#3fb950;
  box-shadow:0 0 0 0 rgba(63,185,80,0.5);
  animation:cpulse 1.8s ease-in-out infinite;
}
@keyframes cpulse{
  0%,100%{box-shadow:0 0 0 0 rgba(63,185,80,0.5)}
  50%{box-shadow:0 0 0 8px rgba(63,185,80,0)}
}
.counter-live-txt{color:#3fb950;font-size:0.62em;font-weight:800;letter-spacing:2px}
/* ── Trust row ── */
.trust-row{display:flex;align-items:center;gap:20px;flex-wrap:wrap}
.trust-item{color:#6e7681;font-size:0.72em;display:flex;align-items:center;gap:6px}
.trust-dot{width:5px;height:5px;border-radius:50%;background:#3fb950;flex-shrink:0}

/* ── Right panel / Card ── */
.card-wrap{width:100%;max-width:400px}
.card-eyebrow{
  text-align:center;color:#6e7681;font-size:0.7em;font-weight:700;
  letter-spacing:2px;text-transform:uppercase;margin-bottom:24px;
}
.card{
  background:rgba(22,27,34,0.95);
  border:1px solid #30363d;
  border-top:3px solid #1f6feb;
  border-radius:16px;
  padding:36px 32px 28px;
  box-shadow:0 20px 80px rgba(0,0,0,0.7),0 0 0 1px rgba(88,166,255,0.04);
}
.card-title{color:#fff;font-size:1.15em;font-weight:800;margin-bottom:4px}
.card-sub{color:#8b949e;font-size:0.8em;margin-bottom:22px;line-height:1.5}
label{
  display:block;color:#8b949e;font-size:0.7em;font-weight:700;
  letter-spacing:1.2px;text-transform:uppercase;margin-bottom:6px;
}
input{
  display:block;width:100%;
  background:#0d1117;
  border:1px solid #30363d;
  border-radius:8px;padding:11px 14px;
  color:#fff;font-size:0.9em;
  font-family:inherit;margin-bottom:16px;
  outline:none;transition:border-color 0.2s,box-shadow 0.2s;
}
input:focus{border-color:#58a6ff;box-shadow:0 0 0 3px rgba(88,166,255,0.12)}
.btn{
  width:100%;padding:13px;border:none;border-radius:9px;
  font-size:0.92em;font-weight:800;cursor:pointer;
  transition:opacity 0.2s,transform 0.1s;
  letter-spacing:0.4px;margin-top:4px;
}
.btn-primary{
  background:linear-gradient(135deg,#1f6feb 0%,#388bfd 50%,#58a6ff 100%);
  color:#fff;
  box-shadow:0 4px 20px rgba(31,111,235,0.4);
}
.btn-primary:hover{opacity:0.9}
.btn-primary:active{transform:scale(0.98)}
.switch{margin-top:18px;text-align:center;color:#6e7681;font-size:0.8em}
.switch a{color:#58a6ff;text-decoration:none;font-weight:700}
.switch a:hover{text-decoration:underline}
.error{
  background:rgba(255,82,82,0.08);border:1px solid rgba(255,82,82,0.3);
  border-radius:8px;padding:10px 14px;color:#ff5252;font-size:0.8em;margin-bottom:14px;
  line-height:1.5;
}
.success{
  background:rgba(63,185,80,0.08);border:1px solid rgba(63,185,80,0.3);
  border-radius:8px;padding:10px 14px;color:#3fb950;font-size:0.8em;margin-bottom:14px;
}
.trial-badge{
  background:linear-gradient(135deg,rgba(88,166,255,0.08),rgba(63,185,80,0.06));
  border:1px solid rgba(88,166,255,0.2);
  border-radius:10px;padding:12px 14px;
  color:#79c0ff;font-size:0.78em;margin-bottom:18px;
  text-align:center;line-height:1.6;
}
.price-pill{
  display:inline-flex;align-items:center;gap:6px;
  background:rgba(63,185,80,0.1);border:1px solid rgba(63,185,80,0.25);
  border-radius:999px;padding:5px 14px;
  color:#3fb950;font-size:0.72em;font-weight:800;
  letter-spacing:0.5px;text-transform:uppercase;
  margin-bottom:20px;
}
.price-pill-dot{width:6px;height:6px;border-radius:50%;background:#3fb950}
.divider{border:none;border-top:1px solid #21262d;margin:20px 0 16px}

/* ── Mobile ── */
@media(max-width:900px){
  .auth-left{display:none}
  .auth-right{width:100%;padding:32px 20px}
  .card{padding:28px 22px 22px}
}
</style>
</head>
<body>
<div class="auth-wrap">

  <!-- ── Left panel ── -->
  <div class="auth-left">
    <div class="auth-brand">Skill Shield BTC</div>
    <div class="auth-tagline">Institutional Crypto Whale Intelligence</div>
    <div class="auth-headline">
      Institutional mempool signals,<br><span>verifiable in real time.</span>
    </div>
    <p class="auth-desc">
      Skill Shield BTC fuses live mempool data, on-chain flow analysis, and
      network velocity into a single dashboard — every transaction links
      directly to mempool.space so you can independently verify every signal
      we show you.
    </p>
    <ul class="feat-list">
      <li class="feat-item">
        <div class="feat-icon">🐋</div>
        <div class="feat-text">
          <div class="feat-title">Whale Actionable Bias</div>
          <div class="feat-desc">Live directional signal fused from 3 on-chain data sources. Bullish, bearish, or neutral — updated every 30 seconds.</div>
        </div>
      </li>
      <li class="feat-item">
        <div class="feat-icon">⚡</div>
        <div class="feat-text">
          <div class="feat-title">Network Velocity Monitor</div>
          <div class="feat-desc">Real-time mempool throughput vs. 24-hour baseline, so you can see accumulation or distribution spikes as they happen — before they confirm on-chain.</div>
        </div>
      </li>
      <li class="feat-item">
        <div class="feat-icon">💎</div>
        <div class="feat-text">
          <div class="feat-title">Smart Money Flow</div>
          <div class="feat-desc">Sweep vs. fan-out transaction classification reveals whether whales are accumulating or distributing right now.</div>
        </div>
      </li>
      <li class="feat-item">
        <div class="feat-icon">📚</div>
        <div class="feat-text">
          <div class="feat-title">Tool Academy — 5 Strategy Chapters</div>
          <div class="feat-desc">Learn to read every signal, identify whale manipulation tactics, and build a rules-based trading framework.</div>
        </div>
      </li>
    </ul>
    <!-- Data source badge (replaces fabricated user counter) -->
    <div class="counter-block">
      <div class="counter-inner">
        <div class="counter-label">Live mempool data sourced directly from blockchain.info · verifiable on mempool.space</div>
      </div>
      <div class="counter-pulse-wrap">
        <span class="counter-pulse"></span>
        <span class="counter-live-txt">LIVE</span>
      </div>
    </div>
    <div class="trust-row">
      <div class="trust-item"><span class="trust-dot"></span>Live blockchain data</div>
      <div class="trust-item"><span class="trust-dot"></span>No subscription traps</div>
      <div class="trust-item"><span class="trust-dot"></span>$99/mo or $999/yr</div>
      <div class="trust-item"><span class="trust-dot"></span>48h free trial</div>
    </div>
  </div>

  <!-- ── Right panel ── -->
  <div class="auth-right">
    <div class="card-wrap">
      <div class="card-eyebrow">🔐 Secure Access Portal</div>
      <div class="card">
        <div class="price-pill"><span class="price-pill-dot"></span>$99/mo or $999/yr · 48h Free Trial</div>
        <div class="card-title">__TITLE__</div>
        <div class="card-sub">__SUB__</div>
        __TRIAL_BADGE__
        __MSG__
        <form method="POST">
          <label for="email">Email Address</label>
          <input type="email" id="email" name="email" placeholder="you@example.com" required autocomplete="email">
          <label for="password">Password</label>
          <input type="password" id="password" name="password" placeholder="••••••••" required autocomplete="__AC__">
          <div style="text-align: right; margin-top: 4px; margin-bottom: 14px;">
    
</div>
          __EXTRA__
          <button type="submit" class="btn btn-primary">__BTN__</button>
        </form>
        <hr class="divider">
        <div class="switch">__SWITCH__</div>
      </div>
    </div>
  </div>

</div>
</body>
</html>"""


def _render_auth(title, sub, btn, switch, msg="", extra="", trial_badge="", ac="current-password"):
    return (LOGIN_HTML
            .replace("__TITLE__", title)
            .replace("__SUB__", sub)
            .replace("__BTN__", btn)
            .replace("__SWITCH__", switch)
            .replace("__MSG__", msg)
            .replace("__EXTRA__", extra)
            .replace("__TRIAL_BADGE__", trial_badge)
            .replace("__AC__", ac))


@auth_bp.route("/login", methods=["GET", "POST"])
def login_page():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    msg = ""
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        user = get_user_by_email(email)
        if user and user["password_hash"] == _hash(password):
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        msg = '<div class="error">Invalid email or password. Please try again.</div>'
    
    badge = '<div class="trial-badge">🔐 New? <a href="/signup" style="color:#58a6ff;font-weight:700;">Create your free account</a> — 48-hour full access trial included.</div>'
    
    # Forgot password link HTML to fill into __EXTRA__
    forgot_link = '''
    <div style="text-align: right; margin-top: 4px; margin-bottom: 14px;">
        <a href="/forgot-password" style="color: #58a6ff; font-size: 0.78em; text-decoration: none;">Forgot Password?</a>
    </div>
    '''
    
    return _render_auth(
        "Sign In to Your Account",
        "Access your live whale intelligence dashboard.",
        "Sign In",
        'Don\'t have an account? <a href="/signup">Start Free Trial</a>',
        msg=msg, 
        trial_badge=badge,
        extra=forgot_link
    )

@auth_bp.route("/signup", methods=["GET", "POST"])
def signup_page():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    msg = ""
    # Preserve ref code across GET and POST
    ref_code = request.args.get("ref", "") or request.form.get("ref_code", "")
    ref_code = ref_code.strip()[:64]  # sanitise length
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if len(password) < 8:
            msg = '<div class="error">Password must be at least 8 characters.</div>'
        elif password != confirm:
            msg = '<div class="error">Passwords do not match.</div>'
        elif get_user_by_email(email):
            msg = '<div class="error">An account with this email already exists. <a href="/login" style="color:#58a6ff;">Sign in instead.</a></div>'
        else:
            signup_ts = datetime.utcnow().timestamp()
            with get_db() as db:
                db.execute(
                    "INSERT INTO users (email,password_hash,signup_ts,payment_status,referred_by) VALUES (?,?,?,?,?)",
                    (email, _hash(password), signup_ts, "Trial",
                     ref_code if ref_code else None),
                )
                db.commit()
            user = get_user_by_email(email)
            session["user_id"] = user["id"]

            # Push new-signup data out to Google Sheets (stored in Google Drive) and
            # notify the admin. Both are no-ops until their env vars are configured —
            # they never block or fail the signup itself.
            send_to_sheet(
                {
                    "email": email,
                    "signup_time_utc": datetime.utcfromtimestamp(signup_ts).isoformat() + "Z",
                    "referred_by": ref_code or "",
                    "plan_status": "Trial",
                },
                webhook_env_var="SIGNUP_SHEETS_WEBHOOK_URL",
            )
            notify_admin(
                subject="[Skill Shield BTC] New user signed up",
                body=(
                    f"A new user just created an account.\n\n"
                    f"Email: {email}\n"
                    f"Signed up: {datetime.utcfromtimestamp(signup_ts).isoformat()}Z\n"
                    f"Referred by: {ref_code or '—'}\n"
                    f"Status: 48-hour free trial started"
                ),
            )

            return redirect(url_for("dashboard"))
    # Pass ref code as hidden field so it survives form submission
    ref_field = f'<input type="hidden" name="ref_code" value="{ref_code}">' if ref_code else ""
    extra = f"""
    <label for="confirm">Confirm Password</label>
    <input type="password" id="confirm" name="confirm" placeholder="••••••••" required autocomplete="new-password">
    {ref_field}
    """
    badge = '<div class="trial-badge">✅ <b>48-Hour Free Trial</b> — Full access to all whale intelligence tools. No credit card required to start.</div>'
    return _render_auth(
        "Create Your Free Account",
        "Start your 48-hour premium trial instantly.",
        "Start Free Trial →",
        'Already have an account? <a href="/login">Sign In</a>',
        msg=msg, extra=extra, trial_badge=badge, ac="new-password",
    )


@auth_bp.route("/contact", methods=["POST"])
def contact():
    from flask import jsonify
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        data = {}
    email   = (data.get("email",   "") or "").strip()
    subject = (data.get("subject", "") or "").strip()
    message = (data.get("message", "") or "").strip()
    if not email or not subject or not message:
        return jsonify({"ok": False, "error": "All fields are required."}), 400
    with get_db() as db:
        db.execute(
            "INSERT INTO support_messages (email, subject, message, submitted_ts) VALUES (?,?,?,?)",
            (email, subject, message, datetime.utcnow().timestamp()),
        )
        db.commit()

    # Notify the admin's personal inbox immediately so submissions don't sit
    # unread in the admin portal. Silently a no-op until SMTP_HOST/SMTP_USER/
    # SMTP_PASSWORD (and optionally ADMIN_NOTIFY_EMAIL) are configured.
    notify_admin(
        subject=f"[Skill Shield BTC] New contact form message: {subject}",
        body=(
            f"A visitor submitted the contact form on Skill Shield BTC.\n\n"
            f"From:    {email}\n"
            f"Subject: {subject}\n\n"
            f"Message:\n{message}\n\n"
            f"— This message is also saved in the admin portal under Support Messages."
        ),
    )

    return jsonify({"ok": True})


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login_page"))


@auth_bp.route("/submit-payment", methods=["POST"])
def submit_payment():
    if "user_id" not in session:
        return redirect(url_for("auth.login_page"))
    tx_hash   = request.form.get("tx_hash",   "").strip()
    plan_type = request.form.get("plan_type", "monthly").strip().capitalize()  # "Monthly" or "Yearly"
    if plan_type not in ("Monthly", "Yearly"):
        plan_type = "Monthly"
    if tx_hash:
        with get_db() as db:
            db.execute(
                "UPDATE users SET tx_hash=?, payment_status='Pending', plan_type=? WHERE id=?",
                (tx_hash, plan_type, session["user_id"]),
            )
            db.commit()
    return redirect(url_for("dashboard"))

import secrets
import smtplib
from email.message import EmailMessage
import os

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user:
                token = secrets.token_urlsafe(32)
                db.execute("UPDATE users SET reset_token = ? WHERE email = ?", (token, email))
                db.commit()
                
                reset_link = url_for("auth.reset_password", token=token, _external=True)
                send_reset_email(email, reset_link)
                
        return render_template_string("""
        <html>
            <body style="background:#0d1117; color:#c9d1d9; font-family:sans-serif; text-align:center; padding-top:100px;">
                <div style="max-width:400px; margin:0 auto; background:#161b22; padding:30px; border-radius:10px; border:1px solid #30363d;">
                    <h2 style="color:#fff;">📧 Check Your Email</h2>
                    <p style="font-size:0.88em; color:#8b949e;">If an account exists with that email, reset instructions have been sent.</p>
                    <a href="/login" style="color:#58a6ff; font-size:0.85em; text-decoration:none; font-weight:bold;">Return to Sign In</a>
                </div>
            </body>
        </html>
        """)
        
    return render_template_string("""
    <html>
        <body style="background:#0d1117; color:#c9d1d9; font-family:sans-serif; text-align:center; padding-top:100px;">
            <div style="max-width:400px; margin:0 auto; background:#161b22; padding:30px; border-radius:10px; border:1px solid #30363d;">
                <h2 style="color:#fff;">Reset Password</h2>
                <p style="font-size:0.85em; color:#8b949e; margin-bottom:20px;">Enter your registered email address.</p>
                <form method="POST">
                    <input type="email" name="email" placeholder="you@example.com" required style="width:100%; padding:11px; background:#0d1117; border:1px solid #30363d; color:#fff; border-radius:6px; margin-bottom:15px; box-sizing:border-box;">
                    <button type="submit" style="width:100%; padding:11px; background:#238636; color:#fff; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Send Reset Link</button>
                </form>
                <p style="margin-top:15px;"><a href="/login" style="color:#58a6ff; font-size:0.8em; text-decoration:none;">Back to Sign In</a></p>
            </div>
        </body>
    </html>
    """)

@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE reset_token = ?", (token,)).fetchone()
        
    if not user:
        return "Invalid or expired reset token.", 400
        
    if request.method == "POST":
        new_password = request.form.get("password")
        hashed_pw = _hash(new_password)
        
        with get_db() as db:
            db.execute("UPDATE users SET password_hash = ?, reset_token = NULL WHERE reset_token = ?", (hashed_pw, token))
            db.commit()
            
        return render_template_string("""
        <html>
            <body style="background:#0d1117; color:#c9d1d9; font-family:sans-serif; text-align:center; padding-top:100px;">
                <div style="max-width:400px; margin:0 auto; background:#161b22; padding:30px; border-radius:10px; border:1px solid #30363d;">
                    <h2 style="color:#3fb950;">Password Updated!</h2>
                    <p style="font-size:0.88em; color:#8b949e;">Your password has been successfully changed.</p>
                    <a href="/login" style="display:inline-block; margin-top:15px; padding:10px 20px; background:#1f6feb; color:#fff; text-decoration:none; border-radius:6px; font-weight:bold;">Sign In Now</a>
                </div>
            </body>
        </html>
        """)
        
    return render_template_string("""
    <html>
        <body style="background:#0d1117; color:#c9d1d9; font-family:sans-serif; text-align:center; padding-top:100px;">
            <div style="max-width:400px; margin:0 auto; background:#161b22; padding:30px; border-radius:10px; border:1px solid #30363d;">
                <h2 style="color:#fff;">Set New Password</h2>
                <form method="POST">
                    <input type="password" name="password" placeholder="Enter new password" required style="width:100%; padding:11px; background:#0d1117; border:1px solid #30363d; color:#fff; border-radius:6px; margin-bottom:15px; box-sizing:border-box;">
                    <button type="submit" style="width:100%; padding:11px; background:#1f6feb; color:#fff; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Update Password</button>
                </form>
            </div>
        </body>
    </html>
    """)

def send_reset_email(to_email, reset_link):
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    
    if not all((host, user, password)):
        print("❌ SMTP Error: Missing credentials in .env file!")
        return False
        
    msg = EmailMessage()
    msg["Subject"] = "Password Reset Request — Skill Shield BTC"
    msg["From"] = os.environ.get("SMTP_FROM", "support@skillshieldbtc.com")
    msg["To"] = to_email
    msg.set_content(f"Hello,\n\nClick the link below to reset your password:\n{reset_link}\n\nIf you didn't request this, please ignore this email.")
    
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=10) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"✅ Email successfully sent to {to_email}!")
        return True
    except Exception as e:
        # 👇 This will print the exact Gmail error in your VS Code terminal!
        print(f"❌ SMTP FAILED with error: {e}")
        return False
