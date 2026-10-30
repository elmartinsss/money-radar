# 🧠 The bot's brain: research notes

Research done on 30 Oct 2026 on rug pulls, memecoins, day trading and long-term crypto
investing, turned into measurable rules. The code lives in `brain.py` (bot) and in the
Quick trade tab of `index.html` (browser). Thresholds live in `config.json → brain`.

**Headline:** no credible study shows a durable after-cost edge in short-term memecoin trading.
The well-supported evidence is about what to avoid (manipulation, paid promotion, chasing,
overtrading, oversizing). So most rules filter and control risk; they don't create profit.

## Rug-pull checks (RugCheck full report, `/v1/tokens/{mint}/report`)
| Rule | Threshold | Evidence |
|---|---|---|
| Already rugged, mint/freeze authority active | block | strong |
| Transfer fee, transfer hook, permanent delegate, non-transferable | block | strong |
| Top-10 holders (pools excluded) / biggest single wallet | > 30% / > 10% → block | some |
| Insider wallets; linked insiders detected | > 10%; > 5% when a network is detected → block | some |
| Total holders | < 150 → block | some (rugs: median 9 holders) |
| Pool locked/burned (not Meteora DLMM) | < 95% → block | strong |
| Creator balance | > 5% of supply → block | some |
| Liquidity drop while holding | −30% → emergency exit | some |

## Memecoin entry rules (DexScreener)
Skip pumps (5m > +25% or 1h > +100%), require 1h > 0, liquidity ≥ 5% of market cap, reject
volume spikes with a flat price (wash trading), few huge trades on pools under $50k,
buy/sell ratio outside 0.9–4. Boosts and influencer calls are not buy signals.

## Risk and exits
1% of the wallet at risk per trade (size = 1% ÷ 15% stop), max 10% per coin, max 50% in risky
coins. Sell half at +30%, trailing stop 15% below the peak once +20%, hard stop −15%, 7-day
time limit. Circuit breaker: no risky buys after 3 losses in a row or −3% in a day.
About 5% round-trip cost on memecoins (0.5% fee + 2% slippage per side).

## Long-term rules
Buy BTC/ETH/SOL and stocks only above their 50-day average. Wait a day after a stock's 7%+ jump.
Fear-dip buys at 1.5x size only in extreme fear. Plan for 75–85% crashes.

## Learning from its own trades
No changes before 30 trades per signal. Results shrunk toward the pooled average (prior weight
20 trades). Bigger size only at t ≥ 2 (1.25x) or t ≥ 3 (1.5x). Losing on average → half size.
50+ trades and still losing lately → benched to quarter size.

## Key sources
- Solidus Labs on pump.fun scams: https://beincrypto.com/pump-fun-tokens-scams-solidus-labs-report/
- SolRugDetector (Solana rug types, 17-minute median lifespan): https://arxiv.org/html/2603.24625v1
- Pump.fun graduation and crash rates: https://arxiv.org/pdf/2602.14860
- Meme coin manipulation (82.8% of 100%+ gainers): https://arxiv.org/html/2507.01963v2
- Pump.fun trader profitability: https://crypto.news/over-60-pump-fun-traders-saw-losses-less-than-0-01-made-over-1m/
- Day traders in Brazil: https://quantpedia.com/retail-day-trading-is-an-uphill-battle/
- Day traders in Taiwan: https://ideas.repec.org/a/oup/rasset/v10y2020i1p61-93..html
- Barber & Odean, trading is hazardous: https://ideas.repec.org/a/bla/jfinan/v55y2000i2p773-806.html
- Kelly sizing, good and bad: https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf
- False discoveries in trading research (t ≥ 3): https://www.nber.org/papers/w20592
- Grayscale on Bitcoin trend signals: https://research.grayscale.com/reports/the-trend-is-your-friend-managing-bitcoins-volatility-with-momentum-signals
- Liu, Tsyvinski & Wu, crypto risk factors: https://cowles.yale.edu/sites/default/files/2022-10/LiuTsyvinskiWu2019%20COMMON%20RISK%20FACTORS.pdf
- Bitcoin drawdowns: https://nydig.com/research/charting-drawdowns-during-up-cycles
- Dead tokens on CoinGecko: https://crypto.news/over-50-of-tokens-listed-on-coingecko-since-2014-have-died-data-shows/
- Influencer picks reverse: https://www.futurity.org/crypto-influencer-advice-investment-3260122
