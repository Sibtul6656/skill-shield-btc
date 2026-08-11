import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, request, session, redirect, url_for, Response
from auth import get_db, get_user_by_id, admin_required, trial_status, _hash, TRIAL_HOURS

admin_bp = Blueprint("admin", __name__)

ADMIN_CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;font-family:'Inter','Segoe UI',sans-serif;color:#c9d1d9;padding:0}
.topbar{background:#161b22;border-bottom:1px solid #30363d;padding:14px 28px;
  display:flex;align-items:center;justify-content:space-between}
.topbar-brand{color:#fff;font-weight:900;font-size:1.1em;letter-spacing:2px}
.topbar-right{display:flex;gap:12px;align-items:center}
.badge-admin{background:rgba(255,82,82,0.15);border:1px solid rgba(255,82,82,0.3);
  color:#ff5252;padding:3px 10px;border-radius:999px;font-size:0.72em;font-weight:700}
.logout-btn{color:#8b949e;font-size:0.82em;text-decoration:none}
.logout-btn:hover{color:#fff}
.container{max-width:1400px;margin:0 auto;padding:28px 24px}
h1{color:#fff;font-size:1.4em;font-weight:800;margin-bottom:6px}
.subtitle{color:#8b949e;font-size:0.82em;margin-bottom:24px}
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:28px}
.stat-card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px}
.stat-label{color:#8b949e;font-size:0.7em;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}
.stat-val{color:#fff;font-size:1.8em;font-weight:800}
.table-wrap{background:#161b22;border:1px solid #30363d;border-radius:12px;overflow:hidden}
table{width:100%;border-collapse:collapse}
th{background:#1c2128;color:#8b949e;font-size:0.72em;text-transform:uppercase;
  letter-spacing:1px;padding:12px 16px;text-align:left;border-bottom:1px solid #30363d}
td{padding:12px 16px;font-size:0.84em;border-bottom:1px solid #21262d;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(88,166,255,0.04)}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:0.72em;font-weight:700}
.pill-trial{background:rgba(88,166,255,0.15);color:#58a6ff;border:1px solid rgba(88,166,255,0.3)}
.pill-active{background:rgba(63,185,80,0.15);color:#3fb950;border:1px solid rgba(63,185,80,0.3)}
.pill-pending{background:rgba(240,183,47,0.15);color:#f0b72f;border:1px solid rgba(240,183,47,0.3)}
.pill-expired{background:rgba(255,82,82,0.15);color:#ff5252;border:1px solid rgba(255,82,82,0.3)}
.pill-admin{background:rgba(255,82,82,0.08);color:#ff5252;border:1px solid rgba(255,82,82,0.2)}
.btn-activate{background:linear-gradient(135deg,#1f6feb,#58a6ff);color:#fff;
  border:none;border-radius:6px;padding:6px 14px;font-size:0.75em;font-weight:700;
  cursor:pointer;transition:opacity 0.2s}
.btn-activate:hover{opacity:0.85}
.btn-revoke{background:rgba(255,82,82,0.1);color:#ff5252;border:1px solid rgba(255,82,82,0.3);
  border-radius:6px;padding:6px 14px;font-size:0.75em;font-weight:700;cursor:pointer}
.tx-hash{font-family:monospace;font-size:0.75em;color:#8b949e;max-width:160px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.no-hash{color:#30363d;font-size:0.78em}
@media(max-width:900px){.stats-row{grid-template-columns:repeat(2,1fr)}}
@media(max-width:600px){.stats-row{grid-template-columns:1fr}}
</style>
"""


@admin_bp.route("/alpha-admin-portal", methods=["GET", "POST"])
@admin_required
def admin_portal():
    msg = ""
    if request.method == "POST":
        action  = request.form.get("action")
        user_id = request.form.get("user_id")
        if action == "approve_monthly":
            end_ts = (datetime.utcnow() + timedelta(days=30)).timestamp()
            with get_db() as db:
                db.execute(
                    "UPDATE users SET payment_status='Active', subscription_end_ts=?, plan_type='Monthly' WHERE id=?",
                    (end_ts, user_id),
                )
                db.commit()
            msg = "✅ Monthly plan activated (30 days)."
        elif action == "approve_yearly":
            end_ts = (datetime.utcnow() + timedelta(days=365)).timestamp()
            with get_db() as db:
                db.execute(
                    "UPDATE users SET payment_status='Active', subscription_end_ts=?, plan_type='Yearly' WHERE id=?",
                    (end_ts, user_id),
                )
                db.commit()
            msg = "✅ Yearly plan activated (365 days)."
        elif action == "revoke":
            with get_db() as db:
                db.execute(
                    "UPDATE users SET payment_status='Trial', subscription_end_ts=NULL, plan_type=NULL WHERE id=?",
                    (user_id,),
                )
                db.commit()
            msg = "⚠ Account revoked back to Trial."

    with get_db() as db:
        users = db.execute(
            "SELECT * FROM users ORDER BY signup_ts DESC"
        ).fetchall()

    total = len(users)
    active_count  = sum(1 for u in users if u["payment_status"] == "Active")
    trial_count   = sum(1 for u in users if u["payment_status"] == "Trial")
    pending_count = sum(1 for u in users if u["payment_status"] == "Pending")

    rows = ""
    for u in users:
        ts   = datetime.utcfromtimestamp(u["signup_ts"])
        ts_s = ts.strftime("%Y-%m-%d %H:%M UTC")
        expiry = ts + timedelta(hours=TRIAL_HOURS)
        now    = datetime.utcnow()

        sub_end_ts = u["subscription_end_ts"] if "subscription_end_ts" in u.keys() else None
        plan_type  = u["plan_type"]           if "plan_type"           in u.keys() else None
        referred   = u["referred_by"]         if "referred_by"         in u.keys() else None

        if u["payment_status"] == "Active":
            pill = '<span class="pill pill-active">ACTIVE</span>'
            if sub_end_ts:
                sub_end_dt = datetime.utcfromtimestamp(sub_end_ts)
                days_left  = (sub_end_dt - now).days
                plan_label = plan_type or "Paid"
                if days_left >= 0:
                    exp = f"{plan_label} · expires {sub_end_dt.strftime('%Y-%m-%d')} ({days_left}d)"
                else:
                    exp = f"{plan_label} · expired"
            else:
                exp = (plan_type or "Active") + " · no end date"
        elif u["payment_status"] == "Pending":
            pill = '<span class="pill pill-pending">PENDING</span>'
            exp  = "Awaiting verify"
        elif now >= expiry:
            pill = '<span class="pill pill-expired">EXPIRED</span>'
            exp  = "Trial expired"
        else:
            left = expiry - now
            h    = int(left.total_seconds() // 3600)
            m    = int((left.total_seconds() % 3600) // 60)
            pill = '<span class="pill pill-trial">TRIAL</span>'
            exp  = f"{h}h {m}m left"

        if u["is_admin"]:
            pill = '<span class="pill pill-admin">ADMIN</span>'

        tx_cell = (
            f'<div class="tx-hash" title="{u["tx_hash"]}">{u["tx_hash"]}</div>'
            if u["tx_hash"] else '<span class="no-hash">—</span>'
        )

        ref_cell = f'<span style="color:#79c0ff;font-size:0.8em">{referred}</span>' if referred else '<span class="no-hash">—</span>'

        actions = ""
        if not u["is_admin"]:
            if u["payment_status"] != "Active":
                actions += f"""
                <form method="POST" style="display:inline;margin-right:4px">
                  <input type="hidden" name="action" value="approve_monthly">
                  <input type="hidden" name="user_id" value="{u['id']}">
                  <button class="btn-activate" style="font-size:0.7em;padding:5px 10px" title="Grant 30 days access">✅ Monthly</button>
                </form>
                <form method="POST" style="display:inline">
                  <input type="hidden" name="action" value="approve_yearly">
                  <input type="hidden" name="user_id" value="{u['id']}">
                  <button class="btn-activate" style="font-size:0.7em;padding:5px 10px;background:linear-gradient(135deg,#238636,#3fb950)" title="Grant 365 days access">✅ Yearly</button>
                </form>"""
            else:
                actions += f"""
                <form method="POST" style="display:inline">
                  <input type="hidden" name="action" value="revoke">
                  <input type="hidden" name="user_id" value="{u['id']}">
                  <button class="btn-revoke">Revoke</button>
                </form>"""

        rows += f"""
        <tr>
          <td style="color:#fff;font-weight:600">{u["email"]}</td>
          <td style="color:#8b949e">{ts_s}</td>
          <td>{pill}</td>
          <td style="color:#79c0ff;font-size:0.82em">{exp}</td>
          <td>{tx_cell}</td>
          <td>{ref_cell}</td>
          <td>{actions}</td>
        </tr>"""

    # Support messages
    with get_db() as db:
        support_msgs = db.execute(
            "SELECT * FROM support_messages ORDER BY submitted_ts DESC"
        ).fetchall()
    support_rows = ""
    if not support_msgs:
        support_rows = '<tr><td colspan="4" style="color:#6e7681;text-align:center;padding:20px">No support messages yet.</td></tr>'
    else:
        for sm in support_msgs:
            ts_s = datetime.utcfromtimestamp(sm["submitted_ts"]).strftime("%Y-%m-%d %H:%M UTC")
            msg_preview = sm["message"][:200] + ("…" if len(sm["message"]) > 200 else "")
            support_rows += f"""
            <tr>
              <td style="color:#79c0ff"><a href="mailto:{sm['email']}" style="color:#79c0ff">{sm['email']}</a></td>
              <td style="color:#e6edf3;font-weight:600">{sm['subject']}</td>
              <td style="color:#8b949e;white-space:pre-wrap;max-width:400px">{msg_preview}</td>
              <td style="color:#6e7681">{ts_s}</td>
            </tr>"""

    msg_html = f'<div style="margin-bottom:16px;padding:10px 16px;background:rgba(63,185,80,0.1);border:1px solid rgba(63,185,80,0.3);border-radius:8px;color:#3fb950;font-size:0.84em">{msg}</div>' if msg else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Portal — SKILL SHIELD BTC</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
{ADMIN_CSS}
</head>
<body>
<div class="topbar">
  <div class="topbar-brand">🐋 SKILL SHIELD BTC · ADMIN PORTAL</div>
  <div class="topbar-right">
    <span class="badge-admin">🔴 ADMIN</span>
    <a href="/alpha-admin-portal/reset-pass" class="logout-btn">Reset Passwords</a>
    <a href="/logout" class="logout-btn">Sign Out →</a>
  </div>
</div>
<div class="container">
  <h1>User Management Dashboard</h1>
  <div class="subtitle">Manage trial users, verify TxIDs, and activate subscription access.</div>
  {msg_html}
  <div class="stats-row">
    <div class="stat-card">
      <div class="stat-label">Total Users</div>
      <div class="stat-val" style="color:#58a6ff">{total}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Active (Paid)</div>
      <div class="stat-val" style="color:#3fb950">{active_count}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">On Trial</div>
      <div class="stat-val" style="color:#58a6ff">{trial_count}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Pending Verify</div>
      <div class="stat-val" style="color:#f0b72f">{pending_count}</div>
    </div>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Email</th>
          <th>Sign-up Date</th>
          <th>Status</th>
          <th>Plan / Expiry</th>
          <th>TxID (Payment Hash)</th>
          <th>Referred By</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

  <h1 style="margin-top:36px">Support Messages</h1>
  <div class="subtitle">Contact form submissions from users — reply to their registered email.</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>From</th>
          <th>Subject</th>
          <th>Message</th>
          <th>Received</th>
        </tr>
      </thead>
      <tbody>{support_rows}</tbody>
    </table>
  </div>
</div>
</body>
</html>"""


@admin_bp.route("/alpha-admin-portal/reset-pass", methods=["GET", "POST"])
@admin_required
def admin_reset_pass():
    msg = ""
    if request.method == "POST":
        email    = request.form.get("email", "").lower().strip()
        new_pass = request.form.get("new_pass", "")
        if email and len(new_pass) >= 8:
            with get_db() as db:
                db.execute("UPDATE users SET password_hash=? WHERE email=?", (_hash(new_pass), email))
                db.commit()
            msg = f"Password updated for {email}."
        else:
            msg = "Invalid input."
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;800&display=swap" rel="stylesheet">
{ADMIN_CSS}
</head>
<body>
<div class="topbar">
  <div class="topbar-brand">🐋 SKILL SHIELD BTC · ADMIN PORTAL</div>
  <div class="topbar-right">
    <a href="/alpha-admin-portal" class="logout-btn">← Back to Portal</a>
    <a href="/logout" class="logout-btn">Sign Out →</a>
  </div>
</div>
<div class="container">
  <h1>Reset User Password</h1>
  <div class="subtitle" style="margin-bottom:24px">Manually reset any user's password.</div>
  {f'<div style="margin-bottom:16px;padding:10px 16px;background:rgba(63,185,80,0.1);border:1px solid rgba(63,185,80,0.3);border-radius:8px;color:#3fb950;font-size:0.84em">{msg}</div>' if msg else ''}
  <form method="POST" style="max-width:400px;background:#161b22;border:1px solid #30363d;border-radius:12px;padding:28px">
    <label style="display:block;color:#8b949e;font-size:0.72em;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">User Email</label>
    <input type="email" name="email" required style="display:block;width:100%;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:11px 14px;color:#fff;font-size:0.9em;font-family:inherit;margin-bottom:16px;outline:none">
    <label style="display:block;color:#8b949e;font-size:0.72em;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">New Password</label>
    <input type="password" name="new_pass" required minlength="8" style="display:block;width:100%;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:11px 14px;color:#fff;font-size:0.9em;font-family:inherit;margin-bottom:20px;outline:none">
    <button type="submit" style="background:linear-gradient(135deg,#1f6feb,#58a6ff);color:#fff;border:none;border-radius:8px;padding:12px 24px;font-size:0.9em;font-weight:700;cursor:pointer;width:100%">Update Password</button>
  </form>
</div>
</body></html>"""
