# 🏛️ Ares — Automated Stock Trading Signal Scanner

An automated, regime-aware stock trading signal scanner that scans the entire US market daily and sends actionable trade alerts via Telegram.

> *Named after Ares, the Greek god of war — disciplined, strategic, and relentless.*

## Current Version: V3.1

**V3.1 corrects the implementation. It does not change the strategy.**
`config/strategy_params.json` is byte-identical to V3.0 — no signal logic, threshold
or sizing was touched, deliberately, so the observation dataset is not split into
incomparable regimes mid-collection.

The `sample_phase` label stays `clean_v3` for the same reason: it tracks the
**strategy generation** the data belongs to, while the version number tracks the
**code**. Data produced under the V3 strategy is `clean_v3` whether the code is
V3.0 or V3.1. A `clean_v4` phase is earned only when parameters actually change.
If the two moved together, the sample would fragment every time a bug was fixed.

## How It Works

```
┌─────────────────────────────────────────────────────┐
│                  ARES V3.0 PIPELINE                  │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Finviz (entire US market)                          │
│    → Screen ~5000 stocks with 5 loose filters       │
│    → Output: 50-100 interesting candidates          │
│                                                     │
│  yfinance (daily candles)                           │
│    → Download OHLCV for all candidates              │
│    → Calculate RSI 21 (OHLC4), MACD, SMA, volume   │
│    → Detect market regime (uptrend/range/downtrend) │
│    → Detect 4 types of divergence                   │
│    → Check confluence (min 2 signals required)      │
│    → Output: 1-5 actionable signals                 │
│                                                     │
│  IBKR (live/delayed prices)                         │
│    → Monitor open trades intraday                   │
│    → Check stop-loss and trailing stop hits          │
│    → Future: automated order execution              │
│                                                     │
│  Telegram                                           │
│    → Send signals + trade alerts to phone            │
│                                                     │
└─────────────────────────────────────────────────────┘
```

## Schedule (Mon-Fri)

The VPS clock is UTC; cron is written in UTC. MYT is UTC+8.

| Time (MYT) | UTC | Type | Description |
|------------|-----|------|-------------|
| 9:00 PM | 13:00 | **Gateway restart** | IB Gateway health check + restart |
| 9:30 PM | 13:30 | **Full Scan** | Market open — screen, exits, queue maintenance, promote |
| 11:45 PM | 15:45 | *(IBKR daily kill)* | Broker-side; the gateway does **not** restart itself |
| 12:00 AM | 16:00 | **Gateway restart** | Recovers from the daily kill |
| 12:10 AM | 16:10 | **Monitor** | IBKR live price check on open trades |
| 1:25 AM | 17:25 | **Gateway restart** | |
| 1:30 AM | 17:30 | **Monitor** | IBKR live price check on open trades |
| 5:00 AM | 21:00 | **Full Scan** | Market close — new daily candle + signals |

A gateway restart precedes every run that needs IBKR, because the 11:45 PM
broker-side kill leaves the gateway down and it has no self-recovery. Hermes
(the XAU/USD sibling system) shares this VPS and is scheduled off the `:00`,
`:10`, `:25` and `:30` marks to avoid colliding with these.

## Strategies

### 1. Momentum Breakout (Uptrend)
- Stock near 52-week high with volume surge
- Blocked by bearish divergence
- Scale-out: 50% at +18% TP | Remaining 50%: 10% trailing stop

### 2. Mean Reversion (Range)
- RSI < 30 + bullish divergence + volume spike
- Price near SMA support
- Scale-out: 50% at +10% TP | Remaining 50%: 10% trailing stop

> **Note:** Trend Continuation strategy was disabled in V3 (24% win rate in backtesting).

### Rules
- ❌ **Never buy in downtrends**
- ✅ Minimum 2 confluence signals required
- 📈 Scale-out: Sell 50% at TP, ride remaining 50% with trailing stop
- 🛑 Trailing stop: 10% from peak price
- 🚨 Emotional extreme exit: RSI > 90
- 📊 Max 5 open positions, 25% cash reserve
- 📋 Signal queue: blocked signals wait 5 days for a slot

### V3 Parameters (Athena-Optimized)
| Parameter | Value | Backtested |
|-----------|-------|-----------|
| TP (momentum) | 18% — scale out 50% | +23% avg on scaled trades |
| TP (reversal) | 10% — scale out 50% | |
| Trailing stop | 10% from peak | +5.66% avg P&L |
| Trend continuation | Disabled | Was 24% win rate |
| Max positions | 5 | Best risk/reward ratio |
| Profit factor | 2.41 (realistic) | Backtested on 1060 trades |

### Realistic Execution Tracking
| Item | Setting |
|------|---------|
| Entry timing | Next-bar execution (signal day N → buy day N+1 open) |
| Slippage | 0.1% per trade (buy higher, sell lower) |
| Commission | $1 per trade (entry + exit + scale-out each) |
| Fill assumption | System always follows signal (no manual override) |

Every trade logs: `signal_price`, `entry_price` (after slippage), `entry_commission`, `exit_slippage`, `exit_commission`, `total_commission`, `pnl_after_costs` — directly comparable to Athena V5 backtest.

Scaled-out trades additionally log `scale_out_shares`, `scale_out_pnl`,
`scale_out_pnl_pct` and `pnl_remaining`. **P&L spans the whole original
position** — the tranche sold at the scale-out plus whatever remains at exit.
`trade['shares']` holds only the remainder after a scale-out, so measuring on it
alone understates every scaled-out winner by exactly the amount the scale-out
locked in. `pnl_pct` is blended over the original cost basis so percent and
dollars agree; for a trade that never scaled out it is identical to the plain
formula.

**Win/loss is classified on net P&L everywhere** — dashboard, scorecard and
export. A trade whose gain is smaller than its commissions is not a win, and
classifying on gross made the win count disagree with the realised total.

> **Note on capital tracking:** Position sizing uses a fixed $1,000 base (not dynamic equity). This is acceptable for the observation phase data collection. Dynamic equity tracking will be added when transitioning to live execution (Phase 4).

## Finviz Screens (V3 — Loosened)

| Screen | Filter | What It Catches |
|--------|--------|----------------|
| `unusual_volume` | Volume > 2x avg, mkt cap > $2B | Something big happening |
| `oversold_bounce` | RSI < 30, mkt cap > $2B | Mean reversion candidates |
| `near_52w_high` | Within 3% of high, mkt cap > $10B | Momentum breakouts |
| `big_movers_up` | Up > 3% today, vol > 500K | Surge candidates |
| `big_movers_down` | Down > 3% today, vol > 500K | Potential reversals |

Max 100 candidates per scan (capped from Finviz output).

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Server | DigitalOcean VPS (Singapore) |
| Data (analysis) | yfinance — daily candles, free, no limit |
| Data (live price) | IBKR API via ib_insync |
| Screener | Finviz — entire US market |
| Indicators | pandas_ta — RSI, MACD, SMA, volume |
| Notifications | Telegram Bot API |
| Broker | Interactive Brokers (paper trading) |
| Scheduler | cron (5x daily, Mon-Fri) |

## Project Structure

```
Ares/
├── config/
│   ├── watchlist.json          # Fallback fixed watchlist (153 stocks)
│   └── strategy_params.json    # SOLE source of truth for all tunable parameters
├── engine/
│   ├── screener.py             # Finviz dynamic market screener
│   ├── data_feed.py            # yfinance + IBKR data
│   ├── indicators.py           # RSI 21, MACD, divergence, regime
│   ├── signals.py              # Strategy logic + confluence check
│   ├── tracker.py              # Virtual trade tracking + exits + pending signals
│   └── sample.py               # SOLE definition of which trades count as clean
├── data/ohlcv/                 # Cached OHLCV CSV files
├── logs/
│   ├── virtual_trades.json     # Active + closed trade log
│   ├── pending_signals.json    # Signals waiting for next-bar execution
│   ├── signal_queue.json       # Blocked signals waiting for open slot
│   ├── queue_ranked.json       # Validated + ranked queue (promotable entries only)
│   ├── queue_events.jsonl      # Append-only audit log of every queue/fill event
│   ├── trades_report.csv       # Trade history export
│   ├── last_scan_summary.txt   # Latest scan results for dashboard
│   ├── psi_state.json          # Memory-stall counters (per-host, NOT tracked)
│   └── archive/                # V1 trade data (archived)
├── daily_report.py             # Full scan + execute pending + new signals
├── build_dashboard.py          # Compact Telegram dashboard builder
├── monitor_trades.py           # Intraday IBKR live price monitor
├── run_ares.sh                 # Main cron: scan + dashboard + Telegram + git push
├── run_monitor.sh              # Monitor + Telegram
├── restart_gateway.sh          # IB Gateway health check + auto-restart
├── start_gateway.sh            # IB Gateway background launcher
├── audit_entry_prices.py       # Measure stale-fill damage vs the true open
├── analyze_queue_bias.py       # Test queue drift-expiry for selection bias
├── backfill_sample_phase.py    # Label trades pre_clean / clean_v3
├── repair_scaled_pnl.py        # One-off: rebook unbooked scale-out gains
├── repair_stale_entries.py     # One-off: re-base open positions on true open
├── repair_queue_stdev.py       # One-off: backfill stdev_20 into legacy queue
├── Ares_Logbook.md             # Daily trading journal
├── ROADMAP.md                  # Phased plan + unimplemented risk controls
└── README.md
```

The `repair_*.py` and `audit_*.py` scripts are diagnostics and one-off repairs,
not part of the scheduled path. All default to a dry run and back up before
writing. They are kept in the repo rather than deleted because each documents a
specific defect and the evidence used to establish it.

```
```

## Configuration — single source of truth

**`config/strategy_params.json` is the only file the runtime reads for tunable behaviour.**
If a value is not in that file, it is not in effect.

A second file, `config/risk_rules.json`, previously sat alongside it and was headed
*"YOUR RULES. Follow these when trading."* No code ever read it. Its values also
contradicted actual behaviour — it declared a 10% position cap while the code sized
positions at 15%, and declared a 5% weekly loss limit that was never implemented.
It was removed rather than corrected, because a config file that looks authoritative
but is inert is worse than no file at all: it invites decisions based on protections
that do not exist.

Risk controls that are **intended but not yet built** now live in [ROADMAP.md](ROADMAP.md)
under *Unimplemented Risk Controls*, where they read as future work rather than as
active configuration.

When adding a new tunable:
1. Add the key to `strategy_params.json`
2. Read it via `params.get('key', <sane_default>)` — never hardcode the value
3. If it cannot be implemented yet, put it in ROADMAP.md, **not** in a config file

## Data integrity — `pre_clean` vs `clean_v3`

Weeks 1-3 are retained as process-validation history. **Official edge measurement
begins from the first clean scheduled fill after the Sep 18-21 fixes.**
Contaminated trades stay labelled and are excluded from edge and ML metrics.
Nothing is deleted and no closed-trade price is rewritten, so the record stays
auditable.

A trade counts as clean only when all four hold:

1. Filled by the scheduled production path, not a manual off-schedule run
2. Entered after the Sep 18-21 fixes (`CLEAN_FROM`, currently 2026-09-19)
3. Not marked `contaminated`
4. Uses current accounting end to end — entry, stops, scale-out, close, net P&L

| Field | Meaning |
|-------|---------|
| `sample_phase` | `pre_clean` or `clean_v3` |
| `contaminated` | boolean gate |
| `contamination_reasons` | e.g. `stale_entry`, `manual_fill`, `stdev_fallback` |
| `fill_source` | `scheduled` or `manual`, stamped at fill time |
| `fill_detect` | `env` or `tty_inferred` — how `fill_source` was decided |
| `entry_price_original` | retained wherever a position was re-based |

**`engine/sample.py` is the only definition of "clean."** The scorecard, the
dashboard and any future ML training import it rather than reimplementing the
test, because three copies of the predicate would eventually disagree and the
disagreement would surface as an unexplained gap between two reports.

`metrics()` returns `None` for an empty sample instead of zeros, and leaves
profit factor undefined when nothing has lost yet — an absent result must not be
readable as a real one.

`CLEAN_FROM` is deliberately the day **after** the fixes. A trade entered on the
fix day itself cannot be proven to have filled post-deployment, because the
record stores a date and no time, and **unprovable is treated as contaminated.**

### Why fill context is stamped, not inferred

A manual fill cannot be reconstructed from a completed record. `open_trade()`
therefore stamps it as it happens: `ARES_SCHEDULED=1` (exported by the cron
scripts) is authoritative, and absent it, whether stdin is a TTY distinguishes
cron from an interactive shell. `fill_detect` records which method decided, so
an inferred classification is never mistaken for a verified one.

For the same reason, a `stdev_fallback` recorded at fill time outranks the
stop-distance signature that can only infer it — a later trailing ratchet or
re-base erases the fingerprint. **Recorded evidence does not decay; inferred
evidence does.**

## Operations — memory

**Do not clear swap.** `swapoff -a && swapon -a` force-faults every evicted page
back into RAM simultaneously — a genuine memory spike, with the IB Gateway JVM
resident — and the kernel then re-evicts the same idle pages over the following
hours. It is churn for no benefit. If the occupancy itself is unwanted, reduce the
cause instead with `sysctl vm.swappiness=10`.

**Swap occupancy is not a pressure signal.** At the default `vm.swappiness=60` the
kernel evicts idle anonymous pages even with gigabytes free, and those pages are
never faulted back in. Measured on this host: after a manual swap clear, refill was
two-phase — 0 → 284 MB rapidly, then 284 → 308 MB over 21 hours (~1 MB/hour) while
1486 MB of RAM stayed free. Equilibrium restoration and a genuine RAM peak produce
the same occupancy curve, so occupancy cannot distinguish them. Thrashing looks like
tens of MB per *minute*, not per day.

The dashboard therefore reports:

| Line | Source | Measures |
|------|--------|----------|
| `RAM` | `/proc/meminfo` `MemAvailable` | State, sampled — warns only on the compound condition swap >200 MB **and** free <400 MB |
| `Swap` | `MemInfo` + `/proc/vmstat` `pswpout` delta | Occupancy, qualified by whether paging is actually happening |
| `Stall` | `/proc/pressure/memory` `full total=` delta | Cumulative stall time — catches spikes that begin **and end** between samples |

`Stall` is the only one of the three that detects a transient event, which is why
per-run peak RAM sampling was not needed. Requires kernel 4.20+ with `CONFIG_PSI`
(Ubuntu 24.04 has it); the block degrades to a no-op if absent. State carries between
runs in `logs/psi_state.json`.

## Version History

### V3.1 — Accounting & Data Integrity (Sep 21, 2026)

No strategy change. Eleven defects fixed across execution, accounting and data
handling, plus the dataset split that separates validated history from the sample
used for edge measurement. See [Ares_Logbook.md](Ares_Logbook.md) for the
evidence behind each.

**Accounting**
- Scale-out P&L was computed, printed and discarded — every scaled-out winner was
  understated by exactly the amount the scale-out locked in ($8.06 on DYN)
- `total_commission` was overwritten at close, dropping the scale-out commission
- Win/loss used gross P&L, realised total used net, the dashboard icon used
  percent — three verdicts on one trade. Now net everywhere

**Execution & data**
- Fills ran before the data refresh, so entries could take the previous session's
  open. 4 of 6 entries measured stale, dispersion ±9.5%
- Open positions re-based on the true open; stops, targets and share counts
  re-derived, preserving the volatility-scaled distance
- A missing `stdev_20` was silently replaced by 0.05, producing a 10% stop where
  the real figure gave 3-5%. Now flagged rather than substituted quietly
- pandas MultiIndex column shape broke queue validation and permanently dropped
  signals; both load paths now flatten

**Data integrity**
- `sample_phase` / `contaminated` / `contamination_reasons` on every trade
- `fill_source` stamped at fill time, because a manual fill cannot be
  reconstructed from a completed record
- `engine/sample.py` as the sole definition of "clean"
- Official scoreboard measures the clean sample only; all-history reported
  separately and labelled as process validation

**Operations**
- Monitor runs now commit their own log state
- Tracked log files made explicit in `.gitignore` rather than surviving by
  having been committed before the ignore rule existed
- PSI memory-stall detection replacing swap occupancy as the pressure signal

### V3.0 — Athena-Optimized (Sep 6, 2026)
- Parameters optimized via 1060+ backtested trades (Athena engine)
- Scale-out: sell 50% at TP, ride 50% with trailing stop
- Trailing stop: 8% → 10% (now profitable at +5.66% avg)
- TP: 12% → 18% (was leaving 12.54% on the table)
- Disabled trend_continuation strategy (24% win rate, losing money)
- Signal queue: blocked signals wait 5 days for open slot
- Max 5 positions with 25% cash reserve
- Backtested portfolio: $1,000 → $4,046 in 5 years (+32.5%/yr)

### V2.1 — Dynamic Screener (Sep 3, 2026)
- Replaced fixed watchlist with Finviz dynamic market screener
- yfinance for daily analysis, IBKR for live price monitoring only
- Separate monitor script for intraday stop-loss checks
- No rate limits, no pauses

### V2.0 — Regime-Aware (Sep 3, 2026)
- RSI 21 OHLC4 (was RSI 14 Close)
- Market regime detection (uptrend/downtrend/range)
- 4 divergence types (regular + hidden, bullish + bearish)
- Confluence requirement (min 2 signals)
- Trailing stop (8% from peak)
- Emotional extreme exit (RSI > 90)

### V1.0 — Initial Build (Aug 27, 2026)
- 53 fixed stocks, GitHub Actions cron
- RSI 14 oversold + momentum breakout
- Basic stop-loss (7%)
- yfinance only

## 5-Phase Roadmap

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Signal scanner + Telegram alerts | ✅ Complete |
| 2 | Virtual paper trading + performance tracking | ✅ Complete |
| 3 | Observation phase — collect 40-60 **clean** trades with realistic friction | 🔄 In Progress |
| 4 | AI/ML signal validation (Athena ML pipeline) | 🔜 Next |
| 5 | Live execution with real capital ($1K ESPP, June 2027) | ⏳ Planned |

## Timeline

| Period | Milestone |
|--------|-----------|
| Sep 2026 | Weeks 1-3: process validation. 11 defects found; data retained as `pre_clean` |
| Sep 19, 2026 | `clean_v3` sample opens — trade counting starts here, not from the first trade ever |
| Sep 2026 - May 2027 | Paper trading observation (~9 months, 40-60 clean trades) |
| June 2027 | Go live with $1,000 (ESPP bonus) |
| Dec 2027+ | +$500 capital injection every 6 months via ESPP |

## Author

Built by **Rickson Kang** — learning trading through building.

Part of **Project Olympus** — see also [Athena](https://github.com/Rcks888/Athena) (backtesting engine).
