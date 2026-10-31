# Top LP Smart Wallet Finder

[**English**](README.en.md) | [Bahasa Indonesia](README.md)

A **read-only** bot that finds smart LP wallets on Solana. It looks for wallets
that add liquidity to winning pools, then estimates whether LP fees beat
impermanent loss (IL). The best candidates are sent to Telegram via cron.

**No private keys and no outgoing transactions.** All data comes from public
APIs and the Solana RPC.

## What counts as a "smart LP"

A wallet enters the radar when it shows at least one form of LP evidence:

1. **Verified LP transaction** — a known deposit/withdraw instruction, or a
   same-direction balance change in both pool tokens.
2. **LP token ownership** — the wallet appears as a holder of the pool's LP
   token for Raydium CPMM / AMM v4 (the strongest evidence).

The position is then scored with a **fee > IL** proxy. Wallets with a high
score and fresh LP evidence enter the ranking and alerts.

## Data sources

| Data | Source | Endpoint |
|---|---|---|
| Trending pools, price, volume, TVL | GeckoTerminal | `/networks/solana/trending_pools` |
| Latest trades | GeckoTerminal | `/networks/solana/pools/{addr}/trades` |
| LP transactions | Solana RPC | `getSignaturesForAddress`, `getTransaction` |
| LP token holders | Solana RPC | `getAccountInfo`, `getTokenSupply`, `getTokenLargestAccounts`, `getMultipleAccounts` |
| Manual check links | Solscan | links in alerts only |

GeckoTerminal needs no API key. Public RPCs work, but
`getTokenLargestAccounts` is heavily rate-limited; see the **RPC** section.

## One scan cycle

1. Fetch trending Solana pools, sorted by 24h volume.
2. Mark **winning pools** (busy, liquid, not dumping).
3. Pull recent trades to discover active wallets.
4. Detect on-chain LP actions:
   - verified deposit/withdraw instruction discriminators;
   - fallback: both pool tokens move in the same direction (both down = add,
     both up = remove).
   - One-sided transactions without a discriminator are **ignored** so that
     PumpSwap buys/sells are not mistaken for LP.
5. Enumerate the largest LP token holders for Raydium CPMM / AMM v4 pools.
6. Compute daily fees, IL, edge, LP score, trade score, and tier.
7. Queue Telegram alerts subject to quotas and cooldowns.

## Winning pool filters

| Filter | Default | Meaning |
|---|---|---|
| 24h volume | ≥ $100,000 | enough activity |
| TVL | ≥ $20,000 | liquidity present |
| V/TVL | ≥ 2.0x | high capital turnover |
| 1h drop | > -25% | not dumping |
| 24h drop | > -60% | not in free fall |
| 1h buyers | ≥ 10 | buyers still present |

Every threshold can be changed in `.env`.

## Fee vs impermanent loss

A consistent proxy, not an accounting PnL report:

```text
fee_yield_daily = (volume_24h / TVL) x fee_rate
IL              = 2 x sqrt(r) / (1 + r) - 1      # r = current price / entry price
edge            = fee_yield_daily x days - |IL|
```

- `fee_rate` comes from GeckoTerminal; when missing, `DEFAULT_FEE_RATE`
  (0.3%) is used.
- Positions only a few minutes old are normalized to **at least 1 day** so
  the daily fee stays measurable.
- Position age is capped by `LP_MAX_HOLD_DAYS` (30 days).
- For LP token holders the entry price is unknown, so edge uses a 1-day proxy
  and the 24h price change.

Positive means fees cover IL. This is an initial filter, not a profit
guarantee.

## Scoring

**LP score** (0–100):

| Component | Weight | Source |
|---|---|---|
| Activity | 20% | number of adds + LP positions held |
| Pool quality | 30% | average pool score |
| Fee-IL edge | 30% | edge / 5% counts as full |
| Recency | 20% | newer is higher |

**Trade score** (0–100): win rate 35%, average peak ROI 30%, activity 15%,
PnL 20%.

**Composite score** = 70% LP score + 30% trade score. Wallets without LP
evidence are capped at 45. Tiers: **S ≥ 85**, **A ≥ 70**, **B ≥ 55**,
**C ≥ 40**, otherwise **D**.

## LP token holder enumeration

This is the strongest evidence and does not rely on transaction guesses. Pool
account layouts are verified against mainnet:

| DEX | Program | `lp_mint` in account data | Status |
|---|---|---|---|
| Raydium CPMM | `CPMMoo8...` | offset 136 | SPL LP token, supported |
| Raydium AMM v4 | `675kPX9...` | offset 464 | SPL LP token, supported |
| PumpSwap | `pAMMBay...` | offset 107 | mint exists, SPL supply 0 → skipped |
| Orca / Raydium CLMM / Meteora DLMM | — | NFT positions | skipped |

The bot takes the top 20 holders, merges multiple token accounts owned by the
same wallet, and stores the share plus an estimated position value
(`share x TVL`). Holders with a significant share/value trigger a
**TOP LP DETECTED** alert.

### RPC

Free public RPCs (`publicnode`, `api.mainnet-beta`, `drpc`) often reject
`getTokenLargestAccounts` with 403/429. The scanner keeps running and skips
holder enumeration when the RPC refuses, but for stable results use a free
Helius/QuickNode endpoint:

```bash
RPC_URL=https://mainnet.helius-rpc.com/?api-key=...
RPC_BUDGET_PER_RUN=300
```

## Install

```bash
git clone git@github.com:lyan88/lp-smart-wallet.git
cd lp-smart-wallet
cp .env.example .env
nano .env                  # set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
python3 doctor.py          # check database, API, RPC, Telegram
python3 scanner.py --once --dry-run --verbose
```

No external dependencies; only **Python 3.8+** and the standard library.

## Cron

```cron
# scan every 5 minutes
*/5 * * * * cd /opt/lp-smart-wallet && flock -n locks/scan.lock python3 scanner.py >> logs/scan.log 2>&1
# refresh prices + scores every 15 minutes
*/15 * * * * cd /opt/lp-smart-wallet && flock -n locks/track.lock python3 track.py >> logs/track.log 2>&1
# daily report at 07:00 UTC
0 7 * * * cd /opt/lp-smart-wallet && python3 report.py --telegram >> logs/report.log 2>&1
```

Or install automatically:

```bash
sudo adduser --system --group lpbot
sudo ./deploy/install.sh
```

## Commands

```bash
python3 scanner.py --once --dry-run --verbose   # one cycle, no Telegram
python3 scanner.py --once                       # one cycle, send alerts
python3 scanner.py --wallet <address>           # profile one wallet
python3 scanner.py --test-telegram              # test message
python3 scanner.py --summary                    # database contents
python3 track.py --status                       # ranking + top holders
python3 track.py                                # refresh prices and scores
python3 report.py --dry-run                     # daily report
python3 doctor.py                               # diagnostics
python3 -m unittest discover -s tests -v        # 71 unit tests
```

## Key configuration

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | bot token from BotFather |
| `TELEGRAM_CHAT_ID` | — | destination chat/group |
| `ALERT_LANG` | `id` | `id`, `en`, or `both` |
| `DRY_RUN` | `false` | `true` = print only |
| `RPC_URL` | publicnode | primary RPC endpoint |
| `RPC_BUDGET_PER_RUN` | 90 | RPC call limit per process |
| `MIN_POOL_VOL_USD` | 100000 | minimum pool volume |
| `MIN_V_TVL` | 2.0 | minimum turnover |
| `MIN_LP_SIZE_USD` | 200 | minimum LP event size for alerts |
| `LP_HOLDER_SCAN_LIMIT` | 3 | holder pools scanned per cycle |
| `LP_HOLDER_ALERT_MIN_PCT` | 0.02 | minimum holder share for alerts (2%) |
| `ALERT_MIN_SCORE` | 70 | minimum wallet score for alerts |
| `ALERT_LP_EVENTS` | `true` | alert on new LP adds |
| `ALERT_LP_HOLDERS` | `true` | alert on new LP holders |

The full list is in `.env.example`.

## Known limitations

- **Proxy, not accounting.** Fees and IL are computed from volume/TVL and
  price changes, not from each wallet's actual position. Treat this as an
  initial filter.
- **Holders only for Raydium CPMM / AMM v4.** DEXes with NFT positions cannot
  be enumerated from an SPL mint. PumpSwap has a mint but its SPL supply is 0.
- **Only the top 20 holders** are returned by `getTokenLargestAccounts`.
  Small wallets outside the top 20 are invisible through this path.
- **Public RPCs are rate-limited.** Without a dedicated RPC, some cycles will
  skip holder enumeration. This does not stop the scanner.
- **GeckoTerminal's free tier is rate-limited.** The limiter persists call
  spacing in `data/rate_*.json`.
- **Transaction detection is deliberately conservative.** Losing some signals
  is preferable to labelling a trader as an LP.
- Always verify pools and contracts yourself before making decisions.

## Structure

```text
scanner.py     single-cycle orchestration + alerts
track.py       refresh prices, edge, scores, status
report.py      daily report
doctor.py      diagnostics
sources.py     GeckoTerminal + RPC (including fetch_lp_holders)
solana_lp.py   LP transaction classification
programs.py    DEX registry, discriminators, lp_mint offsets
scoring.py     fee, IL, edge, scoring
filters.py     pool/wallet filters + formatting
db.py          SQLite: pools, trades, wallets, events, holdings, alerts
render.py      Telegram HTML messages
telegram.py    Telegram transport
i18n.py        id/en labels
tests/         71 unit tests
deploy/        install, cron, logrotate
```

## License

MIT — see `LICENSE`.
