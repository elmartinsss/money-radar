"""The bot's brain: research-based rules turned into code.

Every rule here comes from the research notes in BRAIN.md: deep rug checks, memecoin entry
rules, position sizing, smarter exits, a daily circuit breaker, trend filters for big coins,
and careful learning from the bot's own paper results.
"""
import math
import time

# ----------------------------------------------------------------------------- rug checks
POOL_PROGRAM_HINTS = ("amm", "pool", "raydium", "pump", "meteora", "orca", "whirlpool")


def deep_rugcheck(report, cfg):
    """Look at RugCheck's full report. Returns (ok, note, facts)."""
    r = report or {}
    facts = {}
    if r.get("rugged"):
        return False, "already marked as rugged", facts
    if r.get("mintAuthority"):
        return False, "creator can still print new coins (mint authority)", facts
    if r.get("freezeAuthority"):
        return False, "creator can freeze your coins (freeze authority)", facts
    fee = (r.get("transferFee") or {}).get("pct") or 0
    if fee > 0:
        return False, f"hidden {fee}% transfer tax", facts
    ext = r.get("token_extensions") or {}
    for bad in ("permanentDelegate", "transferHook", "nonTransferable"):
        if ext.get(bad):
            return False, f"dangerous token setting ({bad})", facts
    dangers = [x.get("name") for x in r.get("risks") or [] if x.get("level") == "danger"]
    if dangers:
        return False, ", ".join(dangers), facts

    # Liquidity pool accounts show up as "top holders"; leave them out of concentration.
    pools = set()
    for m in r.get("markets") or []:
        for k in ("pubkey", "liquidityA", "liquidityB"):
            if m.get(k):
                pools.add(m[k])
        for k in ("liquidityAAccount", "liquidityBAccount"):
            if (m.get(k) or {}).get("owner"):
                pools.add(m[k]["owner"])
    for addr, info in (r.get("knownAccounts") or {}).items():
        if any(h in str((info or {}).get("type", "")).lower() + str((info or {}).get("name", "")).lower()
               for h in POOL_PROGRAM_HINTS):
            pools.add(addr)
    holders = [h for h in (r.get("topHolders") or [])
               if h.get("address") not in pools and h.get("owner") not in pools]
    top10 = sum(h.get("pct") or 0 for h in holders[:10])
    biggest = max([h.get("pct") or 0 for h in holders] or [0])
    insiders = sum(h.get("pct") or 0 for h in holders if h.get("insider"))
    facts.update(top10=round(top10, 1), biggest=round(biggest, 1), insiders=round(insiders, 1))
    if top10 > cfg["max_top10_pct"]:
        return False, f"top 10 wallets hold {top10:.0f}%", facts
    if biggest > cfg["max_single_holder_pct"]:
        return False, f"one wallet holds {biggest:.0f}%", facts
    if insiders > cfg["max_insider_pct"]:
        return False, f"insider wallets hold {insiders:.0f}%", facts
    if (r.get("graphInsidersDetected") or 0) > 0 and insiders > cfg["max_insider_pct"] / 2:
        return False, f"linked insider wallets detected holding {insiders:.0f}%", facts

    total = r.get("totalHolders") or 0
    facts["holders"] = total
    if total and total < cfg["min_holders"]:
        return False, f"only {total} holders", facts

    # Meteora DLMM pools have no LP token, so "locked %" means nothing there
    locks = [((m.get("lp") or {}).get("lpLockedPct") or 0) for m in r.get("markets") or []
             if "dlmm" not in str(m.get("marketType", "")).lower()]
    lp = max(locks) if locks else 0
    facts["lp_locked"] = round(lp)
    if locks and lp < cfg["min_lp_locked_pct"]:
        return False, f"only {lp:.0f}% of the trading pool is locked (dev can pull it)", facts

    supply = float((r.get("token") or {}).get("supply") or 0)
    creator = float(r.get("creatorBalance") or 0)
    if supply and creator / supply * 100 > cfg["max_creator_pct"]:
        return False, f"creator still holds {creator / supply * 100:.0f}%", facts

    warns = [x.get("name") for x in r.get("risks") or [] if x.get("level") == "warn"]
    note = f"top 10 hold {top10:.0f}%, {total or '?'} holders, pool {lp:.0f}% locked"
    if warns:
        note += "; minor: " + ", ".join(warns)
    return True, note, facts


# ----------------------------------------------------------------------------- entry rules
def entry_check(p, cfg):
    """Memecoin entry rules from the research. Returns (ok, reason, score_bits)."""
    liq = (p.get("liquidity") or {}).get("usd") or 0
    mc = p.get("marketCap") or p.get("fdv") or 0
    pc = p.get("priceChange") or {}
    vol = p.get("volume") or {}
    tx = (p.get("txns") or {}).get("h1") or {}
    buys, sells = tx.get("buys") or 0, tx.get("sells") or 0
    m5, h1 = pc.get("m5") or 0, pc.get("h1") or 0

    if m5 > cfg["max_m5_pump_pct"] or h1 > cfg["max_h1_pump_pct"]:
        return False, f"chasing a pump ({m5:+.0f}% 5m, {h1:+.0f}% 1h)", {}
    if h1 <= 0:
        return False, "not trending up this hour", {}
    if mc and liq / mc < cfg["min_liq_to_mcap"]:
        return False, f"thin liquidity for its size ({liq / mc * 100:.1f}% of market cap)", {}
    avg5 = (vol.get("h1") or 0) / 12
    if avg5 and (vol.get("m5") or 0) >= 3 * avg5 and abs(m5) < 1:
        return False, "volume spike but price flat (wash trading)", {}
    if liq < 50000 and buys + sells and (vol.get("h1") or 0) / (buys + sells) > 1000:
        return False, "few huge trades on a small pool (fake volume)", {}
    if liq and (vol.get("h1") or 0) / liq > 5 and buys + sells < 100:
        return False, "volume way bigger than the pool with few trades (fake volume)", {}
    ratio = buys / sells if sells else 9
    if ratio < cfg["min_buy_sell_ratio"]:
        return False, f"more sellers than buyers ({ratio:.1f}x)", {}
    if ratio > cfg["max_buy_sell_ratio"]:
        return False, f"suspiciously one-sided buying ({ratio:.1f}x, bots?)", {}
    age_min = max(1, (time.time() * 1000 - (p.get("pairCreatedAt") or time.time() * 1000)) / 60000)
    velocity = (buys + sells) / age_min  # research: speed of trading vs age was the best early signal
    return True, "", {"ratio": round(ratio, 2), "velocity": round(velocity, 3)}


# ----------------------------------------------------------------------------- sizing
def position_size(equity, cash, cfg, mult=1.0):
    """Fixed-fractional risk: lose about risk_pct of the wallet if the stop is hit."""
    eur = equity * cfg["risk_per_trade_pct"] / cfg["stop_loss_pct"]
    eur = min(eur, equity * cfg["max_position_pct"] / 100) * mult
    eur = min(eur, cash)
    return round(eur, 2) if eur >= cfg["min_trade_eur"] else 0


# ----------------------------------------------------------------------------- learning
def learning(history, cfg):
    """Per-signal stats with shrinkage, so a few lucky trades don't fool the bot.

    Returns {signal: {...stats, mult, status}}. mult scales future position sizes.
    """
    by = {}
    for h in history:
        by.setdefault(h.get("signal", "?"), []).append(h.get("pnl_pct", 0))
    allr = [x for v in by.values() for x in v]
    pooled = sum(allr) / len(allr) if allr else 0
    k = cfg["shrink_prior_trades"]
    out = {}
    for sig, rs in by.items():
        n = len(rs)
        mean = sum(rs) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in rs) / (n - 1)) if n > 1 else 0
        t = mean / (sd / math.sqrt(n)) if sd else 0
        shrunk = (sum(rs) + k * pooled) / (n + k)
        recent = rs[-50:]
        recent_mean = sum(recent) / len(recent)
        wins = sum(1 for x in rs if x >= 0)
        if n < cfg["min_trades_to_learn"]:
            mult, status = 1.0, f"still learning ({n}/{cfg['min_trades_to_learn']} trades)"
        elif n >= 50 and recent_mean < 0 and shrunk < 0:
            mult, status = 0.25, "benched: losing lately, only tiny test trades"
        elif shrunk < 0:
            mult, status = 0.5, "losing on average, half size"
        elif t >= 3:
            mult, status = 1.5, "clearly working, bigger size"
        elif t >= 2:
            mult, status = 1.25, "looks good, slightly bigger size"
        else:
            mult, status = 1.0, "no clear edge yet, normal size"
        out[sig] = {"n": n, "wins": wins, "win_rate": round(wins / n * 100), "avg_pct": round(mean, 1),
                    "shrunk_pct": round(shrunk, 1), "t": round(t, 2), "mult": mult, "status": status}
    return out


# ----------------------------------------------------------------------------- trend filter
def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def trend_up(closes, n=50):
    """Long-term research rule: hold big coins only while price is above the 50-day average."""
    avg = sma(closes, n)
    return None if avg is None else closes[-1] > avg
