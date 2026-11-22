"""
Tracking IDs per channel (and optionally per arm) — v1.60.

Why: every post used to carry the one store ID, so Amazon's reports could say WHAT sold but never WHERE the click
came from. Associates Central lets you create extra tracking IDs under the same account; each link carries one, and
the reports split clicks, orders and earnings by tracking ID. One ID per channel turns "is WhatsApp worth it?" into a
number.

Set the repo secret/variable AFF_TAGS, e.g.

    telegram=onezeronetwor-21, whatsapp=ozwa-21, x=ozx-21, bot=ozbot-21, telegram.durable=oztgdur-21

Create each ID in Associates Central first (Account → Manage Your Tracking IDs). Anything missing or malformed falls
back to AFFILIATE_TAG, so nothing breaks while you set them up.
"""
import os
import re

TAG_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,60}-\d{2}$")      # Associates IDs end in a 2-digit marketplace code
CHANNELS = ("telegram", "whatsapp", "x", "bot", "site")


def parse(spec):
    """'telegram=a-21, x=b-21' (commas, semicolons or new lines) → {'telegram': 'a-21', 'x': 'b-21'}."""
    out = {}
    for part in re.split(r"[,;\n]", spec or ""):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip().lower(), v.strip()
        if k and v:
            out[k] = v
    return out


class Tags:
    def __init__(self, default, spec=None):
        self.default = (default or "").strip()
        self.map = parse(os.environ.get("AFF_TAGS", "") if spec is None else spec)
        self.problems = [f"{k}={v}" for k, v in self.map.items() if not TAG_RX.match(v)]

    def tag(self, channel, arm=""):
        """Most specific valid ID: '<channel>.<arm>', then '<channel>', then the default store ID."""
        ch, arm = (channel or "").strip().lower(), (arm or "").strip().lower()
        for key in ([f"{ch}.{arm}"] if arm else []) + [ch]:
            v = self.map.get(key)
            if v and TAG_RX.match(v):
                return v
        return self.default

    def for_pick(self, channel):
        """A function pick-row → tag, for picks.post_messages / wa_bundle (they accept a tag or a function)."""
        def f(p):
            arm = ""
            try:
                arm = str(p.get("arm", "") or "")
            except AttributeError:
                pass
            return self.tag(channel, "" if arm in ("nan", "None") else arm)
        return f

    def channel_of(self, tag):
        """Reverse lookup for the Associates report: 'ozwa-21' → 'whatsapp'. The default store ID → 'telegram'
        when Telegram has no ID of its own (that is where it was used until v1.60), else 'default'."""
        t = (tag or "").strip().lower()
        for k, v in self.map.items():
            if v.lower() == t:
                return k
        if t and t == self.default.lower():
            return "telegram" if "telegram" not in self.map else "default"
        return "other"

    def summary(self):
        used = {ch: self.tag(ch) for ch in CHANNELS}
        own = [ch for ch in CHANNELS if self.map.get(ch) and TAG_RX.match(self.map[ch])]
        return used, own
