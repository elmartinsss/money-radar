#!/usr/bin/env python3
"""Money Radar: scans news, Reddit, CoinGecko, DexScreener and a price
watchlist, then pushes the interesting bits to your phone through ntfy.

Standard library only, so GitHub Actions needs no installs.
Alerts mean "worth a look", never "buy this".
"""
import json
import os
import re
import sys
import time
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state", "seen.json")
CONFIG_FILE = os.path.join(HERE, "config.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) MoneyRadar/1.0"
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
KEEP_SECONDS = 10 * 24 * 3600
NOW = time.time()


# ---------------------------------------------------------------- helpers
def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fetch(url, as_json=True, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return json.loads(data) if as_json else data


def safe(fn, *a):
    """Run one source; a broken source must never kill the whole scan."""
    try:
        return fn(*a) or []
    except Exception as e:  # noqa: BLE001
        log(f"[{fn.__name__}] failed: {e}")
        return []


def key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def kw_hits(text, keywords):
    t = text.lower()
    return [k for k in keywords if re.search(r"\b" + re.escape(k.lower()) + r"\b", t)]


HYPE_PATTERNS = [
    r"\bm+o{2,}n+\b", r"to the moon", r"\b\d{2,5}\s*x\b", r"\bx\s*\d{2,5}\b", r"\bgem\b",
    r"\blambo", r"next (pepe|doge|shib|bonk)", r"don'?t miss", r"\bpresale\b", r"\bpump(ing)?\b",
    r"\bape (in|now)\b", r"\bmoonshot", r"guaranteed", r"\bdegen\b", r"\bsend it\b",
    r"\blast chance\b", r"\bbuy now\b", r"\bget in (early|now)\b", r"millionaire",
]


def is_hype(text, extra=()):
    """True for shill-style titles: moon talk, 100x, rocket spam, ALL CAPS."""
    t = text.lower()
    if any(re.search(p, t) for p in HYPE_PATTERNS) or any(w.lower() in t for w in extra):
        return True
    if text.count("🚀") + text.count("💎") + text.count("🔥") >= 2:
        return True
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.6


def money(x):
    x = float(x or 0)
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(x) >= div:
            return f"${x / div:.1f}{unit}"
    return f"${x:.0f}"


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def alert(source, uid, title, body, url=None, priority=3, tags=None, extra=""):
    """extra lets an item re-alert later (e.g. once per day / per direction)."""
    return {"key": key(source, uid, extra), "title": title[:120], "message": body[:900],
            "click": url, "priority": priority, "tags": tags or []}


# ---------------------------------------------------------------- sources
def parse_feed(raw):
    """Yield (id, title, link, summary, epoch) from RSS or Atom."""
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//a:entry", ns)
    for it in items:
        def tx(*names):
            for n in names:
                el = it.find(n, ns)
                if el is not None:
                    return (el.text or el.get("href") or "").strip()
            return ""
        title = tx("title", "a:title")
        link = tx("link", "a:link")
        summary = re.sub(r"<[^>]+>", " ", tx("description", "a:summary", "a:content"))
        date = tx("pubDate", "a:updated", "a:published")
        try:
            ts = parsedate_to_datetime(date).timestamp()
        except Exception:
            try:
                ts = datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = NOW
        yield (tx("guid", "a:id") or link or title), title, link, summary, ts


def src_news(c):
    out = []
    for feed in c["feeds"]:
        try:
            raw = fetch(feed, as_json=False)
        except Exception as e:
            log(f"[news] {feed[:60]} failed: {e}")
            continue
        for uid, title, link, summary, ts in parse_feed(raw):
            if NOW - ts > c["max_age_hours"] * 3600:
                continue
            if is_hype(title, c.get("extra_blocked_words", [])):
                log(f"[news] hype-filtered: {title[:60]}")
                continue
            hits = kw_hits(f"{title} {summary}", c["keywords"])
            if len(hits) >= c["min_keyword_hits"]:
                host = urllib.parse.urlparse(feed).netloc.replace("www.", "")
                out.append(alert("news", uid, f"📰 {title}",
                                 f"Risk: 🟢 news from a known outlet. Read before acting.\n"
                                 f"Matched: {', '.join(hits)} · via {host}",
                                 link, 3, ["newspaper"]))
    return out


def src_reddit(c):
    out = []
    for sub in c["subreddits"]:
        try:
            data = fetch(f"https://www.reddit.com/r/{sub}/hot.json?limit=30&raw_json=1")
        except Exception as e:
            log(f"[reddit] r/{sub} failed: {e}")
            continue
        for ch in data.get("data", {}).get("children", []):
            p = ch.get("data", {})
            if p.get("stickied"):
                continue
            age_h = (NOW - p.get("created_utc", NOW)) / 3600
            ups = p.get("ups", 0)
            title = p.get("title", "")
            if is_hype(title, c.get("extra_blocked_words", [])):
                log(f"[reddit] hype-filtered: {title[:60]}")
                continue
            if (p.get("upvote_ratio") or 1) < c.get("min_upvote_ratio", 0.8):
                continue  # lots of downvotes = people are calling it a shill
            hits = kw_hits(f"{title} {p.get('link_flair_text') or ''}", c["keywords"])
            if age_h <= c["max_age_hours"] and hits and ups >= c["min_upvotes"]:
                out.append(alert(
                    "reddit", p.get("id"), f"👽 r/{sub}: {p.get('title', '')}",
                    f"Risk: 🟡 crowd opinion, not facts. Check the comments for pushback.\n"
                    f"⬆ {ups} upvotes · 💬 {p.get('num_comments', 0)} · {age_h:.0f}h old"
                    + (f"\nMatched: {', '.join(hits)}" if hits else ""),
                    "https://reddit.com" + p.get("permalink", ""), 3, ["speech_balloon"]))
    return out


def src_coingecko_trending(c):
    data = fetch("https://api.coingecko.com/api/v3/search/trending")
    out = []
    for i, entry in enumerate(data.get("coins", [])[: c["top_n"]]):
        it = entry.get("item", {})
        d = it.get("data", {}) or {}
        chg = (d.get("price_change_percentage_24h") or {}).get("usd")
        chg_s = f"{chg:+.1f}% 24h" if isinstance(chg, (int, float)) else ""
        out.append(alert(
            "cg", it.get("id"),
            f"🔥 Trending #{i + 1}: {it.get('name')} ({it.get('symbol')})",
            f"Rank #{it.get('market_cap_rank') or '?'} · {chg_s}\n"
            f"Risk: 🟡 trending = lots of people searching it. Usually late to the move.",
            f"https://www.coingecko.com/en/coins/{it.get('id')}", 2, ["fire"], today()))
    return out


def rugcheck_ok(mint, strict_words):
    try:
        r = fetch(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary")
    except Exception as e:
        log(f"[rugcheck] {mint[:8]} failed: {e}")
        return False, "rugcheck unavailable"
    dangers = [x.get("name") for x in r.get("risks", []) if x.get("level") == "danger"]
    if dangers:
        return False, ", ".join(dangers)
    warns = [x.get("name") for x in r.get("risks", []) if x.get("level") == "warn"]
    bad = [w for w in warns if any(k in (w or "").lower() for k in strict_words)]
    if bad:
        return False, ", ".join(bad)
    return True, ("warnings: " + ", ".join(warns)) if warns else "no major flags"


def dex_pairs(chain, addresses):
    """Best (most liquid) pair per token address."""
    best = {}
    for i in range(0, len(addresses), 30):
        chunk = ",".join(addresses[i:i + 30])
        for p in fetch(f"https://api.dexscreener.com/tokens/v1/{chain}/{chunk}") or []:
            addr = p.get("baseToken", {}).get("address")
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if addr and liq >= ((best.get(addr, {}).get("liquidity") or {}).get("usd") or 0):
                best[addr] = p
    return best


def src_dexscreener(c):
    boosts = fetch("https://api.dexscreener.com/token-boosts/top/v1") or []
    out = []
    for chain in c["chains"]:
        addrs = list({b["tokenAddress"] for b in boosts if b.get("chainId") == chain})
        if not addrs:
            continue
        for addr, p in dex_pairs(chain, addrs).items():
            liq = (p.get("liquidity") or {}).get("usd") or 0
            vol = (p.get("volume") or {}).get("h24") or 0
            age_h = (NOW * 1000 - (p.get("pairCreatedAt") or NOW * 1000)) / 3.6e6
            pc = p.get("priceChange") or {}
            sym = p["baseToken"].get("symbol", "?")
            name = p["baseToken"].get("name", "")
            why = None
            if liq < c["min_liquidity_usd"]:
                why = "low liquidity"
            elif vol < c["min_volume_24h_usd"]:
                why = "low volume"
            elif age_h < c["min_age_hours"]:
                why = f"too new ({age_h:.0f}h)"
            elif (pc.get("h24") or 0) > c["max_pump_24h_pct"]:
                why = f"already pumped {pc.get('h24')}% (you'd be buying from early holders)"
            elif (pc.get("h1") or 0) < -c["max_dump_1h_pct"]:
                why = f"dumping {pc.get('h1')}% this hour"
            elif vol > liq * c["max_volume_to_liquidity"]:
                why = "volume way bigger than liquidity (often bots/wash trading)"
            elif is_hype(f"{sym} {name}"):
                why = "hype name"
            if why:
                log(f"[dex] skipped {sym}: {why}")
                continue
            note = "rugcheck skipped"
            if c.get("use_rugcheck") and chain == "solana":
                ok, note = rugcheck_ok(addr, c["block_rugcheck_warnings_about"])
                if not ok:
                    log(f"[dex] dropped {p['baseToken'].get('symbol')}: {note}")
                    continue
            out.append(alert(
                "dex", addr,
                f"🐸 Memecoin with real volume: ${sym}",
                f"Liq {money(liq)} · Vol24h {money(vol)} · MC {money(p.get('marketCap') or p.get('fdv'))}\n"
                f"1h {pc.get('h1', 0):+}% · 24h {pc.get('h24', 0):+}% · age {age_h:.0f}h\n"
                f"Rugcheck: {note}\n"
                f"Risk: 🔴 gambling. Passed the safety filters, but it's a paid promo and most memecoins still go to 0.",
                p.get("url"), 3, ["frog", "warning"], today()))
    return out


def src_watchlist(c):
    out, th = [], c["move_threshold_pct"]
    ids = c.get("crypto_coingecko_ids") or []
    if ids:
        try:
            d = fetch("https://api.coingecko.com/api/v3/simple/price?vs_currencies=usd"
                      "&include_24hr_change=true&ids=" + ",".join(ids))
            for cid, v in d.items():
                chg = v.get("usd_24h_change") or 0
                if abs(chg) >= th:
                    up = chg > 0
                    out.append(alert(
                        "wl", cid,
                        f"{'🚀' if up else '📉'} {cid.title()} {chg:+.1f}% in 24h",
                        f"Now ${v.get('usd'):,}\nRisk: 🟢 big coin, real move. Find out WHY before doing anything.", f"https://www.coingecko.com/en/coins/{cid}",
                        4, ["chart_with_upwards_trend" if up else "chart_with_downwards_trend"],
                        f"{today()}{up}"))
        except Exception as e:
            log(f"[watchlist] crypto failed: {e}")
    for sym in c.get("stocks") or []:
        try:
            d = fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d")
            meta = d["chart"]["result"][0]["meta"]
            price, prev = meta["regularMarketPrice"], meta["chartPreviousClose"]
            closes = [x for x in d["chart"]["result"][0]["indicators"]["quote"][0]["close"] if x]
            if len(closes) >= 2:
                prev = closes[-2]
            chg = (price - prev) / prev * 100
            if abs(chg) >= th:
                up = chg > 0
                out.append(alert(
                    "wl", sym, f"{'🚀' if up else '📉'} {sym} {chg:+.1f}% today",
                    f"Now ${price:,.2f} (prev close ${prev:,.2f})\nRisk: 🟢 big stock, real move. Check the news for the reason.",
                    f"https://finance.yahoo.com/quote/{sym}", 4,
                    ["chart_with_upwards_trend" if up else "chart_with_downwards_trend"],
                    f"{today()}{up}"))
        except Exception as e:
            log(f"[watchlist] {sym} failed: {e}")
    return out


def src_my_tokens(c):
    out = []
    by_chain = {}
    for t in c["tokens"]:
        if "PASTE" not in t["address"]:
            by_chain.setdefault(t["chain"], []).append(t)
    for chain, toks in by_chain.items():
        pairs = dex_pairs(chain, [t["address"] for t in toks])
        for t in toks:
            p = pairs.get(t["address"])
            if not p:
                continue
            h1 = (p.get("priceChange") or {}).get("h1") or 0
            v1 = (p.get("volume") or {}).get("h1") or 0
            if abs(h1) >= c["price_move_pct_1h"] or v1 >= c["volume_1h_usd"]:
                hour = datetime.now(timezone.utc).strftime("%Y-%m-%d %H")
                out.append(alert(
                    "mine", t["address"], f"🐀 {t['name']} is moving! {h1:+}% in 1h",
                    f"Vol 1h {money(v1)} · Liq {money((p.get('liquidity') or {}).get('usd'))}"
                    f" · MC {money(p.get('marketCap') or p.get('fdv'))}",
                    p.get("url"), 5, ["rat"], hour))
    return out


# ---------------------------------------------------------------- ntfy + state
def send(a):
    if DRY_RUN or not NTFY_TOPIC:
        log(f"[dry] P{a['priority']} {a['title']}\n      {a['message']}\n      {a.get('click')}")
        return True
    payload = {k: v for k, v in {
        "topic": NTFY_TOPIC, "title": a["title"], "message": a["message"],
        "priority": a["priority"], "tags": a["tags"], "click": a.get("click"),
    }.items() if v}
    req = urllib.request.Request(NTFY_SERVER, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        log(f"[ntfy] sent: {a['title']}")
        return True
    except Exception as e:
        log(f"[ntfy] send failed: {e}")
        print(f"::error::ntfy send failed (topic length {len(NTFY_TOPIC)}): {e}", flush=True)
        return False


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def save_state(seen):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    seen = {k: t for k, t in seen.items() if NOW - t < KEEP_SECONDS}
    with open(STATE_FILE, "w") as f:
        json.dump(seen, f)


SOURCES = [("my_tokens", src_my_tokens), ("watchlist", src_watchlist),
           ("dexscreener", src_dexscreener), ("news", src_news),
           ("reddit", src_reddit), ("coingecko_trending", src_coingecko_trending)]


def main():
    if not DRY_RUN and not NTFY_TOPIC:
        print("::error::NTFY_TOPIC secret is missing or empty", flush=True)
        log("ERROR: NTFY_TOPIC secret is missing or empty. Add it in repo Settings -> "
            "Secrets and variables -> Actions (name must be exactly NTFY_TOPIC).")
        sys.exit(1)
    manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    state = load_state()
    first_run = state is None
    seen = state or {}

    found = []
    for name, fn in SOURCES:
        sc = cfg.get(name, {})
        if sc.get("enabled"):
            got = safe(fn, sc)
            log(f"[{name}] {len(got)} candidates")
            found += got

    fresh, dupes = [], set()
    for a in found:
        if a["key"] not in seen and a["key"] not in dupes:
            dupes.add(a["key"])
            fresh.append(a)
    fresh.sort(key=lambda a: -a["priority"])

    if first_run or manual:
        # Pressing "Run workflow" always sends a status ping, so you can test it.
        # First run also marks the backlog as seen so your phone isn't flooded.
        if first_run:
            for a in fresh:
                seen[a["key"]] = NOW
        ok = send({"title": "✅ Money Radar is live", "priority": 3, "tags": ["satellite"],
                   "message": f"Scan worked: {len(found)} items checked, {len(fresh)} new. "
                              f"Real alerts arrive automatically from now on."})
        if not ok:
            sys.exit(1)  # show a red ❌ in GitHub instead of a fake green ✓
        if first_run:
            save_state(seen)
            return

    cap = cfg.get("max_alerts_per_run", 6)
    for a in fresh[:cap]:
        if send(a):
            seen[a["key"]] = NOW
    rest = fresh[cap:]
    if rest:
        send({"title": f"➕ {len(rest)} more signals", "priority": 2, "tags": ["inbox_tray"],
              "message": "\n".join("• " + r["title"] for r in rest[:15])})
        for a in rest:
            seen[a["key"]] = NOW
    log(f"sent {min(len(fresh), cap)} alerts, {len(rest)} bundled")
    save_state(seen)


if __name__ == "__main__":
    main()
