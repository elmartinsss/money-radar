"""Paper trading: a pretend wallet that trades the radar's signals with live prices.

No real money, no exchange account, no keys. It answers one question:
"If I had followed these alerts with €100, would I have made money?"
"""
import json
import os
import brain
import rewards
import time
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
WALLET_FILE = os.path.join(HERE, "paper", "wallet.json")
REPORT_FILE = os.path.join(HERE, "PORTFOLIO.md")
TZ = ZoneInfo("Europe/Vilnius")


def _now():
    return time.time()


def load(cfg):
    try:
        with open(WALLET_FILE) as f:
            return json.load(f)
    except Exception:
        return {"start_eur": cfg["start_eur"], "cash": cfg["start_eur"], "positions": [],
                "history": [], "started": _now(), "btc_start": None,
                "last_report": "", "changed": True}


def save(w):
    os.makedirs(os.path.dirname(WALLET_FILE), exist_ok=True)
    w.pop("changed", None)
    with open(WALLET_FILE, "w") as f:
        json.dump(w, f, indent=1)


def _act(w, **e):
    e["t"] = int(_now())
    w.setdefault("activity", []).append(e)
    del w["activity"][:-120]
    w["changed"] = True


def _cost_pct(cfg, kind):
    """Exchange fee plus slippage, charged on both buy and sell."""
    return cfg["fee_pct"] + (cfg["memecoin_slippage_pct"] if kind == "dex" else 0)


def value(pos, price):
    return pos["eur"] * price / pos["entry"]


def fetch_prices(positions, fetch):
    """Current USD price for every open position, grouped by price source."""
    prices = {}
    cg = sorted({p["id"] for p in positions if p["kind"] == "cg"})
    if cg:
        try:
            d = fetch("https://api.coingecko.com/api/v3/simple/price?vs_currencies=usd&ids=" + ",".join(cg))
            prices.update({k: v["usd"] for k, v in d.items() if v.get("usd")})
        except Exception:
            pass
    by_chain = {}
    for p in positions:
        if p["kind"] == "dex":
            by_chain.setdefault(p["chain"], []).append(p["id"])
    for chain, addrs in by_chain.items():
        try:
            for pair in fetch(f"https://api.dexscreener.com/tokens/v1/{chain}/" + ",".join(addrs)) or []:
                a = pair.get("baseToken", {}).get("address")
                liq = (pair.get("liquidity") or {}).get("usd") or 0
                if a and pair.get("priceUsd") and liq >= prices.get(("liq", a), 0):
                    prices[a] = float(pair["priceUsd"])
                    prices[("liq", a)] = liq
        except Exception:
            pass
    for p in positions:
        if p["kind"] == "stock":
            try:
                d = fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{p['id']}?range=1d&interval=1d")
                prices[p["id"]] = d["chart"]["result"][0]["meta"]["regularMarketPrice"]
            except Exception:
                pass
    return prices


def btc_price(fetch):
    try:
        return fetch("https://api.coingecko.com/api/v3/simple/price?vs_currencies=usd&ids=bitcoin")["bitcoin"]["usd"]
    except Exception:
        return None


def _sell_half(w, cfg, pos, price, notes):
    """Take half the profit off the table, let the rest run with a trailing stop."""
    half = pos["eur"] / 2
    net = half * price / pos["entry"] * (1 - _cost_pct(cfg, pos["kind"]) / 100)
    pos["eur"] -= half
    pos["realized"] = pos.get("realized", 0) + net
    pos["halved"] = True
    w["cash"] += net
    _act(w, type="sell", symbol=pos["symbol"], signal=pos["signal"], eur=round(net, 2),
         pnl=round(net - pos["paid"] / 2, 2), pct=round((net / (pos["paid"] / 2) - 1) * 100, 1),
         reason="💰 Sold half at profit, rest rides with a trailing stop")
    notes.append({"title": f"📝 Paper: sold half of {pos['symbol']}",
                  "message": f"+{(price / pos['entry'] - 1) * 100:.0f}% · locked in {net:.2f}€, rest rides with a trailing stop",
                  "priority": 2, "tags": ["memo"]})


def _sell(w, cfg, pos, price, reason, notes):
    gross = value(pos, price)
    net = gross * (1 - _cost_pct(cfg, pos["kind"]) / 100)
    pnl = net + pos.get("realized", 0) - pos["paid"]
    net_total = net + pos.get("realized", 0)
    w["cash"] += net
    w["positions"].remove(pos)
    w["history"].append({**pos, "exit": price, "exit_time": _now(), "reason": reason,
                         "pnl_eur": round(pnl, 2), "pnl_pct": round(pnl / pos["paid"] * 100, 1)})
    _act(w, type="sell", symbol=pos["symbol"], signal=pos["signal"], eur=round(net_total, 2),
         pnl=round(pnl, 2), pct=round(pnl / pos["paid"] * 100, 1), reason=reason)
    icon = "✅" if pnl >= 0 else "❌"
    notes.append({"title": f"📝 Paper SELL {pos['symbol']} {icon} {pnl:+.2f}€",
                  "message": f"{reason} · {pnl / pos['paid'] * 100:+.1f}% after fees\n"
                             f"Wallet now {total(w, {}):.2f}€ (fake money)",
                  "priority": 2, "tags": ["memo"]})


def total(w, prices):
    return w["cash"] + sum(value(p, prices.get(p["id"], p.get("last", p["entry"]))) for p in w["positions"])


RISKY = {"memecoin", "fomo", "trending"}


def _trend_ok(w, t, fetch):
    """Research rule: only buy big coins and stocks while price is above their 50-day average."""
    today = datetime.fromtimestamp(_now(), TZ).strftime("%Y-%m-%d")
    cache = w.setdefault("trend", {})
    hit = cache.get(t["id"])
    if hit and hit.get("day") == today:
        return hit["up"]
    up = None
    try:
        if t["kind"] == "cg":
            d = fetch(f"https://api.coingecko.com/api/v3/coins/{t['id']}/market_chart?vs_currency=usd&days=70&interval=daily")
            closes = [p[1] for p in d.get("prices", [])]
        else:
            d = fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{t['id']}?range=6mo&interval=1d")
            closes = [c for c in d["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c]
        up = brain.trend_up(closes, 50)
    except Exception:
        up = None
    cache[t["id"]] = {"day": today, "up": up}
    return up


def _skip_once(w, symbol, signal, reason):
    today = datetime.fromtimestamp(_now(), TZ).strftime("%Y-%m-%d")
    if not any(a.get("type") == "skip" and a.get("symbol") == symbol and a.get("reason") == reason and
               datetime.fromtimestamp(a["t"], TZ).strftime("%Y-%m-%d") == today for a in w.get("activity", [])):
        _act(w, type="skip", symbol=symbol, signal=signal, reason=reason)


def _cooldown(w, bcfg):
    """Circuit breaker: after 3 losses in a row today, or a bad day, stop risky buys until tomorrow."""
    today = datetime.fromtimestamp(_now(), TZ).strftime("%Y-%m-%d")
    todays = [h for h in w["history"] if datetime.fromtimestamp(h["exit_time"], TZ).strftime("%Y-%m-%d") == today]
    streak = 0
    for h in reversed(todays):
        if h["pnl_eur"] < 0:
            streak += 1
        else:
            break
    day_pnl = sum(h["pnl_eur"] for h in todays)
    if streak >= bcfg["max_losses_in_a_row"]:
        return f"{streak} losses in a row today, cooling down until tomorrow"
    if day_pnl <= -total(w, {}) * bcfg["max_daily_loss_pct"] / 100:
        return f"down {day_pnl:.2f}€ today, cooling down until tomorrow"
    return None


def run(cfg, signals, fetch, mood=None, bcfg=None, rcfg=None, rejected=()):
    """Update the wallet. Returns a list of ntfy messages to send."""
    w = load(cfg)
    notes = []
    rcfg = rcfg or {}
    R = w.setdefault("rewards", rewards.blank())
    for k, v in rewards.blank().items():
        R.setdefault(k, v)
    hist_before = len(w["history"])
    if rcfg:
        before = len(R["pending_checks"])
        for c in list(rejected)[:25]:
            rewards.note_rejection(R, c, c["reason"], _now())
        if len(R["pending_checks"]) != before:
            w["changed"] = True
        if rewards.follow_up(R, lambda cs: fetch_prices([{"kind": "dex", "id": c["id"], "chain": c["chain"]} for c in cs], fetch),
                             notes, rcfg, _now()):
            w["changed"] = True
    if w["btc_start"] is None:
        w["btc_start"] = btc_price(fetch)
        w["changed"] = True

    # 1) Check exits on open positions
    prices = fetch_prices(w["positions"], fetch) if w["positions"] else {}
    for pos in list(w["positions"]):
        price = prices.get(pos["id"])
        if not price:
            continue
        pos["last"] = price
        pos["peak"] = max(pos.get("peak", pos["entry"]), price)
        w["changed"] = True  # keep the website's values fresh on every run
        chg = (price / pos["entry"] - 1) * 100
        held_h = (_now() - pos["time"]) / 3600
        peak_chg = (pos["peak"] / pos["entry"] - 1) * 100
        trail_hit = peak_chg >= cfg["trail_after_pct"] and price <= pos["peak"] * (1 - cfg["trail_pct"] / 100)
        if chg >= cfg["take_profit_pct"] and not pos.get("halved"):
            _sell_half(w, cfg, pos, price, notes)
        elif pos["kind"] == "dex" and pos.get("liq0") and prices.get(("liq", pos["id"])) \
                and prices[("liq", pos["id"])] < pos["liq0"] * 0.7:
            _sell(w, cfg, pos, price, f"🚨 Liquidity pulled ({(prices[('liq', pos['id'])] / pos['liq0'] - 1) * 100:.0f}%), emergency exit", notes)
        elif trail_hit:
            _sell(w, cfg, pos, price, f"📉 Trailing stop ({chg:+.0f}%, peak was +{peak_chg:.0f}%)", notes)
        elif chg <= -cfg["stop_loss_pct"]:
            _sell(w, cfg, pos, price, f"🛑 Stop loss ({chg:.0f}%)", notes)
        elif held_h >= cfg["max_hold_hours"]:
            _sell(w, cfg, pos, price, f"⏰ Time's up ({held_h / 24:.0f} days)", notes)

    # Reward the bot for every trade that just finished
    for h in w["history"][hist_before:]:
        rewards.learn_trade(R, h, notes, rcfg or {"min_situation_trades": 6, "shrink_k": 10})
        w["changed"] = True

    # 2) Open new positions from fresh signals
    signals = list(signals)
    mood = mood or {}
    if mood and (w.get("mood") or {}).get("value") != mood.get("value"):
        w["changed"] = True
    if mood:
        w["mood"] = mood
    greedy = mood.get("value", 50) >= cfg["greed_pause_at"]
    risky = RISKY
    allowed = set(cfg["buy_on"])
    bcfg = {**cfg, **(bcfg or {})}
    learn = brain.learning(w["history"], bcfg)
    if learn != w.get("learning"):
        w["learning"] = learn
        w["changed"] = True
    cool = _cooldown(w, bcfg)
    equity = total(w, {})
    # stocks: research says big up-days often partly reverse, so wait a day before buying
    pend = w.setdefault("pending", [])
    for pnd in list(pend):
        age_h = (_now() - pnd["seen"]) / 3600
        if age_h > 72:
            pend.remove(pnd)
            w["changed"] = True
        elif age_h >= 20:
            pend.remove(pnd)
            w["changed"] = True
            pr = fetch_prices([pnd], fetch).get(pnd["id"])
            if pr:
                signals.append({"trade": {**pnd, "price": pr, "waited": True}})
    # Extreme fear: buy the dip on Bitcoin (once a week at most)
    if mood.get("value", 50) <= cfg["fear_dip_at"] and "fear_dip" in allowed:
        recent = [h for h in w["history"] if h["signal"] == "fear_dip" and _now() - h["time"] < 7 * 86400]
        if not recent:
            b = btc_price(fetch)
            if b:
                signals.insert(0, {"trade": {"kind": "cg", "id": "bitcoin", "symbol": "BTC",
                                             "price": b, "signal": "fear_dip"}})
    for s in signals:
        t = s.get("trade")
        if not t or t["signal"] not in allowed:
            continue
        if greedy and t["signal"] in risky:
            # crowd is FOMOing, the bot sits this one out (logged once per coin per day)
            today = datetime.fromtimestamp(_now(), TZ).strftime("%Y-%m-%d")
            if not any(a.get("type") == "skip" and a.get("symbol") == t["symbol"] and
                       datetime.fromtimestamp(a["t"], TZ).strftime("%Y-%m-%d") == today
                       for a in w.get("activity", [])):
                _act(w, type="skip", symbol=t["symbol"], signal=t["signal"],
                     reason=f"market mood {mood.get('value')}/100, crowd too greedy")
            continue
        if t["signal"] == "trending" and t.get("rank", 99) > cfg["trending_top_n"]:
            continue
        if any(p["id"] == t["id"] for p in w["positions"]):
            continue
        if len(w["positions"]) >= cfg["max_open_positions"]:
            break
        if t["signal"] in risky:
            if cool:
                _skip_once(w, t["symbol"], t["signal"], cool)
                continue
            risky_now = sum(p["paid"] for p in w["positions"] if p["signal"] in risky)
            if risky_now >= equity * bcfg["max_risky_share_pct"] / 100:
                _skip_once(w, t["symbol"], t["signal"], f"already {risky_now:.0f}€ in risky coins (limit {bcfg['max_risky_share_pct']}%)")
                continue
        if t["signal"] in ("momentum", "fear_dip"):
            if t["kind"] == "stock" and not t.get("waited"):
                if not any(p["id"] == t["id"] for p in pend):
                    pend.append({k: t[k] for k in ("kind", "id", "symbol", "signal")} | {"seen": _now()})
                    _act(w, type="skip", symbol=t["symbol"], signal=t["signal"],
                         reason="big up-day: waiting a day, stocks often pull back after a jump")
                continue
            up = _trend_ok(w, t, fetch)
            if up is False:
                _skip_once(w, t["symbol"], t["signal"], "below its 50-day average (downtrend), not buying")
                continue
        feats = rewards.features(t, mood, _now())
        exp = worst = None
        if rcfg:
            exp, worst, best, _n = rewards.expect(R, feats, rcfg)
            if exp is not None and exp < rcfg["skip_below_pct"] and t["signal"] in risky:
                _skip_once(w, t["symbol"], t["signal"],
                           f"I remember losing in situations like this ({worst[1] if worst else 'similar trades'}), expecting {exp:+.1f}%")
                continue
        mult = learn.get(t["signal"], {}).get("mult", 1.0) * (1.5 if t["signal"] == "fear_dip" else 1.0)
        mult *= rewards.confidence_mult(R)
        if exp is not None and exp > 3:
            mult *= 1.1
        spend = brain.position_size(equity, w["cash"], bcfg, mult)
        if not spend:
            break
        w["cash"] -= spend
        # Buy costs are taken off the position up front
        eur_in = spend * (1 - _cost_pct(cfg, t["kind"]) / 100)
        w["positions"].append({"kind": t["kind"], "id": t["id"], "chain": t.get("chain"),
                               "symbol": t["symbol"], "signal": t["signal"], "entry": t["price"],
                               "eur": eur_in, "paid": spend, "time": _now(), "last": t["price"],
                               "liq0": t.get("liq"), "feats": feats,
                               "expected": None if exp is None else round(exp, 1)})
        _act(w, type="buy", symbol=t["symbol"], signal=t["signal"], eur=spend, price=t["price"])
        notes.append({"title": f"📝 Paper BUY {t['symbol']} for {spend:.2f}€",
                      "message": f"Signal: {t['signal']} · price ${t['price']:,.6g}\n"
                                 f"Sells half at +{cfg['take_profit_pct']}%, trails the rest, stop -{cfg['stop_loss_pct']}%, "
                                 f"max {cfg['max_hold_hours'] // 24} days. Fake money.",
                      "priority": 2, "tags": ["memo"]})

    # 3) Hourly snapshot for the website chart (wallet vs just holding BTC)
    snaps = w.setdefault("equity", [])
    if not snaps or _now() - snaps[-1]["t"] >= 55 * 60:
        b = btc_price(fetch)
        snaps.append({"t": int(_now()), "v": round(total(w, {}), 2), "btc": b})
        del snaps[:-2000]
        w["changed"] = True

    # 4) Daily report
    local = datetime.now(TZ)
    day = local.strftime("%Y-%m-%d")
    if local.hour >= cfg["report_hour"] and w.get("last_report") != day:
        all_prices = {p["id"]: p.get("last", p["entry"]) for p in w["positions"]}
        notes.append(report_note(w, all_prices, btc_price(fetch)))
        w["last_report"] = day
        w["changed"] = True

    if w.get("changed"):
        w["updated"] = int(_now())
        save(w)
        write_markdown(w, {p["id"]: p.get("last", p["entry"]) for p in w["positions"]})
    return notes


def stats(w, prices, btc_now):
    tot = total(w, prices)
    pnl_pct = (tot / w["start_eur"] - 1) * 100
    closed = w["history"]
    wins = [h for h in closed if h["pnl_eur"] >= 0]
    btc_pct = ((btc_now / w["btc_start"] - 1) * 100) if btc_now and w.get("btc_start") else None
    return tot, pnl_pct, closed, wins, btc_pct


def report_note(w, prices, btc_now):
    tot, pnl_pct, closed, wins, btc_pct = stats(w, prices, btc_now)
    days = max(1, round((_now() - w["started"]) / 86400))
    lines = [f"Wallet: {tot:.2f}€ ({pnl_pct:+.1f}%) after {days} day(s)",
             f"Open: {len(w['positions'])} · Closed: {len(closed)} · Wins: {len(wins)}/{len(closed)}"]
    if btc_pct is not None:
        beat = "✅ beating" if pnl_pct > btc_pct else "❌ losing to"
        lines.append(f"Just holding BTC: {btc_pct:+.1f}% → bot is {beat} it")
    if closed:
        best = max(closed, key=lambda h: h["pnl_pct"])
        worst = min(closed, key=lambda h: h["pnl_pct"])
        lines.append(f"Best: {best['symbol']} {best['pnl_pct']:+.0f}% · Worst: {worst['symbol']} {worst['pnl_pct']:+.0f}%")
    if w.get("mood"):
        lines.append(f"Market mood: {w['mood']['value']}/100 {w['mood']['label']}")
    return {"title": "📊 Paper wallet daily report", "message": "\n".join(lines),
            "priority": 3, "tags": ["bar_chart"]}


def write_markdown(w, prices):
    tot, pnl_pct, closed, wins, btc_pct = stats(w, prices, None)
    started = datetime.fromtimestamp(w["started"], TZ).strftime("%Y-%m-%d")
    md = [f"# 📝 Paper wallet (fake money)\n",
          f"Started {started} with **{w['start_eur']:.0f}€** · now **{tot:.2f}€ ({pnl_pct:+.1f}%)** · "
          f"cash {w['cash']:.2f}€\n",
          f"Closed trades: {len(closed)} · wins: {len(wins)}\n",
          f"_Updated {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} Vilnius time._\n",
          "## Open positions\n", "| Coin | Signal | Bought | Value now | Change |", "|---|---|---|---|---|"]
    for p in w["positions"]:
        v = value(p, p.get("last", p["entry"]))
        md.append(f"| {p['symbol']} | {p['signal']} | {p['paid']:.2f}€ | {v:.2f}€ | {(v / p['paid'] - 1) * 100:+.1f}% |")
    md += ["\n## Last 30 closed trades\n", "| Coin | Signal | Result | Why |", "|---|---|---|---|"]
    for h in reversed(closed[-30:]):
        md.append(f"| {h['symbol']} | {h['signal']} | {h['pnl_eur']:+.2f}€ ({h['pnl_pct']:+.1f}%) | {h['reason']} |")
    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(md) + "\n")
