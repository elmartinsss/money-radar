# 📡 Money Radar

Scans crypto/stock news, Reddit, CoinGecko trending, DexScreener memecoins and your
price watchlist every 5 minutes, then pings your iPhone through **ntfy**. Free.

## Setup (about 10 minutes)

1. **ntfy app:** subscribe to a new topic with a long random name, e.g.
   `radar-elmartin-8k2q9x`. Anyone who knows the name can read it, so make it hard to guess.
   (You can use a different topic from your travel bot so the alerts stay separate.)
2. **GitHub:** create a new **public** repo (public = unlimited free run minutes;
   a private one would burn through the free 2,000 min/month in ~3 weeks).
3. Upload everything in this folder, keeping the `.github/workflows/scan.yml` path.
4. Repo → **Settings → Secrets and variables → Actions → New repository secret**  
   Name: `NTFY_TOPIC`, Value: your topic name.
5. Repo → **Actions** tab → enable workflows → **Money Radar → Run workflow**.  
   You should get "✅ Money Radar is live" within a minute.

## Tuning (`config.json`)

| Section | What it does | Knobs |
|---|---|---|
| `news` | RSS from CoinDesk, Cointelegraph, Decrypt, Google News | `keywords`, `min_keyword_hits` |
| `reddit` | Hot posts in chosen subs | `subreddits`, `min_upvotes` |
| `coingecko_trending` | Most-searched coins (once per coin per day) | `top_n` |
| `dexscreener` | Boosted Solana memecoins that pass liquidity, volume, age and RugCheck filters | `min_liquidity_usd`, `min_age_hours` |
| `watchlist` | Big daily moves in coins/stocks you pick | `move_threshold_pct` |
| `my_tokens` | Pings you (top priority) when **$LABRAT** gets volume or moves | paste your mint address, set `enabled: true` |

Too many alerts? Raise `min_upvotes`, `min_keyword_hits` or `move_threshold_pct`,
or switch a whole section to `"enabled": false`.

## Good to know
- **X/Twitter and Instagram aren't included.** Their APIs are paid or closed and they
  block scrapers. Reddit can also block GitHub's servers sometimes; when a source fails,
  the rest keep running.
- GitHub pauses scheduled runs in a repo after **60 days without commits**. Edit any file
  now and then to keep it going.
- The test: `DRY_RUN=1 python scanner.py` prints alerts instead of sending them.

## 📝 Paper wallet (fake €100)
The bot "buys" €10 of each strong signal (memecoins that pass every filter, top-3 trending
coins, and big upward moves on your watchlist) using live prices, fees and slippage.
It sells automatically at **+30%** (take profit), **−15%** (stop loss) or after **7 days**.
You get a ping for every buy and sell, plus a daily report at 20:00 comparing it with simply
holding Bitcoin. Full history: **PORTFOLIO.md** in this repo. Tune it in `config.json → paper`.
To restart from €100, delete `paper/wallet.json`.

## 👀 FOMO crowd + market mood
- **FOMO crowd:** once an hour the bot checks which Solana memecoins FOMO-app users are buying
  most (read from the blockchain through Bitquery, since FOMO has no public API). Coins with 25+
  buyers still have to pass all memecoin safety filters. Needs a free Bitquery access token saved
  as the repo secret `BITQUERY_TOKEN`; without it this source simply stays off.
- **Market mood:** the Crypto Fear & Greed index (0 = panic, 100 = FOMO). At 75+ the paper bot
  skips memecoins and trending coins; at 20 or below it buys a little Bitcoin on the dip.
