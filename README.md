# 🏛️ Ares — Automated Stock Trading Signal Scanner

An automated, regime-aware stock trading signal scanner that scans the entire US market daily and sends actionable trade alerts via Telegram.

> *Named after Ares, the Greek god of war — disciplined, strategic, and relentless.*

## Current Version: V3.0

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

## Scan Schedule (5x Daily, Mon-Fri)

| Time (MYT) | UTC | Type | Description |
|------------|-----|------|-------------|
| 9:00 PM | 13:00 | **Gateway Check** | Auto-restart IB Gateway if down |
| 9:30 PM | 13:30 | **Full Scan** | Market open — Finviz screen + signal detection + execute pending |
| 11:30 PM | 15:30 | **Monitor** | IBKR live price check on open trades |
| 1:30 AM | 17:30 | **Monitor** | IBKR live price check on open trades |
| 5:00 AM | 21:00 | **Full Scan** | Market close — new daily candle + signals |

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
│   └── strategy_params.json    # V3 strategy parameters
├── engine/
│   ├── screener.py             # Finviz dynamic market screener
│   ├── data_feed.py            # yfinance + IBKR data
│   ├── indicators.py           # RSI 21, MACD, divergence, regime
│   ├── signals.py              # Strategy logic + confluence check
│   └── tracker.py              # Virtual trade tracking + exits + pending signals
├── data/ohlcv/                 # Cached OHLCV CSV files
├── logs/
│   ├── virtual_trades.json     # Active + closed trade log
│   ├── pending_signals.json    # Signals waiting for next-bar execution
│   ├── signal_queue.json       # Blocked signals waiting for open slot
│   ├── trades_report.csv       # Trade history export
│   ├── last_scan_summary.txt   # Latest scan results for dashboard
│   └── archive/                # V1 trade data (archived)
├── daily_report.py             # Full scan + execute pending + new signals
├── build_dashboard.py          # Compact Telegram dashboard builder
├── monitor_trades.py           # Intraday IBKR live price monitor
├── run_ares.sh                 # Main cron: scan + dashboard + Telegram + git push
├── run_monitor.sh              # Monitor + Telegram
├── restart_gateway.sh          # IB Gateway health check + auto-restart
├── start_gateway.sh            # IB Gateway background launcher
├── Ares_Logbook.md             # Daily trading journal
└── README.md
```

## Version History

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
| 3 | Observation phase — collect 40-60 trades with realistic friction | 🔄 In Progress |
| 4 | AI/ML signal validation (Athena ML pipeline) | 🔜 Next |
| 5 | Live execution with real capital ($1K ESPP, June 2027) | ⏳ Planned |

## Timeline

| Period | Milestone |
|--------|-----------|
| Sep 2026 - May 2027 | Paper trading observation (~9 months, 40-60 trades) |
| June 2027 | Go live with $1,000 (ESPP bonus) |
| Dec 2027+ | +$500 capital injection every 6 months via ESPP |

## Author

Built by **Rickson Kang** — learning trading through building.

Part of **Project Olympus** — see also [Athena](https://github.com/Rcks888/Athena) (backtesting engine).
