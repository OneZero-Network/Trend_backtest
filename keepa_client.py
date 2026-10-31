"""
Rate-limited, resumable Keepa client for multi-day pulls.

Guarantees
  * Never advances past a batch/page until it succeeded → nothing is dropped.
  * 429 / 5xx / network errors → wait (Keepa's refillIn if given, else exponential
    backoff capped at 15 min) and retry the SAME request, forever unless MAX_WAIT_H hit.
  * Token budgeting before each call: if the bucket can't cover the call, sleep
    until it can (uses tokensLeft / refillRate / refillIn from the last response).
  * State on disk after every success:
      discover → <out>.state.json (per-category next page) + appends to <out> (asin,category)
      fetch    → one <ASIN>.json per product in the cache dir (skip if exists)
    Kill it any time; re-run the same command to resume.
"""
import os, json, time, random
from datetime import datetime, timedelta

API = "https://api.keepa.com"
MAX_BACKOFF_S = 900
MAX_WAIT_H = 72           # give up (raise) if one request keeps failing this long


def _log(msg):
    print(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}", flush=True)


class KeepaClient:
    def __init__(self, key, session=None, refill_per_min=None, sleep=time.sleep):
        import requests
        self.key = key
        self.s = session or requests.Session()
        self.tokens_left = None
        self.refill_rate = refill_per_min      # tokens/min; learned from responses
        self.refill_in_ms = None
        self.sleep = sleep
        self.spent = 0

    # ── token budgeting ────────────────────────────────────────────────
    def _wait_for(self, cost):
        if self.tokens_left is None or self.tokens_left >= cost:
            return
        deficit = cost - self.tokens_left
        if self.refill_rate:
            wait = deficit / self.refill_rate * 60 + 2
        else:
            wait = (self.refill_in_ms or 60000) / 1000 + 2
        _log(f"  budget: need {cost}, have {self.tokens_left} → sleeping {wait / 60:.1f} min")
        self.sleep(wait)
        self.tokens_left = None            # unknown until next response

    def _update(self, j):
        if not isinstance(j, dict):
            return
        if "tokensLeft" in j:
            self.tokens_left = j["tokensLeft"]
        if j.get("refillRate"):
            self.refill_rate = j["refillRate"]
        if "refillIn" in j:
            self.refill_in_ms = j["refillIn"]
        if "tokensConsumed" in j:
            self.spent += j["tokensConsumed"]

    # ── one call with retry ────────────────────────────────────────────
    def call(self, method, path, cost, params=None, body=None):
        params = {**(params or {}), "key": self.key}
        attempt, t0 = 0, time.time()
        while True:
            self._wait_for(cost)
            try:
                if method == "GET":
                    r = self.s.get(f"{API}/{path}", params=params, timeout=180)
                else:
                    r = self.s.post(f"{API}/{path}", params=params, json=body, timeout=180)
                status = r.status_code
                try:
                    j = r.json()
                except ValueError:
                    j = {}
            except Exception as exc:              # network / timeout
                status, j = None, {"error": f"{type(exc).__name__}: {exc}"}
            self._update(j)
            if status == 200 and not j.get("error"):
                return j
            if status in (400, 401, 402, 403, 404):
                raise RuntimeError(f"Keepa {path} HTTP {status} (not retryable): {str(j)[:300]}")
            attempt += 1
            if (time.time() - t0) / 3600 > MAX_WAIT_H:
                raise RuntimeError(f"Keepa {path} still failing after {MAX_WAIT_H}h: {str(j)[:300]}")
            if status == 429 and self.refill_in_ms:
                wait = self.refill_in_ms / 1000 + 2
            else:
                wait = min(MAX_BACKOFF_S, 5 * 2 ** min(attempt, 8)) + random.uniform(0, 3)
            _log(f"  {path}: HTTP {status} {str(j)[:120]} → retry {attempt} in {wait:.0f}s")
            self.sleep(wait)

    def eta(self, tokens):
        rate = self.refill_rate or 5
        return timedelta(minutes=tokens / rate)


# ══════════════════════════════════════════════════════════════════════
# DISCOVER (Product Finder) — stratified, resumable
# ══════════════════════════════════════════════════════════════════════

def discover(client, categories, sel_extra, per_cat, out_file, finder_cost, per_page=100):
    """Writes 'asin,category' lines. Equal quota per category (stratified).
    sel_extra: dict of finder selection keys shared by all categories."""
    state_f = out_file + ".state.json"
    state = json.load(open(state_f)) if os.path.exists(state_f) else {}
    have = {}
    if os.path.exists(out_file):
        for line in open(out_file):
            if "," in line:
                a, c = line.strip().split(",", 1)
                have.setdefault(c, set()).add(a)
    est_pages = sum(max(0, -(-(per_cat - len(have.get(str(c), ()))) // per_page)) for c in categories)
    _log(f"discover: {len(categories)} categories × {per_cat} · ~{est_pages} finder calls · "
         f"~{est_pages * finder_cost} tokens · ETA ≥ {client.eta(est_pages * finder_cost)}")
    for cat in categories:
        key = str(cat)
        st = state.get(key, {"page": 0, "done": False})
        got = have.setdefault(key, set())
        while not st["done"] and len(got) < per_cat:
            sel = {**sel_extra, "rootCategory": [cat], "perPage": per_page, "page": st["page"]}
            j = client.call("POST", "query", finder_cost, params={"domain": 10}, body=sel)
            asins = j.get("asinList") or []
            new = [a for a in asins if a not in got][: per_cat - len(got)]
            with open(out_file, "a") as fh:
                fh.writelines(f"{a},{key}\n" for a in new)
            got.update(new)
            st["page"] += 1
            st["done"] = len(asins) < per_page
            st["total_results"] = j.get("totalResults")
            state[key] = st
            json.dump(state, open(state_f, "w"))
            _log(f"  cat {key} page {st['page']}: +{len(new)} (have {len(got)}/{per_cat}, "
                 f"finder total {st.get('total_results')}) · tokensLeft {client.tokens_left}")
        if len(got) < per_cat:
            _log(f"  [WARN] cat {key}: only {len(got)} listings exist for this cohort "
                 f"(< {per_cat}) — category is under-represented")
    return {c: len(v) for c, v in have.items()}


# ══════════════════════════════════════════════════════════════════════
# FETCH (product histories) — token-aware batches, resumable
# ══════════════════════════════════════════════════════════════════════

def fetch(client, asins, cache_dir, cost_per_product, batch=20, extra_params=None):
    os.makedirs(cache_dir, exist_ok=True)
    todo = [a for a in dict.fromkeys(asins) if not os.path.exists(os.path.join(cache_dir, f"{a}.json"))]
    total = len(todo) * cost_per_product
    _log(f"fetch: {len(asins)} ASINs, {len(todo)} to pull · ~{total} tokens · "
         f"ETA ≥ {client.eta(total)} at {client.refill_rate or 5}/min")
    i = 0
    while i < len(todo):
        # shrink batch so a single call never needs more than the bucket can ever hold
        b = batch
        if client.tokens_left is not None and client.refill_rate:
            b = max(1, min(batch, int(max(client.tokens_left, client.refill_rate * 5) // cost_per_product)))
        chunk = todo[i:i + b]
        j = client.call("GET", "product", len(chunk) * cost_per_product,
                        params={"domain": 10, "asin": ",".join(chunk), **(extra_params or {})})
        prods = j.get("products") or []
        returned = set()
        for p in prods:
            with open(os.path.join(cache_dir, f"{p['asin']}.json"), "w") as fh:
                json.dump(p, fh)
            returned.add(p["asin"])
        for a in chunk:
            if a not in returned:          # record misses so resume doesn't loop on them
                with open(os.path.join(cache_dir, f"{a}.json"), "w") as fh:
                    json.dump({"asin": a, "_missing": True}, fh)
        i += len(chunk)
        _log(f"  {i}/{len(todo)} · tokensLeft {client.tokens_left} · "
             f"remaining ETA ≥ {client.eta((len(todo) - i) * cost_per_product)}")
