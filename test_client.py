#!/usr/bin/env python3
"""Keepa client tests against a fake API (no network, no tokens). Run: python test_client.py"""
import os, sys, json, shutil, tempfile, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keepa_client as kc

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond); print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")

class Resp:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self): return self._b

class FakeKeepa:
    """Token bucket of 300, refill 5/min (simulated clock). Random 429s, network errors,
    one 503. Finder: 3 categories × 250 listings. Some ASINs not returned by /product."""
    def __init__(self, crash_after=None):
        self.clock, self.tokens, self.cap, self.rate = 0.0, 300.0, 300, 5
        self.calls = self.errs = self.n429 = 0
        self.crash_after = crash_after
        self.db = {str(c): [f"B0{c}{i:05d}" for i in range(250)] for c in (11, 22, 33)}
        self.missing = {"B01100007", "B02200100"}
        self.rng = random.Random(1)
    def sleep(self, s):
        self.clock += s; self.tokens = min(self.cap, self.tokens + s / 60 * self.rate)
    def _meta(self):
        return {"tokensLeft": int(self.tokens), "refillRate": self.rate,
                "refillIn": int((1 - (self.tokens % 1)) * 60000 / self.rate)}
    def _spend(self, cost):
        self.calls += 1
        if self.crash_after and self.calls > self.crash_after:
            raise KeyboardInterrupt("simulated crash")
        if self.rng.random() < .08:
            self.errs += 1; raise ConnectionError("simulated reset")
        if self.rng.random() < .05:
            return Resp(503, {"error": "busy"})
        if self.tokens < cost:
            self.n429 += 1; return Resp(429, {"error": "tokens", **self._meta()})
        self.tokens -= cost
        return None
    def post(self, url, params=None, json=None, timeout=None):
        r = self._spend(10)
        if r: return r
        cat = str(json["rootCategory"][0]); pg, pp = json["page"], json["perPage"]
        lst = self.db[cat][pg * pp:(pg + 1) * pp]
        return Resp(200, {"asinList": lst, "totalResults": len(self.db[cat]), **self._meta()})
    def get(self, url, params=None, timeout=None):
        asins = params["asin"].split(",")
        r = self._spend(2 * len(asins))
        if r: return r
        return Resp(200, {"products": [{"asin": a} for a in asins if a not in self.missing], **self._meta()})

d = tempfile.mkdtemp()
out, cache = os.path.join(d, "asins.txt"), os.path.join(d, "cache")

# 1. discover with crash mid-way, then resume
f1 = FakeKeepa(crash_after=4)
c1 = kc.KeepaClient("k", session=f1, sleep=f1.sleep)
try:
    kc.discover(c1, [11, 22, 33], {}, 200, out, 10)
except KeyboardInterrupt:
    pass
partial = sum(1 for _ in open(out)) if os.path.exists(out) else 0
f2 = FakeKeepa()
c2 = kc.KeepaClient("k", session=f2, sleep=f2.sleep)
got = kc.discover(c2, [11, 22, 33], {}, 200, out, 10)
lines = [l.strip().split(",") for l in open(out)]
check("discover survives crash, resumes without duplicates",
      len(lines) == 600 and len({a for a, _ in lines}) == 600, f"partial={partial} final={len(lines)}")
check("discover is stratified (200 per category)", all(v == 200 for v in got.values()), str(got))

# 2. fetch through 429s, network errors, 503s, with a drained bucket
f3 = FakeKeepa(); f3.tokens = 3
c3 = kc.KeepaClient("k", session=f3, sleep=f3.sleep)
kc.fetch(c3, [a for a, _ in lines], cache, 2, batch=20)
files = os.listdir(cache)
miss = [f for f in files if json.load(open(os.path.join(cache, f))).get("_missing")]
check("fetch completes all 600 despite errors", len(files) == 600,
      f"429s={f3.n429} net_errs={f3.errs} simulated hours={f3.clock / 3600:.1f}")
check("unreturned ASINs recorded, not retried forever", len(miss) == 2, f"{len(miss)} placeholders")
check("token bucket never went negative", f3.tokens >= 0)

# 3. resume fetch = zero calls
f4 = FakeKeepa(); c4 = kc.KeepaClient("k", session=f4, sleep=f4.sleep)
kc.fetch(c4, [a for a, _ in lines], cache, 2)
check("re-running fetch on a full cache costs 0 calls", f4.calls == 0)

# 4. non-retryable error raises instead of looping
class Bad(FakeKeepa):
    def get(self, *a, **k): return Resp(402, {"error": "payment required"})
b = Bad(); cb = kc.KeepaClient("k", session=b, sleep=b.sleep)
try:
    kc.fetch(cb, ["B0X"], os.path.join(d, "c2"), 2); raised = False
except RuntimeError:
    raised = True
check("HTTP 402 raises (no silent infinite retry)", raised)

shutil.rmtree(d)
print("\nALL PASS" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
