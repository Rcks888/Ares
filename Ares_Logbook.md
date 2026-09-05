# Ares Trading System — Logbook

---

## History Summary (Before Sep 3, 2026)

### V1 (Aug 27 – Sep 2, 2026)
- Built initial automated scanner with GitHub Actions
- 53 stocks, 6 categories, yfinance daily data
- RSI 14 (Close), basic oversold/momentum strategies
- Telegram notifications, virtual trade tracking
- **Results:** 2 trades — CRWD (momentum_breakout, -11.14% stop_loss), PINS (rsi_reversal, still open)
- **Lessons:** RSI oversold ≠ buy signal in trends, need market context

### V2 Upgrade (Sep 3, 2026)
- RSI 21 OHLC4, market regime detection (uptrend/downtrend/range)
- 4 divergence types (regular + hidden, bullish + bearish)
- Confluence requirement (min 2 signals)
- Trailing stop (8% from peak)
- Emotional extreme exit (RSI > 90)
- Fixed MACD column swap bug from V1

### V2.1 Upgrade (Sep 3, 2026)
- Replaced fixed watchlist with Finviz dynamic screener
- Scans entire US market → 50-100 candidates per run
- yfinance for daily candle analysis (free, no limit)
- IBKR for live price monitoring on open trades
- Deployed to DigitalOcean VPS (Singapore, $6/month)
- IB Gateway connected to paper account (DUT079340)
- 4x daily cron: 9:30 PM, 11:30 PM, 1:30 AM, 5:00 AM MYT

### V1 Trade Data Cleared
- Previous virtual trades (CRWD, PINS) archived
- Starting fresh data collection with V2.1 logic

---

<div style="page-break-after: always;"></div>

## Daily Log

### Sep 3, 2026 (Wednesday)

**Changes Made:**
- Deployed Ares V2 + V2.1 to DigitalOcean VPS
- Installed IB Gateway + IBC for automated IBKR login
- Configured Finviz screener (5 screens: unusual_volume, oversold_bounce, near_52w_high, big_movers_up, big_movers_down)
- Switched to yfinance for analysis + IBKR for live price checks
- Cleared V1 trade data, fresh start

**Test Run Results:**
- Finviz screened 56 unique candidates from entire market
- 59 stocks total (56 + SPY/QQQ/IWM)
- Market regimes: 20 uptrend | 10 range | 29 downtrend
- Signals: None (market closed, expected)

**Open Trades:** None (fresh start)
**Closed Trades:** None

**Notes:**
- First real V2.1 scan tonight at 9:30 PM MYT
- V2 correctly avoids buying 29 downtrend stocks
- System running smoothly on VPS

---

### Sep 4, 2026 (Thursday)

**Scan Results:**
- 9:30 PM scan: ✅ No signals. Regimes: 9 uptrend | 3 range | 16 downtrend
- 11:30 PM scan: ✅ Monitor — No open positions
- 1:30 AM scan: ✅ Monitor — No open positions
- 5:00 AM scan: ✅ 2 signals detected — CNH, PYPL

**Signals Triggered:**
- **CNH** — momentum_breakout | uptrend | near_52w_high | confluence 3 | RSI 73.7 | vol 1.56x
- **PYPL** — momentum_breakout | uptrend | big_movers_up | confluence 3 | RSI 58.8 | vol 1.7x

**Open Trades:**
| Symbol | Strategy | Entry Date | Entry Price | Current Price | P&L % | SL | TS | TP |
|--------|----------|-----------|-------------|---------------|-------|----|----|-----|
| CNH | momentum_breakout | Sep 4 | $13.84 | $13.84 | 0.0% | $12.83 | $12.83 | $15.50 |
| PYPL | momentum_breakout | Sep 4 | $16.93 | $16.93 | 0.0% | $15.73 | $15.73 | $18.96 |

**Closed Trades:**
| Symbol | Strategy | Entry | Exit | P&L % | Reason |
|--------|----------|-------|------|-------|--------|
| — | — | — | — | — | — |

**Notes:**
- All 4 Telegram messages triggered successfully ✅
- First full V2.1 day running on VPS
- Market heavily bearish (16 downtrend stocks), V2 correctly avoided buys at 9:30 PM
- New daily candle at 5:00 AM revealed 2 signals (CNH, PYPL)
- CNH RSI 73.7 — already near overbought, but trailing stop protects downside
- PYPL RSI 58.8 — healthier entry point
- Both found by Finviz screener (not in original fixed watchlist) — dynamic screening working!

---

### Sep 5, 2026 (Friday)

**Changes Made:**
- Added holding days tracking to trades JSON, CSV, scorecard, and monitor
- Holding days auto-updates on every save for open trades
- Closed trades record final holding days permanently

**Scan Results:**
- 9:30 PM scan: ✅ No signals. Regimes: 16 uptrend | 4 range | 12 downtrend
- 11:30 PM scan: ✅ Monitor — No live price (IBKR couldn't fetch, see bug fix)
- 1:30 AM scan: ✅ Monitor — No live price (same issue)
- 5:00 AM scan: ✅ No signals. Regimes: 14 uptrend | 18 range | 29 downtrend

**Signals Triggered:**
- None

**Open Trades:**
| Symbol | Strategy | Entry Date | Entry Price | Current Price | P&L % | Hold Days | SL | TS | TP |
|--------|----------|-----------|-------------|---------------|-------|-----------|----|----|-----|
| CNH | momentum_breakout | Sep 3 | $13.84 | $13.84 | 0.0% | 1 | $12.83 | $12.83 | $15.50 |
| PAYP | momentum_breakout | Sep 3 | $16.93 | $16.93 | 0.0% | 1 | $15.73 | $15.73 | $18.96 |

**Closed Trades:**
| Symbol | Strategy | Entry | Exit | Hold Days | P&L % | Reason |
|--------|----------|-------|------|-----------|-------|--------|
| — | — | — | — | — | — | — |

**Notes:**
- Prices unchanged (still daily close, no intraday movement captured yet)
- Monitor bug: showed "No open positions" because IBKR returned no live price and script skipped display — fixed
- 5:00 AM scan shows more downtrend stocks (29 vs 12) — different Finviz candidates at different times
- Weekend ahead — no scans until Monday 9:30 PM MYT

---

### [DATE TEMPLATE — Copy for new days]

### Mon DD, 2026 (Day)

**Changes Made:**
-

**Scan Results:**
- 9:30 PM scan:
- 11:30 PM scan:
- 1:30 AM scan:
- 5:00 AM scan:

**Signals Triggered:**
-

**Open Trades:**
| Symbol | Strategy | Entry Date | Entry Price | Current Price | P&L % | SL | TS | TP |
|--------|----------|-----------|-------------|---------------|-------|----|----|-----|
| | | | | | | | | |

**Closed Trades:**
| Symbol | Strategy | Entry | Exit | P&L % | Reason |
|--------|----------|-------|------|-------|--------|
| | | | | | |

**Notes:**
-

---

## Weekly Summary Template

### Week of Mon DD – Fri DD, 2026

| Metric | Value |
|--------|-------|
| Total scans | /20 |
| Signals triggered | |
| Trades opened | |
| Trades closed | |
| Win rate | |
| Total P&L | |
| Best trade | |
| Worst trade | |
| Most common regime | |
| Most common screen | |

**Observations:**
-

**Parameter adjustments:**
-

---

## Monthly Summary Template

### Month 2026

| Metric | Value |
|--------|-------|
| Total signals | |
| Total trades opened | |
| Total trades closed | |
| Win rate | |
| Total P&L ($) | |
| Total P&L (%) | |
| Avg win % | |
| Avg loss % | |
| Best strategy | |
| Worst strategy | |
| Sharpe ratio | |

**Key learnings:**
-

**Changes for next month:**
-

