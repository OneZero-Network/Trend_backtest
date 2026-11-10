"""v1.35 automation-opportunity engine: grouping, supply, gap types, shortlist, waitlist gate, offline run."""
import os, sys, shutil, tempfile
from datetime import datetime, timezone
import pandas as pd
os.environ.setdefault("BR_MODE", "daily")
import automation as A

FAILS = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + str(info) if not ok else ""))
    if not ok: FAILS.append(name)

check("titles map to problem groups",
      A.bucket_of("How to auto-generate GST invoice PDF from Google Sheets") == ["gst_invoice", "pdf_docs"]
      and A.bucket_of("Send WhatsApp message when a row is added") == ["whatsapp_msg"]
      and A.bucket_of("VBA loop through cells") == [], A.bucket_of("How to auto-generate GST invoice PDF from Google Sheets"))
check("trend seeds rotate weekly", A.trend_seeds("2026-11-09") != A.trend_seeds("2026-11-16"))

# Stack Overflow API item shape
so = A.so_demand([
    {"title": "Generate invoice PDF for each row in Google Sheets", "view_count": 900, "is_answered": False},
    {"title": "Create GST invoice numbers automatically in Excel", "view_count": 300, "is_answered": True},
    {"title": "Invoice template VBA copy totals", "view_count": 120, "is_answered": False},
    {"title": "Merge multiple workbooks into one sheet", "view_count": 50, "is_answered": True}])
check("demand counts questions, views, unanswered per group",
      so["gst_invoice"]["so_questions"] == 3 and so["gst_invoice"]["so_views"] == 1320 and so["gst_invoice"]["so_unanswered"] == 2, so)

# GitHub search item shape
now = datetime(2026, 11, 10, tzinfo=timezone.utc)
repos = [
    {"full_name": "a/gst-invoice-lib", "stargazers_count": 400, "pushed_at": "2026-09-01T00:00:00Z",
     "license": {"spdx_id": "MIT"}, "description": "Python library for GST invoices", "topics": [], "html_url": "u1"},
    {"full_name": "b/old-invoice", "stargazers_count": 900, "pushed_at": "2021-01-01T00:00:00Z",
     "license": {"spdx_id": "GPL-3.0"}, "description": "Invoice web app", "topics": [], "html_url": "u2"},
    {"full_name": "c/nolicence", "stargazers_count": 50, "pushed_at": "2026-10-01T00:00:00Z",
     "license": None, "description": "scripts", "topics": [], "html_url": "u3"}]
sp = A.gh_supply(57, repos, now=now)
check("maintained = updated ≤12 months AND ≥20 stars; stale 900★ repo doesn't count", sp["gh_maintained"] == 2, sp)
check("'easy to use' only for app/add-on/web-app style projects", sp["gh_easy"] == 0, sp)
check("licences recorded; only permissive marked reusable",
      [(t["repo"], t["reusable"]) for t in sp["gh_top"]] == [("a/gst-invoice-lib", True), ("b/old-invoice", False), ("c/nolicence", False)])

k1, s1 = A.gap(so["gst_invoice"], sp)
k2, _ = A.gap(so["gst_invoice"], {"gh_maintained": 0, "gh_easy": 0})
k3, s3 = A.gap(so["gst_invoice"], {"gh_maintained": 6, "gh_easy": 5})
check("gap types: PACKAGE (dev-only code exists) · BUILD (nothing maintained) · CROWDED (many easy tools, low score)",
      (k1, k2, k3) == ("PACKAGE", "BUILD", "CROWDED") and s3 < s1, (k1, k2, k3, s1, s3))

gt = A.trends_demand([("gst invoice generator free", 450), ("gst invoice format excel", 900)])
rows = A.week_rows(so, gt, {"gst_invoice": sp}, "2026-11-09")
r0 = rows[0]
check("row: ranked, both sources, reusable vs do-not-reuse split",
      r0["bucket"] == "gst_invoice" and r0["sources"] == "stackoverflow,gtrends"
      and "a/gst-invoice-lib (mit" in r0["reusable_code"] and "c/nolicence (none)" in r0["do_not_reuse"], r0)
check("week 1 → watch (needs 3 weeks)", r0["status"] == "watch" and r0["weeks_in_top"] == 1)
hist = [{"week": "2026-10-26", "bucket": "gst_invoice", "in_top": "True"},
        {"week": "2026-11-02", "bucket": "gst_invoice", "in_top": "True"}]
r3 = A.week_rows(so, gt, {"gst_invoice": sp}, "2026-11-09", hist)[0]
check("3rd week in top 5 with both sources → SHORTLIST", r3["status"].startswith("SHORTLIST") and r3["weeks_in_top"] == 3, r3)
r_one = A.week_rows(so, {}, {"gst_invoice": sp}, "2026-11-09", hist)[0]
check("one source only → never shortlisted", r_one["status"] == "watch")

wv = A.waitlist_verdict([{"bucket": "(example — use a bucket key)", "signups": "99"},
                         {"bucket": "gst_invoice", "signups": "34", "sellers_yes": "1"},
                         {"bucket": "reconcile", "signups": "5", "sellers_yes": "2"}])
check("waitlist gate: 30+ sign-ups OR 3+ sellers yes; example row ignored",
      [(b, ok) for b, ok, _ in wv] == [("gst_invoice", True), ("reconcile", False)], wv)

# offline end-to-end run
import trend_fetcher as tf
TMP = tempfile.mkdtemp(); tf.LOCAL_OUT_DIR = TMP; tf.TODAY = "2026-11-10"
tf.auto_stackoverflow = lambda days=7: [{"title": "Generate invoice PDF for each row", "view_count": 900, "is_answered": False},
                                        {"title": "GST invoice in Excel", "view_count": 300, "is_answered": True},
                                        {"title": "Invoice VBA totals", "view_count": 120, "is_answered": False}]
tf.auto_trends = lambda today: [("gst invoice generator free", 450)]
tf.auto_github = lambda buckets: {"gst_invoice": sp}
sent = []; tf.send_telegram = sent.append
tf.main_automation()
op = pd.read_csv(os.path.join(TMP, "OPPORTUNITIES.csv"))
check("OPPORTUNITIES written for the week (Monday date)", set(op["week"]) == {"2026-11-09"} and "gst_invoice" in set(op["bucket"]), op[["week", "bucket"]])
check("WAITLIST tab created once with an example row", os.path.exists(os.path.join(TMP, "WAITLIST.csv")))
check("admin message: problems, gap type, reusable code, licence rule",
      sent and "Automation opportunities" in sent[0] and "PACKAGE" in sent[0] and "♻️" in sent[0] and "do NOT reuse" in sent[0], sent[:1])
tf.main_automation()
check("re-running in the same week replaces that week (no duplicates)",
      len(pd.read_csv(os.path.join(TMP, "OPPORTUNITIES.csv"))) == len(op))
shutil.rmtree(TMP)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"); sys.exit(1 if FAILS else 0)
