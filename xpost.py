"""
X (Twitter) posting agent — v1.60. OFF unless you switch it on.

AFF_X_MODE
  off       (default) nothing happens
  dry       builds the post, checks every cap, sends it to the admin chat as "would post" — costs nothing
  linkfree  posts text with NO link (about $0.015 a post); the Telegram invite link lives in the X profile
  links     posts with the affiliate link (about $0.20 a post) — only after X is listed in Associates

Why link-free first: X charges ~13x more for a post that contains a URL (third-party pages citing X's April 2026
rate card; check your Developer Console), Buffer's 18.8M-post study found non-Premium link posts get a median of zero
engagement, and Amazon only pays on links placed where your Associates account lists them.

Hard rules the agent cannot break (X automation rules): no replies, no likes, no follows, no DMs, no trending
hashtags, and no near-duplicate posts (content.py rotates templates; the agent also refuses a text it already posted).

Caps (enforced here, from X_POST_LOG, before every call):
  AFF_X_MAX_PER_DAY   default 4
  AFF_X_BUDGET_USD    default 10 per calendar month — counts real posts at X_COST_POST / X_COST_URL_POST
Set the same spend limit in the X Developer Console; this code is the second lock, not the only one.

Credentials (repo secrets): X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET — an app with Read and Write
permission and the access token of the account that posts (OAuth 1.0a user context).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse

API = "https://api.x.com/2/tweets"
MODES = ("off", "dry", "linkfree", "links")
MODE = (os.environ.get("AFF_X_MODE") or "off").strip().lower()
MODE = MODE if MODE in MODES else "off"
MAX_PER_DAY = int(os.environ.get("AFF_X_MAX_PER_DAY") or 4)
BUDGET_USD = float(os.environ.get("AFF_X_BUDGET_USD") or 10)
COST_POST = float(os.environ.get("X_COST_POST") or 0.015)
COST_URL_POST = float(os.environ.get("X_COST_URL_POST") or 0.20)
URL_RX = re.compile(r"(https?://|www\.|t\.me/|\b[a-z0-9-]+\.(?:in|com|to|co|me|ly|link)\b/?)", re.I)


def creds_from_env():
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    vals = [os.environ.get(k) or "" for k in keys]
    return tuple(vals) if all(vals) else None


def has_url(text):
    return bool(URL_RX.search(str(text or "")))


def cost_of(text):
    return COST_URL_POST if has_url(text) else COST_POST


def _pct(s):
    return urllib.parse.quote(str(s), safe="")      # RFC 3986: only A-Z a-z 0-9 - . _ ~ stay as they are


def oauth1_header(method, url, params, consumer_key, consumer_secret, token, token_secret, nonce=None, timestamp=None):
    """OAuth 1.0a HMAC-SHA1 Authorization header → (header, signature). `params` = query/form parameters only;
    a JSON body (POST /2/tweets) is NOT part of the signature."""
    oauth = {"oauth_consumer_key": consumer_key, "oauth_nonce": nonce or secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(timestamp or int(time.time())),
             "oauth_token": token, "oauth_version": "1.0"}
    enc = sorted((_pct(k), _pct(v)) for k, v in {**params, **oauth}.items())
    param_str = "&".join(f"{k}={v}" for k, v in enc)
    base = "&".join([method.upper(), _pct(url), _pct(param_str)])
    key = f"{_pct(consumer_secret)}&{_pct(token_secret)}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items())), sig


def guard(log_rows, text, today, month, mode=None):
    """Every cap, checked from the log before a call. → (ok, reason, est_cost).
    log_rows: list of dicts with posted_at ('YYYY-MM-DD HH:MM'), status ('posted' | 'dry' | 'failed' | 'blocked'),
    est_cost, text."""
    mode = mode or MODE
    if mode == "off":
        return False, "X is off (AFF_X_MODE=off)", 0.0
    if mode == "linkfree" and has_url(text):
        return False, "link-free mode but the text contains a link", 0.0
    est = cost_of(text)
    real = [r for r in log_rows if str(r.get("status")) == "posted"]
    counted = real if mode != "dry" else [r for r in log_rows if str(r.get("status")) in ("posted", "dry")]
    today_n = sum(1 for r in counted if str(r.get("posted_at", "")).startswith(today))
    if today_n >= MAX_PER_DAY:
        return False, f"daily cap reached ({today_n}/{MAX_PER_DAY})", est
    spent = sum(float(r.get("est_cost") or 0) for r in real if str(r.get("posted_at", "")).startswith(month))
    if spent + est > BUDGET_USD + 1e-9:
        return False, f"monthly budget reached (${spent:.2f} + ${est:.3f} > ${BUDGET_USD:.2f})", est
    norm = re.sub(r"\W+", " ", str(text)).strip().lower()
    if any(re.sub(r"\W+", " ", str(r.get("text", ""))).strip().lower() == norm for r in counted):
        return False, "same text already posted (X forbids duplicate posts)", est
    return True, "", est


def post(text, creds, http_post=None):
    """POST /2/tweets → (ok, tweet_id_or_error, http_status). `http_post` is injectable for tests."""
    if http_post is None:
        import requests
        http_post = requests.post
    ck, cs, tk, ts = creds
    header, _ = oauth1_header("POST", API, {}, ck, cs, tk, ts)
    r = http_post(API, headers={"Authorization": header, "Content-Type": "application/json"},
                  data=json.dumps({"text": text}), timeout=20)
    status = getattr(r, "status_code", 0)
    try:
        body = r.json()
    except Exception:
        body = {}
    if status in (200, 201) and isinstance(body, dict) and body.get("data", {}).get("id"):
        return True, str(body["data"]["id"]), status
    detail = body.get("detail") or body.get("title") or (getattr(r, "text", "") or "")[:200] if isinstance(body, dict) else ""
    return False, f"HTTP {status}: {detail}", status
