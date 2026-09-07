# 🏛️ Ares — Automated Stock Trading Signal Scanner

An automated, regime-aware stock trading signal scanner that scans the entire US market daily and sends actionable trade alerts via Telegram.

> *Named after Ares, the Greek god of war — disciplined, strategic, and relentless.*

## Current Version: V3.0

## How It Works

```
┌─────────────────────────────────────────────────────┐
│                  ARES V2.1 PIPELINE                  │
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

## Scan Schedule (4x Daily, Mon-Fri)

| Time (MYT) | Type | Description |
|------------|------|-------------|
| 9:30 PM | **Full Scan** | Market open — Finviz screen + signal detection |
| 11:30 PM | **Monitor** | IBKR live price check on open trades |
| 1:30 AM | **Monitor** | IBKR live price check on open trades |
| 5:00 AM | **Full Scan** | Market close — new daily candle + signals |

## Strategies

### 1. Momentum Breakout (Uptrend)
- Stock near 52-week high with volume surge
- Blocked by bearish divergence
- TP: +12% | Trailing Stop: 8% from peak

### 2. Trend Continuation (Uptrend)
- Hidden bullish divergence + RSI pullback to 40-50
- MACD confirmation
- TP: +12% | Trailing Stop: 8% from peak

### 3. Mean Reversion (Range)
- RSI < 30 + bullish divergence + volume spike
- Price near SMA support
- TP: +8% | Trailing Stop: 8% from peak

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
| Profit factor | 2.42-3.00 | Backtested on 1060 trades |

## Finviz Screens

| Screen | What It Catches |
|--------|----------------|
| `unusual_volume` | Volume > 3x avg, large cap — something big happening |
| `oversold_bounce` | RSI < 30, large cap — mean reversion candidates |
| `near_52w_high` | Within 3% of high + volume — momentum breakouts |
| `big_movers_up` | Up > 5% today — surge candidates |
| `big_movers_down` | Down > 5% today — potential reversals |

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
| Scheduler | cron (4x daily, Mon-Fri) |

## Project Structure

```
Ares/
├── config/
│   ├── watchlist.json          # Fallback fixed watchlist (153 stocks)
│   └── strategy_params.json    # V2.1 strategy parameters
├── engine/
│   ├── screener.py             # Finviz dynamic market screener
│   ├── data_feed.py            # yfinance + IBKR data
│   ├── indicators.py           # RSI 21, MACD, divergence, regime
│   ├── signals.py              # Strategy logic + confluence check
│   └── tracker.py              # Virtual trade tracking + exits
├── data/ohlcv/                 # Cached OHLCV CSV files
├── logs/
│   ├── virtual_trades.json     # Active trade log
│   ├── trades_report.csv       # Trade history export
│   └── archive/                # V1 trade data (archived)
├── daily_report.py             # Full scan script
├── monitor_trades.py           # Intraday IBKR monitor
├── run_monitor.sh              # Monitor + Telegram
└── start_gateway.sh            # IB Gateway background launcher
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
| 3 | AI analysis (Claude) for signal validation | 🔜 Next |
| 4 | Automated trade execution via IBKR | ⏳ Planned |
| 5 | Portfolio optimization + risk management | ⏳ Planned |

## Author

Built by **Rickson Kang** — learning trading through building.
