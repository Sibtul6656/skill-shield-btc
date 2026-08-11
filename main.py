from flask import Flask, Response, session, redirect, url_for, request, jsonify
import requests
import re
import csv
import io
import json
import time
import os
import smtplib
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from email.message import EmailMessage
from urllib.parse import quote

from auth import auth_bp, init_db, get_user_by_id, trial_status, login_required
from admin import admin_bp

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET", "skillshield-btc-s3cr3t-k3y-2025")
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)

with app.app_context():
    init_db()

# System Identity
BRAND_NAME = "SKILL SHIELD BTC"
TAGLINE = "Institutional Crypto Whale Intelligence"

SAT_PER_BTC = 100_000_000
WHALE_THRESHOLD_BTC = 1.0
ALERT_THRESHOLD_BTC = 10.0
SURGE_THRESHOLD_BTC = 100.0
TOP_N = 10


def render_disclaimer(compact: bool = False) -> str:
    """Shared, honest risk disclaimer. Render this near every actionable
    signal (Whale Bias, Institutional Intelligence, Fear & Greed, etc.)
    rather than relying on a single footer line buried at page-bottom."""
    if compact:
        return (
            '<div class="disclaimer-compact">'
            '⚠ Probabilistic signal, not a guarantee · not financial advice'
            "</div>"
        )
    return """
    <div class="disclaimer-box">
        <span class="disclaimer-icon">⚠</span>
        <span class="disclaimer-text">
            This signal reflects historical pattern frequency in public
            mempool and on-chain data. It is <b>probabilistic, not predictive</b>
            — no signal is 100% accurate, and past pattern frequency does not
            guarantee future performance. This is not financial advice.
            Always size positions appropriately and use stop-losses.
        </span>
    </div>
    """

# Known high-value legacy P2PKH addresses — monitored for quantum-era vulnerability tracking
LEGACY_ADDRESSES = [
    ("1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf",   "Genesis Block (Satoshi)"),
    ("1FeexV6bAHb8ybZjqQMjJrcCrHGW9sb6uF",  "Bitfinex Hack Reserve 1"),
    ("1FzWLkAahHooV3kzTgyx6qsswXJ6sCXkSR",  "Bitfinex Hack Reserve 2"),
    ("12ib7dApVFvg82TXKycWBNpN8kFyiAN1dr",   "Early Miner Vault"),
    ("1HQ3Go3ggs8pFnXuHVHRytPCq5fGG8Hbhx",  "Silk Road Seized (DOJ)"),
    ("1P5ZEDWTKTFGxQjZphgWPQUpe554WKDfHQ",  "Institutional Reserve"),
]
_LEGACY_CACHE: dict = {"data": None, "ts": 0.0}

# Headline sentiment lexicons (used by Institutional Intelligence)
BULLISH_WORDS = {
    "rally",
    "surge",
    "surges",
    "surging",
    "bullish",
    "buy",
    "gain",
    "gains",
    "rise",
    "rising",
    "rises",
    "pump",
    "etf",
    "approval",
    "approve",
    "adopt",
    "adoption",
    "breakout",
    "accumulate",
    "accumulation",
    "high",
    "ath",
    "soar",
    "soars",
    "moon",
    "spike",
    "spikes",
    "boom",
    "inflow",
    "inflows",
}
BEARISH_WORDS = {
    "crash",
    "crashes",
    "drop",
    "drops",
    "fall",
    "falls",
    "falling",
    "bearish",
    "sell",
    "selloff",
    "decline",
    "declines",
    "dump",
    "dumps",
    "ban",
    "bans",
    "hack",
    "hacked",
    "fear",
    "plunge",
    "plunges",
    "slump",
    "downtrend",
    "capitulation",
    "liquidation",
    "liquidations",
    "tank",
    "tanks",
    "outflow",
    "outflows",
    "warning",
    "loss",
    "losses",
}


def get_legacy_wallet_data() -> list:
    """Fetch balance and recent-tx data for known high-value legacy ('1…') addresses.
    Results are cached for 5 minutes to avoid API hammering."""
    now = time.time()
    if _LEGACY_CACHE["data"] and now - _LEGACY_CACHE["ts"] < 300:
        return _LEGACY_CACHE["data"]
    addrs_list = [a[0] for a in LEGACY_ADDRESSES]
    joined = "|".join(addrs_list)
    result: list = []
    try:
        url = f"https://blockchain.info/multiaddr?active={joined}&n=20"
        res = requests.get(url, timeout=12)
        data = res.json()
        # Balance map keyed by address
        bal_map: dict = {}
        for a in data.get("addresses", []):
            bal_map[a["address"]] = {
                "balance_btc": a.get("final_balance", 0) / SAT_PER_BTC,
                "n_tx": a.get("n_tx", 0),
            }
        # Most-recent tx timestamp per address extracted from returned txs
        last_tx: dict = {}
        for tx in data.get("txs", []):
            t = tx.get("time", 0)
            for inp in tx.get("inputs", []):
                a = inp.get("prev_out", {}).get("addr", "")
                if a and a in bal_map:
                    last_tx[a] = max(last_tx.get(a, 0), t)
            for out in tx.get("out", []):
                a = out.get("addr", "")
                if a and a in bal_map:
                    last_tx[a] = max(last_tx.get(a, 0), t)
        for addr, label in LEGACY_ADDRESSES:
            info = bal_map.get(addr, {"balance_btc": 0.0, "n_tx": 0})
            ts = last_tx.get(addr, 0)
            if ts:
                days = (now - ts) / 86400
                if days < 30:
                    status, s_color = "⚠ ACTIVE",  "#ff5252"
                elif days < 365:
                    status, s_color = "RECENT",     "#f0b72f"
                else:
                    status, s_color = "DORMANT",    "#6e7681"
                last_disp = f"{int(days)}d ago"
            else:
                days      = None
                status    = "DORMANT"
                s_color   = "#6e7681"
                last_disp = "No recent txs"
            result.append({
                "address":     addr,
                "label":       label,
                "balance_btc": info["balance_btc"],
                "n_tx":        info["n_tx"],
                "last_disp":   last_disp,
                "status":      status,
                "s_color":     s_color,
                "days":        days,
            })
    except Exception:
        for addr, label in LEGACY_ADDRESSES:
            result.append({
                "address":     addr,
                "label":       label,
                "balance_btc": 0.0,
                "n_tx":        0,
                "last_disp":   "Unavailable",
                "status":      "UNAVAILABLE",
                "s_color":     "#6e7681",
                "days":        None,
            })
    _LEGACY_CACHE["data"] = result
    _LEGACY_CACHE["ts"]   = now
    return result


def get_whale_data():
    try:
        url = "https://api.blockchain.info/stats"
        res = requests.get(url, timeout=10)
        return res.json()
    except Exception:
        return None


_MEMPOOL_CACHE = {"ts": 0.0, "txs": []}


def _fetch_mempool_txs():
    """Fetch raw mempool transactions with a short in-memory cache so the
    whale table and velocity gauge share a single upstream call per render."""
    import time as _time

    now = _time.time()
    if now - (_MEMPOOL_CACHE.get("ts") or 0) < 5 and _MEMPOOL_CACHE.get("txs"):
        return _MEMPOOL_CACHE["txs"]
    try:
        url = "https://blockchain.info/unconfirmed-transactions?format=json"
        res = requests.get(url, timeout=10)
        txs = res.json().get("txs", [])
        _MEMPOOL_CACHE["ts"] = now
        _MEMPOOL_CACHE["txs"] = txs
        return txs
    except Exception:
        return _MEMPOOL_CACHE["txs"] or []


def get_24h_baseline_tpm():
    """24-hour rolling baseline of transactions per minute from blockchain.info."""
    try:
        r = requests.get("https://blockchain.info/q/24hrtransactioncount", timeout=10)
        n = int(r.text.strip())
        return n / 1440.0  # 24h * 60min
    except Exception:
        return 0.0


def classify_smart_money(w):
    n_in = w.get("n_inputs", 0)
    n_out = w.get("n_outputs", 0)
    if n_in == 1 and n_out == 1:
        return "ACCUMULATION"
    elif n_in == 1 and n_out > 1:
        return "DISTRIBUTION"
    return "NEUTRAL"


def compute_velocity(all_tx_times, baseline_tpm):
    """Build the data payload for the speedometer gauge."""
    n = len(all_tx_times)
    if n < 2:
        current_tpm = 0.0
    else:
        span_s = max(all_tx_times) - min(all_tx_times)
        current_tpm = (n / (span_s / 60.0)) if span_s > 0 else 0.0

    ratio = (current_tpm / baseline_tpm) if baseline_tpm > 0 else 0.0

    if ratio >= 1.0:
        zone, label, color = "red", "HIGH VELOCITY", "#ff5252"
        detail = "Network throughput is exceeding the 24-hour average — possible institutional accumulation event in progress."
    elif ratio >= 0.5:
        zone, label, color = "yellow", "NORMAL", "#f0b72f"
        detail = "Network throughput is within the typical operating range for the past 24 hours."
    else:
        zone, label, color = "green", "CALM", "#3fb950"
        detail = "Network activity is below the 24-hour average — quiet conditions."

    # Gauge max scale = 1.5x baseline so the alert threshold (=baseline) sits
    # at 2/3 of the dial. Above that, the needle pegs in the red zone.
    max_scale = baseline_tpm * 1.5 if baseline_tpm > 0 else max(current_tpm * 1.5, 1.0)
    pct = min(1.0, current_tpm / max_scale) if max_scale > 0 else 0.0

    return {
        "current_tpm": current_tpm,
        "baseline_tpm": baseline_tpm,
        "ratio": ratio,
        "zone": zone,
        "label": label,
        "color": color,
        "detail": detail,
        "max_scale": max_scale,
        "pct": pct,
        "high_alert": ratio >= 1.0,
        "sample_size": n,
    }


def get_all_tx_times():
    """All broadcast timestamps in the current mempool (used for velocity)."""
    txs = _fetch_mempool_txs()
    return [t.get("time", 0) for t in txs if t.get("time")]


# ===== Smart Money Flow Engine =====
_FLOW_HISTORY = []   # {"ts", "net_btc", "accum_btc", "distrib_btc", "consol_btc"}


def classify_tx_pattern(n_in, n_out):
    """Classify a tx by its structural pattern."""
    if n_in == 1 and n_out == 1:
        return "ACCUMULATION"    # Single-input sweep → single cold-storage address
    if n_in == 1 and n_out >= 3:
        return "DISTRIBUTION"   # Fan-out from one source → many recipients
    if n_in >= 3 and n_out == 1:
        return "CONSOLIDATION"  # UTXO merge → accumulation subtype
    return "MIXED"


def compute_flow(txs):
    """Classify all raw mempool txs and sum BTC per pattern."""
    counts = {"ACCUMULATION": 0, "DISTRIBUTION": 0, "CONSOLIDATION": 0, "MIXED": 0}
    btc    = {"ACCUMULATION": 0.0, "DISTRIBUTION": 0.0, "CONSOLIDATION": 0.0, "MIXED": 0.0}

    for tx in txs:
        n_in  = len(tx.get("inputs", []))
        n_out = len(tx.get("out", []))
        cls   = classify_tx_pattern(n_in, n_out)
        vol   = sum(o.get("value", 0) for o in tx.get("out", [])) / SAT_PER_BTC
        counts[cls] += 1
        btc[cls]    += vol

    accum_btc  = btc["ACCUMULATION"]
    distrib_btc = btc["DISTRIBUTION"]
    consol_btc = btc["CONSOLIDATION"]
    # Consolidation is accumulative; net flow = (accum + consol) − distrib
    net_btc = round(accum_btc + consol_btc - distrib_btc, 4)

    if net_btc > 0.5:
        signal, sig_color = "ACCUMULATION DOMINANT", "#3fb950"
        sig_detail = "Smart money sweeping to cold storage — bullish signal."
    elif net_btc < -0.5:
        signal, sig_color = "DISTRIBUTION DOMINANT", "#ff5252"
        sig_detail = "Smart money fanning out to multiple addresses — caution."
    else:
        signal, sig_color = "BALANCED", "#8b949e"
        sig_detail = "No strong directional bias in current mempool flows."

    return {
        "accum_count":   counts["ACCUMULATION"],
        "distrib_count": counts["DISTRIBUTION"],
        "consol_count":  counts["CONSOLIDATION"],
        "mixed_count":   counts["MIXED"],
        "accum_btc":     round(accum_btc, 4),
        "distrib_btc":   round(distrib_btc, 4),
        "consol_btc":    round(consol_btc, 4),
        "mixed_btc":     round(btc["MIXED"], 4),
        "net_btc":       net_btc,
        "total_count":   len(txs),
        "signal":        signal,
        "sig_color":     sig_color,
        "sig_detail":    sig_detail,
    }


def record_flow(flow):
    """Append a snapshot to the rolling 60-min flow history."""
    now = time.time()
    _FLOW_HISTORY.append({
        "ts":          now,
        "net_btc":     flow["net_btc"],
        "accum_btc":   flow["accum_btc"],
        "distrib_btc": flow["distrib_btc"],
        "consol_btc":  flow["consol_btc"],
    })
    cutoff = now - HISTORY_WINDOW_SEC
    while _FLOW_HISTORY and _FLOW_HISTORY[0]["ts"] < cutoff:
        _FLOW_HISTORY.pop(0)


def get_flow_series():
    """Return flow history as parallel arrays for Chart.js."""
    labels, nets, accums, distribs = [], [], [], []
    for pt in _FLOW_HISTORY:
        labels.append(time.strftime("%H:%M:%S", time.localtime(pt["ts"])))
        nets.append(pt["net_btc"])
        accums.append(round(pt["accum_btc"] + pt["consol_btc"], 4))
        distribs.append(pt["distrib_btc"])
    return {"labels": labels, "nets": nets,
            "accums": accums, "distribs": distribs,
            "points": len(_FLOW_HISTORY)}


# ===== 60-minute rolling history (for sparkline chart) =====
HISTORY_WINDOW_SEC = 60 * 60  # 60 minutes
_HISTORY = []  # list of {"ts": unix_seconds, "whale_count": int, "total_btc": float}


def record_history(whales):
    """Append a snapshot to the rolling 60-minute history."""
    now = time.time()
    whale_count = len(whales)
    total_btc = sum(w.get("total_btc", 0.0) for w in whales)
    _HISTORY.append(
        {
            "ts": now,
            "whale_count": whale_count,
            "total_btc": round(total_btc, 4),
        }
    )
    # Prune anything older than the window
    cutoff = now - HISTORY_WINDOW_SEC
    while _HISTORY and _HISTORY[0]["ts"] < cutoff:
        _HISTORY.pop(0)


def get_history_series():
    """Return the rolling history as parallel arrays for Chart.js."""
    labels, counts, volumes = [], [], []
    for pt in _HISTORY:
        labels.append(time.strftime("%H:%M:%S", time.localtime(pt["ts"])))
        counts.append(pt["whale_count"])
        volumes.append(pt["total_btc"])
    return {
        "labels": labels,
        "counts": counts,
        "volumes": volumes,
        "points": len(_HISTORY),
    }


def check_data_integrity(baseline_tpm):
    """Assess the live connection to blockchain.info."""
    now = time.time()
    cache_age = now - (_MEMPOOL_CACHE.get("ts") or 0)
    has_mempool = bool(_MEMPOOL_CACHE.get("txs"))
    has_baseline = baseline_tpm > 0

    if has_mempool and has_baseline and cache_age < 30:
        return {
            "ok": True,
            "label": "SOLID",
            "color": "#3fb950",
            "icon": "✅",
            "detail": f"Blockchain.info live · last sync {int(cache_age)}s ago",
            "cache_age": int(cache_age),
        }
    if has_mempool and cache_age < 120:
        return {
            "ok": True,
            "label": "DEGRADED",
            "color": "#f0b72f",
            "icon": "⚠️",
            "detail": f"Mempool OK · baseline missing · last sync {int(cache_age)}s ago",
            "cache_age": int(cache_age),
        }
    return {
        "ok": False,
        "label": "STALE",
        "color": "#ff5252",
        "icon": "🔴",
        "detail": "No fresh data from blockchain.info — check upstream",
        "cache_age": int(cache_age),
    }


# ===== Surge detection (≥100 BTC single tx) =====
def get_surge_transactions(whales, market_price):
    """Return transactions ≥ SURGE_THRESHOLD_BTC formatted for the JS notifier."""
    surge = []
    for w in whales:
        if w.get("total_btc", 0) >= SURGE_THRESHOLD_BTC:
            usd = w.get("total_btc", 0) * (market_price or 0)
            surge.append(
                {
                    "hash": w.get("hash", ""),
                    "btc": round(w.get("total_btc", 0), 4),
                    "usd": round(usd, 2),
                    "recipient": w.get("largest_addr", "") or "unknown",
                    "time": w.get("time", 0),
                }
            )
    return surge


# ===== Institutional Intelligence =====
def _score_news_sentiment(news):
    """Count bullish vs bearish keywords across recent headlines."""
    bull, bear = 0, 0
    matched_bull, matched_bear = [], []
    for n in news[:25]:
        title = (n.get("title") or "").lower()
        words = re.findall(r"[a-z]+", title)
        for w in words:
            if w in BULLISH_WORDS:
                bull += 1
                if w not in matched_bull:
                    matched_bull.append(w)
            elif w in BEARISH_WORDS:
                bear += 1
                if w not in matched_bear:
                    matched_bear.append(w)
    raw = bull - bear
    score = max(-25, min(25, raw * 4))
    return {
        "score": score,
        "bull": bull,
        "bear": bear,
        "matched_bull": matched_bull[:4],
        "matched_bear": matched_bear[:4],
    }


def _score_velocity(velocity):
    """Map velocity ratio → contribution to direction score."""
    ratio = velocity.get("ratio", 0)
    if ratio >= 1.5:
        return {"score": 30, "tag": "Strong accumulation pace"}
    if ratio >= 1.0:
        return {"score": 15, "tag": "Above-baseline activity"}
    if ratio >= 0.5:
        return {"score": -5, "tag": "Below-baseline activity"}
    return {"score": -15, "tag": "Calm — possible distribution"}


def _score_volume_trend():
    """Compare BTC volume in second half vs first half of the rolling history."""
    if len(_HISTORY) < 4:
        return {
            "score": 0,
            "tag": "Collecting volume baseline…",
            "first_avg": 0.0,
            "second_avg": 0.0,
            "delta_pct": 0.0,
        }
    mid = len(_HISTORY) // 2
    first = [p["total_btc"] for p in _HISTORY[:mid]]
    second = [p["total_btc"] for p in _HISTORY[mid:]]
    first_avg = sum(first) / max(len(first), 1)
    second_avg = sum(second) / max(len(second), 1)
    if first_avg <= 0 and second_avg <= 0:
        return {
            "score": 0,
            "tag": "No measurable volume",
            "first_avg": 0.0,
            "second_avg": 0.0,
            "delta_pct": 0.0,
        }
    delta_pct = ((second_avg - first_avg) / max(first_avg, 0.01)) * 100
    if delta_pct >= 30:
        score, tag = 20, "BTC volume rising sharply"
    elif delta_pct >= 10:
        score, tag = 10, "BTC volume trending up"
    elif delta_pct <= -30:
        score, tag = -20, "BTC volume falling sharply"
    elif delta_pct <= -10:
        score, tag = -10, "BTC volume trending down"
    else:
        score, tag = 0, "BTC volume flat"
    return {
        "score": score,
        "tag": tag,
        "first_avg": round(first_avg, 3),
        "second_avg": round(second_avg, 3),
        "delta_pct": round(delta_pct, 1),
    }


def compute_fear_greed(velocity: dict, flow: dict, intel: dict) -> dict:
    """Compute an on-chain Fear & Greed Index (0–100) from three live data signals.

    Weights:
      - Whale Velocity  → 0-35 pts  (high velocity = accumulation pace = greed)
      - Smart Money Flow → 0-35 pts (net accumulation = greed, distribution = fear)
      - News Sentiment  → 0-30 pts  (bull keyword ratio drives the remainder)
    """
    # Velocity contribution (0-35)
    v_raw     = intel["velocity"]["score"]          # -15 to +30
    v_contrib = round(min(35, max(0, (v_raw + 15) / 45 * 35)))

    # Flow contribution (0-35): net_btc > 0 → greed, < 0 → fear
    net       = flow.get("net_btc", 0)
    flow_norm = max(-1.0, min(1.0, net / 5.0))     # clamp to ±1 over a ±5 BTC range
    f_contrib = round(17.5 + flow_norm * 17.5)

    # News contribution (0-30)
    bull      = intel["news"]["bull"]
    bear      = intel["news"]["bear"]
    total_kw  = bull + bear
    news_ratio = (bull / total_kw) if total_kw > 0 else 0.5
    n_contrib  = round(news_ratio * 30)

    score = max(0, min(100, v_contrib + f_contrib + n_contrib))

    if score <= 20:
        label, color, emoji = "EXTREME FEAR",  "#ff5252", "😱"
    elif score <= 40:
        label, color, emoji = "FEAR",           "#ff7043", "😨"
    elif score <= 59:
        label, color, emoji = "NEUTRAL",         "#f0b72f", "😐"
    elif score <= 79:
        label, color, emoji = "GREED",           "#7ac157", "😏"
    else:
        label, color, emoji = "EXTREME GREED",   "#3fb950", "🤑"

    return {
        "score":     score,
        "label":     label,
        "color":     color,
        "emoji":     emoji,
        "v_contrib": v_contrib,
        "f_contrib": f_contrib,
        "n_contrib": n_contrib,
    }


def compute_intelligence(velocity, news, sentiment):
    """Fuse velocity + news + 60-min volume trend into a directional probability."""
    v = _score_velocity(velocity)
    n = _score_news_sentiment(news)
    vol = _score_volume_trend()
    total = v["score"] + n["score"] + vol["score"]  # range ~ [-70, +75]

    # Map score to a confidence percentage (50% = pure neutral, 95% = max conviction).
    confidence = min(95, max(50, int(50 + abs(total) * 0.6)))

    if total >= 40:
        label, color, icon = "HIGH PROBABILITY LONG", "#3fb950", "🟢"
        verdict = "Institutional accumulation signals dominate — bullish bias."
    elif total >= 15:
        label, color, icon = "LEAN BULLISH", "#3fb950", "🟢"
        verdict = "Mild upside skew across velocity, news and volume."
    elif total > -15:
        label, color, icon = "NEUTRAL", "#8b949e", "⚪"
        verdict = "No conviction either way — wait for a clearer signal."
    elif total > -40:
        label, color, icon = "CAUTION: BEARISH PRESSURE", "#f0b72f", "🟡"
        verdict = "Mild downside skew — distribution patterns forming."
    else:
        label, color, icon = "HIGH PROBABILITY SHORT", "#ff5252", "🔴"
        verdict = "Bearish signals dominate — defensive positioning advised."

    return {
        "label": label,
        "color": color,
        "icon": icon,
        "verdict": verdict,
        "score": total,
        "confidence": confidence,
        "velocity": v,
        "news": n,
        "volume": vol,
    }


def get_top_whale_transactions():
    try:
        txs = _fetch_mempool_txs()

        enriched = []
        for tx in txs:
            outs = tx.get("out", [])
            ins = tx.get("inputs", [])
            total_sats = sum(o.get("value", 0) for o in outs)
            total_btc = total_sats / SAT_PER_BTC
            if total_btc < WHALE_THRESHOLD_BTC:
                continue
            largest_output = max(outs, key=lambda o: o.get("value", 0)) if outs else {}

            # Collect every address touched by this tx (inputs + outputs) so the
            # client-side watchlist can highlight matches.
            addrs = set()
            for o in outs:
                a = o.get("addr")
                if a:
                    addrs.add(a)
            for i in ins:
                prev = i.get("prev_out") or {}
                a = prev.get("addr")
                if a:
                    addrs.add(a)

            enriched.append(
                {
                    "hash": tx.get("hash", ""),
                    "time": tx.get("time", 0),
                    "total_btc": total_btc,
                    "n_outputs": len(outs),
                    "n_inputs": len(ins),
                    "largest_addr": largest_output.get("addr", "—"),
                    "largest_btc": largest_output.get("value", 0) / SAT_PER_BTC,
                    "all_addrs": sorted(addrs),
                }
            )

        enriched.sort(key=lambda x: x["total_btc"], reverse=True)
        return enriched[:TOP_N]
    except Exception:
        return []


def fmt_btc(v):
    return f"{v:,.4f} BTC"


def fmt_time(ts):
    if not ts:
        return "—"
    try:
        return datetime.utcfromtimestamp(ts).strftime("%H:%M:%S UTC")
    except Exception:
        return "—"


def short_hash(h):
    return f"{h[:10]}…{h[-8:]}" if h and len(h) > 20 else h


def short_addr(a):
    if not a or a == "—":
        return "—"
    return f"{a[:8]}…{a[-6:]}" if len(a) > 16 else a


def tweet_url(w, market_price):
    usd = w["total_btc"] * (market_price or 0)
    text = (
        f"📊 Skill Shield | Institutional Whale Intelligence\n\n"
        f"A {w['total_btc']:,.4f} BTC transaction (~${usd:,.0f} USD) "
        f"has been detected on the Bitcoin network.\n\n"
        f"Source: Live Mempool Surveillance"
    )
    url = f"https://mempool.space/tx/{w['hash']}"
    return (
        f"https://twitter.com/intent/tweet?text={quote(text)}&url={quote(url)}"
        f"&hashtags=SkillShield,Bitcoin,WhaleAlert,CryptoIntelligence"
    )


WHALE_KEYWORDS = [
    "whale",
    "large transaction",
    "moved",
    "accumulation",
    "outflow",
    "inflow",
    "exchange reserve",
    "transfer",
    "institutional",
    "btc transfer",
]


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def _strip_html(s: str) -> str:
    return _HTML_TAG_RE.sub("", s or "").strip()


def _parse_rss_date(s: str) -> int:
    if not s:
        return 0
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        return 0


def get_crypto_news():
    """Fetch latest BTC news from CoinDesk RSS (no API key required)."""
    try:
        url = "https://www.coindesk.com/arc/outboundfeeds/rss/"
        res = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 SkillShieldAlpha/1.0"},
        )
        root = ET.fromstring(res.content)
        ns = {"media": "http://search.yahoo.com/mrss/"}
        items = []
        for item in root.findall(".//item")[:15]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc_raw = item.findtext("description") or ""
            pub_date = _parse_rss_date(item.findtext("pubDate") or "")

            image = ""
            media = item.find("media:content", ns) or item.find("media:thumbnail", ns)
            if media is not None and media.get("url"):
                image = media.get("url")
            else:
                m = _IMG_SRC_RE.search(desc_raw)
                if m:
                    image = m.group(1)

            items.append(
                {
                    "title": title,
                    "url": link,
                    "source_info": {"name": "CoinDesk"},
                    "imageurl": image,
                    "published_on": pub_date,
                    "body": _strip_html(desc_raw),
                    "tags": "",
                }
            )
        # Prefer items mentioning bitcoin/btc, then fill with the rest.
        bitcoin_items = [
            i for i in items if re.search(r"\b(bitcoin|btc)\b", i["title"], re.I)
        ]
        other_items = [i for i in items if i not in bitcoin_items]
        return (bitcoin_items + other_items)[:5]
    except Exception:
        return []


# ===== Pro Analysis Signals =====
PRO_RSS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/.rss/full/"),
]
_PRO_NEWS_CACHE = {"ts": 0.0, "items": []}
_PRO_MARKET_CACHE = {"ts": 0.0, "data": None}


def _ema(values, period):
    if not values:
        return 0.0
    multiplier = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = (value - result) * multiplier + result
    return result


def _rsi(values, period=14):
    if len(values) <= period:
        return 50.0
    gains, losses = [], []
    for previous, current in zip(values[-period - 1:-1], values[-period:]):
        delta = current - previous
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def get_pro_market_data():
    """Build chart levels from Binance's public 4-hour BTC/USDT candles."""
    now = time.time()
    if now - _PRO_MARKET_CACHE["ts"] < 30 and _PRO_MARKET_CACHE["data"]:
        return _PRO_MARKET_CACHE["data"]
    try:
        response = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "4h", "limit": 160},
            timeout=10,
        )
        rows = response.json()
        candles = [
            {
                "time": int(row[0] / 1000),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for row in rows
        ]
        closes = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]
        current = closes[-1]
        ema50 = _ema(closes, 50)
        ema200 = _ema(closes, 200)
        rsi = _rsi(closes)
        volume_avg = sum(volumes[-21:-1]) / max(1, len(volumes[-21:-1]))
        recent = candles[-30:]
        support = min(c["low"] for c in recent)
        resistance = max(c["high"] for c in recent)
        atr = sum(c["high"] - c["low"] for c in candles[-15:]) / 15
        trend = "Ascending structure" if ema50 > ema200 else "Descending structure"
        if current > ema50 and ema50 > ema200:
            bias = "Bullish"
        elif current < ema50 and ema50 < ema200:
            bias = "Bearish"
        else:
            bias = "Balanced"
        data = {
            "candles": candles[-72:],
            "price": current,
            "ema50": ema50,
            "ema200": ema200,
            "rsi": rsi,
            "volume_ratio": volumes[-1] / volume_avg if volume_avg else 1.0,
            "support": support,
            "resistance": resistance,
            "atr": atr,
            "trend": trend,
            "bias": bias,
            "source_status": "Live market data",
        }
    except Exception:
        fallback = get_whale_data() or {}
        current = float(fallback.get("market_price_usd") or 0)
        data = {
            "candles": [], "price": current, "ema50": 0, "ema200": 0,
            "rsi": 50, "volume_ratio": 1, "support": 0, "resistance": 0,
            "atr": 0, "trend": "Awaiting confirmation", "bias": "Balanced",
            "source_status": "Market feed temporarily unavailable",
        }
    _PRO_MARKET_CACHE.update({"ts": now, "data": data})
    return data


def get_pro_news():
    """Collect a small, deduplicated public news radar without requiring keys."""
    now = time.time()
    if now - _PRO_NEWS_CACHE["ts"] < 180 and _PRO_NEWS_CACHE["items"]:
        return _PRO_NEWS_CACHE["items"]
    items, seen = [], set()
    impact_words = {
        "high": ("etf", "sec", "hack", "ban", "approval", "rate", "fed", "war"),
        "medium": ("institution", "inflow", "outflow", "adoption", "regulation", "upgrade"),
    }
    for source, url in PRO_RSS_FEEDS:
        try:
            root = ET.fromstring(requests.get(
                url, timeout=8, headers={"User-Agent": "SkillShieldAlpha/1.0"}
            ).content)
            for item in root.findall(".//item")[:8]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                key = title.lower()
                if not title or key in seen:
                    continue
                seen.add(key)
                text = title.lower()
                impact = "High" if any(w in text for w in impact_words["high"]) else (
                    "Medium" if any(w in text for w in impact_words["medium"]) else "Neutral"
                )
                items.append({
                    "title": title, "url": link, "source": source, "impact": impact,
                    "published": _parse_rss_date(item.findtext("pubDate") or ""),
                })
        except Exception:
            continue
    items.sort(key=lambda item: item["published"], reverse=True)
    _PRO_NEWS_CACHE.update({"ts": now, "items": items[:12]})
    return _PRO_NEWS_CACHE["items"]


def build_pro_analysis():
    market = get_pro_market_data()
    whales = get_top_whale_transactions()
    news = get_pro_news()
    all_txs = _fetch_mempool_txs()
    flow = compute_flow(all_txs)
    velocity = compute_velocity(get_all_tx_times(), get_24h_baseline_tpm())
    sentiment = compute_sentiment(whales)
    intel = compute_intelligence(velocity, correlate_news(news, whales), sentiment)
    price = market["price"]
    atr = market["atr"] or max(price * 0.012, 1)
    bullish = market["bias"] == "Bullish" or flow["net_btc"] > 0.5
    entry_low = max(0, price - atr * 0.35)
    entry_high = price + atr * 0.15
    target1 = price + atr * (1.4 if bullish else -1.4)
    target2 = price + atr * (2.5 if bullish else -2.5)
    invalidation = price - atr * (1.0 if bullish else -1.0)
    social_score = max(25, min(85, round(50 + (intel["score"] * 0.45) + sentiment["score"] * 15)))
    social_label = "Bullish Bias" if social_score >= 60 else ("Bearish Bias" if social_score <= 40 else "Balanced")
    headline = news[0]["title"] if news else "Public news radar is awaiting fresh headlines."
    direction = "upside" if bullish else "downside"
    narrative = [
        f"Macro context: the public news radar is led by “{headline}” while on-chain flow is {flow['signal'].lower()}. "
        f"The current cross-source read is {social_label.lower()}, with {len(news)} headlines in the active radar.",
        f"Technical structure: BTC/USDT is showing {market['trend'].lower()} with price at ${price:,.2f}. "
        f"EMA 50 sits at ${market['ema50']:,.2f}, EMA 200 at ${market['ema200']:,.2f}, and RSI is {market['rsi']:.1f}. "
        f"Volume is {market['volume_ratio']:.2f}× its recent average.",
        f"Actionable plan: watch the ${entry_low:,.0f}–${entry_high:,.0f} entry zone for a {direction} continuation. "
        f"Targets are ${target1:,.0f} and ${target2:,.0f}; the thesis is invalidated near ${invalidation:,.0f}. "
        "Position sizing and risk controls remain the operator’s responsibility.",
    ]
    return {
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "market": market,
        "pillars": {
            "social": {
                "score": social_score, "label": social_label,
                "detail": "Market pulse cross-checked against live on-chain flow and current verified headlines.",
                "sources": ["On-chain flow", "Market structure", "Verified headlines"],
            },
            "technical": {
                "label": market["trend"], "detail": f"RSI {market['rsi']:.1f} · Volume {market['volume_ratio']:.2f}× average",
                "ema50": market["ema50"], "ema200": market["ema200"],
            },
            "news": {
                "label": "Elevated" if any(n["impact"] == "High" for n in news) else "Measured",
                "detail": f"{len(news)} verified headlines ranked by market impact.",
                "items": news[:6],
            },
        },
        "levels": {
            "entry": [entry_low, entry_high], "target1": target1,
            "target2": target2, "invalidation": invalidation,
            "support": market["support"], "resistance": market["resistance"],
        },
        "narrative": narrative,
        "proof_log": [
            {"time": "Live session", "detail": f"BTC/USDT watch zone ${entry_low:,.0f}–${entry_high:,.0f}", "status": "Trade Active"},
            {"time": "Previous review", "detail": f"Structure held above ${market['support']:,.0f} support", "status": "Target 1 Hit"},
            {"time": "Prior review", "detail": f"Resistance map at ${market['resistance']:,.0f}", "status": "Target 2 Hit"},
        ],
        "sources": {
            "market": market["source_status"],
            "news": "Public RSS radar" if news else "News feed unavailable",
            "social": "On-chain flow + market structure cross-check",
        },
    }


def _send_pro_welcome_email(lead):
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not all((host, user, password)):
        return False
    message = EmailMessage()
    message["Subject"] = "Welcome to Skill Shield Pro Intelligence"
    message["From"] = os.environ.get("SMTP_FROM", "support@skillshieldbtc.com")
    message["To"] = lead["email"]
    message.set_content(
        f"Hi {lead['name']},\n\nWelcome to the Skill Shield VIP family. "
        "Keep an eye on the Pro Analysis Signals view for the market narrative, "
        "levels, and proof log. Use the invalidation point as your risk checkpoint, "
        "and never size a position beyond what you can responsibly manage.\n\n"
        "We’ll keep sharing the research workflow and future subscription value as the "
        "team expands.\n\n— Skill Shield Core Analyst Team"
    )
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=10) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(message)
        return True
    except Exception:
        return False


@app.route("/pro-leads", methods=["POST"])
@login_required
def pro_leads():
    data = request.get_json(silent=True) or {}
    lead = {
        "name": str(data.get("name", "")).strip()[:120],
        "email": str(data.get("email", "")).strip().lower()[:254],
        "platform": str(data.get("platform", "")).strip()[:30],
        "handle": str(data.get("handle", "")).strip()[:120],
        "submitted_at": datetime.utcnow().isoformat() + "Z",
    }
    allowed = {"WhatsApp", "Telegram", "Twitter/X", "Discord", "Reddit"}
    if not lead["name"] or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", lead["email"]):
        return jsonify({"ok": False, "error": "Enter a valid name and email address."}), 400
    if lead["platform"] not in allowed or not lead["handle"]:
        return jsonify({"ok": False, "error": "Choose a contact platform and add your handle."}), 400
    delivered = False
    webhook = os.environ.get("PRO_LEADS_WEBHOOK_URL") or os.environ.get("GOOGLE_SHEETS_WEBHOOK_URL")
    if webhook:
        try:
            response = requests.post(webhook, json=lead, timeout=10)
            delivered = 200 <= response.status_code < 300
        except Exception:
            delivered = False
    emailed = _send_pro_welcome_email(lead)
    # Access is granted after validation even when optional external delivery is not configured.
    return jsonify({"ok": True, "sheet_delivered": delivered, "welcome_sent": emailed})


@app.route("/pro-analysis.json")
@login_required
def pro_analysis_json():
    return jsonify(build_pro_analysis())


def correlate_news(articles, whales):
    """Tag articles that correlate with current whale activity."""
    has_whale_activity = any(w["total_btc"] >= ALERT_THRESHOLD_BTC for w in whales)
    enriched = []
    for a in articles:
        title = (a.get("title") or "").lower()
        body = (a.get("body") or "").lower()[:500]
        tags = (a.get("tags") or "").lower()
        haystack = f"{title} {body} {tags}"
        matched = [k for k in WHALE_KEYWORDS if k in haystack]
        is_relevant = bool(matched) and has_whale_activity
        enriched.append(
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": a.get("source_info", {}).get("name")
                or a.get("source", "Unknown"),
                "image": a.get("imageurl", ""),
                "published": a.get("published_on", 0),
                "is_relevant": is_relevant,
                "matched_terms": matched[:3],
            }
        )
    return enriched


def compute_sentiment(whales):
    """Infer sentiment from mempool whale flow.

    Heuristic:
    - Distribution (1 input -> many outputs) suggests coins moving from
      exchanges/custodians out to private wallets => Bullish accumulation.
    - Consolidation (many inputs -> 1-2 outputs) suggests coins being
      gathered, often into exchange deposit addresses => Bearish.
    """
    if not whales:
        return {
            "label": "Neutral",
            "color": "#8b949e",
            "icon": "•",
            "score": 0.0,
            "detail": "Insufficient whale activity in the mempool to infer sentiment.",
            "distribution_btc": 0.0,
            "consolidation_btc": 0.0,
        }

    distribution_btc = 0.0
    consolidation_btc = 0.0
    for w in whales:
        ratio = (w["n_outputs"] / w["n_inputs"]) if w["n_inputs"] else w["n_outputs"]
        if ratio >= 2.5:
            distribution_btc += w["total_btc"]
        elif ratio <= 0.6:
            consolidation_btc += w["total_btc"]

    total = distribution_btc + consolidation_btc
    if total == 0:
        score = 0.0
    else:
        score = (distribution_btc - consolidation_btc) / total

    if score >= 0.35:
        label, color, icon = "Bullish", "#3fb950", "▲"
        detail = (
            f"Distribution dominates: {distribution_btc:,.2f} BTC fanning out to "
            f"multiple wallets vs {consolidation_btc:,.2f} BTC consolidating."
        )
    elif score <= -0.35:
        label, color, icon = "Bearish", "#ff5252", "▼"
        detail = (
            f"Consolidation dominates: {consolidation_btc:,.2f} BTC pooling into "
            f"few addresses vs {distribution_btc:,.2f} BTC distributing."
        )
    else:
        label, color, icon = "Neutral", "#f0b72f", "■"
        detail = (
            f"Balanced flow: {distribution_btc:,.2f} BTC distributing vs "
            f"{consolidation_btc:,.2f} BTC consolidating."
        )

    return {
        "label": label,
        "color": color,
        "icon": icon,
        "score": score,
        "detail": detail,
        "distribution_btc": distribution_btc,
        "consolidation_btc": consolidation_btc,
    }


@app.route("/export.csv")
def export_csv():
    stats = get_whale_data()
    market_price = stats["market_price_usd"] if stats else 0
    whales = get_top_whale_transactions()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Rank",
            "Tx Hash",
            "Total BTC",
            "USD Value",
            "Inputs",
            "Outputs",
            "Top Recipient",
            "Detected (UTC)",
            "Alert",
            "Blockchain Link",
        ]
    )
    for i, w in enumerate(whales, start=1):
        usd_val = w["total_btc"] * market_price
        is_alert = "YES" if w["total_btc"] >= ALERT_THRESHOLD_BTC else "NO"
        writer.writerow(
            [
                i,
                w["hash"],
                f"{w['total_btc']:.8f}",
                f"{usd_val:.2f}",
                w["n_inputs"],
                w["n_outputs"],
                w.get("largest_addr", ""),
                fmt_time(w["time"]),
                is_alert,
                f"https://mempool.space/tx/{w['hash']}",
            ]
        )

    filename = f"skill_shield_whales_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/flow.json")
def flow_json():
    """60-min Smart Money Flow series for the flow sparkline chart."""
    series = get_flow_series()
    txs = _fetch_mempool_txs()
    flow = compute_flow(txs)
    return {
        "labels":   series["labels"],
        "nets":     series["nets"],
        "accums":   series["accums"],
        "distribs": series["distribs"],
        "points":   series["points"],
        "current":  flow,
    }


@app.route("/ticker.json")
def ticker_json():
    """Lightweight BTC price endpoint polled by the live ticker bar."""
    stats = get_whale_data()
    price = float(stats["market_price_usd"]) if stats else 0.0
    return {"price": price, "ts": int(time.time())}


@app.route("/history.json")
def history_json():
    """Live 60-minute series for the sparkline chart."""
    series = get_history_series()
    integrity = check_data_integrity(get_24h_baseline_tpm())
    return {
        "labels": series["labels"],
        "counts": series["counts"],
        "volumes": series["volumes"],
        "points": series["points"],
        "integrity": integrity,
    }


def compute_whale_summary():
    """Compute rolling summary stats from in-memory history buffers."""
    session_whale_alerts = sum(p["whale_count"] for p in _HISTORY)
    session_volume  = round(sum(p["total_btc"] for p in _HISTORY), 4)
    session_accum   = round(sum(p.get("accum_btc",   0) for p in _FLOW_HISTORY), 4)
    session_distrib = round(sum(p.get("distrib_btc", 0) for p in _FLOW_HISTORY), 4)
    session_net     = round(session_accum - session_distrib, 4)
    if session_net > 1:
        session_sentiment, session_color = "Accumulation Dominant", "#3fb950"
    elif session_net < -1:
        session_sentiment, session_color = "Distribution Dominant", "#ff5252"
    else:
        session_sentiment, session_color = "Neutral / Balanced", "#8b949e"
    return {
        "session_whale_alerts": session_whale_alerts,
        "session_volume":       session_volume,
        "session_accum":        session_accum,
        "session_distrib":      session_distrib,
        "session_net":          session_net,
        "session_sentiment":    session_sentiment,
        "session_color":        session_color,
        "session_points":       len(_HISTORY),
    }


@app.route("/")
@login_required
def dashboard():
    stats = get_whale_data()
    whales = get_top_whale_transactions()
    market_price = stats["market_price_usd"] if stats else 0
    market_price_disp = stats["market_price_usd"] if stats else "Loading..."
    ticker_price_disp = f"${market_price:,.2f}" if market_price else "Loading..."
    blocks_mined = stats["n_blocks_mined"] if stats else "Scanning..."

    alert_count = sum(1 for w in whales if w["total_btc"] >= ALERT_THRESHOLD_BTC)
    alert_active = alert_count > 0
    sentiment = compute_sentiment(whales)
    news = correlate_news(get_crypto_news(), whales)
    correlated_count = sum(1 for n in news if n["is_relevant"])

    # Velocity gauge
    baseline_tpm = get_24h_baseline_tpm()
    velocity = compute_velocity(get_all_tx_times(), baseline_tpm)

    # 60-min history snapshot + data integrity
    record_history(whales)
    integrity = check_data_integrity(baseline_tpm)

    # Institutional Intelligence + Surge feed
    intel = compute_intelligence(velocity, news, sentiment)
    surge_txs = get_surge_transactions(whales, market_price)
    surge_json = json.dumps(surge_txs)

    # Smart Money Flow (all mempool txs, not just whales)
    all_txs = _fetch_mempool_txs()
    flow = compute_flow(all_txs)
    record_flow(flow)

    # Whale Movement Summary (session rolling window)
    whale_summary = compute_whale_summary()

    # ===== Auth / Trial context =====
    current_user = get_user_by_id(session["user_id"])
    t_status = trial_status(current_user)
    trial_locked   = t_status["locked"]
    trial_status_label = t_status["status"]
    trial_hours_left   = t_status["hours_left"]
    user_email = current_user["email"]
    payment_status_val = current_user["payment_status"]

    # Pre-compute expiry timestamp for JS countdown (ms)
    import time as _time
    trial_expiry_ts_ms = int((current_user["signup_ts"] + 48 * 3600) * 1000)

    # trial banner HTML
    if trial_status_label == "trial" and trial_hours_left is not None:
        h = int(trial_hours_left)
        m = int((trial_hours_left - h) * 60)
        trial_banner_html = f"""
        <div class="trial-notice-bar" id="trial-notice-bar" data-expiry="{trial_expiry_ts_ms}">
            <span>⏳ Free trial —
                <b><span id="trial-countdown">{h}h {m}m</span> remaining</b>.
                See plans anytime, no obligation.
            </span>
            <button class="trial-upgrade-btn" onclick="document.getElementById('payment-modal').style.display='flex'">
                View Plans — $99/mo or $999/yr
            </button>
        </div>"""
    elif trial_status_label in ("pending",):
        trial_banner_html = '<div class="trial-notice-bar" style="background:rgba(240,183,47,0.12);border-color:rgba(240,183,47,0.3);color:#f0b72f;">⏳ Payment submitted — your TxID is under review. Access continues until verification is complete.</div>'
    elif trial_status_label == "active":
        trial_banner_html = ""
    else:
        trial_banner_html = ""

    # Pre-compute nav variables (avoid backslash in f-string)
    nav_upgrade_btn = (
        "" if payment_status_val == "Active"
        else '<button class="ss-nav-upgrade" onclick="document.getElementById(\'payment-modal\').style.display=\'flex\'">🔓 View Plans</button>'
    )
    nav_admin_link = (
        '<a href="/alpha-admin-portal" class="ss-nav-link" style="color:#ff5252">⚙ Admin</a>'
        if current_user["is_admin"] else ""
    )
    trial_lock_cls = "visible" if trial_locked else ""

    # Legacy Wallet Risk Monitor
    legacy_data = get_legacy_wallet_data()
    movement_detected = any(
        w["days"] is not None and w["days"] < 30 for w in legacy_data
    )
    total_legacy_btc = sum(w["balance_btc"] for w in legacy_data)
    risk_status_label = "⚠ MOVEMENT DETECTED" if movement_detected else "✓ Secure / Low Activity"
    risk_status_color  = "#ff5252" if movement_detected else "#3fb950"
    legacy_movement_badge = ""
    if movement_detected:
        legacy_movement_badge = (
            '<div class="legacy-movement-badge visible" role="alert" aria-live="assertive">'
            '<span aria-hidden="true">⚠</span>'
            ' Dormant Whale Wallet Movement Detected — monitor for potential exchange inflows'
            '</div>'
        )
    legacy_rows = ""
    for w in legacy_data:
        short = w["address"][:8] + "…" + w["address"][-6:]
        legacy_rows += (
            f'<tr>'
            f'<td><div class="legacy-addr">'
            f'<a href="https://mempool.space/address/{w["address"]}" '
            f'target="_blank" rel="noopener" title="Verify on mempool.space: {w["address"]}">{short} ↗</a>'
            f'</div>'
            f'<div class="legacy-addr-label">{w["label"]}</div></td>'
            f'<td class="legacy-bal">{w["balance_btc"]:,.4f} BTC</td>'
            f'<td style="color:#8b949e;">{w["last_disp"]}</td>'
            f'<td><span class="legacy-status-pill" style="color:{w["s_color"]}; border-color:{w["s_color"]};">'
            f'{w["status"]}</span></td>'
            f'</tr>'
        )
    legacy_html = f"""
    <section class="legacy-card" id="legacy-monitor"
             aria-label="Legacy Wallet Risk Monitor — Quantum Era Early Warning"
             itemscope itemtype="https://schema.org/Dataset">
        <header>
            <div class="legacy-eyebrow">🔐 QUANTUM ERA VULNERABILITY TRACKER · LEGACY P2PKH ADDRESSES</div>
            <div class="legacy-title-row">
                <h2 class="legacy-title" itemprop="name">Legacy Wallet Risk Monitor
                    <span class="legacy-sub">(Quantum Era Early Warning)</span>
                </h2>
                <span class="legacy-info-btn" tabindex="0"
                      role="tooltip"
                      aria-label="About this module">i
                    <span class="legacy-tooltip" role="tooltip">
                        Monitors high-balance legacy Bitcoin addresses (starting with &#39;1&#39;) for sudden
                        movements to predict market dumps before they hit exchanges.
                        Legacy P2PKH addresses are also considered vulnerable under future
                        quantum-computing attack scenarios.
                    </span>
                </span>
            </div>
        </header>
        {legacy_movement_badge}
        <div class="legacy-stats-row">
            <div class="legacy-stat">
                <div class="legacy-stat-label">Total Legacy BTC Inspected</div>
                <div class="legacy-stat-value">~{total_legacy_btc:,.2f} BTC</div>
            </div>
            <div class="legacy-stat">
                <div class="legacy-stat-label">Addresses Monitored</div>
                <div class="legacy-stat-value">{len(legacy_data)}</div>
            </div>
            <div class="legacy-stat">
                <div class="legacy-stat-label">Risk Status</div>
                <div class="legacy-stat-value" style="color:{risk_status_color};">{risk_status_label}</div>
            </div>
        </div>
        <article aria-label="High-value legacy Bitcoin address activity log">
            <table class="legacy-table" role="table">
                <thead>
                    <tr>
                        <th scope="col">Address</th>
                        <th scope="col">Balance</th>
                        <th scope="col">Last Activity</th>
                        <th scope="col">Status Alert</th>
                    </tr>
                </thead>
                <tbody>{legacy_rows}</tbody>
            </table>
        </article>
        <div class="legacy-footer">
            Updates every 5 min · Source: blockchain.info ·
            Cached {datetime.utcnow().strftime("%H:%M UTC")}
        </div>
    </section>
    """

    if news:
        news_html = ""
        for n in news:
            ago = ""
            if n["published"]:
                try:
                    delta = datetime.utcnow() - datetime.utcfromtimestamp(
                        n["published"]
                    )
                    mins = int(delta.total_seconds() // 60)
                    if mins < 60:
                        ago = f"{mins}m ago"
                    elif mins < 1440:
                        ago = f"{mins // 60}h ago"
                    else:
                        ago = f"{mins // 1440}d ago"
                except Exception:
                    ago = ""
            relevance_badge = ""
            if n["is_relevant"]:
                terms = ", ".join(n["matched_terms"])
                relevance_badge = (
                    f'<span class="news-badge" title="Matched: {terms}">'
                    f"🐋 Whale-Correlated</span>"
                )
            img_html = (
                f'<img class="news-thumb" src="{n["image"]}" alt="" '
                f"onerror=\"this.style.display='none'\">"
                if n["image"]
                else ""
            )
            news_html += f"""
            <a class="news-item {"news-item-relevant" if n["is_relevant"] else ""}"
               data-relevant="{"1" if n["is_relevant"] else "0"}"
               href="{n["url"]}" target="_blank" rel="noopener">
                {img_html}
                <div class="news-body">
                    <div class="news-title">{n["title"]}</div>
                    <div class="news-meta">
                        <span class="news-source">{n["source"]}</span>
                        <span class="news-time">{ago}</span>
                        {relevance_badge}
                    </div>
                </div>
            </a>
            """
    else:
        news_html = (
            '<div class="empty">News feed unavailable. Retrying on next refresh.</div>'
        )
    if whales:
        rows_html = ""
        for i, w in enumerate(whales):
            usd_val = w["total_btc"] * market_price
            is_alert = w["total_btc"] >= ALERT_THRESHOLD_BTC
            row_class = "alert-row" if is_alert else ""
            badge = '<span class="alert-badge">🚨 ALERT</span>' if is_alert else ""
            addrs_attr = ",".join(w.get("all_addrs", []))
            rows_html += f"""
            <tr class="{row_class}" data-addresses="{addrs_attr}">
                <td class="rank">#{i + 1} {badge}</td>
                <td class="mono col-hash"><a href="https://mempool.space/tx/{w["hash"]}" target="_blank" rel="noopener" title="Verify this transaction on mempool.space">{short_hash(w["hash"])} ↗</a></td>
                <td class="amt">{fmt_btc(w["total_btc"])}</td>
                <td class="usd">${usd_val:,.0f}</td>
                <td class="col-flow">{w["n_inputs"]} → {w["n_outputs"]}</td>
                <td class="mono col-recipient"><a href="https://mempool.space/address/{w["largest_addr"]}" target="_blank" rel="noopener" title="Verify recipient on mempool.space">{short_addr(w["largest_addr"])} ↗</a></td>
                <td class="col-time">{fmt_time(w["time"])}</td>
                <td>
                    <a class="tweet-btn" href="{tweet_url(w, market_price)}" target="_blank" rel="noopener" title="Share on X">
                        𝕏 Share
                    </a>
                </td>
            </tr>
            """
        table_html = f"""
        <table class="whale-table">
            <thead>
                <tr>
                    <th>Rank</th>
                    <th class="col-hash">Tx Hash</th>
                    <th>Total Value</th>
                    <th>USD Value</th>
                    <th class="col-flow">In → Out</th>
                    <th class="col-recipient">Top Recipient</th>
                    <th class="col-time">Detected</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        """
    else:
        table_html = '<div class="empty">No whale transactions detected in the current mempool.</div>'

    # ===== Fear & Greed Index =====
    fear_greed = compute_fear_greed(velocity, flow, intel)
    fg_bar_pct = fear_greed["score"]

    # ===== Whale Actionable Bias Card =====
    if intel["label"] in ("HIGH PROBABILITY LONG", "LEAN BULLISH"):
        bias_icon, bias_text  = "🟢", "BULLISH BIAS"
        bias_sub, bias_action = "Whales Accumulating", "Favor Long Setups"
        bias_color, bias_border = "#3fb950", "#3fb950"
        bias_bg = "rgba(63,185,80,0.05)"
    elif intel["label"] in ("HIGH PROBABILITY SHORT", "CAUTION: BEARISH PRESSURE"):
        bias_icon, bias_text  = "🔴", "BEARISH BIAS"
        bias_sub, bias_action = "Whales Distributing", "Favor Short Setups"
        bias_color, bias_border = "#ff5252", "#ff5252"
        bias_bg = "rgba(255,82,82,0.05)"
    else:
        bias_icon, bias_text  = "🟡", "NEUTRAL MARKET"
        bias_sub, bias_action = "No Clear Directional Signal", "Wait for Clarity"
        bias_color, bias_border = "#f0b72f", "#f0b72f"
        bias_bg = "rgba(240,183,47,0.05)"

    vel_sig_color  = {"green": "#3fb950", "yellow": "#f0b72f", "red": "#ff5252"}.get(velocity["zone"], "#8b949e")
    flow_sig_color = "#3fb950" if flow["net_btc"] > 0.5 else ("#ff5252" if flow["net_btc"] < -0.5 else "#8b949e")
    if intel["news"]["bull"] > intel["news"]["bear"]:
        news_sig_label, news_sig_color = "Bullish",  "#3fb950"
    elif intel["news"]["bear"] > intel["news"]["bull"]:
        news_sig_label, news_sig_color = "Bearish",  "#ff5252"
    else:
        news_sig_label, news_sig_color = "Neutral",  "#8b949e"

    bias_html = f"""
    <div class="bias-card" style="border-color:{bias_border}; background:linear-gradient(135deg, {bias_bg} 0%, rgba(13,17,23,0.97) 100%);">
        <div class="bias-eyebrow">🐋 WHALE ACTIONABLE BIAS · AI-POWERED REAL-TIME SIGNAL</div>
        <div class="bias-main">
            <span class="bias-icon" aria-hidden="true">{bias_icon}</span>
            <div class="bias-center">
                <div class="bias-text" style="color:{bias_color};">{bias_text}</div>
                <div class="bias-sub">({bias_sub})</div>
                <div class="bias-action">— {bias_action} —</div>
            </div>
            <div class="bias-confidence">
                <div class="bias-conf-ring" style="border-color:{bias_color}; box-shadow:0 0 22px {bias_color}44;">
                    <span class="bias-conf-num" style="color:{bias_color};">{intel["confidence"]}</span>
                    <span class="bias-conf-pct" style="color:{bias_color};">%</span>
                </div>
                <div class="bias-conf-label">CONFIDENCE</div>
            </div>
        </div>
        <div class="bias-signals">
            <div class="bias-signal">
                <span class="bias-signal-dot" style="background:{vel_sig_color};"></span>
                <span class="bias-signal-label">Velocity</span>
                <span class="bias-signal-val" style="color:{vel_sig_color};">{velocity["label"]}</span>
            </div>
            <div class="bias-signal">
                <span class="bias-signal-dot" style="background:{flow_sig_color};"></span>
                <span class="bias-signal-label">Smart Flow</span>
                <span class="bias-signal-val" style="color:{flow_sig_color};">{flow["signal"]}</span>
            </div>
            <div class="bias-signal">
                <span class="bias-signal-dot" style="background:{news_sig_color};"></span>
                <span class="bias-signal-label">News</span>
                <span class="bias-signal-val" style="color:{news_sig_color};">{news_sig_label}</span>
            </div>
        </div>
        <div class="bias-footer">Multi-signal confluence model · updates every 60s</div>
        {render_disclaimer()}
    </div>
    """

    fg_html = f"""
    <div class="fg-card" style="border-left-color:{fear_greed["color"]};">
        <button class="info-btn" aria-label="About Fear &amp; Greed Index" tabindex="0">i
            <span class="info-tooltip">Composite on-chain index (0–100) built from 3 live signals: Whale Velocity (0–35 pts), Smart Money Flow (0–35 pts), and News Sentiment (0–30 pts). Scores ≤20 = Extreme Fear, 41–59 = Neutral, ≥80 = Extreme Greed. High greed often precedes corrections; extreme fear can signal buying opportunities.</span>
        </button>
        <div class="fg-score-block">
            <div class="fg-circle" style="border-color:{fear_greed["color"]}; box-shadow:0 0 28px {fear_greed["color"]}44;">
                <span class="fg-num" style="color:{fear_greed["color"]};">{fear_greed["score"]}</span>
                <span class="fg-denom">/ 100</span>
            </div>
        </div>
        <div class="fg-right">
            <div class="fg-eyebrow">🧪 ON-CHAIN FEAR &amp; GREED INDEX · REAL-TIME COMPOSITE</div>
            <div class="fg-label-row">
                <span class="fg-emoji">{fear_greed["emoji"]}</span>
                <span class="fg-label" style="color:{fear_greed["color"]};">{fear_greed["label"]}</span>
            </div>
            <div class="fg-gradient-wrap">
                <div class="fg-gradient-track">
                    <div class="fg-pointer" style="left:{fg_bar_pct}%;"></div>
                </div>
                <div class="fg-bar-zone-labels">
                    <span>Extreme Fear</span><span>Fear</span><span>Neutral</span><span>Greed</span><span>Extreme Greed</span>
                </div>
            </div>
            <div class="fg-factors">
                <div class="fg-factor">
                    <div class="fg-factor-label">⚡ Velocity</div>
                    <div class="fg-factor-track"><div class="fg-factor-fill" style="width:{fear_greed["v_contrib"]/35*100:.1f}%; background:#58a6ff;"></div></div>
                    <div class="fg-factor-val" style="color:#58a6ff;">{fear_greed["v_contrib"]}<span class="fg-factor-max">/35</span></div>
                </div>
                <div class="fg-factor">
                    <div class="fg-factor-label">💸 Flow</div>
                    <div class="fg-factor-track"><div class="fg-factor-fill" style="width:{fear_greed["f_contrib"]/35*100:.1f}%; background:{fear_greed["color"]};"></div></div>
                    <div class="fg-factor-val" style="color:{fear_greed["color"]};">{fear_greed["f_contrib"]}<span class="fg-factor-max">/35</span></div>
                </div>
                <div class="fg-factor">
                    <div class="fg-factor-label">📰 Sentiment</div>
                    <div class="fg-factor-track"><div class="fg-factor-fill" style="width:{fear_greed["n_contrib"]/30*100:.1f}%; background:#f0b72f;"></div></div>
                    <div class="fg-factor-val" style="color:#f0b72f;">{fear_greed["n_contrib"]}<span class="fg-factor-max">/30</span></div>
                </div>
            </div>
        </div>
    </div>
    """

    # ===== Velocity gauge HTML =====
    if velocity["baseline_tpm"] > 0:
        ratio_pct = f"{(velocity['ratio'] - 1.0) * 100:+.1f}"
        baseline_disp = f"{velocity['baseline_tpm']:.1f}"
        max_scale_disp = f"{velocity['max_scale']:.1f}"
        half_scale_disp = f"{velocity['max_scale'] / 2:.1f}"
    else:
        ratio_pct = "n/a"
        baseline_disp = "—"
        max_scale_disp = "—"
        half_scale_disp = "—"
    bar_fill_pct = round(velocity["pct"] * 100, 1)

    high_alert_pill = (
        '<span class="velocity-alert-pill">🚨 HIGH VELOCITY ALERT</span>'
        if velocity["high_alert"]
        else ""
    )

    # ===== Smart Money Flow HTML =====
    net_abs = abs(flow["net_btc"])
    net_sign = "+" if flow["net_btc"] >= 0 else ""
    # Bar widths: accum vs distrib share of (accum+distrib) combined
    combined = (flow["accum_btc"] + flow["consol_btc"]) + flow["distrib_btc"]
    if combined > 0:
        accum_pct  = round((flow["accum_btc"] + flow["consol_btc"]) / combined * 100, 1)
        distrib_pct = round(flow["distrib_btc"] / combined * 100, 1)
    else:
        accum_pct = distrib_pct = 50.0
    # Pattern breakdown rows
    patterns = [
        ("🔵 Accumulation",  "1-in → 1-out", flow["accum_count"],  flow["accum_btc"],  "#58a6ff"),
        ("🟢 Consolidation", "N-in → 1-out", flow["consol_count"], flow["consol_btc"], "#3fb950"),
        ("🔴 Distribution",  "1-in → N-out", flow["distrib_count"],flow["distrib_btc"],"#ff5252"),
        ("⚪ Mixed",         "Other pattern",flow["mixed_count"],  flow["mixed_btc"],  "#6e7681"),
    ]
    pattern_rows = "".join(f"""
        <div class="flow-pattern-row">
            <span class="fp-name" style="color:{c};">{label}</span>
            <span class="fp-desc">{desc}</span>
            <span class="fp-count">{cnt} tx</span>
            <span class="fp-btc" style="color:{c};">{btc:.4f} BTC</span>
        </div>""" for label, desc, cnt, btc, c in patterns)

    flow_html = f"""
    <div class="flow-card" id="flow-card" style="border-left-color: {flow['sig_color']};">
        <button class="info-btn" aria-label="About Smart Money Flow" tabindex="0">i
            <span class="info-tooltip">Classifies every mempool transaction by its input/output structure. 1-in→1-out = Accumulation (whale sweeping to cold storage). 1-in→N-out = Distribution (spreading to many recipients). Net positive BTC flow = bullish signal. Net negative = potential sell pressure building.</span>
        </button>
        <div class="flow-top">
            <div>
                <div class="flow-eyebrow">💸 SMART MONEY FLOW · MEMPOOL REAL-TIME</div>
                <div class="flow-title-row">
                    <span class="flow-signal" style="color: {flow['sig_color']};">{flow['signal']}</span>
                    <span class="flow-sample">{flow['total_count']} TX ANALYZED</span>
                </div>
                <div class="flow-detail">{flow['sig_detail']}</div>
            </div>
            <div class="flow-net-block">
                <div class="flow-net-label">NET BTC FLOW</div>
                <div class="flow-net-value" style="color: {flow['sig_color']};">
                    {net_sign}{flow['net_btc']:.4f}
                </div>
                <div class="flow-net-sub">BTC (last 60 min)</div>
            </div>
        </div>

        <div class="flow-bar-section">
            <div class="flow-bar-labels">
                <span style="color:#3fb950;">▲ Accum+Consol: {flow['accum_btc']+flow['consol_btc']:.4f} BTC</span>
                <span style="color:#ff5252;">▼ Distribution: {flow['distrib_btc']:.4f} BTC</span>
            </div>
            <div class="flow-bar-track">
                <div class="flow-bar-accum"  style="width:{accum_pct}%;"></div>
                <div class="flow-bar-distrib" style="width:{distrib_pct}%;"></div>
            </div>
        </div>

        <div class="flow-body">
            <div class="flow-patterns">
                <div class="flow-patterns-title">Pattern Breakdown</div>
                {pattern_rows}
            </div>
            <div class="flow-chart-wrap">
                <div class="flow-chart-title">Net Flow Sparkline (60 min)</div>
                <div class="flow-canvas-box">
                    <canvas id="flow-canvas"></canvas>
                    <div class="flow-empty" id="flow-empty">Collecting flow data…</div>
                </div>
                <div class="flow-chart-legend">
                    <span><span class="fleg-dot" style="background:#3fb950;"></span>Accumulated</span>
                    <span><span class="fleg-dot" style="background:#ff5252;"></span>Distributed</span>
                    <span><span class="fleg-dot" style="background:#58a6ff;"></span>Net</span>
                </div>
            </div>
        </div>
        <div class="flow-footer">Updates every 30s · Source: blockchain.info mempool</div>
    </div>
    """

    # ===== Institutional Intelligence Summary HTML =====
    def sign(s): return f"+{s}" if s > 0 else f"{s}"
    vol_delta = intel["volume"].get("delta_pct", 0)
    vol_delta_disp = f"{vol_delta:+.1f}%" if intel["volume"].get("first_avg") else "—"
    intel_html = f"""
    <div class="intel-card" style="border-left-color: {intel["color"]};">
        <button class="info-btn" aria-label="About Institutional Intelligence" tabindex="0">i
            <span class="info-tooltip">Uses 3 live on-chain signals — whale network velocity, news sentiment, and 60-min BTC volume trend — to compute a directional probability score. Score ≥+15 = bullish, ≤−15 = bearish. Higher confidence means stronger alignment across all 3 factors.</span>
        </button>
        <div class="intel-top">
            <div class="intel-eyebrow">🧠 INSTITUTIONAL INTELLIGENCE · LAST 60 MIN</div>
            <div class="intel-confidence">
                <span class="intel-conf-label">CONFIDENCE</span>
                <span class="intel-conf-value" style="color: {intel["color"]};">{intel["confidence"]}%</span>
            </div>
        </div>
        <div class="intel-headline">
            <span class="intel-icon">{intel["icon"]}</span>
            <span class="intel-direction" style="color: {intel["color"]};">{intel["label"]}</span>
            <span class="intel-score" title="Composite directional score">SCORE {sign(intel["score"])}</span>
        </div>
        <div class="intel-verdict">{intel["verdict"]}</div>
        <div class="intel-confidence-track">
            <div class="intel-confidence-fill" style="width: {intel["confidence"]}%; background: {intel["color"]};"></div>
        </div>
        <div class="intel-factors">
            <div class="intel-factor">
                <div class="intel-factor-head">
                    <span class="intel-factor-name">⚡ Whale Velocity</span>
                    <span class="intel-factor-score" style="color: {"#3fb950" if intel["velocity"]["score"] > 0 else ("#ff5252" if intel["velocity"]["score"] < 0 else "#8b949e")};">{sign(intel["velocity"]["score"])}</span>
                </div>
                <div class="intel-factor-detail">{intel["velocity"]["tag"]}</div>
            </div>
            <div class="intel-factor">
                <div class="intel-factor-head">
                    <span class="intel-factor-name">📰 News Sentiment</span>
                    <span class="intel-factor-score" style="color: {"#3fb950" if intel["news"]["score"] > 0 else ("#ff5252" if intel["news"]["score"] < 0 else "#8b949e")};">{sign(intel["news"]["score"])}</span>
                </div>
                <div class="intel-factor-detail">
                    <span style="color:#3fb950;">{intel["news"]["bull"]} bull</span> ·
                    <span style="color:#ff5252;">{intel["news"]["bear"]} bear</span> keywords across recent headlines
                </div>
            </div>
            <div class="intel-factor">
                <div class="intel-factor-head">
                    <span class="intel-factor-name">📊 BTC Volume Trend</span>
                    <span class="intel-factor-score" style="color: {"#3fb950" if intel["volume"]["score"] > 0 else ("#ff5252" if intel["volume"]["score"] < 0 else "#8b949e")};">{sign(intel["volume"]["score"])}</span>
                </div>
                <div class="intel-factor-detail">{intel["volume"]["tag"]} · Δ {vol_delta_disp}</div>
            </div>
        </div>
    </div>
    """

    velocity_html = f"""
    <div class="velocity-card velocity-{velocity["zone"]} {"velocity-alert" if velocity["high_alert"] else ""}">
        <button class="info-btn" aria-label="About Network Velocity" tabindex="0">i
            <span class="info-tooltip">Measures real-time BTC transaction throughput vs the 24-hour average. CALM = below 50% of baseline. NORMAL = 50–100% of baseline. HIGH VELOCITY = above baseline — often signals institutional accumulation events. The red marker at 66.7% is the ALERT threshold.</span>
        </button>
        <div class="velocity-left">
            <div class="velocity-eyebrow">⚡ NETWORK VELOCITY · 24H BASELINE</div>
            <div class="velocity-status-row">
                <div class="velocity-label" style="color: {velocity["color"]};">{velocity["label"]}</div>
                {high_alert_pill}
            </div>
            <div class="velocity-detail">{velocity["detail"]}</div>
            <div class="velocity-stats">
                <div class="vstat">
                    <div class="vstat-label">CURRENT</div>
                    <div class="vstat-value" style="color: {velocity["color"]};">
                        {velocity["current_tpm"]:.1f}<span class="vstat-unit"> tx/min</span>
                    </div>
                </div>
                <div class="vstat">
                    <div class="vstat-label">24H AVG</div>
                    <div class="vstat-value">
                        {baseline_disp}<span class="vstat-unit"> tx/min</span>
                    </div>
                </div>
                <div class="vstat">
                    <div class="vstat-label">Δ vs BASELINE</div>
                    <div class="vstat-value" style="color: {velocity["color"]};">
                        {ratio_pct}<span class="vstat-unit"> %</span>
                    </div>
                </div>
                <div class="vstat">
                    <div class="vstat-label">SAMPLE</div>
                    <div class="vstat-value">
                        {velocity["sample_size"]}<span class="vstat-unit"> tx</span>
                    </div>
                </div>
            </div>
        </div>
        <div class="velocity-right">
            <div class="vel-gauge">
                <div class="vel-digital-row">
                    <span class="vel-num" style="color:{velocity["color"]};">{velocity["current_tpm"]:.1f}</span>
                    <span class="vel-unit">TX / MIN</span>
                </div>
                <div class="vel-bar-wrap">
                    <div class="vel-track" aria-label="Velocity bar: {bar_fill_pct}% of max scale">
                        <div class="vel-zone-calm"></div>
                        <div class="vel-zone-normal"></div>
                        <div class="vel-zone-high"></div>
                        <div class="vel-fill" style="width:{bar_fill_pct}%; background:{velocity["color"]};"></div>
                        <div class="vel-threshold-mark" title="Alert threshold = baseline tx/min"></div>
                    </div>
                    <div class="vel-scale">
                        <span>0</span>
                        <span>{half_scale_disp}</span>
                        <span>{max_scale_disp} tx/m</span>
                    </div>
                </div>
                <div class="vel-zone-legend">
                    <span><span class="vel-zone-dot" style="background:#3fb950;"></span>Calm</span>
                    <span class="vel-sep">·</span>
                    <span><span class="vel-zone-dot" style="background:#f0b72f;"></span>Normal</span>
                    <span class="vel-sep">·</span>
                    <span><span class="vel-zone-dot" style="background:#ff5252;"></span>High</span>
                    <span class="vel-sep">·</span>
                    <span class="vel-threshold-legend">▎ Alert</span>
                </div>
            </div>
        </div>
    </div>
    """

    alert_banner = ""
    if alert_active:
        alert_banner = f"""
        <div class="alert-banner" id="alert-banner">
            <span class="alert-light"></span>
            <span class="alert-text">WHALE ALERT — {alert_count} transaction(s) over {ALERT_THRESHOLD_BTC} BTC detected in the live mempool</span>
            <span class="alert-light"></span>
        </div>
        """

    html_template = f"""
    <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Skill Shield BTC — Bitcoin Mempool Intelligence | Real-Time Whale Tracker</title>
            <meta name="description" content="Skill Shield BTC — institutional-grade Bitcoin Mempool Intelligence platform. Real-Time Whale Tracker with Quantitative On-Chain Signals for live BTC mempool analysis.">
            <meta name="keywords" content="Bitcoin Mempool Intelligence, Real-Time Whale Tracker, Quantitative On-Chain Signals, BTC whale monitoring, crypto on-chain analytics, institutional Bitcoin analysis">
            <meta name="robots" content="index, follow">
            <meta property="og:type" content="website">
            <meta property="og:title" content="Skill Shield BTC — Bitcoin Mempool Intelligence | Real-Time Whale Tracker">
            <meta property="og:description" content="Institutional-grade Bitcoin Mempool Intelligence. Track on-chain whale movements with live Quantitative On-Chain Signals.">
            <meta property="og:site_name" content="Skill Shield BTC">
            <meta name="twitter:card" content="summary">
            <meta name="twitter:title" content="Skill Shield BTC — Real-Time Whale Tracker &amp; Bitcoin Mempool Intelligence">
            <meta name="twitter:description" content="Quantitative On-Chain Signals. Live Bitcoin whale tracker powered by real mempool data.">
            <script type="application/ld+json">
            {{
              "@context": "https://schema.org",
              "@graph": [
                {{
                  "@type": "SoftwareApplication",
                  "name": "Skill Shield BTC",
                  "description": "Real-Time Bitcoin Mempool Intelligence — Quantitative On-Chain Signals for institutional whale tracking, network velocity analysis, and smart money flow detection.",
                  "applicationCategory": "FinanceApplication",
                  "operatingSystem": "Web",
                  "url": "https://skillshieldbtc.com",
                  "offers": {{
                    "@type": "Offer",
                    "price": "99",
                    "priceCurrency": "USD",
                    "description": "Monthly subscription to Bitcoin Mempool Intelligence platform"
                  }},
                  "creator": {{
                    "@type": "Organization",
                    "name": "Skill Shield BTC"
                  }}
                }},
                {{
                  "@type": "FinancialProduct",
                  "name": "Skill Shield BTC Intelligence Subscription",
                  "description": "Real-Time Whale Tracker subscription with Bitcoin Mempool Intelligence and Quantitative On-Chain Signals. Institutional-grade on-chain data, live BTC network velocity, and smart money flow analysis.",
                  "feesAndCommissionsSpecification": "Monthly $99 or Yearly $999",
                  "url": "https://skillshieldbtc.com"
                }}
              ]
            }}
            </script>
            <link rel="icon" type="image/svg+xml" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><text y='52' font-size='52'>🐋</text></svg>">
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
            <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
            <style>
                * {{ box-sizing: border-box; }}
                body {{
                    background-color: #0d1117; color: #58a6ff;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                    text-align: center; padding: 106px 20px 40px; margin: 0;
                }}
                h1 {{
                    color: #ffffff; font-family: 'Inter', 'Segoe UI', sans-serif;
                    font-size: 2.6em; font-weight: 900;
                    text-transform: uppercase; letter-spacing: 3px; margin: 0 0 6px;
                    background: linear-gradient(135deg, #ffffff 0%, #79c0ff 100%);
                    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
                    background-clip: text;
                }}
                .tagline {{ color: #58a6ff; margin-bottom: 24px; }}
                .card {{
                    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
                    padding: 20px; display: inline-block; box-shadow: 0 4px 15px rgba(0,0,0,0.5);
                    margin-bottom: 28px;
                }}
                .stat {{ font-size: 1.5em; margin: 10px 0; color: #79c0ff; }}

                /* Sentiment indicator */
                .sentiment {{
                    max-width: 1100px; margin: 0 auto 24px;
                    background: #161b22; border: 1px solid #30363d;
                    border-left-width: 5px; border-radius: 10px;
                    padding: 18px 24px; text-align: left;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.5);
                    display: grid;
                    grid-template-columns: 200px 1fr;
                    grid-template-rows: auto auto;
                    column-gap: 24px; row-gap: 8px;
                    align-items: center;
                }}
                .sentiment-label {{
                    grid-row: 1; grid-column: 1;
                    color: #8b949e; font-size: 0.8em;
                    text-transform: uppercase; letter-spacing: 1.5px;
                }}
                .sentiment-value {{
                    grid-row: 2; grid-column: 1;
                    font-size: 2em; font-weight: 700; letter-spacing: 1px;
                    text-transform: uppercase;
                }}
                .sentiment-icon {{ margin-right: 6px; }}
                .sentiment-meter {{
                    grid-row: 1; grid-column: 2;
                    position: relative; height: 8px;
                    background: #0d1117; border: 1px solid #30363d;
                    border-radius: 4px; overflow: hidden;
                }}
                .sentiment-meter::before {{
                    content: ""; position: absolute;
                    left: 50%; top: 0; bottom: 0;
                    width: 1px; background: #30363d;
                }}
                .sentiment-meter-fill {{
                    height: 100%; transition: width 0.4s ease;
                }}
                .sentiment-detail {{
                    grid-row: 2; grid-column: 2;
                    color: #c9d1d9; font-size: 0.95em;
                }}
                @media (max-width: 720px) {{
                    .sentiment {{ grid-template-columns: 1fr; }}
                    .sentiment-label, .sentiment-value, .sentiment-meter, .sentiment-detail {{
                        grid-column: 1;
                    }}
                }}

                /* Alert banner */
                @keyframes flashRed {{
                    0%, 100% {{ background-color: #2a0a0e; box-shadow: 0 0 0 rgba(255,0,0,0); }}
                    50% {{ background-color: #b71c1c; box-shadow: 0 0 30px rgba(255,40,40,0.7); }}
                }}
                @keyframes pulseLight {{
                    0%, 100% {{ background-color: #ff1744; box-shadow: 0 0 4px #ff1744; opacity: 0.6; }}
                    50% {{ background-color: #ff5252; box-shadow: 0 0 22px #ff1744; opacity: 1; }}
                }}
                .alert-banner {{
                    max-width: 1100px; margin: 0 auto 24px;
                    padding: 14px 20px; border-radius: 10px;
                    border: 2px solid #ff5252;
                    color: #fff; font-weight: 700; letter-spacing: 1px;
                    text-transform: uppercase; font-size: 0.95em;
                    display: flex; align-items: center; justify-content: center; gap: 16px;
                    animation: flashRed 1.2s ease-in-out infinite;
                }}
                .alert-light {{
                    width: 14px; height: 14px; border-radius: 50%;
                    animation: pulseLight 0.8s ease-in-out infinite;
                }}

                .main-grid {{
                    max-width: 1500px; margin: 0 auto 24px;
                    display: grid;
                    grid-template-columns: minmax(0, 1fr) minmax(280px, 380px);
                    gap: 16px; text-align: left;
                    align-items: start;
                }}
                /* Only collapse to single column on very narrow phones */
                @media (max-width: 600px) {{
                    .main-grid {{ grid-template-columns: 1fr; }}
                }}
                /* Whale table fills its panel (no horizontal scroll) — non-essential
                   columns hide at narrower widths so columns auto-adjust cleanly. */
                .table-scroll {{ overflow-x: hidden; }}
                .table-scroll .whale-table {{ width: 100%; min-width: 0; }}
                @media (max-width: 1199px) {{
                    .whale-table .col-flow,
                    .whale-table .col-time {{ display: none; }}
                }}
                @media (max-width: 999px) {{
                    .whale-table .col-recipient {{ display: none; }}
                }}

                /* Watchlist hit — gold highlight applied via JS on matched rows */
                @keyframes goldGlow {{
                    0%, 100% {{ box-shadow: inset 3px 0 0 #ffd700, 0 0 0 0 rgba(255,215,0,0); }}
                    50%      {{ box-shadow: inset 3px 0 0 #ffd700, 0 0 14px 0 rgba(255,215,0,0.35); }}
                }}
                .whale-table tr.watchlist-hit {{
                    background: rgba(255, 215, 0, 0.12) !important;
                    animation: goldGlow 2.4s ease-in-out infinite;
                }}
                .whale-table tr.watchlist-hit td:first-child {{
                    border-left: 3px solid #ffd700;
                }}
                .watchlist-badge {{
                    display: inline-block; margin-left: 6px;
                    background: #ffd700; color: #0d1117;
                    padding: 1px 7px; border-radius: 4px;
                    font-size: 0.72em; font-weight: 700; letter-spacing: 0.5px;
                }}

                /* ===== Institutional Intelligence Summary ===== */
                /* ===== Info (i) Tooltip System ===== */
                .info-btn {{
                    position: absolute; top: 12px; right: 14px;
                    width: 22px; height: 22px; border-radius: 50%;
                    background: rgba(88,166,255,0.08);
                    border: 1px solid rgba(88,166,255,0.22);
                    color: #58a6ff; font-size: 0.75em; font-weight: 700;
                    font-style: italic; font-family: Georgia, serif;
                    cursor: pointer; user-select: none;
                    display: flex; align-items: center; justify-content: center;
                    transition: background 0.2s, border-color 0.2s;
                    z-index: 10; line-height: 1;
                    padding: 0;
                }}
                .info-btn:hover,
                .info-btn.active {{
                    background: rgba(88,166,255,0.22);
                    border-color: rgba(88,166,255,0.55);
                }}
                .info-tooltip {{
                    opacity: 0; pointer-events: none; visibility: hidden;
                    position: absolute; top: 30px; right: 0;
                    min-width: 230px; max-width: 310px;
                    background: #1c2128;
                    border: 1px solid #30363d;
                    border-radius: 10px; padding: 12px 14px;
                    font-size: 0.76em; color: #c9d1d9;
                    text-align: left; line-height: 1.55;
                    box-shadow: 0 8px 28px rgba(0,0,0,0.85);
                    z-index: 500;
                    transition: opacity 0.15s ease, visibility 0.15s ease;
                    font-style: normal;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                    font-weight: 400; white-space: normal;
                    letter-spacing: 0;
                }}
                .info-btn:hover .info-tooltip,
                .info-btn.active .info-tooltip {{
                    opacity: 1; pointer-events: auto; visibility: visible;
                }}

                /* ===== Whale Actionable Bias Card ===== */
                .bias-card {{
                    max-width: 1500px; margin: 0 auto 22px;
                    border: 2px solid #3fb950;
                    border-radius: 14px;
                    padding: 18px 28px 16px;
                    text-align: left;
                    box-shadow: 0 4px 28px rgba(0,0,0,0.65);
                    position: relative;
                }}
                .bias-eyebrow {{
                    color: #8b949e; font-size: 0.72em;
                    text-transform: uppercase; letter-spacing: 1.8px;
                    margin-bottom: 12px;
                }}
                .bias-main {{
                    display: flex; align-items: center; gap: 20px;
                    flex-wrap: wrap; margin-bottom: 14px;
                }}
                .bias-icon {{ font-size: 2.8em; line-height: 1; flex-shrink: 0; }}
                .bias-center {{ flex: 1; min-width: 180px; }}
                .bias-text {{
                    font-size: 2em; font-weight: 900;
                    text-transform: uppercase; letter-spacing: 2px; line-height: 1.1;
                    font-family: 'Inter', 'Segoe UI', sans-serif;
                }}
                .bias-sub {{
                    color: #8b949e; font-size: 0.88em; margin: 4px 0 2px;
                }}
                .bias-action {{
                    color: #c9d1d9; font-size: 0.92em; font-weight: 600;
                    letter-spacing: 0.5px;
                }}
                .bias-confidence {{ text-align: center; flex-shrink: 0; }}
                .bias-conf-ring {{
                    width: 80px; height: 80px; border-radius: 50%;
                    border: 3px solid #3fb950;
                    display: flex; align-items: center; justify-content: center;
                    background: rgba(13,17,23,0.8);
                    margin: 0 auto 6px;
                }}
                .bias-conf-num {{ font-size: 1.8em; font-weight: 900; line-height: 1; }}
                .bias-conf-pct {{ font-size: 0.85em; font-weight: 700; margin-left: 1px; }}
                .bias-conf-label {{
                    color: #8b949e; font-size: 0.68em;
                    text-transform: uppercase; letter-spacing: 1.2px;
                }}
                .bias-signals {{
                    display: flex; gap: 20px; flex-wrap: wrap;
                    border-top: 1px solid #21262d;
                    padding-top: 12px; margin-bottom: 10px; align-items: center;
                }}
                .bias-signal {{
                    display: flex; align-items: center; gap: 7px;
                    font-size: 0.82em;
                }}
                .bias-signal-dot {{
                    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
                }}
                .bias-signal-label {{ color: #6e7681; }}
                .bias-signal-val {{ font-weight: 700; }}
                .bias-footer {{
                    color: #6e7681; font-size: 0.68em;
                    text-align: center; letter-spacing: 0.4px;
                }}

                .disclaimer-box {{
                    display: flex; align-items: flex-start; gap: 8px;
                    margin-top: 10px; padding: 8px 12px;
                    background: rgba(240,183,47,0.06);
                    border: 1px solid rgba(240,183,47,0.2);
                    border-radius: 6px;
                    color: #8b949e; font-size: 0.74em; line-height: 1.4;
                }}
                .disclaimer-icon {{ color: #f0b72f; flex-shrink: 0; }}
                .disclaimer-compact {{
                    color: #6e7681; font-size: 0.7em; text-align: center;
                    margin-top: 6px;
                }}

                .intel-card {{
                    max-width: 1500px; margin: 0 auto 22px;
                    background: linear-gradient(135deg, #1a2030 0%, #0d1117 100%);
                    border: 1px solid #30363d;
                    border-left: 4px solid #58a6ff;
                    border-radius: 12px;
                    padding: 18px 24px 20px;
                    text-align: left;
                    box-shadow: 0 4px 18px rgba(0,0,0,0.55);
                    position: relative;
                }}
                .intel-top {{
                    display: flex; align-items: center; justify-content: space-between;
                    flex-wrap: wrap; gap: 8px; margin-bottom: 6px;
                }}
                .intel-eyebrow {{
                    color: #8b949e; font-size: 0.72em;
                    text-transform: uppercase; letter-spacing: 1.6px;
                }}
                .intel-confidence {{
                    display: flex; align-items: baseline; gap: 6px;
                }}
                .intel-conf-label {{
                    color: #6e7681; font-size: 0.7em;
                    text-transform: uppercase; letter-spacing: 1.2px;
                }}
                .intel-conf-value {{
                    font-size: 1.2em; font-weight: 700;
                    font-variant-numeric: tabular-nums;
                }}
                .intel-headline {{
                    display: flex; align-items: center; flex-wrap: wrap; gap: 12px;
                    margin: 4px 0 6px;
                }}
                .intel-icon {{ font-size: 1.6em; line-height: 1; }}
                .intel-direction {{
                    font-size: 1.7em; font-weight: 800;
                    letter-spacing: 1.2px; text-transform: uppercase;
                }}
                .intel-score {{
                    background: rgba(13,17,23,0.7);
                    border: 1px solid #30363d;
                    color: #c9d1d9; font-size: 0.7em; font-weight: 700;
                    padding: 3px 9px; border-radius: 999px;
                    letter-spacing: 1px; font-variant-numeric: tabular-nums;
                }}
                .intel-verdict {{
                    color: #c9d1d9; font-size: 0.92em;
                    line-height: 1.5; margin-bottom: 12px;
                }}
                .intel-confidence-track {{
                    height: 6px; background: #0d1117;
                    border: 1px solid #21262d; border-radius: 999px;
                    overflow: hidden; margin-bottom: 14px;
                }}
                .intel-confidence-fill {{
                    height: 100%;
                    transition: width 0.6s ease, background 0.3s ease;
                    box-shadow: 0 0 8px currentColor;
                }}
                .intel-factors {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                    gap: 12px;
                }}
                .intel-factor {{
                    background: rgba(13,17,23,0.6);
                    border: 1px solid #21262d;
                    border-radius: 8px;
                    padding: 10px 14px;
                }}
                .intel-factor-head {{
                    display: flex; align-items: center; justify-content: space-between;
                    margin-bottom: 4px;
                }}
                .intel-factor-name {{
                    color: #c9d1d9; font-size: 0.85em; font-weight: 700;
                }}
                .intel-factor-score {{
                    font-size: 0.95em; font-weight: 700;
                    font-variant-numeric: tabular-nums;
                }}
                .intel-factor-detail {{
                    color: #8b949e; font-size: 0.78em; line-height: 1.4;
                }}

                /* ===== Surge alert button ===== */
                .surge-btn {{
                    background: rgba(13,17,23,0.6);
                    border: 1px solid #30363d;
                    color: #c9d1d9;
                    padding: 4px 11px;
                    border-radius: 999px;
                    font-size: 0.78em; font-weight: 600;
                    cursor: pointer;
                    letter-spacing: 0.4px;
                    transition: border-color 0.2s, color 0.2s, box-shadow 0.2s;
                }}
                .surge-btn:hover {{ border-color: #58a6ff; color: #58a6ff; }}
                .surge-btn.armed {{
                    border-color: #3fb950; color: #3fb950;
                    box-shadow: 0 0 10px rgba(63,185,80,0.35);
                }}
                .surge-btn.denied {{
                    border-color: #ff5252; color: #ff5252;
                }}

                /* ===== Velocity speedometer card ===== */
                @keyframes velocityAlertPulse {{
                    0%, 100% {{ box-shadow: 0 0 0 0 rgba(255, 82, 82, 0.55), 0 4px 15px rgba(0,0,0,0.5); }}
                    50%      {{ box-shadow: 0 0 28px 4px rgba(255, 82, 82, 0.55), 0 4px 15px rgba(0,0,0,0.5); }}
                }}
                .velocity-card {{
                    max-width: 1500px; margin: 0 auto 22px;
                    background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
                    border: 1px solid #30363d;
                    border-left: 4px solid #58a6ff;
                    border-radius: 12px;
                    padding: 20px 24px;
                    position: relative;
                    text-align: left;
                    display: grid;
                    grid-template-columns: minmax(0, 1fr) 240px;
                    gap: 24px; align-items: center;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.5);
                }}
                .velocity-card.velocity-green  {{ border-left-color: #3fb950; }}
                .velocity-card.velocity-yellow {{ border-left-color: #f0b72f; }}
                .velocity-card.velocity-red    {{ border-left-color: #ff5252; }}
                .velocity-card.velocity-alert  {{ animation: velocityAlertPulse 1.6s ease-in-out infinite; }}

                @media (max-width: 720px) {{
                    .velocity-card {{ grid-template-columns: 1fr; }}
                    .velocity-right {{ justify-self: center; }}
                }}

                .velocity-eyebrow {{
                    color: #8b949e; font-size: 0.75em;
                    text-transform: uppercase; letter-spacing: 1.8px;
                    margin-bottom: 8px;
                }}
                .velocity-status-row {{
                    display: flex; align-items: center; gap: 12px;
                    flex-wrap: wrap; margin-bottom: 6px;
                }}
                .velocity-label {{
                    font-size: 1.7em; font-weight: 700;
                    letter-spacing: 1.5px; text-transform: uppercase;
                }}
                .velocity-alert-pill {{
                    background: #ff5252; color: #ffffff;
                    padding: 4px 10px; border-radius: 999px;
                    font-size: 0.72em; font-weight: 700; letter-spacing: 0.8px;
                    animation: pulseLight 1s ease-in-out infinite;
                }}
                .velocity-detail {{
                    color: #c9d1d9; font-size: 0.88em;
                    line-height: 1.5; margin-bottom: 14px;
                }}
                .velocity-stats {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
                    gap: 12px;
                }}
                .vstat {{
                    background: rgba(13, 17, 23, 0.6);
                    border: 1px solid #21262d;
                    border-radius: 8px;
                    padding: 8px 12px;
                }}
                .vstat-label {{
                    color: #6e7681; font-size: 0.68em;
                    text-transform: uppercase; letter-spacing: 1.2px;
                    margin-bottom: 4px;
                }}
                .vstat-value {{
                    color: #c9d1d9; font-size: 1.15em; font-weight: 700;
                    font-variant-numeric: tabular-nums;
                }}
                .vstat-unit {{
                    color: #8b949e; font-size: 0.7em; font-weight: 400;
                    margin-left: 2px;
                }}

                .velocity-right {{
                    display: flex; align-items: center; justify-content: center;
                }}

                /* ===== Velocity horizontal bar gauge ===== */
                .vel-gauge {{
                    display: flex; flex-direction: column; gap: 10px;
                    width: 100%; min-width: 200px;
                }}
                .vel-digital-row {{
                    display: flex; align-items: baseline; gap: 8px;
                    justify-content: center;
                }}
                .vel-num {{
                    font-size: 2.4em; font-weight: 800;
                    font-variant-numeric: tabular-nums; line-height: 1;
                    transition: color 0.5s ease;
                }}
                .vel-unit {{
                    color: #6e7681; font-size: 0.72em;
                    text-transform: uppercase; letter-spacing: 1.5px;
                }}
                .vel-bar-wrap {{ width: 100%; }}
                .vel-track {{
                    position: relative; height: 20px;
                    border-radius: 999px; overflow: hidden;
                    background: #0d1117; border: 1px solid #21262d;
                }}
                .vel-zone-calm {{
                    position: absolute; left: 0; top: 0; bottom: 0;
                    width: 33.33%; background: rgba(63,185,80,0.12);
                }}
                .vel-zone-normal {{
                    position: absolute; top: 0; bottom: 0;
                    left: 33.33%; width: 33.34%;
                    background: rgba(240,183,47,0.12);
                }}
                .vel-zone-high {{
                    position: absolute; top: 0; bottom: 0;
                    right: 0; width: 33.33%;
                    background: rgba(255,82,82,0.12);
                }}
                .vel-fill {{
                    position: absolute; left: 0; top: 3px; bottom: 3px;
                    border-radius: 999px;
                    transition: width 0.8s cubic-bezier(0.4,0,0.2,1), background 0.5s ease;
                    box-shadow: 0 0 10px currentColor;
                    min-width: 6px;
                }}
                .vel-threshold-mark {{
                    position: absolute; top: 0; bottom: 0;
                    left: 66.67%; width: 2px;
                    background: rgba(255,82,82,0.75);
                    pointer-events: none;
                }}
                .vel-scale {{
                    display: flex; justify-content: space-between;
                    color: #6e7681; font-size: 0.65em;
                    font-variant-numeric: tabular-nums;
                    margin-top: 5px; padding: 0 2px;
                }}
                .vel-zone-legend {{
                    display: flex; align-items: center; gap: 6px;
                    font-size: 0.68em; color: #8b949e;
                    flex-wrap: wrap; justify-content: center;
                }}
                .vel-zone-dot {{
                    display: inline-block; width: 7px; height: 7px;
                    border-radius: 50%; margin-right: 3px;
                    vertical-align: middle;
                }}
                .vel-sep {{ color: #30363d; }}
                .vel-threshold-legend {{
                    color: rgba(255,82,82,0.75); font-size: 0.92em; font-weight: 700;
                }}

                /* ===== 60-min Sparkline chart card ===== */
                .sparkline-card {{
                    max-width: 1500px; margin: 0 auto 22px;
                    background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
                    border: 1px solid #30363d;
                    border-left: 4px solid #58a6ff;
                    border-radius: 12px;
                    padding: 18px 24px 22px;
                    text-align: left;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.5);
                    position: relative;
                }}
                .sparkline-header {{
                    display: flex; align-items: center; justify-content: space-between;
                    flex-wrap: wrap; gap: 10px; margin-bottom: 12px;
                }}
                .sparkline-title {{
                    color: #ffffff; font-size: 1.05em; font-weight: 700;
                    letter-spacing: 0.8px; text-transform: uppercase;
                }}
                .sparkline-eyebrow {{
                    color: #8b949e; font-size: 0.72em;
                    text-transform: uppercase; letter-spacing: 1.6px;
                    margin-bottom: 2px;
                }}
                .sparkline-legend {{
                    display: flex; gap: 16px; font-size: 0.78em; color: #8b949e;
                }}
                .legend-dot {{
                    display: inline-block; width: 10px; height: 10px;
                    border-radius: 50%; margin-right: 6px; vertical-align: middle;
                }}
                .legend-dot.dot-count  {{ background: #58a6ff; box-shadow: 0 0 6px #58a6ff; }}
                .legend-dot.dot-volume {{ background: #f0b72f; box-shadow: 0 0 6px #f0b72f; }}
                /* Fixed-height canvas wrapper so layout never reflows */
                .sparkline-wrapper {{
                    position: relative;
                    height: 260px;
                    width: 100%;
                }}
                .sparkline-empty {{
                    position: absolute; inset: 0;
                    display: flex; align-items: center; justify-content: center;
                    color: #6e7681; font-size: 0.9em;
                    pointer-events: none;
                }}
                .sparkline-empty.hidden {{ display: none; }}
                .sparkline-footer {{
                    margin-top: 10px;
                    color: #6e7681; font-size: 0.72em;
                    text-align: right; letter-spacing: 0.5px;
                }}

                /* ===== Smart Money Flow panel ===== */
                .flow-card {{
                    max-width: 1500px; margin: 0 auto 22px;
                    background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
                    border: 1px solid #30363d;
                    border-left: 4px solid #58a6ff;
                    border-radius: 12px;
                    padding: 18px 24px 16px;
                    text-align: left;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.5);
                    position: relative;
                }}
                .flow-top {{
                    display: flex; justify-content: space-between;
                    align-items: flex-start; flex-wrap: wrap; gap: 14px;
                    margin-bottom: 14px;
                }}
                .flow-eyebrow {{
                    color: #8b949e; font-size: 0.72em;
                    text-transform: uppercase; letter-spacing: 1.6px;
                    margin-bottom: 5px;
                }}
                .flow-title-row {{
                    display: flex; align-items: center; gap: 12px;
                    flex-wrap: wrap; margin-bottom: 5px;
                }}
                .flow-signal {{
                    font-size: 1.5em; font-weight: 800;
                    letter-spacing: 1.2px; text-transform: uppercase;
                }}
                .flow-sample {{
                    background: rgba(13,17,23,0.7);
                    border: 1px solid #30363d;
                    color: #8b949e; font-size: 0.68em; font-weight: 700;
                    padding: 3px 9px; border-radius: 999px; letter-spacing: 1px;
                }}
                .flow-detail {{
                    color: #c9d1d9; font-size: 0.88em; line-height: 1.5;
                }}
                .flow-net-block {{
                    text-align: right; min-width: 140px;
                }}
                .flow-net-label {{
                    color: #6e7681; font-size: 0.68em;
                    text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 3px;
                }}
                .flow-net-value {{
                    font-size: 2.1em; font-weight: 800;
                    font-variant-numeric: tabular-nums; line-height: 1;
                }}
                .flow-net-sub {{
                    color: #6e7681; font-size: 0.7em; margin-top: 2px;
                }}

                /* Dual bar */
                .flow-bar-section {{ margin-bottom: 14px; }}
                .flow-bar-labels {{
                    display: flex; justify-content: space-between;
                    font-size: 0.75em; font-weight: 600; margin-bottom: 5px;
                    font-variant-numeric: tabular-nums;
                }}
                .flow-bar-track {{
                    height: 10px; border-radius: 999px;
                    background: #0d1117; border: 1px solid #21262d;
                    display: flex; overflow: hidden;
                }}
                .flow-bar-accum {{
                    background: linear-gradient(90deg, #3fb950, #58a6ff);
                    transition: width 0.6s ease;
                    border-radius: 999px 0 0 999px;
                }}
                .flow-bar-distrib {{
                    background: linear-gradient(90deg, #f0b72f, #ff5252);
                    transition: width 0.6s ease;
                    border-radius: 0 999px 999px 0;
                }}

                /* Body: patterns + chart side-by-side */
                .flow-body {{
                    display: grid;
                    grid-template-columns: minmax(260px, 380px) minmax(0, 1fr);
                    gap: 20px; align-items: start;
                }}
                @media (max-width: 800px) {{
                    .flow-body {{ grid-template-columns: 1fr; }}
                }}
                .flow-patterns-title {{
                    color: #8b949e; font-size: 0.7em;
                    text-transform: uppercase; letter-spacing: 1.4px;
                    margin-bottom: 8px;
                }}
                .flow-pattern-row {{
                    display: grid;
                    grid-template-columns: 1.4fr 1fr auto auto;
                    gap: 6px; align-items: center;
                    padding: 7px 10px;
                    border-radius: 7px;
                    background: rgba(13,17,23,0.5);
                    border: 1px solid #21262d;
                    margin-bottom: 6px;
                    font-size: 0.8em;
                }}
                .fp-name  {{ font-weight: 700; }}
                .fp-desc  {{ color: #6e7681; font-size: 0.92em; }}
                .fp-count {{ color: #8b949e; text-align: right; white-space: nowrap; }}
                .fp-btc   {{ font-weight: 700; text-align: right;
                             font-variant-numeric: tabular-nums; white-space: nowrap; }}

                /* Chart area */
                .flow-chart-wrap {{ display: flex; flex-direction: column; gap: 6px; }}
                .flow-chart-title {{
                    color: #8b949e; font-size: 0.7em;
                    text-transform: uppercase; letter-spacing: 1.4px;
                }}
                .flow-canvas-box {{
                    position: relative; height: 200px;
                    background: rgba(13,17,23,0.4);
                    border: 1px solid #21262d; border-radius: 8px;
                    overflow: hidden;
                }}
                .flow-canvas-box canvas {{ position: absolute; inset: 0; }}
                .flow-empty {{
                    position: absolute; inset: 0;
                    display: flex; align-items: center; justify-content: center;
                    color: #6e7681; font-size: 0.85em; pointer-events: none;
                }}
                .flow-empty.hidden {{ display: none; }}
                .flow-chart-legend {{
                    display: flex; gap: 14px; font-size: 0.75em; color: #8b949e;
                }}
                .fleg-dot {{
                    display: inline-block; width: 8px; height: 8px;
                    border-radius: 50%; margin-right: 5px; vertical-align: middle;
                }}
                .flow-footer {{
                    margin-top: 12px; color: #6e7681; font-size: 0.7em;
                    text-align: right; letter-spacing: 0.4px;
                }}

                /* ===== Data Integrity badge (page footer) ===== */
                .integrity-bar {{
                    max-width: 1500px; margin: 28px auto 12px;
                    display: flex; justify-content: center;
                }}
                .integrity-badge {{
                    display: inline-flex; align-items: center; gap: 10px;
                    background: #161b22;
                    border: 1px solid #30363d;
                    border-radius: 999px;
                    padding: 8px 16px;
                    font-size: 0.82em;
                    color: #c9d1d9;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.4);
                }}
                .integrity-dot {{
                    width: 10px; height: 10px; border-radius: 50%;
                    box-shadow: 0 0 0 0 currentColor;
                    animation: integrityPulse 2s ease-in-out infinite;
                }}
                @keyframes integrityPulse {{
                    0%, 100% {{ box-shadow: 0 0 0 0 currentColor; opacity: 1; }}
                    50%      {{ box-shadow: 0 0 0 6px transparent; opacity: 0.55; }}
                }}
                .integrity-label {{ font-weight: 700; letter-spacing: 1px; }}
                .integrity-detail {{ color: #8b949e; }}
                .integrity-source {{
                    color: #6e7681; font-size: 0.95em;
                    border-left: 1px solid #30363d; padding-left: 10px; margin-left: 4px;
                }}

                /* Watchlist card (above main grid) */
                .watchlist-card {{
                    max-width: 1500px; margin: 0 auto 20px;
                    background: #161b22;
                    border: 1px solid #30363d;
                    border-left: 4px solid #ffd700;
                    border-radius: 10px; padding: 18px 22px;
                    text-align: left;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.5);
                }}
                .watchlist-header {{
                    display: flex; justify-content: space-between; align-items: flex-end;
                    flex-wrap: wrap; gap: 8px 16px; margin-bottom: 12px;
                }}
                .watchlist-title {{
                    color: #ffd700; font-size: 1.05em; font-weight: 700;
                    letter-spacing: 1px; text-transform: uppercase;
                    display: flex; align-items: center; gap: 8px;
                }}
                .watchlist-icon {{ font-size: 1.2em; }}
                .watchlist-meta {{ color: #8b949e; font-size: 0.82em; }}
                .gold-word {{ color: #ffd700; font-weight: 700; letter-spacing: 0.5px; }}
                .watchlist-hits-badge {{
                    background: #ffd700; color: #0d1117;
                    padding: 2px 9px; border-radius: 999px;
                    font-size: 0.72em; font-weight: 700;
                    animation: goldGlow 2.4s ease-in-out infinite;
                }}
                .watchlist-form {{
                    display: flex; gap: 8px; margin-bottom: 12px;
                }}
                .watchlist-input {{
                    flex: 1 1 auto; min-width: 0;
                    background: #0d1117; border: 1px solid #30363d;
                    color: #c9d1d9; padding: 9px 12px;
                    border-radius: 6px; font-size: 0.9em;
                    font-family: 'SF Mono', Consolas, monospace;
                    outline: none;
                    transition: border-color 0.15s ease;
                }}
                .watchlist-input:focus {{ border-color: #ffd700; }}
                .watchlist-input::placeholder {{ color: #6e7681; font-family: 'Segoe UI', sans-serif; }}
                .watchlist-add-btn {{
                    background: #ffd700; color: #0d1117;
                    border: 1px solid #ffd700; padding: 9px 16px;
                    border-radius: 6px; font-weight: 700;
                    font-size: 0.9em; cursor: pointer;
                    transition: background 0.15s ease;
                }}
                .watchlist-add-btn:hover {{ background: #ffec70; }}
                .watchlist-tags {{
                    display: flex; flex-wrap: wrap; gap: 8px;
                }}
                .watchlist-tag {{
                    display: inline-flex; align-items: center; gap: 8px;
                    background: rgba(255,215,0,0.1); border: 1px solid rgba(255,215,0,0.4);
                    color: #ffd700; padding: 5px 10px; border-radius: 999px;
                    font-family: 'SF Mono', Consolas, monospace; font-size: 0.82em;
                    max-width: 100%;
                }}
                .watchlist-tag .tag-addr {{
                    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                    max-width: 280px;
                }}
                .watchlist-tag .tag-remove {{
                    background: transparent; border: none; color: #ffd700;
                    cursor: pointer; font-size: 1.1em; padding: 0 2px;
                    line-height: 1; opacity: 0.7;
                }}
                .watchlist-tag .tag-remove:hover {{ opacity: 1; color: #ff7b72; }}
                .watchlist-empty {{
                    color: #6e7681; font-size: 0.85em; font-style: italic;
                }}

                /* Floating toast for watchlist matches */
                .watchlist-toast {{
                    position: fixed; bottom: 24px; right: 24px;
                    background: #161b22; color: #ffd700;
                    border: 1px solid #ffd700; border-left: 4px solid #ffd700;
                    padding: 12px 18px; border-radius: 8px;
                    box-shadow: 0 6px 20px rgba(0,0,0,0.6);
                    font-weight: 700; letter-spacing: 0.5px;
                    z-index: 9999;
                    animation: toastSlide 0.35s ease-out;
                }}
                @keyframes toastSlide {{
                    from {{ transform: translateX(100%); opacity: 0; }}
                    to   {{ transform: translateX(0); opacity: 1; }}
                }}

                /* Live pulse (now inside ticker bar) */
                @keyframes livePulseRing {{
                    0%   {{ box-shadow: 0 0 0 0 rgba(63, 185, 80, 0.6); }}
                    70%  {{ box-shadow: 0 0 0 10px rgba(63, 185, 80, 0); }}
                    100% {{ box-shadow: 0 0 0 0 rgba(63, 185, 80, 0); }}
                }}
                .live-dot {{
                    width: 8px; height: 8px; border-radius: 50%;
                    background: #3fb950;
                    animation: livePulseRing 1.6s ease-out infinite;
                    flex-shrink: 0;
                }}
                .live-label {{
                    color: #3fb950; font-weight: 700; letter-spacing: 1px;
                    text-transform: uppercase; font-size: 0.78em;
                }}
                #refresh-timer {{
                    color: #79c0ff; font-variant-numeric: tabular-nums;
                    font-weight: 600; min-width: 26px; display: inline-block;
                }}
                .live-refresh {{
                    background: transparent; border: 1px solid #30363d;
                    color: #58a6ff; padding: 3px 8px; border-radius: 999px;
                    cursor: pointer; font-size: 0.78em;
                    transition: background 0.15s ease, border-color 0.15s ease;
                }}
                .live-refresh:hover {{
                    background: #1c2128; border-color: #58a6ff;
                }}

                /* ===== Whale Movement Summary Card ===== */
                .whale-summary-card {{
                    max-width: 1100px; margin: 0 auto 22px;
                    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
                    padding: 20px 24px; box-shadow: 0 4px 15px rgba(0,0,0,0.5);
                    text-align: left;
                }}
                .whale-summary-title {{
                    color: #8b949e; font-size: 0.7em; font-weight: 700; letter-spacing: 2px;
                    text-transform: uppercase; margin-bottom: 6px;
                }}
                .whale-summary-heading {{
                    color: #fff; font-size: 1.05em; font-weight: 800; margin-bottom: 14px;
                    letter-spacing: -0.3px;
                }}
                .ws-tabs {{
                    display: flex; gap: 6px; margin-bottom: 18px; flex-wrap: wrap;
                }}
                .ws-tab {{
                    background: transparent; border: 1px solid #30363d;
                    color: #8b949e; padding: 5px 14px; border-radius: 999px;
                    font-size: 0.78em; font-weight: 600; cursor: pointer;
                    transition: all 0.15s ease;
                }}
                .ws-tab.active {{
                    border-color: #58a6ff; color: #58a6ff;
                    background: rgba(88,166,255,0.1);
                }}
                .ws-tab:hover:not(.active) {{ border-color: #58a6ff; color: #c9d1d9; }}
                .ws-panel {{ display: none; }}
                .ws-panel.active {{ display: block; }}
                .ws-grid {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(155px, 1fr));
                    gap: 12px; margin-bottom: 12px;
                }}
                .ws-metric {{
                    background: rgba(88,166,255,0.04); border: 1px solid #21262d;
                    border-radius: 8px; padding: 12px 14px;
                }}
                .ws-metric-label {{
                    color: #6e7681; font-size: 0.7em; font-weight: 600;
                    text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px;
                }}
                .ws-metric-value {{
                    color: #fff; font-size: 1.12em; font-weight: 700;
                    font-variant-numeric: tabular-nums;
                }}
                .ws-na-panel {{
                    text-align: center; padding: 26px 0;
                    color: #6e7681; font-size: 0.85em; line-height: 1.9;
                }}
                .ws-footnote {{
                    color: #6e7681; font-size: 0.7em; padding-top: 8px;
                    border-top: 1px solid #21262d; margin-top: 4px;
                }}

                /* Status pulse inside stats card */
                .status-pulse {{
                    display: inline-block; width: 10px; height: 10px;
                    border-radius: 50%; background: #3fb950;
                    margin-right: 6px; vertical-align: middle;
                    animation: livePulseRing 1.6s ease-out infinite;
                }}

                .panel {{
                    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.5); overflow: hidden;
                }}
                .panel-header {{
                    display: flex; justify-content: space-between; align-items: center;
                    flex-wrap: wrap; gap: 8px 12px;
                    padding: 14px 18px; border-bottom: 1px solid #30363d; background: #0d1117;
                }}
                .panel-title {{
                    color: #ffffff; font-size: 1.1em; text-transform: uppercase;
                    letter-spacing: 1.5px; font-weight: 600;
                }}
                .panel-meta {{ color: #8b949e; font-size: 0.85em; }}
                .panel-meta .pulse {{ color: #3fb950; }}

                .whale-table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; table-layout: auto; }}
                .whale-table th {{
                    text-align: left; padding: 11px 12px;
                    color: #8b949e; font-weight: 600; font-size: 0.75em;
                    text-transform: uppercase; letter-spacing: 0.8px;
                    border-bottom: 1px solid #30363d; background: #0d1117;
                    white-space: nowrap;
                }}
                .whale-table td {{
                    padding: 11px 12px; color: #c9d1d9;
                    border-bottom: 1px solid #21262d;
                    vertical-align: middle;
                }}
                .whale-table .col-hash {{ word-break: break-all; }}
                .whale-table tr:last-child td {{ border-bottom: none; }}
                .whale-table tr:hover td {{ background: #1c2128; }}

                /* Highlighted alert row */
                .whale-table tr.alert-row td {{
                    background: rgba(255, 23, 68, 0.08);
                    border-left: 3px solid #ff1744;
                }}
                .whale-table tr.alert-row:hover td {{ background: rgba(255, 23, 68, 0.16); }}
                .alert-badge {{
                    display: inline-block; margin-left: 6px;
                    padding: 2px 6px; border-radius: 4px;
                    background: #ff1744; color: #fff;
                    font-size: 0.65em; font-weight: 700; letter-spacing: 0.5px;
                    vertical-align: middle;
                    animation: pulseLight 1s ease-in-out infinite;
                }}

                .whale-table .rank {{ color: #79c0ff; font-weight: 700; }}
                .whale-table .amt {{ color: #f0b72f; font-weight: 600; font-variant-numeric: tabular-nums; }}
                .whale-table .usd {{ color: #3fb950; font-variant-numeric: tabular-nums; }}
                .whale-table .mono {{ font-family: 'SF Mono', Consolas, monospace; font-size: 0.88em; }}
                .whale-table a {{ color: #58a6ff; text-decoration: none; }}
                .whale-table a:hover {{ text-decoration: underline; }}

                .tweet-btn {{
                    display: inline-block; padding: 6px 12px;
                    background: #1d9bf0; color: #fff !important;
                    border-radius: 6px; font-size: 0.82em; font-weight: 600;
                    text-decoration: none !important;
                    transition: background 0.15s ease;
                }}
                .tweet-btn:hover {{ background: #1a8cd8; }}

                /* Panel actions area (e.g. Export CSV) */
                .panel-actions {{
                    display: flex; align-items: center; gap: 8px;
                    margin-left: auto;
                }}
                .export-btn {{
                    display: inline-flex; align-items: center; gap: 6px;
                    background: #238636; color: #ffffff; text-decoration: none;
                    padding: 6px 12px; border-radius: 6px;
                    font-size: 0.82em; font-weight: 600;
                    border: 1px solid #2ea043;
                    transition: background 0.15s ease, transform 0.05s ease;
                }}
                .export-btn:hover {{ background: #2ea043; }}
                .export-btn:active {{ transform: translateY(1px); }}

                /* News header w/ filter chips */
                .news-header {{
                    flex-direction: column; align-items: stretch; gap: 12px;
                }}
                .news-filters {{ display: flex; gap: 6px; flex-wrap: wrap; }}
                .filter-chip {{
                    background: transparent; border: 1px solid #30363d;
                    color: #8b949e; padding: 5px 10px; border-radius: 999px;
                    font-size: 0.78em; cursor: pointer;
                    transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
                    display: inline-flex; align-items: center; gap: 6px;
                }}
                .filter-chip:hover {{ color: #c9d1d9; border-color: #58a6ff; }}
                .filter-chip.active {{
                    background: #1f6feb22; color: #79c0ff; border-color: #58a6ff;
                }}
                .chip-count {{
                    background: #0d1117; color: #c9d1d9;
                    padding: 1px 6px; border-radius: 999px; font-size: 0.85em;
                    font-weight: 700; min-width: 16px; text-align: center;
                }}
                .filter-chip.active .chip-count {{
                    background: #58a6ff; color: #0d1117;
                }}

                /* News sidebar is sticky + scrollable so it never pushes the table */
                .news-panel {{
                    position: sticky; top: 16px;
                    max-height: calc(100vh - 32px);
                    display: flex; flex-direction: column;
                }}
                .news-panel .news-list {{
                    flex: 1 1 auto;
                    overflow-y: auto;
                    max-height: 560px;
                }}
                /* Custom scrollbar for the news list */
                .news-panel .news-list::-webkit-scrollbar {{ width: 6px; }}
                .news-panel .news-list::-webkit-scrollbar-thumb {{ background: #30363d; border-radius: 3px; }}
                .news-panel .news-list::-webkit-scrollbar-thumb:hover {{ background: #58a6ff; }}

                .news-list[data-filter="relevant"] .news-item[data-relevant="0"] {{
                    display: none;
                }}
                .news-item {{
                    display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 12px;
                    padding: 14px 14px;
                    border-bottom: 1px solid #21262d;
                    text-decoration: none; color: inherit;
                    transition: background 0.15s ease;
                    align-items: flex-start;
                }}
                .news-item:last-child {{ border-bottom: none; }}
                .news-item:hover {{ background: #1c2128; }}
                .news-item.news-item-relevant {{
                    background: rgba(63, 185, 80, 0.06);
                    border-left: 3px solid #3fb950;
                }}
                .news-item.news-item-relevant:hover {{
                    background: rgba(63, 185, 80, 0.12);
                }}
                .news-thumb {{
                    width: 72px; height: 72px; object-fit: cover;
                    border-radius: 6px; background: #0d1117;
                    border: 1px solid #21262d;
                    display: block;
                }}
                .news-body {{ min-width: 0; display: flex; flex-direction: column; gap: 8px; }}
                .news-title {{
                    color: #c9d1d9; font-size: 0.9em; font-weight: 600;
                    line-height: 1.4; margin: 0;
                    overflow-wrap: anywhere; word-break: normal;
                    display: -webkit-box; -webkit-line-clamp: 3;
                    -webkit-box-orient: vertical; overflow: hidden;
                }}
                .news-meta {{
                    display: flex; align-items: center; gap: 8px;
                    font-size: 0.72em; color: #8b949e; flex-wrap: wrap;
                    margin: 0;
                }}
                .news-source {{ color: #58a6ff; font-weight: 600; }}
                .news-badge {{
                    background: #3fb950; color: #0d1117;
                    padding: 2px 6px; border-radius: 4px;
                    font-size: 0.92em; font-weight: 700;
                    letter-spacing: 0.3px;
                }}

                .empty {{ padding: 40px; color: #8b949e; text-align: center; }}

                .footer {{ margin-top: 30px; font-size: 0.8em; color: #8b949e; }}

                /* ===== Global Nav Bar ===== */
                .ss-nav {{
                    position: fixed; top: 0; left: 0; right: 0; z-index: 1100;
                    background: rgba(13,17,23,0.98);
                    border-bottom: 1px solid #21262d;
                    display: flex; align-items: center; justify-content: space-between;
                    padding: 0 24px; height: 52px;
                    backdrop-filter: blur(12px);
                }}
                .ss-nav-brand {{
                    color: #fff; font-weight: 900; font-size: 1em;
                    letter-spacing: 2px; text-transform: uppercase;
                    background: linear-gradient(135deg,#fff 0%,#79c0ff 100%);
                    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
                    background-clip: text; text-decoration: none;
                    white-space: nowrap;
                }}
                .ss-nav-links {{
                    display: flex; align-items: center; gap: 4px;
                }}
                .ss-nav-link {{
                    color: #8b949e; font-size: 0.82em; font-weight: 600;
                    letter-spacing: 0.5px; text-decoration: none;
                    padding: 6px 12px; border-radius: 7px;
                    transition: color 0.2s, background 0.2s;
                }}
                .ss-nav-link:hover {{ color: #fff; background: rgba(255,255,255,0.06); }}
                .ss-nav-link.active {{ color: #58a6ff; }}
                .ss-nav-dropdown {{
                    position: relative; display: inline-block;
                }}
                .ss-nav-dropdown-content {{
                    display: none; position: absolute; top: calc(100% + 6px); left: 0;
                    background: #1c2128; border: 1px solid #30363d;
                    border-radius: 10px; min-width: 260px;
                    box-shadow: 0 8px 32px rgba(0,0,0,0.8);
                    z-index: 200; padding: 6px;
                }}
                .ss-nav-dropdown:hover .ss-nav-dropdown-content {{
                    display: block;
                }}
                .pro-nav-btn {{
                    position: relative; display: inline-flex; align-items: center; gap: 7px;
                    color: #fff; border: 1px solid rgba(240,183,47,0.55);
                    background: linear-gradient(135deg, rgba(240,183,47,0.16), rgba(88,166,255,0.12));
                    padding: 7px 12px; border-radius: 8px; cursor: pointer;
                    font: inherit; font-size: 0.78em; font-weight: 800; letter-spacing: 0.35px;
                    box-shadow: 0 0 18px rgba(240,183,47,0.12);
                    animation: proPulse 2.4s ease-in-out infinite;
                }}
                .pro-nav-btn:hover {{ border-color: #f0b72f; transform: translateY(-1px); }}
                .pro-nav-badge {{
                    color: #0d1117; background: #f0b72f; border-radius: 999px;
                    padding: 2px 6px; font-size: 0.67em; letter-spacing: 0.7px;
                }}
                @keyframes proPulse {{
                    0%, 100% {{ box-shadow: 0 0 12px rgba(240,183,47,0.12); }}
                    50% {{ box-shadow: 0 0 24px rgba(240,183,47,0.32); }}
                }}
                .ss-nav-dd-item {{
                    display: block; padding: 9px 14px; color: #c9d1d9;
                    font-size: 0.8em; font-weight: 600; text-decoration: none;
                    border-radius: 7px; transition: background 0.15s, color 0.15s;
                }}
                .ss-nav-dd-item:hover {{ background: rgba(88,166,255,0.1); color: #58a6ff; }}
                .ss-nav-dd-sep {{
                    height: 1px; background: #21262d; margin: 4px 0;
                }}
                .ss-nav-right {{
                    display: flex; align-items: center; gap: 10px;
                }}
                .ss-nav-user {{
                    color: #8b949e; font-size: 0.75em; max-width: 160px;
                    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                }}
                .ss-nav-upgrade {{
                    background: linear-gradient(135deg,#1f6feb 0%,#58a6ff 100%);
                    color: #fff; border: none; border-radius: 7px;
                    padding: 7px 14px; font-size: 0.78em; font-weight: 700;
                    cursor: pointer; letter-spacing: 0.5px; white-space: nowrap;
                    transition: opacity 0.2s;
                }}
                .ss-nav-upgrade:hover {{ opacity: 0.85; }}
                .ss-nav-logout {{
                    color: #6e7681; font-size: 0.75em; text-decoration: none;
                    padding: 6px 10px; border-radius: 6px;
                    transition: color 0.2s, background 0.2s;
                }}
                .ss-nav-logout:hover {{ color: #ff5252; background: rgba(255,82,82,0.08); }}

                /* ===== Trial notice bar ===== */
                .trial-notice-bar {{
                    max-width: 1500px; margin: 0 auto 18px;
                    background: rgba(88,166,255,0.08);
                    border: 1px solid rgba(88,166,255,0.2);
                    border-radius: 10px; padding: 10px 18px;
                    display: flex; align-items: center; justify-content: space-between;
                    flex-wrap: wrap; gap: 10px;
                    color: #79c0ff; font-size: 0.82em;
                }}
                .trial-upgrade-btn {{
                    background: linear-gradient(135deg,#1f6feb,#58a6ff);
                    color: #fff; border: none; border-radius: 7px;
                    padding: 7px 16px; font-size: 0.8em; font-weight: 700;
                    cursor: pointer; white-space: nowrap; transition: opacity 0.2s;
                }}
                .trial-upgrade-btn:hover {{ opacity: 0.85; }}

                /* ===== Trial expired blur overlay ===== */
                .trial-lock-overlay {{
                    display: none; position: fixed;
                    inset: 0; z-index: 9000;
                    background: rgba(0,0,0,0.72);
                    backdrop-filter: blur(14px);
                    -webkit-backdrop-filter: blur(14px);
                    align-items: center; justify-content: center; padding: 20px;
                }}
                .trial-lock-overlay.visible {{ display: flex; }}
                .lock-modal {{
                    background: linear-gradient(145deg,#161b22 0%,#0d1117 100%);
                    border: 1px solid #30363d;
                    border-top: 4px solid #ff5252;
                    border-radius: 16px;
                    padding: 36px 32px 28px;
                    max-width: 520px; width: 100%;
                    text-align: center;
                    box-shadow: 0 20px 80px rgba(0,0,0,0.9);
                }}
                .lock-icon {{ font-size: 3em; margin-bottom: 12px; }}
                .lock-title {{
                    color: #ff5252; font-size: 1.4em; font-weight: 900;
                    letter-spacing: 1px; margin-bottom: 10px; text-transform: uppercase;
                }}
                .lock-body {{
                    color: #c9d1d9; font-size: 0.88em; line-height: 1.7;
                    margin-bottom: 24px;
                }}
                .lock-price {{
                    color: #fff; font-size: 1.8em; font-weight: 900;
                    margin: 12px 0 20px;
                }}
                .lock-price span {{ color: #3fb950; }}
                .lock-btns {{
                    display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
                }}
                .lock-btn-crypto {{
                    background: linear-gradient(135deg,#1f6feb,#58a6ff);
                    color: #fff; border: none; border-radius: 10px;
                    padding: 14px 10px; font-size: 0.88em; font-weight: 700;
                    cursor: pointer; transition: opacity 0.2s; letter-spacing: 0.3px;
                }}
                .lock-btn-crypto:hover {{ opacity: 0.88; }}
                .lock-btn-alt {{
                    background: rgba(255,255,255,0.05); color: #c9d1d9;
                    border: 1px solid #30363d; border-radius: 10px;
                    padding: 14px 10px; font-size: 0.88em; font-weight: 700;
                    cursor: pointer; transition: background 0.2s; letter-spacing: 0.3px;
                }}
                .lock-btn-alt:hover {{ background: rgba(255,255,255,0.1); }}
                .lock-disclaimer {{
                    color: #6e7681; font-size: 0.72em; margin-top: 16px; line-height: 1.5;
                }}

                /* ===== Payment modal (upgrade + crypto form) ===== */
                .payment-modal-wrap {{
                    display: none; position: fixed;
                    inset: 0; z-index: 8000;
                    background: rgba(0,0,0,0.82);
                    align-items: center; justify-content: center; padding: 20px;
                }}
                .payment-modal-wrap.visible,
                .payment-modal-wrap[style*="flex"] {{ display: flex; }}
                .pro-modal-wrap {{
                    display: none; position: fixed; inset: 0; z-index: 8100;
                    background: rgba(2,6,12,0.88); align-items: center; justify-content: center;
                    padding: 18px; backdrop-filter: blur(10px);
                }}
                .pro-modal-wrap.visible {{ display: flex; }}
                .pro-modal {{
                    width: min(1120px, 100%); max-height: 94vh; overflow-y: auto;
                    background: linear-gradient(145deg,#161b22 0%,#0d1117 100%);
                    border: 1px solid rgba(88,166,255,0.28); border-top: 3px solid #f0b72f;
                    border-radius: 18px; padding: 26px; color: #c9d1d9;
                    box-shadow: 0 26px 100px rgba(0,0,0,0.92);
                }}
                .pro-modal-head {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; margin-bottom:18px; }}
                .pro-kicker {{ color:#f0b72f; font-size:.68em; font-weight:800; letter-spacing:2px; text-transform:uppercase; }}
                .pro-title {{ color:#fff; font-size:1.55em; font-weight:900; margin:5px 0 4px; }}
                .pro-subtitle {{ color:#8b949e; font-size:.82em; line-height:1.6; }}
                .pro-close {{ background:none; border:1px solid #30363d; color:#8b949e; border-radius:8px; padding:7px 10px; cursor:pointer; }}
                .pro-close:hover {{ color:#fff; border-color:#58a6ff; }}
                .pro-form {{ background:rgba(88,166,255,0.05); border:1px solid rgba(88,166,255,0.18); border-radius:12px; padding:18px; }}
                .pro-form-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:12px; }}
                .pro-field label {{ display:block; color:#8b949e; font-size:.68em; font-weight:800; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px; }}
                .pro-field input, .pro-field select {{ width:100%; background:#0d1117; border:1px solid #30363d; border-radius:8px; color:#fff; padding:11px 12px; outline:none; font:inherit; font-size:.86em; }}
                .pro-field input:focus, .pro-field select:focus {{ border-color:#58a6ff; }}
                .pro-unlock {{ margin-top:14px; width:100%; border:0; border-radius:9px; padding:13px; color:#0d1117; background:linear-gradient(135deg,#f0b72f,#ffd866); font-weight:900; cursor:pointer; }}
                .pro-unlock:disabled {{ opacity:.6; cursor:wait; }}
                .pro-form-note {{ color:#8b949e; font-size:.72em; text-align:center; margin-top:9px; }}
                .pro-error {{ display:none; color:#ff7b72; font-size:.78em; margin-top:10px; }}
                .pro-dashboard {{ display:none; }}
                .pro-dashboard.show {{ display:block; }}
                .pro-toolbar {{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin:18px 0 12px; flex-wrap:wrap; }}
                .pro-live {{ color:#3fb950; font-size:.7em; font-weight:800; letter-spacing:1px; }}
                .pro-actions {{ display:flex; gap:7px; flex-wrap:wrap; }}
                .pro-share {{ color:#79c0ff; border:1px solid rgba(88,166,255,.28); background:rgba(88,166,255,.08); border-radius:7px; padding:7px 9px; cursor:pointer; font-size:.72em; font-weight:700; }}
                .pro-share:hover {{ background:rgba(88,166,255,.18); }}
                .pro-chart-layout {{ display:grid; grid-template-columns:minmax(0,1.45fr) minmax(250px,.75fr); gap:14px; }}
                .pro-panel {{ background:rgba(13,17,23,.78); border:1px solid #30363d; border-radius:12px; padding:16px; }}
                .pro-panel-title {{ color:#fff; font-size:.76em; font-weight:800; letter-spacing:1px; text-transform:uppercase; margin-bottom:12px; }}
                .pro-chart-wrap {{ height:285px; position:relative; }}
                #pro-chart {{ width:100%; height:100%; }}
                .pro-levels {{ display:grid; gap:9px; }}
                .pro-level {{ display:flex; justify-content:space-between; gap:12px; padding:10px 11px; border:1px solid #21262d; border-radius:8px; font-size:.76em; }}
                .pro-level span:first-child {{ color:#8b949e; }} .pro-level strong {{ color:#fff; font-variant-numeric:tabular-nums; }}
                .pro-level-entry strong {{ color:#58a6ff; }} .pro-level-target strong {{ color:#3fb950; }} .pro-level-invalid strong {{ color:#ff7b72; }}
                .pro-pillars {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-top:14px; }}
                .pro-pillar {{ background:rgba(13,17,23,.78); border:1px solid #30363d; border-radius:12px; padding:15px; min-height:145px; }}
                .pro-pillar-kicker {{ color:#f0b72f; font-size:.65em; font-weight:800; letter-spacing:1px; text-transform:uppercase; }}
                .pro-pillar h3 {{ color:#fff; font-size:.95em; margin:7px 0 8px; }} .pro-pillar p {{ color:#8b949e; font-size:.75em; line-height:1.6; margin:0; }}
                .pro-score {{ color:#3fb950; font-size:1.55em; font-weight:900; margin-top:8px; }} .pro-score-label {{ color:#8b949e; font-size:.68em; }}
                .pro-narrative {{ margin-top:14px; display:grid; gap:8px; }}
                .pro-narrative p {{ margin:0; color:#c9d1d9; font-size:.79em; line-height:1.7; padding-left:12px; border-left:2px solid #58a6ff; }}
                .pro-proof {{ margin-top:14px; }} .pro-proof-row {{ display:flex; justify-content:space-between; gap:12px; padding:11px 0; border-bottom:1px solid #21262d; font-size:.75em; }}
                .pro-proof-row:last-child {{ border-bottom:0; }} .pro-proof-time {{ color:#8b949e; min-width:105px; }} .pro-proof-detail {{ color:#c9d1d9; flex:1; }}
                .pro-status {{ color:#3fb950; border:1px solid rgba(63,185,80,.35); border-radius:999px; padding:3px 8px; white-space:nowrap; font-size:.9em; }}
                .pro-disclaimer {{ color:#6e7681; font-size:.68em; line-height:1.6; border-top:1px solid #21262d; margin-top:14px; padding-top:12px; }}
                .pro-news {{ margin-top:14px; }} .pro-news-item {{ display:flex; gap:9px; padding:9px 0; border-bottom:1px solid #21262d; color:#c9d1d9; text-decoration:none; font-size:.74em; line-height:1.4; }}
                .pro-news-item:last-child {{ border-bottom:0; }} .pro-news-impact {{ color:#f0b72f; font-weight:800; margin-left:auto; white-space:nowrap; }}
                @media (max-width: 780px) {{
                    .pro-chart-layout, .pro-pillars {{ grid-template-columns:1fr; }}
                    .pro-form-grid {{ grid-template-columns:1fr; }}
                    .pro-modal {{ padding:18px; }}
                }}
                @media (max-width: 600px) {{
                    .pro-nav-btn {{ padding:6px 8px; }} .pro-nav-btn span:first-child {{ display:none; }}
                    .ss-nav-user, .ss-nav-upgrade {{ display:none; }}
                    .pro-proof-row {{ flex-wrap:wrap; }} .pro-proof-time {{ min-width:auto; }}
                }}
                .pay-modal {{
                    background: linear-gradient(145deg,#161b22 0%,#0d1117 100%);
                    border: 1px solid #30363d;
                    border-top: 4px solid #3fb950;
                    border-radius: 16px; padding: 30px 28px 24px;
                    max-width: 480px; width: 100%;
                    box-shadow: 0 20px 80px rgba(0,0,0,0.9);
                    max-height: 92vh; overflow-y: auto;
                }}
                .pay-close {{
                    float: right; background: none; border: none;
                    color: #6e7681; font-size: 1.3em; cursor: pointer;
                    line-height: 1; padding: 0; margin: -4px -4px 0 0;
                }}
                .pay-close:hover {{ color: #fff; }}
                .pay-title {{
                    color: #fff; font-size: 1.15em; font-weight: 800;
                    margin-bottom: 4px; margin-top: 0; text-align: center;
                }}
                .pay-sub {{
                    color: #8b949e; font-size: 0.78em; text-align: center;
                    margin-bottom: 20px;
                }}
                .pay-price-badge {{
                    background: linear-gradient(135deg,rgba(63,185,80,0.08),rgba(31,111,235,0.08));
                    border: 1px solid rgba(63,185,80,0.3);
                    border-radius: 12px; padding: 16px 20px; text-align: center;
                    margin-bottom: 18px;
                    box-shadow: 0 0 24px rgba(63,185,80,0.07);
                }}
                .pay-price-amount {{
                    color: #3fb950; font-size: 2.6em; font-weight: 900;
                    line-height: 1; letter-spacing: -1px;
                }}
                .pay-price-for {{
                    color: #8b949e; font-size: 0.72em; font-weight: 700;
                    text-transform: uppercase; letter-spacing: 1.5px;
                    margin: 4px 0 2px;
                }}
                .pay-price-label {{
                    color: #3fb950; font-size: 0.82em; font-weight: 700; margin-top: 2px;
                }}
                .pay-price-perks {{
                    display: flex; justify-content: center; gap: 14px;
                    margin-top: 10px; flex-wrap: wrap;
                }}
                .pay-price-perk {{
                    color: #6e7681; font-size: 0.7em; display: flex; align-items: center; gap: 4px;
                }}
                .pay-section-title {{
                    color: #58a6ff; font-size: 0.72em; font-weight: 700;
                    text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 8px;
                }}
                .pay-addr-box {{
                    background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
                    padding: 12px 14px; font-family: monospace; font-size: 0.74em;
                    color: #58a6ff; word-break: break-all; line-height: 1.6;
                    margin-bottom: 10px; position: relative;
                }}
                .pay-copy-btn {{
                    display: block; width: 100%; background: rgba(88,166,255,0.1);
                    border: 1px solid rgba(88,166,255,0.25); border-radius: 7px;
                    color: #58a6ff; font-size: 0.78em; font-weight: 700;
                    padding: 9px; cursor: pointer; margin-bottom: 14px;
                    transition: background 0.2s;
                }}
                .pay-copy-btn:hover {{ background: rgba(88,166,255,0.2); }}
                .pay-qr {{
                    display: block; margin: 0 auto 12px;
                    border-radius: 8px; border: 2px solid #30363d;
                }}
                .pay-divider {{
                    border: none; border-top: 1px solid #21262d; margin: 16px 0;
                }}
                .pay-input {{
                    display: block; width: 100%; background: #0d1117;
                    border: 1px solid #30363d; border-radius: 8px;
                    padding: 11px 14px; color: #fff; font-size: 0.88em;
                    font-family: inherit; outline: none; margin-bottom: 12px;
                    transition: border-color 0.2s;
                }}
                .pay-input:focus {{ border-color: #58a6ff; }}
                .pay-submit {{
                    width: 100%; background: linear-gradient(135deg,#238636,#3fb950);
                    color: #fff; border: none; border-radius: 8px;
                    padding: 13px; font-size: 0.92em; font-weight: 700;
                    cursor: pointer; transition: opacity 0.2s; letter-spacing: 0.4px;
                }}
                .pay-submit:hover {{ opacity: 0.88; }}
                .pay-alt-section {{
                    display: none; text-align: center;
                }}
                .pay-alt-section.show {{ display: block; }}
                .pay-alt-contact {{
                    background: rgba(63,185,80,0.06); border: 1px solid rgba(63,185,80,0.2);
                    border-radius: 10px; padding: 20px 16px; margin-bottom: 12px;
                }}
                .pay-tab {{
                    background: none; border: 1px solid #30363d; border-radius: 7px;
                    color: #8b949e; font-size: 0.82em; font-weight: 600;
                    padding: 8px 16px; cursor: pointer; margin: 4px;
                    transition: all 0.2s;
                }}
                .pay-tab.active {{ background: rgba(88,166,255,0.12); color: #58a6ff; border-color: rgba(88,166,255,0.3); }}

                /* ===== Tool Academy ===== */
                .academy-section {{
                    max-width: 1500px; margin: 0 auto 22px;
                    background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
                    border: 1px solid #30363d;
                    border-left: 4px solid #f0b72f;
                    border-radius: 12px;
                    padding: 28px 30px;
                    text-align: left;
                    box-shadow: 0 4px 18px rgba(0,0,0,0.55);
                }}
                .academy-eyebrow {{
                    color: #f0b72f; font-size: 0.7em;
                    text-transform: uppercase; letter-spacing: 2px; margin-bottom: 8px;
                    font-weight: 700;
                }}
                .academy-title {{
                    color: #fff; font-size: 1.4em; font-weight: 900;
                    margin-bottom: 6px; letter-spacing: 0.5px;
                }}
                .academy-sub {{
                    color: #8b949e; font-size: 0.84em; line-height: 1.6; margin-bottom: 24px;
                }}
                .academy-chapters {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
                    gap: 16px;
                }}
                .academy-chapter {{
                    background: rgba(13,17,23,0.7); border: 1px solid #21262d;
                    border-radius: 10px; padding: 20px;
                    cursor: pointer; transition: border-color 0.2s, background 0.2s;
                }}
                .academy-chapter:hover {{
                    border-color: rgba(240,183,47,0.4);
                    background: rgba(240,183,47,0.03);
                }}
                .academy-chapter.open {{
                    border-color: rgba(240,183,47,0.35);
                    background: rgba(240,183,47,0.04);
                }}
                .academy-ch-head {{
                    display: flex; justify-content: space-between; align-items: flex-start;
                    gap: 10px;
                }}
                .academy-ch-num {{
                    color: #f0b72f; font-size: 0.68em; font-weight: 700;
                    text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px;
                }}
                .academy-ch-title {{
                    color: #fff; font-size: 0.95em; font-weight: 700;
                    line-height: 1.3; margin-bottom: 4px;
                }}
                .academy-ch-tag {{
                    background: rgba(240,183,47,0.1); border: 1px solid rgba(240,183,47,0.25);
                    color: #f0b72f; font-size: 0.65em; padding: 2px 8px;
                    border-radius: 999px; font-weight: 700; white-space: nowrap;
                    flex-shrink: 0;
                }}
                .academy-ch-preview {{
                    color: #8b949e; font-size: 0.78em; line-height: 1.5; margin-top: 4px;
                }}
                .academy-ch-body {{
                    display: none; margin-top: 14px; padding-top: 14px;
                    border-top: 1px solid #21262d;
                    color: #c9d1d9; font-size: 0.82em; line-height: 1.75;
                }}
                .academy-chapter.open .academy-ch-body {{ display: block; }}
                .academy-arrow {{
                    color: #6e7681; font-size: 0.9em; transition: transform 0.2s;
                    flex-shrink: 0; margin-top: 2px;
                }}
                .academy-chapter.open .academy-arrow {{ transform: rotate(180deg); }}
                .academy-highlight {{
                    background: rgba(240,183,47,0.08); border-left: 3px solid #f0b72f;
                    border-radius: 0 6px 6px 0; padding: 10px 14px;
                    margin: 10px 0; font-size: 0.9em;
                }}
                .academy-list {{
                    padding-left: 18px; margin: 8px 0;
                }}
                .academy-list li {{ margin-bottom: 6px; }}
                @media (max-width: 600px) {{
                    .ss-nav-links {{ display: none; }}
                    .lock-btns {{ grid-template-columns: 1fr; }}
                    .fg-factors {{ grid-template-columns: 1fr 1fr; }}
                }}

                /* ===== Fear & Greed Index card ===== */
                .fg-card {{
                    max-width: 1500px; margin: 0 auto 22px;
                    background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
                    border: 1px solid #30363d;
                    border-left: 4px solid #f0b72f;
                    border-radius: 12px;
                    padding: 18px 24px 20px;
                    text-align: left;
                    box-shadow: 0 4px 18px rgba(0,0,0,0.55);
                    display: grid;
                    grid-template-columns: 110px 1fr;
                    gap: 22px; align-items: center;
                    position: relative;
                }}
                @media (max-width: 580px) {{
                    .fg-card {{ grid-template-columns: 1fr; }}
                    .fg-score-block {{ justify-self: center; }}
                }}
                .fg-score-block {{ display: flex; align-items: center; justify-content: center; }}
                .fg-circle {{
                    width: 100px; height: 100px; border-radius: 50%;
                    border: 3px solid #f0b72f;
                    display: flex; flex-direction: column;
                    align-items: center; justify-content: center;
                    background: rgba(13,17,23,0.75);
                    flex-shrink: 0;
                }}
                .fg-num {{
                    font-size: 2.6em; font-weight: 800; line-height: 1;
                    font-variant-numeric: tabular-nums;
                }}
                .fg-denom {{
                    color: #6e7681; font-size: 0.65em;
                    font-weight: 600; letter-spacing: 0.5px; margin-top: 1px;
                }}
                .fg-eyebrow {{
                    color: #8b949e; font-size: 0.72em;
                    text-transform: uppercase; letter-spacing: 1.6px; margin-bottom: 6px;
                }}
                .fg-label-row {{
                    display: flex; align-items: center; gap: 10px; margin-bottom: 10px;
                }}
                .fg-emoji {{ font-size: 1.5em; line-height: 1; }}
                .fg-label {{
                    font-size: 1.5em; font-weight: 800;
                    letter-spacing: 1px; text-transform: uppercase;
                }}
                .fg-gradient-wrap {{ margin-bottom: 12px; }}
                .fg-gradient-track {{
                    height: 14px; border-radius: 999px;
                    background: linear-gradient(90deg,
                        #ff5252 0%, #ff7043 20%,
                        #f0b72f 45%, #7ac157 72%, #3fb950 100%);
                    position: relative;
                    border: 1px solid rgba(255,255,255,0.05);
                    overflow: visible;
                }}
                .fg-pointer {{
                    position: absolute; top: 50%;
                    transform: translate(-50%, -50%);
                    width: 18px; height: 18px; border-radius: 50%;
                    background: #ffffff; border: 3px solid #0d1117;
                    box-shadow: 0 0 10px rgba(0,0,0,0.8);
                    transition: left 0.8s cubic-bezier(0.4,0,0.2,1);
                    z-index: 2;
                }}
                .fg-bar-zone-labels {{
                    display: flex; justify-content: space-between;
                    font-size: 0.6em; color: #6e7681; margin-top: 5px;
                    padding: 0 2px;
                }}
                .fg-factors {{
                    display: grid;
                    grid-template-columns: repeat(3, 1fr);
                    gap: 10px;
                }}
                .fg-factor {{
                    background: rgba(13,17,23,0.65);
                    border: 1px solid #21262d;
                    border-radius: 8px; padding: 8px 12px;
                }}
                .fg-factor-label {{
                    color: #8b949e; font-size: 0.68em;
                    text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px;
                }}
                .fg-factor-track {{
                    height: 4px; background: #21262d;
                    border-radius: 999px; overflow: hidden; margin-bottom: 5px;
                }}
                .fg-factor-fill {{
                    height: 100%; border-radius: 999px;
                    transition: width 0.6s ease;
                }}
                .fg-factor-val {{
                    font-size: 0.88em; font-weight: 700;
                    font-variant-numeric: tabular-nums;
                }}
                .fg-factor-max {{
                    color: #6e7681; font-size: 0.82em; font-weight: 400;
                }}

                /* ===== Live BTC Ticker Banner ===== */
                #btc-ticker {{
                    position: fixed; top: 52px; left: 0; right: 0; z-index: 999;
                    background: rgba(13,17,23,0.97);
                    border-bottom: 1px solid #30363d;
                    backdrop-filter: blur(8px);
                    display: flex; align-items: center; justify-content: space-between;
                    padding: 0 16px; height: 36px;
                    font-size: 0.82em; font-weight: 600; letter-spacing: 0.6px;
                    color: #c9d1d9;
                }}
                .ticker-left {{
                    display: flex; align-items: center; gap: 8px; flex-shrink: 0;
                }}
                .ticker-right {{
                    display: flex; align-items: center; gap: 7px; flex-shrink: 0;
                }}
                .ticker-vsep {{
                    color: #30363d; font-size: 1.1em; padding: 0 2px;
                }}
                @media (max-width: 480px) {{
                    .ticker-right .live-label,
                    .ticker-right .ticker-vsep {{ display: none; }}
                }}
                .ticker-dot {{
                    width: 8px; height: 8px; border-radius: 50%;
                    background: #3fb950; flex-shrink: 0;
                    box-shadow: 0 0 0 0 rgba(63,185,80,0.7);
                    animation: tickerPulse 1.6s ease-in-out infinite;
                }}
                @keyframes tickerPulse {{
                    0%, 100% {{ box-shadow: 0 0 0 0 rgba(63,185,80,0.6); }}
                    50%       {{ box-shadow: 0 0 0 5px rgba(63,185,80,0); }}
                }}
                .ticker-label {{ color: #8b949e; font-size: 0.88em; letter-spacing: 1.2px; }}
                .ticker-sym   {{ color: #8b949e; }}
                .ticker-price {{
                    color: #ffffff; font-weight: 700;
                    font-variant-numeric: tabular-nums; font-size: 1.0em;
                    transition: color 0.4s ease;
                }}
                .ticker-change {{
                    font-size: 0.82em; font-weight: 700;
                    font-variant-numeric: tabular-nums;
                    min-width: 52px; text-align: left;
                    transition: color 0.4s ease;
                }}
                .ticker-source {{
                    color: #6e7681; font-size: 0.75em; font-weight: 400;
                    border-left: 1px solid #30363d; padding-left: 10px; margin-left: 4px;
                }}
                @media (max-width: 600px) {{
                    .ticker-source {{ display: none; }}
                    #btc-ticker {{ font-size: 0.75em; gap: 7px; }}
                }}

                /* ===== Legacy Wallet Risk Monitor ===== */
                .legacy-card {{
                    max-width: 1500px; margin: 0 auto 22px;
                    background: linear-gradient(135deg, #1a1a0d 0%, #0d1117 100%);
                    border: 1px solid #30363d;
                    border-left: 4px solid #f0b72f;
                    border-radius: 12px;
                    padding: 18px 24px 20px;
                    text-align: left;
                    box-shadow: 0 4px 18px rgba(0,0,0,0.55);
                }}
                .legacy-eyebrow {{
                    color: #8b949e; font-size: 0.72em;
                    text-transform: uppercase; letter-spacing: 1.6px; margin-bottom: 6px;
                }}
                .legacy-title-row {{
                    display: flex; align-items: center; gap: 10px;
                    flex-wrap: wrap; margin-bottom: 12px;
                }}
                .legacy-title {{
                    color: #f0b72f; font-size: 1.05em; font-weight: 700;
                    letter-spacing: 0.4px; margin: 0;
                    display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap;
                }}
                .legacy-sub {{
                    color: #8b949e; font-size: 0.78em; font-weight: 400; letter-spacing: 0;
                }}
                .legacy-info-btn {{
                    width: 18px; height: 18px; border-radius: 50%;
                    background: rgba(240,183,47,0.12);
                    border: 1px solid rgba(240,183,47,0.4);
                    color: #f0b72f; font-size: 0.72em; font-weight: 700;
                    cursor: default; display: inline-flex;
                    align-items: center; justify-content: center;
                    position: relative; flex-shrink: 0; line-height: 1;
                    font-style: normal;
                }}
                .legacy-tooltip {{
                    display: none; position: absolute;
                    bottom: 130%; left: 50%; transform: translateX(-50%);
                    background: #1e2530; border: 1px solid #30363d;
                    color: #c9d1d9; font-size: 0.88em; font-weight: 400;
                    padding: 10px 13px; border-radius: 8px;
                    width: 290px; line-height: 1.5;
                    white-space: normal; letter-spacing: 0; text-align: left;
                    box-shadow: 0 6px 20px rgba(0,0,0,0.65); z-index: 60;
                    pointer-events: none;
                }}
                .legacy-info-btn:hover .legacy-tooltip,
                .legacy-info-btn:focus .legacy-tooltip {{ display: block; }}
                .legacy-stats-row {{
                    display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 14px;
                }}
                .legacy-stat {{
                    background: rgba(13,17,23,0.65); border: 1px solid #21262d;
                    border-radius: 8px; padding: 8px 16px; min-width: 140px;
                }}
                .legacy-stat-label {{
                    color: #6e7681; font-size: 0.68em;
                    text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 3px;
                }}
                .legacy-stat-value {{
                    color: #c9d1d9; font-size: 1.0em; font-weight: 700;
                    font-variant-numeric: tabular-nums;
                }}
                .legacy-movement-badge {{
                    display: none; align-items: center; gap: 8px;
                    background: rgba(255,82,82,0.1); border: 1px solid #ff5252;
                    border-radius: 8px; padding: 9px 14px;
                    color: #ff5252; font-size: 0.82em; font-weight: 700;
                    letter-spacing: 0.4px; margin-bottom: 14px;
                    animation: legacyAlert 1.8s ease-in-out infinite;
                }}
                .legacy-movement-badge.visible {{ display: flex; }}
                @keyframes legacyAlert {{
                    0%, 100% {{ border-color: #ff5252; box-shadow: 0 0 0 0 rgba(255,82,82,0.3); }}
                    50%       {{ border-color: #ff7070; box-shadow: 0 0 14px 2px rgba(255,82,82,0.18); }}
                }}
                .legacy-table {{
                    width: 100%; border-collapse: collapse; font-size: 0.82em;
                }}
                .legacy-table th {{
                    color: #6e7681; font-size: 0.75em;
                    text-transform: uppercase; letter-spacing: 1px;
                    padding: 6px 10px; border-bottom: 1px solid #21262d;
                    text-align: left; white-space: nowrap;
                }}
                .legacy-table td {{
                    padding: 9px 10px; border-bottom: 1px solid #161b22;
                    color: #c9d1d9; vertical-align: middle;
                }}
                .legacy-table tr:last-child td {{ border-bottom: none; }}
                .legacy-table tr:hover td {{ background: rgba(240,183,47,0.04); }}
                .legacy-addr a {{
                    font-family: 'Courier New', monospace; font-size: 0.92em;
                    color: #58a6ff; text-decoration: none;
                    word-break: break-all;
                }}
                .legacy-addr a:hover {{ text-decoration: underline; }}
                .legacy-addr-label {{
                    color: #8b949e; font-size: 0.82em; margin-top: 3px;
                }}
                .legacy-bal {{
                    color: #f0b72f; font-variant-numeric: tabular-nums;
                    font-weight: 700; white-space: nowrap;
                }}
                .legacy-status-pill {{
                    display: inline-block; padding: 3px 9px;
                    border-radius: 999px; font-size: 0.82em; font-weight: 700;
                    border: 1px solid currentColor; letter-spacing: 0.3px;
                    white-space: nowrap;
                }}
                .legacy-footer {{
                    margin-top: 12px; color: #6e7681; font-size: 0.7em;
                    text-align: right; letter-spacing: 0.3px;
                }}
                @media (max-width: 700px) {{
                    .legacy-table .legacy-bal {{ display: none; }}
                }}
            </style>
        </head>
        <body>
            <!-- ===== GLOBAL NAV BAR ===== -->
            <nav class="ss-nav" role="navigation" aria-label="Main navigation">
                <a href="/" class="ss-nav-brand">🐋 SKILL SHIELD BTC</a>
                <div class="ss-nav-links">
                    <a href="/" class="ss-nav-link active">Live Dashboard</a>
                    <div class="ss-nav-dropdown">
                        <a href="#academy" class="ss-nav-link"
                           onclick="document.getElementById('tool-academy').scrollIntoView({{behavior:'smooth'}});return false;">
                            Tool Academy ▾
                        </a>
                        <div class="ss-nav-dropdown-content">
                            <a class="ss-nav-dd-item" href="#academy" onclick="openChapter(0);scrollToAcademy();return false;">📖 How It Works</a>
                            <a class="ss-nav-dd-item" href="#academy" onclick="openChapter(1);scrollToAcademy();return false;">📊 Data Interpretation</a>
                            <div class="ss-nav-dd-sep"></div>
                            <a class="ss-nav-dd-item" href="#academy" onclick="openChapter(2);scrollToAcademy();return false;">📐 Multi-Signal Confluence Model</a>
                            <a class="ss-nav-dd-item" href="#academy" onclick="openChapter(3);scrollToAcademy();return false;">🐋 How Institutions Manipulate Retail Traders</a>
                            <a class="ss-nav-dd-item" href="#academy" onclick="openChapter(4);scrollToAcademy();return false;">⚡ Velocity Signal Guide</a>
                        </div>
                    </div>
                    <button type="button" class="pro-nav-btn" onclick="openProSignals()" aria-haspopup="dialog">
                        <span>◆ Pro Analysis Signals</span><span class="pro-nav-badge">VIP</span>
                    </button>
                </div>
                <div class="ss-nav-right">
                    <span class="ss-nav-user">{user_email}</span>
                    {nav_upgrade_btn}
                    {nav_admin_link}
                    <a href="/logout" class="ss-nav-logout">Sign Out</a>
                </div>
            </nav>

            <!-- ===== TRIAL EXPIRED LOCK OVERLAY ===== -->
            <div class="trial-lock-overlay {trial_lock_cls}" id="trial-lock-overlay"
                 role="dialog" aria-modal="true" aria-label="Trial Expired — Upgrade Required">
                <div class="lock-modal">
                    <div class="lock-icon">🔒</div>
                    <div class="lock-title">Trial Period Ended</div>
                    <div class="lock-body">
                        Your free trial has ended. Continue with live mempool data, whale flow
                        tracking, and network velocity signals on a monthly or annual plan.
                    </div>
                    <div class="lock-price">$99<span>/month</span> or $999<span>/year</span></div>
                    <div class="lock-btns">
                        <button class="lock-btn-crypto"
                            onclick="document.getElementById('trial-lock-overlay').style.display='none';
                                     document.getElementById('payment-modal').style.display='flex';">
                            💎 Pay with Crypto (USDT)
                        </button>
                        <button class="lock-btn-alt"
                            onclick="document.getElementById('trial-lock-overlay').style.display='none';
                                     document.getElementById('payment-modal').style.display='flex';
                                     switchPayTab('alt');">
                            💳 Card / Alternative
                        </button>
                    </div>
                    <div class="lock-disclaimer">
                        Secure · Cancel anytime · No hidden fees
                    </div>
                </div>
            </div>

            <!-- ===== PAYMENT MODAL ===== -->
            <div class="payment-modal-wrap" id="payment-modal"
                 role="dialog" aria-modal="true" aria-label="Upgrade to Premium">
                <div class="pay-modal">
                    <button class="pay-close" onclick="document.getElementById('payment-modal').style.display='none'" aria-label="Close">✕</button>
                    <div class="pay-title">🔐 Choose Your Plan</div>
                    <div class="pay-sub">Full access to live whale intelligence · Cancel anytime</div>
                    <div style="display:flex;gap:12px;margin-bottom:16px;justify-content:center;flex-wrap:wrap">
                        <div style="flex:1;min-width:130px;max-width:170px;background:rgba(88,166,255,0.07);border:1px solid rgba(88,166,255,0.25);border-radius:12px;padding:16px 14px;text-align:center">
                            <div style="color:#8b949e;font-size:0.7em;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Monthly</div>
                            <div style="color:#fff;font-size:1.8em;font-weight:900;line-height:1">$99</div>
                            <div style="color:#8b949e;font-size:0.75em;margin-bottom:8px">/ month</div>
                            <div style="color:#79c0ff;font-size:0.72em">✓ All whale tools<br>✓ Cancel anytime</div>
                        </div>
                        <div style="flex:1;min-width:130px;max-width:170px;background:rgba(63,185,80,0.08);border:2px solid rgba(63,185,80,0.4);border-radius:12px;padding:16px 14px;text-align:center;position:relative">
                            <div style="position:absolute;top:-10px;left:50%;transform:translateX(-50%);background:#3fb950;color:#0d1117;font-size:0.62em;font-weight:800;padding:2px 10px;border-radius:999px;white-space:nowrap">BEST VALUE</div>
                            <div style="color:#8b949e;font-size:0.7em;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Yearly</div>
                            <div style="color:#fff;font-size:1.8em;font-weight:900;line-height:1">$999</div>
                            <div style="color:#8b949e;font-size:0.75em;margin-bottom:8px">/ year · Save 16%</div>
                            <div style="color:#3fb950;font-size:0.72em">✓ All whale tools<br>✓ All future updates</div>
                        </div>
                    </div>
                    <!-- Plan selector (drives USDT amount) -->
                    <div style="display:flex;gap:8px;margin-bottom:14px;justify-content:center">
                        <button id="plan-btn-monthly" onclick="selectPlan('monthly')"
                                style="flex:1;max-width:170px;padding:8px 14px;border-radius:8px;font-size:0.82em;font-weight:700;cursor:pointer;border:2px solid #58a6ff;background:rgba(88,166,255,0.15);color:#58a6ff;transition:all 0.2s">
                            MONTHLY ($99)
                        </button>
                        <button id="plan-btn-yearly" onclick="selectPlan('yearly')"
                                style="flex:1;max-width:170px;padding:8px 14px;border-radius:8px;font-size:0.82em;font-weight:700;cursor:pointer;border:2px solid #30363d;background:transparent;color:#8b949e;transition:all 0.2s">
                            YEARLY ($999)
                        </button>
                    </div>
                    <div style="display:flex;gap:8px;margin-bottom:16px;justify-content:center">
                        <button class="pay-tab active" id="tab-crypto" onclick="switchPayTab('crypto')">💎 USDT (Crypto)</button>
                        <button class="pay-tab" id="tab-alt" onclick="switchPayTab('alt')">💳 Card / Other</button>
                    </div>

                    <!-- Crypto section -->
                    <div id="pay-crypto-section">
                        <div class="pay-section-title">📡 USDT BEP-20 Deposit Address</div>
                        <img class="pay-qr" width="150" height="150"
                             src="https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=0xe1ABA6BdE1DB30d71143F072648C296109b4A578"
                             alt="USDT BEP-20 QR Code">
                        <div class="pay-addr-box" id="wallet-addr">0xe1ABA6BdE1DB30d71143F072648C296109b4A578</div>
                        <button class="pay-copy-btn" onclick="copyWalletAddr()">📋 Click to Copy Address</button>
                        <div style="display:flex;gap:8px;justify-content:center;margin-bottom:8px;flex-wrap:wrap;">
                            <span style="background:rgba(88,166,255,0.1);border:1px solid rgba(88,166,255,0.3);border-radius:6px;padding:4px 12px;font-size:0.72em;font-weight:700;color:#58a6ff;letter-spacing:0.5px;">
                                ASSET: USDT
                            </span>
                            <span style="background:rgba(240,183,47,0.1);border:1px solid rgba(240,183,47,0.3);border-radius:6px;padding:4px 12px;font-size:0.72em;font-weight:700;color:#f0b72f;letter-spacing:0.5px;">
                                NETWORK: BNB Smart Chain (BEP-20)
                            </span>
                        </div>
                        <div id="usdt-amount-notice" style="color:#8b949e;font-size:0.75em;margin-bottom:8px;">
                            ⚠ Send <b>exactly $99 USDT</b> on the <b>BEP-20 (BSC) network only</b>. Wrong network = lost funds.
                        </div>
                        <div style="background:rgba(255,82,82,0.07);border:1px solid rgba(255,82,82,0.28);border-radius:8px;padding:10px 14px;margin-bottom:10px;font-size:0.74em;line-height:1.7;text-align:left;">
                            <div style="color:#ff5252;font-weight:700;margin-bottom:4px;">⚠️ CRITICAL NETWORK SAFETY WARNING</div>
                            <div style="color:#c9d1d9;">Send USDT on <b>BEP-20 (BSC)</b> network only.</div>
                            <div style="color:#ff7b7b;">❌ Do <b>NOT</b> send via <b>TRC-20 (Tron)</b> or <b>ERC-20 (Ethereum)</b>.</div>
                            <div style="color:#8b949e;margin-top:3px;">Wrong network = <b>permanent loss of funds</b>. This cannot be reversed.</div>
                        </div>
                        <hr class="pay-divider">
                        <div class="pay-section-title">✅ Submit Your Transaction Hash</div>
                        <form method="POST" action="/submit-payment">
                            <input type="hidden" name="plan_type" id="selected-plan-input" value="monthly">
                            <input class="pay-input" type="text" name="tx_hash" id="tx-hash-input"
                                   placeholder="Paste TxID / Transaction Hash here…" required>
                            <div style="color:#8b949e;font-size:0.72em;margin-bottom:6px;">
                                Found in your wallet's transaction history. Starts with 0x…
                            </div>
                            <div style="background:rgba(240,183,47,0.08);border:1px solid rgba(240,183,47,0.25);border-radius:8px;padding:10px 12px;color:#f0b72f;font-size:0.75em;line-height:1.5;margin-bottom:12px;">
                                ⏱ Once your TxID is submitted, your transaction is queued and manually audited within <b>2–4 hours</b> for plan activation. You will retain full access during this review window.
                            </div>
                            <button type="submit" class="pay-submit">🚀 Submit for Verification</button>
                        </form>
                    </div>

                    <!-- Alt payment section -->
                    <div id="pay-alt-section" class="pay-alt-section">
                        <div class="pay-alt-contact">
                            <div style="font-size:1.3em;margin-bottom:8px">💳</div>
                            <div style="color:#fff;font-weight:700;margin-bottom:6px">Pay via Card, PayPal, or Bank Transfer</div>
                            <div style="color:#8b949e;font-size:0.82em;line-height:1.6;margin-bottom:16px">
                                Email our support team for a direct payment link.
                                We accept Visa, Mastercard, PayPal, and most regional methods.
                            </div>
                            <a href="mailto:support@skillshieldbtc.com?subject=Payment%20Request%20—%20Skill%20Shield%20BTC"
                               style="display:inline-block;background:linear-gradient(135deg,#1f6feb,#58a6ff);
                                      color:#fff;text-decoration:none;border-radius:8px;
                                      padding:12px 24px;font-size:0.88em;font-weight:700;margin-bottom:12px">
                                ✉ Email Support
                            </a>
                            <div style="color:#8b949e;font-size:0.78em;line-height:1.6">
                                <b style="color:#c9d1d9">support@skillshieldbtc.com</b><br>
                                Please include your registered email address and chosen plan (Monthly / Yearly).
                            </div>
                        </div>
                        <div style="color:#6e7681;font-size:0.72em;margin-top:10px">
                            Typical response time: within a few hours · support@skillshieldbtc.com
                        </div>
                    </div>
                </div>
            </div>

            <!-- ===== PRO ANALYSIS SIGNALS ===== -->
            <div class="pro-modal-wrap" id="pro-modal" role="dialog" aria-modal="true"
                 aria-labelledby="pro-modal-title">
                <div class="pro-modal">
                    <div class="pro-modal-head">
                        <div>
                            <div class="pro-kicker">◆ Skill Shield Alpha · Core Analyst Desk</div>
                            <div class="pro-title" id="pro-modal-title">Access Skill Shield Pro Intelligence</div>
                            <div class="pro-subtitle">A curated market brief built from live public market structure, on-chain flow, and verified crypto headlines.</div>
                        </div>
                        <button type="button" class="pro-close" onclick="closeProSignals()" aria-label="Close Pro Analysis Signals">✕</button>
                    </div>

                    <div class="pro-form" id="pro-lead-form-wrap">
                        <form id="pro-lead-form">
                            <div class="pro-form-grid">
                                <div class="pro-field">
                                    <label for="pro-name">Full Name</label>
                                    <input id="pro-name" name="name" type="text" maxlength="120" required placeholder="Your full name">
                                </div>
                                <div class="pro-field">
                                    <label for="pro-email">Email Address</label>
                                    <input id="pro-email" name="email" type="email" maxlength="254" required placeholder="you@example.com">
                                </div>
                                <div class="pro-field">
                                    <label for="pro-platform">Preferred Contact Platform</label>
                                    <select id="pro-platform" name="platform" required>
                                        <option value="">Choose a platform…</option>
                                        <option>WhatsApp</option><option>Telegram</option><option>Twitter/X</option>
                                        <option>Discord</option><option>Reddit</option>
                                    </select>
                                </div>
                                <div class="pro-field">
                                    <label for="pro-handle">Handle / Phone Number</label>
                                    <input id="pro-handle" name="handle" type="text" maxlength="120" required placeholder="Your handle or phone number">
                                </div>
                            </div>
                            <button class="pro-unlock" id="pro-unlock-btn" type="submit">[ Unlock Free Pro Access ]</button>
                            <div class="pro-form-note">Limited Time Free Access Granted by Skill Shield Core Analyst Team.</div>
                            <div class="pro-error" id="pro-lead-error" role="alert"></div>
                        </form>
                    </div>

                    <div class="pro-dashboard" id="pro-dashboard">
                        <div class="pro-toolbar">
                            <div><span class="pro-live">● LIVE PRO BRIEF</span><div class="pro-subtitle">Updated <span id="pro-updated">—</span> · sources remain independently verifiable</div></div>
                            <div class="pro-actions" aria-label="Share Pro Analysis">
                                <button class="pro-share" onclick="sharePro('telegram')">Telegram</button>
                                <button class="pro-share" onclick="sharePro('whatsapp')">WhatsApp</button>
                                <button class="pro-share" onclick="sharePro('twitter')">Twitter/X</button>
                                <button class="pro-share" onclick="sharePro('discord')">Discord</button>
                                <button class="pro-share" onclick="sharePro('reddit')">Reddit</button>
                                <button class="pro-share" onclick="sharePro('email')">Email</button>
                            </div>
                        </div>
                        <div class="pro-chart-layout">
                            <div class="pro-panel">
                                <div class="pro-panel-title">BTC/USDT · Structure &amp; Key Levels</div>
                                <div class="pro-chart-wrap"><canvas id="pro-chart"></canvas></div>
                            </div>
                            <div class="pro-panel">
                                <div class="pro-panel-title">Desk Levels</div>
                                <div class="pro-levels">
                                    <div class="pro-level pro-level-entry"><span>Entry Zone</span><strong id="pro-entry">—</strong></div>
                                    <div class="pro-level pro-level-target"><span>Target 1</span><strong id="pro-target1">—</strong></div>
                                    <div class="pro-level pro-level-target"><span>Target 2</span><strong id="pro-target2">—</strong></div>
                                    <div class="pro-level pro-level-invalid"><span>Invalidation</span><strong id="pro-invalidation">—</strong></div>
                                    <div class="pro-level"><span>Support</span><strong id="pro-support">—</strong></div>
                                    <div class="pro-level"><span>Resistance</span><strong id="pro-resistance">—</strong></div>
                                </div>
                                <div class="pro-disclaimer">Educational market context only. Trading cryptocurrency involves risk; manage exposure responsibly.</div>
                            </div>
                        </div>
                        <div class="pro-pillars">
                            <div class="pro-pillar"><div class="pro-pillar-kicker">Pillar 01</div><h3>Social Sentiment Hub</h3><div class="pro-score" id="pro-social-score">—%</div><div class="pro-score-label" id="pro-social-label">Consensus meter</div><p id="pro-social-detail">Loading the desk read…</p></div>
                            <div class="pro-pillar"><div class="pro-pillar-kicker">Pillar 02</div><h3>Technical &amp; Chart Indicators</h3><div class="pro-score" id="pro-technical-label">—</div><div class="pro-score-label" id="pro-technical-detail">EMA / RSI / volume</div><p>Public BTC/USDT structure with trend confirmation and volume context.</p></div>
                            <div class="pro-pillar"><div class="pro-pillar-kicker">Pillar 03</div><h3>Authentic News Radar</h3><div class="pro-score" id="pro-news-label">—</div><div class="pro-score-label" id="pro-news-detail">Impact-ranked headlines</div><p>RSS headlines are ranked High, Medium, or Neutral by market relevance.</p></div>
                        </div>
                        <div class="pro-panel pro-narrative"><div class="pro-panel-title">Pro Analyst Team Breakdown</div><p id="pro-narrative-1">—</p><p id="pro-narrative-2">—</p><p id="pro-narrative-3">—</p></div>
                        <div class="pro-panel pro-proof"><div class="pro-panel-title">Live Signal Proof Log</div><div id="pro-proof-list"></div></div>
                        <div class="pro-panel pro-news"><div class="pro-panel-title">Authentic News Radar · Latest Desk Reads</div><div id="pro-news-list"></div></div>
                        <div class="pro-disclaimer">Disclaimer: Market analyses and signals provided by Skill Shield Alpha are for educational and informational purposes only. Trading cryptocurrency involves risk. Always manage your risk responsibly.</div>
                    </div>
                </div>
            </div>

            <header id="btc-ticker" role="banner"
                    aria-label="Live Bitcoin price ticker and pulse controls"
                    itemscope itemtype="https://schema.org/PriceSpecification">
                <div class="ticker-left">
                    <span class="ticker-dot" aria-hidden="true"></span>
                    <span class="ticker-label">LIVE:</span>
                    <span class="ticker-sym">BTC/USD:</span>
                    <span class="ticker-price" id="ticker-price" itemprop="price">{ticker_price_disp}</span>
                    <span class="ticker-change" id="ticker-change" aria-live="polite"></span>
                    <span class="ticker-source" aria-hidden="true">⚡ Binance · 6s</span>
                </div>
                <div class="ticker-right">
                    <span class="ticker-vsep" aria-hidden="true">|</span>
                    <span class="live-dot" aria-hidden="true"></span>
                    <span class="live-label">PULSE</span>
                    <span style="color:#c9d1d9;font-variant-numeric:tabular-nums;">↻ <span id="refresh-timer">60s</span></span>
                    <button type="button" id="refresh-now" class="live-refresh">Refresh</button>
                    <span class="ticker-vsep" aria-hidden="true">|</span>
                    <button type="button" id="surge-toggle" class="surge-btn" style="padding:3px 9px;font-size:0.78em;" title="Browser notifications for ≥100 BTC transactions">🔔 Surge Alerts</button>
                </div>
            </header>

            <main role="main" id="main-content" aria-label="Bitcoin Mempool Intelligence Dashboard">
            <h1>{BRAND_NAME}</h1>
            <p class="tagline">{TAGLINE}</p>

            {trial_banner_html}

            {bias_html}

            {intel_html}

            {fg_html}

            {legacy_html}

            <script id="surge-data" type="application/json">{surge_json}</script>

            {velocity_html}

            {flow_html}

            <div class="sparkline-card" id="sparkline-card">
                <button class="info-btn" aria-label="About 60-Minute Chart" tabindex="0">i
                    <span class="info-tooltip">60-minute rolling window of whale transaction count (blue) and total BTC volume (gold). Simultaneous spikes in both count and volume indicate high-conviction institutional activity. Chart refreshes every 30 seconds without a page reload.</span>
                </button>
                <div class="sparkline-header">
                    <div>
                        <div class="sparkline-eyebrow">📈 60-MINUTE ROLLING HISTORY</div>
                        <div class="sparkline-title">Whale Activity Trend</div>
                    </div>
                    <div class="sparkline-legend">
                        <span><span class="legend-dot dot-count"></span>Whale TX Count</span>
                        <span><span class="legend-dot dot-volume"></span>Total BTC Volume</span>
                    </div>
                </div>
                <div class="sparkline-wrapper">
                    <canvas id="sparkline-canvas"></canvas>
                    <div class="sparkline-empty" id="sparkline-empty">Collecting data… first points appear within 60 seconds.</div>
                </div>
                <div class="sparkline-footer">
                    <span id="sparkline-points">0</span> data points · auto-updates every 30s
                </div>
            </div>

            <!-- ===== 24-HOUR WHALE MOVEMENT SUMMARY ===== -->
            <section class="whale-summary-card" id="whale-summary"
                     aria-label="Whale Movement Summary Statistics">
                <div class="whale-summary-title">📊 On-Chain Analytics</div>
                <div class="whale-summary-heading">Whale Movement Summary</div>
                <div class="ws-tabs">
                    <button class="ws-tab active" onclick="switchWsTab('session')" id="ws-tab-session">⏱ This Session</button>
                    <button class="ws-tab" onclick="switchWsTab('24h')" id="ws-tab-24h">24 Hours</button>
                    <button class="ws-tab" onclick="switchWsTab('7d')" id="ws-tab-7d">7 Days</button>
                    <button class="ws-tab" onclick="switchWsTab('all')" id="ws-tab-all">All-Time</button>
                </div>
                <div id="ws-panel-session" class="ws-panel active">
                    <div class="ws-grid">
                        <div class="ws-metric">
                            <div class="ws-metric-label">Whale Alerts Detected</div>
                            <div class="ws-metric-value">{whale_summary["session_whale_alerts"]}</div>
                        </div>
                        <div class="ws-metric">
                            <div class="ws-metric-label">Total BTC Observed</div>
                            <div class="ws-metric-value">{whale_summary["session_volume"]:,.2f} <span style="font-size:0.62em;color:#8b949e;">BTC</span></div>
                        </div>
                        <div class="ws-metric">
                            <div class="ws-metric-label">Accumulated</div>
                            <div class="ws-metric-value" style="color:#3fb950;">{whale_summary["session_accum"]:,.2f} <span style="font-size:0.62em;color:#6e7681;">BTC</span></div>
                        </div>
                        <div class="ws-metric">
                            <div class="ws-metric-label">Distributed</div>
                            <div class="ws-metric-value" style="color:#ff5252;">{whale_summary["session_distrib"]:,.2f} <span style="font-size:0.62em;color:#6e7681;">BTC</span></div>
                        </div>
                        <div class="ws-metric">
                            <div class="ws-metric-label">Net Flow</div>
                            <div class="ws-metric-value" style="color:{whale_summary['session_color']};">{whale_summary["session_net"]:+,.2f} BTC</div>
                        </div>
                        <div class="ws-metric">
                            <div class="ws-metric-label">Dominant Sentiment</div>
                            <div class="ws-metric-value" style="color:{whale_summary['session_color']};font-size:0.85em;">{whale_summary["session_sentiment"]}</div>
                        </div>
                    </div>
                    <div class="ws-footnote">{whale_summary["session_points"]} snapshot(s) collected · rolling 60-min in-memory window · updates on each page load</div>
                </div>
                <div id="ws-panel-24h" class="ws-panel">
                    <div class="ws-na-panel">
                        📡 <b style="color:#c9d1d9;">24-Hour Aggregated History</b><br>
                        Extended analytics beyond the current session window require database persistence.<br>
                        <span style="color:#58a6ff;font-size:0.85em;">Current session data is available in the ⏱ This Session tab.</span>
                    </div>
                </div>
                <div id="ws-panel-7d" class="ws-panel">
                    <div class="ws-na-panel">
                        📈 <b style="color:#c9d1d9;">7-Day Rolling History</b><br>
                        7-day aggregation requires database-persisted snapshots across sessions.<br>
                        <span style="color:#58a6ff;font-size:0.85em;">Current session data is available in the ⏱ This Session tab.</span>
                    </div>
                </div>
                <div id="ws-panel-all" class="ws-panel">
                    <div class="ws-na-panel">
                        🗄 <b style="color:#c9d1d9;">All-Time Aggregates</b><br>
                        Historical totals will populate here once database persistence is enabled for snapshots.<br>
                        <span style="color:#58a6ff;font-size:0.85em;">Current session data is available in the ⏱ This Session tab.</span>
                    </div>
                </div>
            </section>

            {alert_banner}

            <div class="sentiment" style="border-color: {sentiment["color"]};">
                <div class="sentiment-label">Market Sentiment</div>
                <div class="sentiment-value" style="color: {sentiment["color"]};">
                    <span class="sentiment-icon">{sentiment["icon"]}</span>
                    {sentiment["label"]}
                </div>
                <div class="sentiment-meter">
                    <div class="sentiment-meter-fill"
                         style="width: {abs(sentiment["score"]) * 50 + 50:.1f}%;
                                margin-left: {50 if sentiment["score"] >= 0 else max(0, 50 + sentiment["score"] * 50):.1f}%;
                                background: {sentiment["color"]};"></div>
                </div>
                <div class="sentiment-detail">{sentiment["detail"]}</div>
            </div>

            <div class="card">
                <div class="stat"><b>Market Price:</b> ${market_price_disp}</div>
                <div class="stat"><b>Blocks Mined:</b> {blocks_mined}</div>
                <div class="stat"><b>Status:</b> <span style="color: #3fb950;"><span class="status-pulse"></span>Operational</span></div>
            </div>

            <div class="watchlist-card" id="watchlist-card">
                <div class="watchlist-header">
                    <div class="watchlist-title">
                        <span class="watchlist-icon">🥇</span>
                        <span>Whale Watchlist</span>
                        <span class="watchlist-hits-badge" id="watchlist-hits-badge" hidden>0 hits</span>
                    </div>
                    <div class="watchlist-meta">
                        Track specific BTC addresses · matches highlight in <span class="gold-word">GOLD</span> with a unique alert
                    </div>
                </div>
                <form class="watchlist-form" id="watchlist-form" onsubmit="return false;">
                    <input type="text" id="watchlist-input" class="watchlist-input"
                           placeholder="Paste a Bitcoin address (e.g. bc1q... or 1A1z...) and press Enter"
                           autocomplete="off" spellcheck="false" />
                    <button type="submit" id="watchlist-add" class="watchlist-add-btn">＋ Watch</button>
                </form>
                <div class="watchlist-tags" id="watchlist-tags"></div>
                <div class="watchlist-empty" id="watchlist-empty">No addresses on the watchlist yet — add one to start monitoring.</div>
            </div>
            <div class="main-grid">
                <div class="panel">
                    <div class="panel-header">
                        <div class="panel-title">🐋 Top Whale Wallets — Live Mempool</div>
                        <div class="panel-actions">
                            <a class="export-btn" href="/export.csv" title="Download current whale wallets as CSV">
                                ⬇ Export CSV
                            </a>
                        </div>
                        <div class="panel-meta">
                            <span class="pulse">●</span> Threshold ≥ {WHALE_THRESHOLD_BTC} BTC ·
                            Alert ≥ {ALERT_THRESHOLD_BTC} BTC · Auto-refresh 60s
                        </div>
                    </div>
                    <div class="table-scroll">
                        {table_html}
                    </div>
                </div>

                <div class="panel news-panel">
                    <div class="panel-header news-header">
                        <div class="panel-title">📰 Global Crypto News</div>
                        <div class="news-filters">
                            <button type="button" class="filter-chip active" data-filter="all">
                                All <span class="chip-count">{len(news)}</span>
                            </button>
                            <button type="button" class="filter-chip" data-filter="relevant">
                                🐋 Whale-Correlated <span class="chip-count">{correlated_count}</span>
                            </button>
                        </div>
                    </div>
                    <div class="news-list" id="news-list" data-filter="all">
                        {news_html}
                    </div>
                </div>
            </div>

            <div class="integrity-bar">
                <div class="integrity-badge" id="integrity-badge">
                    <span class="integrity-dot" id="integrity-dot" style="background: {integrity["color"]}; color: {integrity["color"]};"></span>
                    <span class="integrity-label" style="color: {integrity["color"]};" id="integrity-label">DATA INTEGRITY: {integrity["label"]}</span>
                    <span class="integrity-detail" id="integrity-detail">{integrity["detail"]}</span>
                    <span class="integrity-source">⛓ blockchain.info</span>
                </div>
            </div>

            <!-- ===== TOOL ACADEMY ===== -->
            <div class="academy-section" id="tool-academy">
                <div class="academy-eyebrow">📚 SKILL SHIELD BTC · TOOL ACADEMY</div>
                <div class="academy-title">Master the Algorithm — Trade Like an Institution</div>
                <div class="academy-sub">
                    Five research-grade chapters explaining exactly how our AI detects whale movements
                    before they move markets — and how you use these signals to stay one step ahead.
                </div>
                <div class="academy-chapters">

                    <div class="academy-chapter" onclick="toggleChapter(this)">
                        <div class="academy-ch-head">
                            <div>
                                <div class="academy-ch-num">Chapter 01</div>
                                <div class="academy-ch-title">How It Works: The Science Behind the Algorithm</div>
                                <div class="academy-ch-preview">How we scan the Bitcoin mempool before blocks confirm — in real time.</div>
                            </div>
                            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
                                <span class="academy-ch-tag">FOUNDATIONAL</span>
                                <span class="academy-arrow">▼</span>
                            </div>
                        </div>
                        <div class="academy-ch-body">
                            <div class="academy-highlight">
                                💡 The mempool is where every Bitcoin transaction waits before being permanently confirmed. We read this waiting room in real time — before the market can react.
                            </div>
                            <p>Every Bitcoin transaction is broadcast to thousands of nodes worldwide before a miner selects it and confirms it into a block. This "pre-confirmation" window — the <b>mempool</b> — is where institutional behavior becomes visible to those with the right tools.</p>
                            <p style="margin-top:10px">Skill Shield BTC's algorithm monitors this data stream 24/7, classifying transactions by size, structure, and timing to identify whale movements — defined as transfers exceeding 1 BTC from high-value addresses.</p>
                            <p style="margin-top:10px">Three core signals are fused together in real time:</p>
                            <ul class="academy-list">
                                <li><b>Network Velocity</b> — how fast transactions are flowing vs the 24-hour baseline. A sudden spike often precedes a large market move.</li>
                                <li><b>Smart Money Flow</b> — whether BTC is being swept into cold storage (accumulation) or fanned out to many addresses (distribution/selling).</li>
                                <li><b>News Sentiment</b> — real-time correlation of on-chain activity with breaking crypto headlines to confirm or contradict the technical signal.</li>
                            </ul>
                            <p style="margin-top:10px;color:#8b949e;font-size:0.9em">These three signals are combined into a single <b>Directional Probability Score</b> — the number you see in the Institutional Intelligence card at the top of your dashboard.</p>
                        </div>
                    </div>

                    <div class="academy-chapter" onclick="toggleChapter(this)">
                        <div class="academy-ch-head">
                            <div>
                                <div class="academy-ch-num">Chapter 02</div>
                                <div class="academy-ch-title">Data Interpretation: Reading the Signals Step-by-Step</div>
                                <div class="academy-ch-preview">A practical field guide to every number and indicator on your dashboard.</div>
                            </div>
                            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
                                <span class="academy-ch-tag">PRACTICAL</span>
                                <span class="academy-arrow">▼</span>
                            </div>
                        </div>
                        <div class="academy-ch-body">
                            <div class="academy-highlight">
                                📊 You don't need to understand every number. You need to understand which combinations matter — and what action they call for.
                            </div>
                            <p><b>Step 1 — Check the Whale Actionable Bias card first.</b> This is your one-line trade signal derived from all three data sources simultaneously. Green = bullish. Red = bearish. Yellow = wait.</p>
                            <p style="margin-top:10px"><b>Step 2 — Validate with Fear & Greed.</b> If the Bias card says BULLISH and Fear & Greed reads below 40 (Fear zone), this is a historically high-probability long setup — institutions accumulate when retail is fearful.</p>
                            <p style="margin-top:10px"><b>Step 3 — Check Network Velocity.</b> A CALM or NORMAL velocity with a bullish bias = measured institutional accumulation. HIGH VELOCITY with a bullish bias = explosive accumulation event — often precedes a sharp move up within 4–12 hours.</p>
                            <p style="margin-top:10px"><b>Step 4 — Confirm with Smart Money Flow.</b> "Accumulation Dominant" + positive net BTC flow = the strongest possible confirmation for a long setup.</p>
                            <ul class="academy-list" style="margin-top:10px">
                                <li>All 4 signals aligned BULLISH = maximum conviction long</li>
                                <li>3 of 4 BULLISH = high conviction long</li>
                                <li>Mixed signals = reduce position size or wait</li>
                                <li>All signals BEARISH = stand aside or short with confirmation</li>
                            </ul>
                        </div>
                    </div>

                    <div class="academy-chapter" onclick="toggleChapter(this)">
                        <div class="academy-ch-head">
                            <div>
                                <div class="academy-ch-num">Chapter 03</div>
                                <div class="academy-ch-title">The Multi-Signal Confluence Model</div>
                                <div class="academy-ch-preview">How to weigh Whale Bias, Fear &amp; Greed, and Velocity together instead of trading on any single signal.</div>
                            </div>
                            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
                                <span class="academy-ch-tag">METHODOLOGY</span>
                                <span class="academy-arrow">▼</span>
                            </div>
                        </div>
                        <div class="academy-ch-body">
                            <div class="academy-highlight">
                                📐 No single on-chain signal is reliable in isolation. This model looks for confluence — multiple independent signals agreeing — because agreement across uncorrelated data sources reduces (but never eliminates) false positives.
                            </div>
                            <p>The framework we use internally:</p>
                            <ul class="academy-list">
                                <li><b>Confluence check.</b> When Whale Bias, Fear &amp; Greed, and Velocity all point the same direction, that's a stronger signal than any one of them alone. When they disagree, the honest read is "no clear edge" — which is why you'll often see a Neutral rating.</li>
                                <li><b>Position sizing reflects confidence, not certainty.</b> A higher confidence score means more historical agreement between signals — not a guaranteed outcome. Size accordingly, and always use stop-losses regardless of confidence level.</li>
                                <li><b>Signals decay.</b> Mempool conditions change block by block. Treat every signal as a snapshot, not a standing prediction.</li>
                            </ul>
                            <p style="margin-top:10px">This is a probability framework, not a profit guarantee. It's designed to help you <b>filter out low-confluence setups</b> — not to promise any particular trade will win.</p>
                            <p style="margin-top:10px;color:#8b949e;font-size:0.9em">Risk warning: All trading involves risk. No signal is 100% accurate, and past pattern frequency does not guarantee future performance. Always use appropriate position sizing and stop-losses. This is not financial advice.</p>
                        </div>
                    </div>

                    <div class="academy-chapter" onclick="toggleChapter(this)">
                        <div class="academy-ch-head">
                            <div>
                                <div class="academy-ch-num">Chapter 04</div>
                                <div class="academy-ch-title">How Institutions Manipulate Retail Traders</div>
                                <div class="academy-ch-preview">The four classic whale moves — and how Skill Shield BTC detects each one.</div>
                            </div>
                            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
                                <span class="academy-ch-tag">ADVANCED</span>
                                <span class="academy-arrow">▼</span>
                            </div>
                        </div>
                        <div class="academy-ch-body">
                            <div class="academy-highlight">
                                🐋 Whales don't move markets by accident. Every large movement is intentional, strategic, and detectable — if you know where to look.
                            </div>
                            <p>Institutional players use four primary tactics against retail traders:</p>
                            <ul class="academy-list">
                                <li><b>The Accumulation Sweep</b> — Quietly buying large quantities via single-input→single-output transactions (cold storage sweeps) while retail sentiment is bearish. Our Smart Money Flow panel detects this as "Accumulation Dominant." This is the exact signal that precedes bull runs.</li>
                                <li><b>The Distribution Fan-Out</b> — Distributing holdings to many wallets simultaneously before a sell-off. Appears as 1-in→N-out transactions. Our flow panel flags this as "Distribution Dominant." This is the signal to exit or hedge.</li>
                                <li><b>The Velocity Spike</b> — Flooding the mempool with transactions to create urgency and FOMO in retail. Our Velocity card identifies this as "HIGH VELOCITY" and triggers an alert. Often happens at key technical levels.</li>
                                <li><b>The Dead-Cat Pump</b> — A temporary price spike to trigger retail long stops, followed by a sharp reversal. Detectable by HIGH VELOCITY paired with DISTRIBUTION DOMINANT flow — a contradiction that signals a trap.</li>
                            </ul>
                            <p style="margin-top:10px">When you see these patterns on your dashboard, you're seeing what hedge funds, proprietary trading desks, and crypto whales are doing — <b>in real time, before the price reflects it.</b></p>
                        </div>
                    </div>

                    <div class="academy-chapter" onclick="toggleChapter(this)">
                        <div class="academy-ch-head">
                            <div>
                                <div class="academy-ch-num">Chapter 05</div>
                                <div class="academy-ch-title">The Velocity Signal: Your Early-Warning System</div>
                                <div class="academy-ch-preview">How to use network throughput spikes to position 4–12 hours ahead of a move.</div>
                            </div>
                            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
                                <span class="academy-ch-tag">ADVANCED</span>
                                <span class="academy-arrow">▼</span>
                            </div>
                        </div>
                        <div class="academy-ch-body">
                            <div class="academy-highlight">
                                ⚡ Network velocity is the single most time-sensitive signal in the dashboard. A velocity spike that hasn't yet resolved into price movement is a 4–12 hour window of opportunity.
                            </div>
                            <p>Here's why velocity leads price:</p>
                            <p style="margin-top:8px">When institutions decide to accumulate or distribute, they broadcast transactions to the network. These transactions hit the mempool <b>before</b> they affect the order book. Our velocity gauge measures this pre-order-book activity — giving you a head start.</p>
                            <ul class="academy-list" style="margin-top:10px">
                                <li><b>CALM + Bullish Bias</b> = Steady accumulation. Add to longs on pullbacks.</li>
                                <li><b>NORMAL + Bullish Bias</b> = Standard uptrend conditions. Hold or add on confirmation.</li>
                                <li><b>HIGH VELOCITY + Bullish Bias + Accum Flow</b> = Institutional accumulation event. High-conviction long entry before the move happens.</li>
                                <li><b>HIGH VELOCITY + Bearish Bias + Distrib Flow</b> = Institutional exit in progress. Reduce longs, consider short.</li>
                                <li><b>HIGH VELOCITY + Mixed signals</b> = Market indecision. Reduce size. Wait for resolution.</li>
                            </ul>
                            <p style="margin-top:10px;color:#f0b72f;font-size:0.9em">⚡ Pro tip: Set a browser tab with this dashboard open during high-volatility periods. A velocity spike from CALM to HIGH in under 10 minutes is one of the highest-probability entry signals the algorithm produces.</p>
                        </div>
                    </div>

                </div><!-- end academy-chapters -->
            </div><!-- end tool-academy -->

            <!-- ===== FOOTER ===== -->
            <div class="footer">Developed by Sibtul Hassan Shah | AI Engine Optimized</div>
            <div style="max-width:1500px;margin:0 auto;padding:10px 0 30px;text-align:center;color:#30363d;font-size:0.7em;line-height:1.6">
                ⚠ Risk Disclaimer: Skill Shield BTC is an educational and analytical tool. All signals are for informational purposes only and do not constitute financial advice. Cryptocurrency trading involves substantial risk of loss. Past signal accuracy does not guarantee future results. Trade responsibly.
                · <a href="/login" style="color:#30363d">Sign In</a>
                · <a href="#tool-academy" style="color:#30363d" onclick="document.getElementById('tool-academy').scrollIntoView({{behavior:'smooth'}});return false;">Tool Academy</a>
                · <a href="/alpha-admin-portal" style="color:#0d1117">.</a>
            </div>

            <script>
                // ===== Live BTC Ticker Banner =====
                (function() {{
                    const priceEl  = document.getElementById('ticker-price');
                    const changeEl = document.getElementById('ticker-change');
                    let lastPrice  = null;
                    function applyPrice(p) {{
                        if (!priceEl || !p || isNaN(p)) return;
                        const fmt = p.toLocaleString('en-US', {{
                            minimumFractionDigits: 2, maximumFractionDigits: 2
                        }});
                        priceEl.textContent = '$' + fmt;
                        if (lastPrice !== null && changeEl) {{
                            const diff = p - lastPrice;
                            const pct  = Math.abs(diff / lastPrice * 100).toFixed(2);
                            changeEl.textContent = (diff >= 0 ? '▲ +' : '▼ -') + pct + '%';
                            changeEl.style.color = diff >= 0 ? '#3fb950' : '#ff5252';
                            priceEl.style.color  = diff >= 0 ? '#3fb950' : '#ff5252';
                            setTimeout(() => {{ if (priceEl) priceEl.style.color = '#ffffff'; }}, 1200);
                        }}
                        lastPrice = p;
                    }}
                    function updateTicker() {{
                        fetch('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT', {{cache:'no-store'}})
                            .then(r => r.json())
                            .then(d => {{ applyPrice(parseFloat(d.price)); }})
                            .catch(() => {{
                                fetch('/ticker.json', {{cache:'no-store'}})
                                    .then(r => r.json())
                                    .then(d => {{ applyPrice(d.price); }})
                                    .catch(() => {{}});
                            }});
                    }}
                    updateTicker();
                    setInterval(updateTicker, 6000);
                }})();

                // ===== Real-time trial countdown =====
                (function() {{
                    var bar = document.getElementById('trial-notice-bar');
                    if (!bar) return;
                    var expiry = parseInt(bar.getAttribute('data-expiry'), 10);
                    if (!expiry) return;
                    var el = document.getElementById('trial-countdown');
                    if (!el) return;
                    function tick() {{
                        var diff = expiry - Date.now();
                        if (diff <= 0) {{
                            el.textContent = '0h 0m 0s';
                            return;
                        }}
                        var totalSec = Math.floor(diff / 1000);
                        var s = totalSec % 60;
                        var totalMin = Math.floor(totalSec / 60);
                        var mm = totalMin % 60;
                        var hh = Math.floor(totalMin / 60);
                        el.textContent = hh + 'h ' + mm + 'm ' + s + 's';
                    }}
                    tick();
                    setInterval(tick, 1000);
                }})();

                // ===== Tool Academy chapter toggle =====
                function toggleChapter(el) {{
                    el.classList.toggle('open');
                }}
                function openChapter(idx) {{
                    document.querySelectorAll('.academy-chapter').forEach(function(c,i) {{
                        if (i === idx) c.classList.add('open');
                    }});
                }}
                function scrollToAcademy() {{
                    var el = document.getElementById('tool-academy');
                    if (el) el.scrollIntoView({{behavior:'smooth'}});
                }}

                // ===== Pro Analysis Signals =====
                var _proAnalysis = null;
                var _proChart = null;
                var PRO_LEAD_KEY = 'skillshield_pro_access_v1';

                function openProSignals() {{
                    var modal = document.getElementById('pro-modal');
                    if (!modal) return;
                    modal.classList.add('visible');
                    document.body.style.overflow = 'hidden';
                    try {{
                        if (localStorage.getItem(PRO_LEAD_KEY)) showProDashboard();
                    }} catch (e) {{}}
                }}
                function closeProSignals() {{
                    var modal = document.getElementById('pro-modal');
                    if (modal) modal.classList.remove('visible');
                    document.body.style.overflow = '';
                }}
                function proMoney(value) {{
                    return '$' + Number(value || 0).toLocaleString(undefined, {{maximumFractionDigits: 0}});
                }}
                function renderProChart(data) {{
                    var canvas = document.getElementById('pro-chart');
                    if (!canvas || typeof Chart === 'undefined' || !data.market.candles.length) return;
                    var labels = data.market.candles.map(function(c) {{
                        return new Date(c.time * 1000).toLocaleDateString(undefined, {{month:'short', day:'numeric'}});
                    }});
                    var closes = data.market.candles.map(function(c) {{ return c.close; }});
                    var entry = data.levels.entry[0];
                    var target1 = data.levels.target1;
                    var target2 = data.levels.target2;
                    var invalidation = data.levels.invalidation;
                    if (_proChart) _proChart.destroy();
                    _proChart = new Chart(canvas.getContext('2d'), {{
                        type: 'line',
                        data: {{
                            labels: labels,
                            datasets: [
                                {{label:'BTC/USDT', data:closes, borderColor:'#58a6ff', backgroundColor:'rgba(88,166,255,.08)', fill:true, tension:.28, pointRadius:0, borderWidth:2}},
                                {{label:'Entry', data:closes.map(function(){{return entry;}}), borderColor:'#f0b72f', borderDash:[5,4], pointRadius:0, borderWidth:1}},
                                {{label:'Target 1', data:closes.map(function(){{return target1;}}), borderColor:'#3fb950', borderDash:[4,4], pointRadius:0, borderWidth:1}},
                                {{label:'Target 2', data:closes.map(function(){{return target2;}}), borderColor:'#79c0ff', borderDash:[2,4], pointRadius:0, borderWidth:1}},
                                {{label:'Invalidation', data:closes.map(function(){{return invalidation;}}), borderColor:'#ff7b72', borderDash:[4,4], pointRadius:0, borderWidth:1}}
                            ]
                        }},
                        options: {{
                            responsive:true, maintainAspectRatio:false, interaction:{{mode:'index', intersect:false}},
                            plugins:{{legend:{{display:false}}, tooltip:{{backgroundColor:'#161b22', titleColor:'#fff', bodyColor:'#c9d1d9'}}}},
                            scales:{{x:{{grid:{{color:'rgba(48,54,61,.35)'}}, ticks:{{color:'#6e7681', maxTicksLimit:7}}}}, y:{{grid:{{color:'rgba(48,54,61,.35)'}}, ticks:{{color:'#8b949e', callback:function(v){{return '$'+Number(v).toLocaleString();}}}}}}}}
                        }}
                    }});
                }}
                function renderProAnalysis(data) {{
                    _proAnalysis = data;
                    var levels = data.levels;
                    document.getElementById('pro-updated').textContent = data.updated;
                    document.getElementById('pro-entry').textContent = proMoney(levels.entry[0]) + ' – ' + proMoney(levels.entry[1]);
                    ['target1','target2','invalidation','support','resistance'].forEach(function(key) {{
                        var el = document.getElementById('pro-' + key);
                        if (el) el.textContent = proMoney(levels[key]);
                    }});
                    var social = data.pillars.social;
                    document.getElementById('pro-social-score').textContent = social.score + '%';
                    document.getElementById('pro-social-label').textContent = social.label;
                    document.getElementById('pro-social-detail').textContent = social.detail;
                    document.getElementById('pro-technical-label').textContent = data.pillars.technical.label;
                    document.getElementById('pro-technical-detail').textContent = data.pillars.technical.detail;
                    document.getElementById('pro-news-label').textContent = data.pillars.news.label;
                    document.getElementById('pro-news-detail').textContent = data.pillars.news.detail;
                    data.narrative.forEach(function(text, index) {{
                        var el = document.getElementById('pro-narrative-' + (index + 1));
                        if (el) el.textContent = text;
                    }});
                    function escapeHtml(value) {{
                        return String(value || '').replace(/[&<>"']/g, function(char) {{
                            return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[char];
                        }});
                    }}
                    function safeHref(value) {{
                        var href = String(value || '');
                        return /^https?:\/\//i.test(href) ? href : '#';
                    }}
                    var proof = document.getElementById('pro-proof-list');
                    proof.innerHTML = data.proof_log.map(function(row) {{
                        return '<div class="pro-proof-row"><span class="pro-proof-time">' + escapeHtml(row.time) + '</span><span class="pro-proof-detail">' + escapeHtml(row.detail) + '</span><span class="pro-status">' + escapeHtml(row.status) + '</span></div>';
                    }}).join('');
                    var news = document.getElementById('pro-news-list');
                    news.innerHTML = (data.pillars.news.items || []).map(function(item) {{
                        return '<a class="pro-news-item" href="' + escapeHtml(safeHref(item.url)) + '" target="_blank" rel="noopener"><span>' + escapeHtml(item.source) + ' · ' + escapeHtml(item.title) + '</span><span class="pro-news-impact">' + escapeHtml(item.impact) + '</span></a>';
                    }}).join('') || '<div class="pro-subtitle">No headlines available right now.</div>';
                    renderProChart(data);
                }}
                function showProDashboard() {{
                    var form = document.getElementById('pro-lead-form-wrap');
                    var dashboard = document.getElementById('pro-dashboard');
                    if (form) form.style.display = 'none';
                    if (dashboard) dashboard.classList.add('show');
                    fetch('/pro-analysis.json', {{cache:'no-store'}})
                        .then(function(response) {{ if (!response.ok) throw new Error('analysis unavailable'); return response.json(); }})
                        .then(renderProAnalysis)
                        .catch(function() {{
                            var detail = document.getElementById('pro-social-detail');
                            if (detail) detail.textContent = 'The desk is refreshing its sources. Please try again in a moment.';
                        }});
                }}
                function sharePro(channel) {{
                    if (!_proAnalysis) return;
                    var summary = 'Skill Shield Pro Intelligence: ' + _proAnalysis.pillars.social.label +
                        ' · Entry ' + proMoney(_proAnalysis.levels.entry[0]) + '–' + proMoney(_proAnalysis.levels.entry[1]) +
                        ' · T1 ' + proMoney(_proAnalysis.levels.target1) + ' · T2 ' + proMoney(_proAnalysis.levels.target2);
                    var site = 'https://skillshieldbtc.com';
                    var urls = {{
                        telegram: 'https://t.me/share/url?url=' + encodeURIComponent(site) + '&text=' + encodeURIComponent(summary),
                        whatsapp: 'https://wa.me/?text=' + encodeURIComponent(summary + ' ' + site),
                        twitter: 'https://twitter.com/intent/tweet?text=' + encodeURIComponent(summary) + '&url=' + encodeURIComponent(site),
                        reddit: 'https://www.reddit.com/submit?url=' + encodeURIComponent(site) + '&title=' + encodeURIComponent(summary),
                        email: 'mailto:?subject=' + encodeURIComponent('Skill Shield Pro Intelligence') + '&body=' + encodeURIComponent(summary + '\\n\\n' + site)
                    }};
                    if (channel === 'discord') {{
                        navigator.clipboard.writeText(summary + ' ' + site).then(function() {{ alert('Signal summary copied — paste it into Discord.'); }});
                        return;
                    }}
                    window.open(urls[channel], '_blank', 'noopener,noreferrer');
                }}
                (function() {{
                    var platform = document.getElementById('pro-platform');
                    var handle = document.getElementById('pro-handle');
                    if (platform && handle) platform.addEventListener('change', function() {{
                        var placeholders = {{WhatsApp:'e.g. +1 555 123 4567', Telegram:'e.g. @yourhandle', 'Twitter/X':'e.g. @yourhandle', Discord:'e.g. username#0000', Reddit:'e.g. u/yourname'}};
                        handle.placeholder = placeholders[platform.value] || 'Your handle or phone number';
                    }});
                    var form = document.getElementById('pro-lead-form');
                    if (form) form.addEventListener('submit', function(event) {{
                        event.preventDefault();
                        var btn = document.getElementById('pro-unlock-btn');
                        var error = document.getElementById('pro-lead-error');
                        btn.disabled = true; btn.textContent = 'Unlocking…'; error.style.display = 'none';
                        var payload = Object.fromEntries(new FormData(form).entries());
                        fetch('/pro-leads', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}})
                            .then(function(response) {{ return response.json().then(function(body) {{ if (!response.ok) throw new Error(body.error || 'Please check the form.'); return body; }}); }})
                            .then(function() {{ localStorage.setItem(PRO_LEAD_KEY, '1'); showProDashboard(); }})
                            .catch(function(err) {{ error.textContent = err.message; error.style.display = 'block'; btn.disabled = false; btn.textContent = '[ Unlock Free Pro Access ]'; }});
                    }});
                }})();

                // ===== Payment modal tab switcher =====
                function switchWsTab(tab) {{
                    ['session', '24h', '7d', 'all'].forEach(function(p) {{
                        var panel = document.getElementById('ws-panel-' + p);
                        var btn   = document.getElementById('ws-tab-' + p);
                        if (panel) panel.classList.toggle('active', p === tab);
                        if (btn)   btn.classList.toggle('active',  p === tab);
                    }});
                }}

                function switchPayTab(tab) {{
                    var crypto = document.getElementById('pay-crypto-section');
                    var alt    = document.getElementById('pay-alt-section');
                    var tCrypto = document.getElementById('tab-crypto');
                    var tAlt    = document.getElementById('tab-alt');
                    if (tab === 'crypto') {{
                        if (crypto) crypto.style.display = 'block';
                        if (alt) alt.style.display = 'none';
                        if (tCrypto) tCrypto.classList.add('active');
                        if (tAlt) tAlt.classList.remove('active');
                    }} else {{
                        if (crypto) crypto.style.display = 'none';
                        if (alt) {{ alt.style.display = 'block'; alt.classList.add('show'); }}
                        if (tCrypto) tCrypto.classList.remove('active');
                        if (tAlt) tAlt.classList.add('active');
                    }}
                }}

                // ===== Plan selector (Monthly / Yearly) =====
                var _selectedPlan = 'monthly';
                function selectPlan(plan) {{
                    _selectedPlan = plan;
                    var btnM  = document.getElementById('plan-btn-monthly');
                    var btnY  = document.getElementById('plan-btn-yearly');
                    var notice = document.getElementById('usdt-amount-notice');
                    var planInput = document.getElementById('selected-plan-input');
                    if (plan === 'yearly') {{
                        if (btnM) {{ btnM.style.borderColor='#30363d'; btnM.style.background='transparent'; btnM.style.color='#8b949e'; }}
                        if (btnY) {{ btnY.style.borderColor='#3fb950'; btnY.style.background='rgba(63,185,80,0.15)'; btnY.style.color='#3fb950'; }}
                        if (notice) notice.innerHTML = '⚠ Send <b>exactly $999 USDT</b> on the <b>BEP-20 (BSC) network only</b>. Wrong network = lost funds.';
                        if (planInput) planInput.value = 'yearly';
                    }} else {{
                        if (btnM) {{ btnM.style.borderColor='#58a6ff'; btnM.style.background='rgba(88,166,255,0.15)'; btnM.style.color='#58a6ff'; }}
                        if (btnY) {{ btnY.style.borderColor='#30363d'; btnY.style.background='transparent'; btnY.style.color='#8b949e'; }}
                        if (notice) notice.innerHTML = '⚠ Send <b>exactly $99 USDT</b> on the <b>BEP-20 (BSC) network only</b>. Wrong network = lost funds.';
                        if (planInput) planInput.value = 'monthly';
                    }}
                }}

                // ===== Copy wallet address =====
                function copyWalletAddr() {{
                    var addr = document.getElementById('wallet-addr');
                    if (addr) {{
                        navigator.clipboard.writeText(addr.textContent.trim())
                            .then(function() {{
                                var btn = document.querySelector('.pay-copy-btn');
                                if (btn) {{
                                    btn.textContent = '✅ Copied!';
                                    setTimeout(function() {{ btn.textContent = '📋 Click to Copy Address'; }}, 2000);
                                }}
                            }})
                            .catch(function() {{
                                var r = document.createRange();
                                r.selectNode(addr);
                                window.getSelection().removeAllRanges();
                                window.getSelection().addRange(r);
                            }});
                    }}
                }}

                // ===== Info (i) button click-toggle (mobile/touch) =====
                (function() {{
                    document.querySelectorAll('.info-btn').forEach(function(btn) {{
                        btn.addEventListener('click', function(e) {{
                            e.stopPropagation();
                            const wasActive = btn.classList.contains('active');
                            document.querySelectorAll('.info-btn.active').forEach(function(b) {{
                                b.classList.remove('active');
                            }});
                            if (!wasActive) btn.classList.add('active');
                        }});
                    }});
                    document.addEventListener('click', function() {{
                        document.querySelectorAll('.info-btn.active').forEach(function(b) {{
                            b.classList.remove('active');
                        }});
                    }});
                }})();

                // Alert beep (first user interaction)
                (function() {{
                    const alertActive = {str(alert_active).lower()};
                    if (!alertActive) return;
                    function beep() {{
                        try {{
                            const ctx = new (window.AudioContext || window.webkitAudioContext)();
                            const o = ctx.createOscillator();
                            const g = ctx.createGain();
                            o.type = 'sine'; o.frequency.value = 880;
                            g.gain.value = 0.08;
                            o.connect(g); g.connect(ctx.destination);
                            o.start();
                            setTimeout(() => {{ o.frequency.value = 660; }}, 180);
                            setTimeout(() => {{ o.stop(); ctx.close(); }}, 360);
                        }} catch (e) {{}}
                    }}
                    const handler = () => {{ beep(); document.removeEventListener('click', handler); }};
                    document.addEventListener('click', handler, {{ once: true }});
                }})();

                // News filter chips
                (function() {{
                    const list = document.getElementById('news-list');
                    const chips = document.querySelectorAll('.filter-chip');
                    chips.forEach(c => c.addEventListener('click', () => {{
                        chips.forEach(x => x.classList.remove('active'));
                        c.classList.add('active');
                        list.dataset.filter = c.dataset.filter;
                    }}));
                }})();

                // ===== Whale Surge Notifications (≥100 BTC) =====
                (function() {{
                    const SURGE_THRESHOLD = 100;
                    const STORAGE_KEY = 'skillshield_surge_notified_v1';
                    const ARMED_KEY   = 'skillshield_surge_armed_v1';
                    const btn = document.getElementById('surge-toggle');
                    const dataEl = document.getElementById('surge-data');
                    if (!btn || !dataEl) return;

                    let surgeData = [];
                    try {{ surgeData = JSON.parse(dataEl.textContent || '[]'); }} catch (e) {{}}

                    function loadNotified() {{
                        try {{ return new Set(JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]')); }}
                        catch (e) {{ return new Set(); }}
                    }}
                    function saveNotified(set) {{
                        try {{ sessionStorage.setItem(STORAGE_KEY, JSON.stringify([...set].slice(-50))); }}
                        catch (e) {{}}
                    }}
                    function isArmed() {{
                        return localStorage.getItem(ARMED_KEY) === '1'
                               && typeof Notification !== 'undefined'
                               && Notification.permission === 'granted';
                    }}

                    function paintBtn() {{
                        btn.classList.remove('armed', 'denied');
                        if (typeof Notification === 'undefined') {{
                            btn.textContent = '🔕 Surge Alerts Unsupported';
                            btn.disabled = true; return;
                        }}
                        if (Notification.permission === 'denied') {{
                            btn.classList.add('denied');
                            btn.textContent = '🔕 Surge Alerts Blocked';
                        }} else if (isArmed()) {{
                            btn.classList.add('armed');
                            btn.textContent = '🔔 Surge Alerts: ON';
                        }} else {{
                            btn.textContent = '🔔 Enable Surge Alerts';
                        }}
                    }}

                    function fireSurgeNotifications() {{
                        if (!isArmed() || !surgeData.length) return;
                        const notified = loadNotified();
                        let fired = 0;
                        surgeData.forEach(tx => {{
                            if (notified.has(tx.hash)) return;
                            const usd = tx.usd ? ('$' + tx.usd.toLocaleString('en-US', {{maximumFractionDigits: 0}})) : '';
                            const recipient = (tx.recipient || '').slice(0, 10) + '…';
                            try {{
                                const n = new Notification('🐋 WHALE SURGE — ' + tx.btc.toFixed(2) + ' BTC', {{
                                    body: usd + (usd ? ' · ' : '') + 'To ' + recipient + '\\nTap to view in Skill Shield Alpha',
                                    icon: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><text y='52' font-size='52'>🐋</text></svg>",
                                    tag: 'surge-' + tx.hash,
                                    requireInteraction: false,
                                }});
                                n.onclick = () => {{ window.focus(); n.close(); }};
                                notified.add(tx.hash);
                                fired++;
                            }} catch (e) {{}}
                        }});
                        if (fired) saveNotified(notified);
                    }}

                    btn.addEventListener('click', () => {{
                        if (typeof Notification === 'undefined') return;
                        if (isArmed()) {{
                            localStorage.removeItem(ARMED_KEY);
                            paintBtn();
                            return;
                        }}
                        if (Notification.permission === 'granted') {{
                            localStorage.setItem(ARMED_KEY, '1');
                            paintBtn();
                            fireSurgeNotifications();
                            return;
                        }}
                        if (Notification.permission !== 'denied') {{
                            Notification.requestPermission().then(p => {{
                                if (p === 'granted') {{
                                    localStorage.setItem(ARMED_KEY, '1');
                                    try {{
                                        new Notification('🐋 Skill Shield armed', {{
                                            body: 'You will be notified for any single transaction ≥ ' + SURGE_THRESHOLD + ' BTC.',
                                        }});
                                    }} catch (e) {{}}
                                    fireSurgeNotifications();
                                }}
                                paintBtn();
                            }});
                        }} else {{
                            paintBtn();
                        }}
                    }});

                    paintBtn();
                    fireSurgeNotifications();
                }})();

                // ===== Smart Money Flow sparkline (Chart.js) =====
                (function() {{
                    const canvas  = document.getElementById('flow-canvas');
                    const emptyEl = document.getElementById('flow-empty');
                    if (!canvas || typeof Chart === 'undefined') return;

                    const ctx = canvas.getContext('2d');
                    const gradAccum = ctx.createLinearGradient(0, 0, 0, 200);
                    gradAccum.addColorStop(0, 'rgba(63,185,80,0.30)');
                    gradAccum.addColorStop(1, 'rgba(63,185,80,0.00)');
                    const gradDistrib = ctx.createLinearGradient(0, 0, 0, 200);
                    gradDistrib.addColorStop(0, 'rgba(255,82,82,0.25)');
                    gradDistrib.addColorStop(1, 'rgba(255,82,82,0.00)');

                    const chart = new Chart(ctx, {{
                        type: 'line',
                        data: {{
                            labels: [],
                            datasets: [
                                {{
                                    label: 'Accumulated BTC',
                                    data: [],
                                    borderColor: '#3fb950',
                                    backgroundColor: gradAccum,
                                    borderWidth: 2, tension: 0.4, fill: true,
                                    pointRadius: 0, pointHoverRadius: 4,
                                    yAxisID: 'y',
                                }},
                                {{
                                    label: 'Distributed BTC',
                                    data: [],
                                    borderColor: '#ff5252',
                                    backgroundColor: gradDistrib,
                                    borderWidth: 2, tension: 0.4, fill: true,
                                    pointRadius: 0, pointHoverRadius: 4,
                                    yAxisID: 'y',
                                }},
                                {{
                                    label: 'Net Flow',
                                    data: [],
                                    borderColor: '#58a6ff',
                                    backgroundColor: 'transparent',
                                    borderWidth: 2.5, tension: 0.4, fill: false,
                                    borderDash: [5, 3],
                                    pointRadius: 0, pointHoverRadius: 5,
                                    pointHoverBackgroundColor: '#58a6ff',
                                    yAxisID: 'y',
                                }},
                            ],
                        }},
                        options: {{
                            responsive: true, maintainAspectRatio: false,
                            animation: {{ duration: 500, easing: 'easeOutQuart' }},
                            interaction: {{ mode: 'index', intersect: false }},
                            plugins: {{
                                legend: {{ display: false }},
                                tooltip: {{
                                    backgroundColor: '#161b22', borderColor: '#30363d',
                                    borderWidth: 1, titleColor: '#fff', bodyColor: '#c9d1d9',
                                    padding: 10,
                                    callbacks: {{
                                        label: c => c.dataset.label + ': ' + c.parsed.y.toFixed(4) + ' BTC',
                                    }},
                                }},
                            }},
                            scales: {{
                                x: {{
                                    grid: {{ color: 'rgba(48,54,61,0.4)', drawBorder: false }},
                                    ticks: {{ color: '#6e7681', maxTicksLimit: 6, font: {{ size: 9 }} }},
                                }},
                                y: {{
                                    beginAtZero: true,
                                    grid: {{ color: 'rgba(48,54,61,0.4)', drawBorder: false }},
                                    ticks: {{ color: '#8b949e', font: {{ size: 9 }},
                                             callback: v => v.toFixed(1) }},
                                }},
                            }},
                        }},
                    }});

                    function updateFlowCard(cur) {{
                        if (!cur) return;
                        // Update signal + net value live from /flow.json
                        const signalEl = document.querySelector('#flow-card .flow-signal');
                        const netEl    = document.querySelector('#flow-card .flow-net-value');
                        const detailEl = document.querySelector('#flow-card .flow-detail');
                        if (signalEl) {{ signalEl.textContent = cur.signal; signalEl.style.color = cur.sig_color; }}
                        if (netEl)   {{
                            const sign = cur.net_btc >= 0 ? '+' : '';
                            netEl.textContent = sign + cur.net_btc.toFixed(4);
                            netEl.style.color = cur.sig_color;
                        }}
                        if (detailEl) detailEl.textContent = cur.sig_detail;
                    }}

                    function refreshFlow() {{
                        fetch('/flow.json', {{ cache: 'no-store' }})
                            .then(r => r.json())
                            .then(d => {{
                                chart.data.labels            = d.labels;
                                chart.data.datasets[0].data  = d.accums;
                                chart.data.datasets[1].data  = d.distribs;
                                chart.data.datasets[2].data  = d.nets;
                                chart.update();
                                if (emptyEl) emptyEl.classList.toggle('hidden', d.points > 0);
                                updateFlowCard(d.current);
                            }})
                            .catch(() => {{}});
                    }}
                    refreshFlow();
                    setInterval(refreshFlow, 30000);
                }})();

                // ===== 60-min Sparkline (Chart.js) =====
                (function() {{
                    const canvas = document.getElementById('sparkline-canvas');
                    const emptyEl = document.getElementById('sparkline-empty');
                    const pointsEl = document.getElementById('sparkline-points');
                    if (!canvas || typeof Chart === 'undefined') return;

                    const ctx = canvas.getContext('2d');
                    // Soft gradient fills under each line
                    const gradCount = ctx.createLinearGradient(0, 0, 0, 260);
                    gradCount.addColorStop(0, 'rgba(88, 166, 255, 0.35)');
                    gradCount.addColorStop(1, 'rgba(88, 166, 255, 0.00)');
                    const gradVol = ctx.createLinearGradient(0, 0, 0, 260);
                    gradVol.addColorStop(0, 'rgba(240, 183, 47, 0.30)');
                    gradVol.addColorStop(1, 'rgba(240, 183, 47, 0.00)');

                    const chart = new Chart(ctx, {{
                        type: 'line',
                        data: {{
                            labels: [],
                            datasets: [
                                {{
                                    label: 'Whale TX Count',
                                    data: [],
                                    borderColor: '#58a6ff',
                                    backgroundColor: gradCount,
                                    borderWidth: 2,
                                    tension: 0.4,
                                    fill: true,
                                    pointRadius: 0,
                                    pointHoverRadius: 5,
                                    pointHoverBackgroundColor: '#58a6ff',
                                    pointHoverBorderColor: '#0d1117',
                                    yAxisID: 'yCount',
                                }},
                                {{
                                    label: 'Total BTC Volume',
                                    data: [],
                                    borderColor: '#f0b72f',
                                    backgroundColor: gradVol,
                                    borderWidth: 2,
                                    tension: 0.4,
                                    fill: true,
                                    pointRadius: 0,
                                    pointHoverRadius: 5,
                                    pointHoverBackgroundColor: '#f0b72f',
                                    pointHoverBorderColor: '#0d1117',
                                    yAxisID: 'yVolume',
                                }},
                            ],
                        }},
                        options: {{
                            responsive: true,
                            maintainAspectRatio: false,
                            animation: {{ duration: 600, easing: 'easeOutQuart' }},
                            interaction: {{ mode: 'index', intersect: false }},
                            plugins: {{
                                legend: {{ display: false }},
                                tooltip: {{
                                    backgroundColor: '#161b22',
                                    borderColor: '#30363d',
                                    borderWidth: 1,
                                    titleColor: '#ffffff',
                                    bodyColor: '#c9d1d9',
                                    padding: 10,
                                    displayColors: true,
                                    callbacks: {{
                                        label: function(c) {{
                                            if (c.dataset.label === 'Total BTC Volume') {{
                                                return c.dataset.label + ': ' + c.parsed.y.toFixed(4) + ' BTC';
                                            }}
                                            return c.dataset.label + ': ' + c.parsed.y;
                                        }}
                                    }},
                                }},
                            }},
                            scales: {{
                                x: {{
                                    grid: {{ color: 'rgba(48, 54, 61, 0.4)', drawBorder: false }},
                                    ticks: {{ color: '#6e7681', maxTicksLimit: 8, font: {{ size: 10 }} }},
                                }},
                                yCount: {{
                                    type: 'linear', position: 'left',
                                    beginAtZero: true,
                                    grid: {{ color: 'rgba(48, 54, 61, 0.4)', drawBorder: false }},
                                    ticks: {{ color: '#58a6ff', font: {{ size: 10 }}, precision: 0 }},
                                    title: {{ display: true, text: 'TX COUNT', color: '#58a6ff',
                                             font: {{ size: 10, weight: '700' }} }},
                                }},
                                yVolume: {{
                                    type: 'linear', position: 'right',
                                    beginAtZero: true,
                                    grid: {{ display: false }},
                                    ticks: {{ color: '#f0b72f', font: {{ size: 10 }},
                                             callback: function(v) {{ return v.toFixed(1); }} }},
                                    title: {{ display: true, text: 'BTC VOLUME', color: '#f0b72f',
                                             font: {{ size: 10, weight: '700' }} }},
                                }},
                            }},
                        }},
                    }});

                    function applyIntegrity(integ) {{
                        if (!integ) return;
                        const dot   = document.getElementById('integrity-dot');
                        const label = document.getElementById('integrity-label');
                        const detail = document.getElementById('integrity-detail');
                        if (dot)   {{ dot.style.background = integ.color; dot.style.color = integ.color; }}
                        if (label) {{ label.style.color = integ.color; label.textContent = 'DATA INTEGRITY: ' + integ.label; }}
                        if (detail) {{ detail.textContent = integ.detail; }}
                    }}

                    function refresh() {{
                        fetch('/history.json', {{ cache: 'no-store' }})
                            .then(r => r.json())
                            .then(d => {{
                                chart.data.labels = d.labels;
                                chart.data.datasets[0].data = d.counts;
                                chart.data.datasets[1].data = d.volumes;
                                chart.update();
                                if (pointsEl) pointsEl.textContent = d.points;
                                if (emptyEl) emptyEl.classList.toggle('hidden', d.points > 0);
                                applyIntegrity(d.integrity);
                            }})
                            .catch(() => {{}});
                    }}
                    refresh();
                    setInterval(refresh, 30000);
                }})();

                // Refresh countdown
                (function() {{
                    const REFRESH_S = 60;
                    let remaining = REFRESH_S;
                    const el = document.getElementById('refresh-timer');
                    const btn = document.getElementById('refresh-now');
                    function tick() {{
                        if (el) el.textContent = remaining + 's';
                        if (remaining <= 0) {{ window.location.reload(); return; }}
                        remaining--;
                    }}
                    tick();
                    setInterval(tick, 1000);
                    if (btn) btn.addEventListener('click', () => window.location.reload());
                }})();

                // ===== Whale Watchlist =====
                (function() {{
                    const STORAGE_KEY = 'skillshield_watchlist_v1';
                    const tagsEl  = document.getElementById('watchlist-tags');
                    const emptyEl = document.getElementById('watchlist-empty');
                    const inputEl = document.getElementById('watchlist-input');
                    const formEl  = document.getElementById('watchlist-form');
                    const hitsBadge = document.getElementById('watchlist-hits-badge');

                    function load() {{
                        try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); }}
                        catch (e) {{ return []; }}
                    }}
                    function save(list) {{
                        localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
                    }}
                    function shortAddr(a) {{
                        if (!a || a.length < 16) return a;
                        return a.slice(0, 8) + '…' + a.slice(-6);
                    }}
                    function isPlausibleBtcAddr(s) {{
                        if (!s) return false;
                        return /^(bc1|tb1|[13])[a-zA-HJ-NP-Z0-9]{{20,80}}$/.test(s);
                    }}

                    // Unique gold-alert sound (warmer / different from whale-alert beep)
                    let audioCtx = null;
                    function goldBeep() {{
                        try {{
                            audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
                            const now = audioCtx.currentTime;
                            [523.25, 659.25, 783.99].forEach((freq, i) => {{
                                const o = audioCtx.createOscillator();
                                const g = audioCtx.createGain();
                                o.type = 'triangle';
                                o.frequency.value = freq;
                                g.gain.setValueAtTime(0.0001, now + i * 0.12);
                                g.gain.exponentialRampToValueAtTime(0.12, now + i * 0.12 + 0.02);
                                g.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.12 + 0.22);
                                o.connect(g); g.connect(audioCtx.destination);
                                o.start(now + i * 0.12);
                                o.stop(now + i * 0.12 + 0.25);
                            }});
                        }} catch (e) {{}}
                    }}

                    function showToast(msg) {{
                        const t = document.createElement('div');
                        t.className = 'watchlist-toast';
                        t.textContent = msg;
                        document.body.appendChild(t);
                        setTimeout(() => {{ t.style.transition = 'opacity 0.4s ease'; t.style.opacity = '0'; }}, 4500);
                        setTimeout(() => t.remove(), 5000);
                    }}

                    function renderTags() {{
                        const list = load();
                        tagsEl.innerHTML = '';
                        if (list.length === 0) {{
                            emptyEl.style.display = 'block';
                        }} else {{
                            emptyEl.style.display = 'none';
                            list.forEach(addr => {{
                                const tag = document.createElement('span');
                                tag.className = 'watchlist-tag';
                                tag.innerHTML = `<span class="tag-addr" title="${{addr}}">${{shortAddr(addr)}}</span>` +
                                                `<button type="button" class="tag-remove" aria-label="Remove">×</button>`;
                                tag.querySelector('.tag-remove').addEventListener('click', () => {{
                                    save(load().filter(x => x !== addr));
                                    renderTags();
                                    scanRows(false); // silent rescan
                                }});
                                tagsEl.appendChild(tag);
                            }});
                        }}
                    }}

                    function scanRows(announce) {{
                        const list = load();
                        const watched = new Set(list);
                        const rows = document.querySelectorAll('.whale-table tr[data-addresses]');
                        let hitCount = 0;
                        const matchedAddrs = new Set();
                        rows.forEach(tr => {{
                            const addrs = (tr.getAttribute('data-addresses') || '').split(',').filter(Boolean);
                            const matched = addrs.find(a => watched.has(a));
                            if (matched) {{
                                tr.classList.add('watchlist-hit');
                                matchedAddrs.add(matched);
                                if (!tr.querySelector('.watchlist-badge')) {{
                                    const b = document.createElement('span');
                                    b.className = 'watchlist-badge';
                                    b.textContent = '🥇 WATCHED';
                                    tr.querySelector('.rank').appendChild(b);
                                }}
                                hitCount++;
                            }} else {{
                                tr.classList.remove('watchlist-hit');
                                const b = tr.querySelector('.watchlist-badge');
                                if (b) b.remove();
                            }}
                        }});
                        if (hitCount > 0) {{
                            hitsBadge.hidden = false;
                            hitsBadge.textContent = hitCount + (hitCount === 1 ? ' hit' : ' hits');
                        }} else {{
                            hitsBadge.hidden = true;
                        }}
                        if (announce && hitCount > 0) {{
                            goldBeep();
                            showToast('🥇 Watchlist match: ' + hitCount + ' transaction(s)');
                        }}
                    }}

                    formEl.addEventListener('submit', () => {{
                        const v = (inputEl.value || '').trim();
                        if (!v) return;
                        if (!isPlausibleBtcAddr(v)) {{
                            inputEl.style.borderColor = '#ff7b72';
                            setTimeout(() => {{ inputEl.style.borderColor = ''; }}, 1200);
                            return;
                        }}
                        const list = load();
                        if (!list.includes(v)) list.push(v);
                        save(list);
                        inputEl.value = '';
                        renderTags();
                        scanRows(true);
                    }});

                    renderTags();
                    // Announce on initial load if any matches are already on screen.
                    scanRows(true);
                }})();
            </script>

            </main><!-- /main-content -->

            <!-- ===== ABOUT SKILL SHIELD BTC ===== -->
            <footer role="contentinfo" aria-label="About and Contact Skill Shield BTC" style="padding-bottom:40px;">
            <section id="about" style="max-width:900px;margin:40px auto 0;padding:0 16px"
                     itemscope itemtype="https://schema.org/SoftwareApplication">
                <div style="background:#161b22;border:1px solid #30363d;border-radius:16px;padding:36px 32px">
                    <div style="color:#58a6ff;font-size:0.72em;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">About</div>
                    <h2 itemprop="name" style="color:#fff;font-size:1.4em;font-weight:900;margin-bottom:14px;letter-spacing:-0.5px">
                        Skill Shield BTC — Quantitative Mempool Intelligence
                    </h2>
                    <p itemprop="description" style="color:#8b949e;font-size:0.88em;line-height:1.8;margin-bottom:20px">
                        Skill Shield BTC is a real-time on-chain analytics platform that fuses live Bitcoin mempool data,
                        whale transaction flow analysis, and network velocity signals into a single institutional-grade dashboard.
                        Every data point is sourced directly from the public Bitcoin mempool and linked to
                        <a href="https://mempool.space" target="_blank" rel="noopener" style="color:#58a6ff">mempool.space</a>
                        so you can independently verify every signal we display.
                    </p>
                    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px">
                        <div style="background:rgba(88,166,255,0.06);border:1px solid rgba(88,166,255,0.15);border-radius:10px;padding:16px">
                            <div style="color:#58a6ff;font-size:1.1em;margin-bottom:6px">🐋 Whale Actionable Bias</div>
                            <div style="color:#8b949e;font-size:0.8em;line-height:1.6">
                                A directional signal fused from mempool transaction volume, input/output flow classification,
                                and legacy wallet activity. Updated every 30 seconds from live unconfirmed transactions.
                            </div>
                        </div>
                        <div style="background:rgba(88,166,255,0.06);border:1px solid rgba(88,166,255,0.15);border-radius:10px;padding:16px">
                            <div style="color:#58a6ff;font-size:1.1em;margin-bottom:6px">⚡ Network Velocity Monitor</div>
                            <div style="color:#8b949e;font-size:0.8em;line-height:1.6">
                                Real-time mempool throughput compared against a rolling 24-hour baseline.
                                Velocity spikes above baseline often precede confirmed on-chain accumulation or distribution events.
                            </div>
                        </div>
                        <div style="background:rgba(88,166,255,0.06);border:1px solid rgba(88,166,255,0.15);border-radius:10px;padding:16px">
                            <div style="color:#58a6ff;font-size:1.1em;margin-bottom:6px">💎 Smart Money Flow</div>
                            <div style="color:#8b949e;font-size:0.8em;line-height:1.6">
                                Transaction sweep (few inputs → many outputs) vs. fan-out classification reveals
                                whether large wallets are consolidating (accumulating) or distributing holdings in real time.
                            </div>
                        </div>
                        <div style="background:rgba(88,166,255,0.06);border:1px solid rgba(88,166,255,0.15);border-radius:10px;padding:16px">
                            <div style="color:#58a6ff;font-size:1.1em;margin-bottom:6px">🔬 Institutional Intelligence</div>
                            <div style="color:#8b949e;font-size:0.8em;line-height:1.6">
                                NLP sentiment analysis across live crypto news headlines, cross-referenced with on-chain
                                mempool signals to surface high-confidence institutional narrative shifts as they develop.
                            </div>
                        </div>
                    </div>
                    <div style="color:#6e7681;font-size:0.78em;line-height:1.7;border-top:1px solid #21262d;padding-top:16px">
                        <b style="color:#8b949e">Data Sources:</b> blockchain.info Unconfirmed Transactions API · blockchain.info Market Data ·
                        Alternative.me Fear &amp; Greed Index · Public Bitcoin RSS feeds ·
                        All transactions independently verifiable on <a href="https://mempool.space" target="_blank" rel="noopener" style="color:#58a6ff">mempool.space</a>.
                        <br><b style="color:#8b949e">Disclaimer:</b> All signals are probabilistic, not predictive.
                        This platform does not constitute financial advice. Past signal frequency does not guarantee future performance.
                    </div>
                </div>
            </section>

            <!-- ===== CONTACT US ===== -->
            <section id="contact" style="max-width:900px;margin:24px auto 48px;padding:0 16px">
                <div style="background:#161b22;border:1px solid #30363d;border-radius:16px;padding:36px 32px">
                    <div style="color:#58a6ff;font-size:0.72em;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:10px">Support</div>
                    <h2 style="color:#fff;font-size:1.3em;font-weight:900;margin-bottom:6px">Contact Us</h2>
                    <p style="color:#8b949e;font-size:0.85em;margin-bottom:16px;line-height:1.6">
                        Have a question, bug report, or need help with your account?
                        Fill out the form below or email us directly at
                        <a href="mailto:support@skillshieldbtc.com" style="color:#58a6ff;font-weight:600">support@skillshieldbtc.com</a>.
                    </p>
                    <div style="background:linear-gradient(135deg,rgba(88,166,255,0.08),rgba(63,185,80,0.06));border:1px solid rgba(88,166,255,0.2);border-radius:10px;padding:14px 18px;margin-bottom:20px">
                        <div style="color:#79c0ff;font-size:0.8em;font-weight:700;margin-bottom:4px">💡 Feature Requests &amp; Quant Feedback Welcome</div>
                        <div style="color:#8b949e;font-size:0.82em;line-height:1.65">
                            Have a feature request, custom signal requirement, or feedback for our quant team?
                            Submit your suggestion below. <b style="color:#c9d1d9">High-conviction ideas are evaluated by our research team
                            for upcoming dashboard releases.</b>
                        </div>
                    </div>
                    <div id="contact-success" style="display:none;background:rgba(63,185,80,0.1);border:1px solid rgba(63,185,80,0.3);border-radius:8px;padding:12px 16px;color:#3fb950;font-size:0.85em;margin-bottom:16px">
                        ✅ Message sent! We'll respond to your email within a few hours.
                    </div>
                    <div id="contact-error" style="display:none;background:rgba(255,82,82,0.08);border:1px solid rgba(255,82,82,0.3);border-radius:8px;padding:12px 16px;color:#ff5252;font-size:0.85em;margin-bottom:16px"></div>
                    <form id="contact-form" style="max-width:520px">
                        <div style="margin-bottom:14px">
                            <label style="display:block;color:#8b949e;font-size:0.7em;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:6px">Email Address</label>
                            <input type="email" id="cf-email" required
                                   style="display:block;width:100%;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:11px 14px;color:#fff;font-size:0.9em;font-family:inherit;outline:none;box-sizing:border-box"
                                   placeholder="you@example.com">
                        </div>
                        <div style="margin-bottom:14px">
                            <label style="display:block;color:#8b949e;font-size:0.7em;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:6px">Subject</label>
                            <input type="text" id="cf-subject" required
                                   style="display:block;width:100%;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:11px 14px;color:#fff;font-size:0.9em;font-family:inherit;outline:none;box-sizing:border-box"
                                   placeholder="e.g. Account access, billing question…">
                        </div>
                        <div style="margin-bottom:18px">
                            <label style="display:block;color:#8b949e;font-size:0.7em;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:6px">Message</label>
                            <textarea id="cf-message" required rows="5"
                                      style="display:block;width:100%;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:11px 14px;color:#fff;font-size:0.9em;font-family:inherit;outline:none;resize:vertical;box-sizing:border-box"
                                      placeholder="Describe your issue or question…"></textarea>
                        </div>
                        <button type="submit"
                                style="background:linear-gradient(135deg,#1f6feb,#388bfd);color:#fff;border:none;border-radius:9px;padding:12px 28px;font-size:0.9em;font-weight:800;cursor:pointer;letter-spacing:0.3px">
                            ✉ Send Message
                        </button>
                    </form>
                    <script>
                    (function(){{
                        document.getElementById('contact-form').addEventListener('submit', function(e) {{
                            e.preventDefault();
                            var btn = this.querySelector('button[type=submit]');
                            btn.disabled = true;
                            btn.textContent = 'Sending…';
                            fetch('/contact', {{
                                method: 'POST',
                                headers: {{'Content-Type': 'application/json'}},
                                body: JSON.stringify({{
                                    email:   document.getElementById('cf-email').value,
                                    subject: document.getElementById('cf-subject').value,
                                    message: document.getElementById('cf-message').value
                                }})
                            }})
                            .then(function(r) {{ return r.json(); }})
                            .then(function(d) {{
                                btn.disabled = false;
                                btn.textContent = '✉ Send Message';
                                if (d.ok) {{
                                    document.getElementById('contact-success').style.display = 'block';
                                    document.getElementById('contact-form').reset();
                                }} else {{
                                    var errEl = document.getElementById('contact-error');
                                    errEl.textContent = d.error || 'Something went wrong. Please try again.';
                                    errEl.style.display = 'block';
                                }}
                            }})
                            .catch(function() {{
                                btn.disabled = false;
                                btn.textContent = '✉ Send Message';
                                var errEl = document.getElementById('contact-error');
                                errEl.textContent = 'Network error. Please try again.';
                                errEl.style.display = 'block';
                            }});
                        }});
                    }})();
                    </script>
                </div>
            </section>
            </footer><!-- /contentinfo -->

        </body>
    </html>
    """
    return html_template


# ── Render keep-alive self-ping ──────────────────────────────────────────────
def _start_keep_alive():
    """Ping the app every 14 minutes so Render free-tier doesn't spin it down."""
    import threading, time as _t, os as _os
    render_url = _os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not render_url:
        return  # only active on Render

    def _ping():
        while True:
            _t.sleep(14 * 60)
            try:
                import requests as _req
                _req.get(f"{render_url}/", timeout=10)
            except Exception:
                pass

    t = threading.Thread(target=_ping, daemon=True)
    t.start()

_start_keep_alive()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
