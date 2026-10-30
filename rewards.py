"""Reward-based learning for the paper bot (a small, honest form of reinforcement learning).

After every finished trade the bot gets a reward: its net profit in %. It remembers the
situation it bought in (signal, market mood, coin age, liquidity, momentum, time of day)
and keeps a running score per situation. Before the next buy it asks itself:
"how did trades like this go before?" and skips situations that keep losing.

It also checks coins it rejected a day later: if they crashed, the rule that blocked them
earns a "good save"; if they flew, it gets a "missed".

XP, levels, medals and confidence are the bot's report card. Confidence nudges bet size a
little; everything else is for you to watch it grow.
"""
import math
import time

LEVELS = [(0, "Lab rat"), (50, "Paper trader"), (150, "Chart reader"), (300, "Rug dodger"),
          (500, "Risk manager"), (800, "Strategist"), (1200, "Market veteran"), (1800, "Legend of the lab")]


def _bucket(x, edges, names):
    for e, n in zip(edges, names):
        if x < e:
            return n
    return names[-1]


def features(trade, mood, now):
    """Describe the situation a trade was opened in, as simple labels."""
    ctx = trade.get("ctx") or {}
    f = {"signal": trade.get("signal", "?")}
    m = (mood or {}).get("value")
    if m is not None:
        f["mood"] = _bucket(m, [25, 45, 56, 75], ["extreme fear", "fear", "neutral", "greed", "extreme greed"])
    hour = time.gmtime(now).tm_hour
    f["time"] = _bucket(hour, [6, 12, 18], ["night (UTC 0-6)", "morning (UTC 6-12)", "afternoon (UTC 12-18)", "evening (UTC 18-24)"])
    if ctx.get("liq") is not None:
        f["liquidity"] = _bucket(ctx["liq"], [100e3, 300e3, 1e6], ["under $100k", "$100k-300k", "$300k-1M", "over $1M"])
    if ctx.get("age_h") is not None:
        f["age"] = _bucket(ctx["age_h"], [24, 72, 24 * 7, 24 * 30], ["under 1 day", "1-3 days", "3-7 days", "1-4 weeks", "over a month"])
    if ctx.get("h1") is not None:
        f["momentum"] = _bucket(ctx["h1"], [3, 10, 30], ["1h up 0-3%", "1h up 3-10%", "1h up 10-30%", "1h up 30%+"])
    if ctx.get("ratio") is not None:
        f["buyers"] = _bucket(ctx["ratio"], [1.2, 2, 3], ["balanced", "more buyers", "strong buying", "very strong buying"])
    return f


def blank():
    return {"xp": 0, "level": LEVELS[0][1], "medals": [], "confidence": 0.0, "streak": 0, "best_streak": 0,
            "situations": {}, "rules": {}, "journal": [], "pending_checks": [], "total_reward": 0.0, "trades": 0}


def _upd(stats, key, reward):
    s = stats.setdefault(key, {"n": 0, "sum": 0.0, "sq": 0.0})
    s["n"] += 1
    s["sum"] += reward
    s["sq"] += reward * reward


def shrunk(s, prior_mean, k):
    return (s["sum"] + k * prior_mean) / (s["n"] + k)


def expect(R, feats, cfg):
    """What the bot expects from a new trade, based on similar past ones.

    Returns (expected_reward_pct, worst_label, best_label, evidence_n)."""
    sits = R["situations"]
    n_all = R["trades"]
    if n_all < cfg["min_trades"]:
        return None, None, None, n_all
    g = R["total_reward"] / n_all
    parts = []
    for k, v in feats.items():
        s = sits.get(f"{k}={v}")
        if s and s["n"] >= cfg["min_situation_trades"]:
            parts.append((shrunk(s, g, cfg["shrink_k"]) - g, f"{k}: {v}", s["n"]))
    if not parts:
        return g, None, None, n_all
    # add up how each part of the situation differs from normal (clipped, so overlaps can't run away)
    exp = max(-50.0, min(50.0, g + sum(p[0] for p in parts)))
    worst = min(parts, key=lambda p: p[0])
    best = max(parts, key=lambda p: p[0])
    return exp, worst, best, n_all


def confidence_mult(R):
    """Confidence after recent results. Shaken bots bet a bit smaller, confident ones a bit bigger."""
    c = R.get("confidence", 0.0)
    return max(0.75, min(1.15, 1 + c / 40))


def mood_word(R):
    c = R.get("confidence", 0.0)
    return "confident 😎" if c > 4 else "shaken 😟" if c < -4 else "focused 🧐"


def _level_for(xp):
    name = LEVELS[0][1]
    for need, n in LEVELS:
        if xp >= need:
            name = n
    return name


def _medal(R, notes, key, title, why):
    if key in [m["key"] for m in R["medals"]]:
        return
    R["medals"].append({"key": key, "title": title, "why": why, "t": int(time.time())})
    notes.append({"title": f"🏅 The bot earned a medal: {title}", "message": why, "priority": 3, "tags": ["medal"]})


def _xp(R, amount, notes):
    before = R["level"]
    R["xp"] = max(0, round(R["xp"] + amount, 1))
    R["level"] = _level_for(R["xp"])
    if R["level"] != before and R["xp"] > 0 and amount > 0:
        notes.append({"title": f"⭐ Level up: {R['level']}", "message": f"The bot reached {R['xp']:.0f} XP from good trades and smart saves.",
                      "priority": 3, "tags": ["star"]})


def learn_trade(R, trade, notes, cfg):
    """Reward the bot for a finished trade and write a journal entry."""
    reward = trade["pnl_pct"]
    feats = trade.get("feats") or {"signal": trade.get("signal", "?")}
    g_before = R["total_reward"] / R["trades"] if R["trades"] else 0
    for k, v in feats.items():
        _upd(R["situations"], f"{k}={v}", reward)
    R["trades"] += 1
    R["total_reward"] += reward
    R["confidence"] = round(R.get("confidence", 0.0) * 0.8 + reward * 0.2, 2)  # recent trades matter most

    win = reward >= 0
    R["streak"] = R["streak"] + 1 if win else 0
    R["best_streak"] = max(R["best_streak"], R["streak"])
    # XP: wins pay by size, losses still teach a little, and following the rules counts
    _xp(R, (10 + min(reward, 50)) if win else 2, notes)

    # Journal: what did this trade teach?
    g = R["total_reward"] / R["trades"]
    lesson = ""
    worst = None
    for k, v in feats.items():
        s = R["situations"].get(f"{k}={v}")
        if s and s["n"] >= cfg["min_situation_trades"]:
            d = shrunk(s, g, cfg["shrink_k"]) - g
            if worst is None or d < worst[0]:
                worst = (d, k, v, s)
    if worst and worst[0] < -2:
        d, k, v, s = worst
        lesson = (f"Trades with {k} \"{v}\" average {s['sum'] / s['n']:+.1f}% over {s['n']} tries. "
                  f"I'll be more careful in that situation.")
    elif win and reward >= 20:
        lesson = "That one worked. Letting the winner run with a trailing stop paid off."
    elif not win and "Liquidity pulled" in trade.get("reason", ""):
        lesson = "Liquidity vanished. My emergency exit saved part of it. Rug checks can't catch everything."
    elif not win:
        lesson = "A loss inside the rules. Small losses are the price of finding winners."
    else:
        lesson = "Small win. After fees, every win counts."
    R["journal"].append({"t": int(time.time()), "symbol": trade.get("symbol"), "signal": trade.get("signal"),
                         "reward": round(reward, 1), "reason": trade.get("reason", ""), "lesson": lesson,
                         "situation": feats})
    del R["journal"][:-60]

    # Medals
    if win:
        _medal(R, notes, "first_win", "First win", f"Its first profitable trade: {trade.get('symbol')} {reward:+.1f}%.")
    if win and reward >= 30:
        _medal(R, notes, "big_win", "Let it run", f"{trade.get('symbol')} closed at {reward:+.1f}% after costs.")
    if R["streak"] >= 5:
        _medal(R, notes, "streak5", "Hot streak", "5 winning trades in a row.")
    if not win and reward > -10 and "Liquidity pulled" in trade.get("reason", ""):
        _medal(R, notes, "escape", "Great escape", f"Got out of {trade.get('symbol')} when its liquidity was pulled.")
    if R["trades"] >= 50:
        _medal(R, notes, "fifty", "Battle-tested", "50 finished trades of experience.")
    return reward


def note_rejection(R, coin, reason, now):
    """Remember a rejected memecoin so we can check tomorrow whether rejecting it was smart."""
    rule = rule_name(reason)
    if not rule or any(c["id"] == coin["id"] for c in R["pending_checks"]):
        return
    R["pending_checks"].append({"id": coin["id"], "chain": coin.get("chain", "solana"), "symbol": coin["symbol"],
                                "price": coin["price"], "t": int(now), "rule": rule, "reason": reason})
    del R["pending_checks"][:-150]


def rule_name(reason):
    r = (reason or "").lower()
    table = [("top 10", "Top-10 wallet limit"), ("one wallet", "Single-wallet limit"), ("insider", "Insider check"),
             ("holders", "Minimum holders"), ("pool is locked", "Locked pool check"), ("print", "Mint authority"),
             ("freeze", "Freeze authority"), ("tax", "Transfer tax"), ("creator still holds", "Creator holdings"),
             ("chasing", "No chasing pumps"), ("wash", "Wash-trading check"), ("fake volume", "Fake-volume check"),
             ("sellers", "Buyers vs sellers"), ("one-sided", "Bot-buying check"), ("thin liquidity", "Liquidity vs size"),
             ("already pumped", "Already pumped"), ("too new", "Minimum age"), ("rug", "Rug flags"),
             ("not trending up", "Rising this hour"), ("hype", "Hype names")]
    for k, name in table:
        if k in r:
            return name
    return None


def follow_up(R, fetch_prices, notes, cfg, now):
    """A day later: did the rejected coins crash (good save) or fly (missed)?"""
    due = [c for c in R["pending_checks"] if now - c["t"] >= cfg["followup_hours"] * 3600]
    if not due:
        return False
    prices = fetch_prices(due)
    saves_before = sum(v.get("saves", 0) for v in R["rules"].values())
    for c in due:
        R["pending_checks"].remove(c)
        p = prices.get(c["id"])
        if p is None:
            continue
        chg = (p / c["price"] - 1) * 100 if c["price"] else 0
        r = R["rules"].setdefault(c["rule"], {"saves": 0, "missed": 0, "neutral": 0, "examples": []})
        if chg <= -cfg["save_drop_pct"]:
            r["saves"] += 1
            _xp(R, 5, notes)
            r["examples"].append(f"${c['symbol']} {chg:+.0f}% (saved)")
        elif chg >= cfg["miss_rise_pct"]:
            r["missed"] += 1
            r["examples"].append(f"${c['symbol']} {chg:+.0f}% (missed)")
        else:
            r["neutral"] += 1
        del r["examples"][:-5]
    saves = sum(v.get("saves", 0) for v in R["rules"].values())
    if saves >= 1:
        _medal(R, notes, "first_save", "Good call", "A coin it refused to buy crashed within a day.")
    if saves >= 10:
        _medal(R, notes, "ten_saves", "Rug dodger", "10 rejected coins crashed afterwards.")
    if saves > saves_before:
        notes.append({"title": f"🛡️ {saves - saves_before} good save{'s' if saves - saves_before > 1 else ''} today",
                      "message": "Coins the bot refused yesterday have since crashed. Its rules earned XP.",
                      "priority": 2, "tags": ["shield"]})
    return True
