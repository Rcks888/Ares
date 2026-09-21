# Ares Trading System — Logbook

---

## Index

Quiet stretches are collapsed into a single row. Days with significant changes get
their own row.

| Date | Type | What happened |
|------|------|---------------|
| [Before Sep 3](#history) | 📜 History | V1 → V2 → V2.1 evolution. V1 trade data cleared for a fresh start |
| [Sep 3](#d-sep03) | 🚀 Deploy | V2.1 live on DigitalOcean VPS. IB Gateway + IBC auto-login, Finviz screener (5 screens) |
| [Sep 4](#d-sep04) | 📊 Collection | First signals detected — CNH, PYPL. Dynamic screening confirmed working |
| [Sep 5](#d-sep05) | ✨ Feature | Holding-days tracking + 30-day shadow tracking. Fixed cron missing `PATH`/`DISPLAY` |
| [Sep 6-7](#d-sep0607) | 💤 Weekend | No scans |
| [Sep 8-12](#w2) | 🚀 V3 + 📊 | **Ares V3.** True next-bar execution, friction tracking, capital $10K → $1K, Telegram dashboard. 4 positions opened, first scale-out (DYN). Fixed IB Gateway read-only + silent git push failure |
| [Sep 14-18](#w3) | 🐛 Major | Queue redesign (two-pass). First closed trade (PINS, −5.9%). **IBKR live price fixed — missing `tzdata`.** Telegram token hijacked → rotated. VPS memory cleanup |
| [Sep 18](#d-sep18) | 🔴 **Critical** | **Eight bugs behind one dashboard symptom** — see bug index below |
| [Sep 19](#d-sep21) | 📊 Collection | DYN closed on trailing stop. SDGR opened. PSI reported its first real deltas |
| [Sep 21](#d-sep21) | 🔴 **Critical** | **Scale-out gain never booked** — a winning trade made realised P&L worse. Silent bias in the ML training data |

### Major bug index

Direct links to the significant defects, newest first.

| Date | Severity | Bug | Impact |
|------|----------|-----|--------|
| [Sep 21](#entry-audit) | 🔴 | Stale entry reached into `stop_loss`, not just records | **ABM carried 2.7x intended risk** while shown as +8.5% when actually -1.7%. 4 of 6 entries stale, dispersion ±9.5% |
| [Sep 21](#b-scaleout) | 🔴 | Scale-out gain computed, printed, then discarded | **$8.06 error on DYN.** Understated every scaled-out winner — would have taught the ML that scaling out destroys returns |
| [Sep 21](#b-scaleout) | 🟠 | `total_commission` overwritten at close | Scale-out commission dropped; costs under-counted $1 per scaled trade |
| [Sep 21](#b-scaleout) | 🟠 | Win/loss used gross, Realized used net, icon used percent | Three verdicts on one trade; `W:` disagreed with `Realized:` |
| [Sep 18](#b-signalloss) | 🔴 | Fills ran before the data refresh | **Every entry filled at the previous session's open.** Contaminated `entry_quality_pct` for all 5 trades |
| [Sep 18](#b-signalloss) | 🔴 | Pending deleted on any data failure | RVTY destroyed by one transient fetch error, with no log |
| [Sep 18](#b-signalloss) | 🔴 | `load_stock()` returned two column shapes | NTSK + SLDE killed by a `vol_ratio` crash. **Regression from the Sep 17 stale-cache fix** |
| [Sep 18](#b-signalloss) | 🔴 | "Couldn't evaluate" treated as "invalid" | Silent permanent deletion across 3 code paths — the unifying defect |
| [Sep 18](#b-signalloss) | 🟠 | Queue discarded `stdev_20` | Promoted trades got a generic 10% stop instead of volatility-scaled |
| [Sep 18](#b-signalloss) | 🟠 | `stdev_20` default was a price, not a fraction | Would compute a **negative** stop loss |
| [Sep 18](#b-psi) | 🟠 | Swap occupancy used as a proxy for peak RAM | Wrong signal — replaced with PSI stall-time deltas |
| [Sep 17](#w3) | 🔴 | Signal queue was write-only | No promotion logic existed; queued signals sat indefinitely |
| [Sep 17](#w3) | 🔴 | Cache had no staleness check | Queue validation judged signals on weeks-old data |
| [Sep 16](#w3) | 🔴 | Missing `tzdata` on the VPS | `ZoneInfoNotFoundError` silently turned all IBKR data into `NaN` |
| [Sep 16](#w3) | 🔴 | Telegram bot token committed publicly | Bot hijacked and renamed to spam. Token rotated, moved to `.env` |
| [Sep 15](#w3) | 🟠 | Dashboard labelled UTC times as MYT | 8-hour reporting error |
| [Sep 10](#w2) | 🟠 | IB Gateway read-only API | `ReadOnlyLogin=no` was insufficient; needed `ReadOnlyApi=no` |
| [Sep 10](#w2) | 🟡 | Silent git push failure | `2>/dev/null` hid the error for two days |

---

<a id="history"></a>
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

<a id="d-sep03"></a>
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

<a id="d-sep04"></a>
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

<a id="d-sep05"></a>
### Sep 5, 2026 (Friday)

**Changes Made:**
- Added holding days tracking to trades JSON, CSV, scorecard, and monitor
- Holding days auto-updates on every save for open trades
- Closed trades record final holding days permanently
- Added shadow tracking: monitors price 30 days after trade closes
  - Tracks peak/trough after exit, missed upside %, avoided downside %
  - Verdict: "Good exit" (<5% missed) or "Left money on table"
- Fixed monitor bug: now shows positions even without IBKR live price
- Fixed run_ares.sh: added PATH + DISPLAY so cron can connect to IB Gateway
- Restarted IB Gateway (had died since Thursday)

**Scan Results:**
- 9:30 PM scan: ✅ No signals. Regimes: 16 uptrend | 4 range | 12 downtrend
- 11:30 PM scan: ✅ Monitor — No live price (market closed, IBKR returned None)
- 1:30 AM scan: ✅ Monitor — No live price (same)
- 5:00 AM scan: ✅ No signals. Regimes: 14 uptrend | 18 range | 29 downtrend

**Signals Triggered:**
- None

**Open Trades:**
| Symbol | Strategy | Entry Date | Entry Price | Current Price | P&L % | Hold Days | SL | TS | TP |
|--------|----------|-----------|-------------|---------------|-------|-----------|----|----|-----|
| CNH | momentum_breakout | Sep 3 | $13.84 | $13.84 (daily) | 0.0% | 1 | $12.83 | $12.83 | $15.50 |
| PAYP | momentum_breakout | Sep 3 | $16.93 | $16.93 (daily) | 0.0% | 1 | $15.73 | $15.73 | $18.96 |

**Closed Trades:**
| Symbol | Strategy | Entry | Exit | Hold Days | P&L % | Reason |
|--------|----------|-------|------|-----------|-------|--------|
| — | — | — | — | — | — | — |

**Notes:**
- Prices unchanged — yfinance daily candle hasn't updated (no new trading day yet)
- IBKR live prices not available: (1) market closed for monitors, (2) run_ares.sh was missing PATH/DISPLAY — fixed
- IB Gateway had stopped since Thursday — restarted Saturday
- Shadow tracking ready — will activate when first trade closes
- Weekend: no scans Sat/Sun, next scan Monday 9:30 PM MYT
- **Plan: collect full week of data Mon-Fri, update logbook manually via iPad GitHub app**

---

<a id="d-sep0607"></a>
### Sep 6-7, 2026 (Saturday-Sunday) — Weekend

**Changes Made:**
- Built Athena backtesting engine (separate repo: github.com/Rcks888/Athena)
- Ran 5 backtest versions (V1-V5), 130 stocks, 5 years of data
- V1: Original params → PF 2.12, 52.5% WR
- V2: TP 18%, TS 10%, no trend_cont → PF 2.42
- V3: No fixed TP, trailing only → PF 3.00 🏆
- V4: Full portfolio sim $1K/5 slots → $1K→$4,046 (+32.5%/yr)
- V5: Realistic friction (slippage, commission, next-bar exec) → +22.8%/yr, -15.9% DD
- V5 universe sensitivity: tested different 100 mid-cap stocks → strategy works across universes ✅
- Upgraded Ares to V3 based on Athena findings
- V3 changes: TP 18% scale-out, TS 10%, disabled trend_continuation, signal queue, max 5 positions
- Cleared V2 trade data, fresh start for V3

**Athena Key Findings:**
| Metric | Optimistic (V4) | Realistic (V5) |
|--------|-----------------|----------------|
| Annual return | +32.5% | +22.8% |
| Max drawdown | -5.0% | -15.9% |
| Profit factor | 2.42 | 2.41 |
| 5yr growth | $1K→$4,046 | $1K→$2,774 |

**External Review:**
- Reviewer validated V5 as "credible baseline for live paper trading"
- Recommended: 4-6 weeks paper trading (40-60 closed trades) before any parameter tweaks
- No ML layer until real data collected

**Plan Forward:**
- Run Ares V3 paper trading for 4-6 weeks
- Target: 40-60 closed trades for statistical validation
- Compare live results vs Athena backtest
- Only then consider parameter tweaks or ML (Phase 3)

**Friction Tracking Added (per reviewer):**
- Entry slippage: 0.1% applied to buy price
- Exit slippage: 0.1% applied to sell price
- Commission: $1 per entry, exit, and scale-out (tracked individually)
- Next-bar execution flagged (signal day N → entry day N+1)
- pnl_after_costs field for true P&L after all friction
- All trades now directly comparable to Athena V5 backtest

**Observation Phase Rules (DO NOT CHANGE):**
- No RSI/confluence/trailing stop parameter changes
- No new filters or indicators
- No manual signal overrides
- No ML training until 40-60 trades collected

---

<a id="w2"></a>
### Sep 8-12, 2026 (Monday-Friday) — Week 2

**Changes Made:**
- Ares V3 first full week of live paper trading
- Implemented true next-bar execution (signal day N → buy at day N+1 open price)
- Added friction tracking: 0.1% slippage + $1 commission per trade logged
- Fixed capital from $10K → $1K, position sizing now respects 25% cash reserve ($150/trade)
- Built Telegram dashboard (build_dashboard.py) with portfolio, SL/TP/TS, scan summary
- Fixed monitor crash when IBKR returns no live price
- Loosened Finviz screener: >$2B mkt cap, >500K vol, >2x rel vol, >3% movers
- Added IB Gateway health check cron (restart_gateway.sh, runs 30min before first scan)
- Fixed signal_date vs entry_date tracking, holding days now counts from actual execution
- Cleared all V2 stale data, fresh V3 start

**Monday Sep 8:**
- 1:30 AM manual test: HAFN momentum_breakout detected, conf3, uptrend
- HAFN executed via next-bar: signal $9.22 (close) → entry $8.95 (open) = -2.94% better entry ✅
- 282 Finviz candidates → capped to 100 → 1 signal passed Ares V3 filter
- Telegram dashboard working (plain text, no parse errors)
- Monitor working (shows positions even without IBKR live price)
- IB Gateway read-only issue — may resolve during market hours tonight
- 1:31 PM scan: HAFN open (1/5), DYN signal detected → PENDING
- 11:30 PM / 1:30 AM monitors: HAFN showing, no IBKR live price
- 9:01 PM scan: DYN executed at $17.09 (mean_reversion, conf3, range). ABM signal → PENDING
- Signals: HAFN (open), DYN (opened), ABM (pending)
- Notes: First automated V3 cron run successful. Next-bar execution confirmed working.

**Tuesday Sep 9:**
- 1:31 PM scan: ABM executed at $45.86. Portfolio now 3/5 slots (HAFN, DYN, ABM)
- 11:30 PM / 1:30 AM monitors: All 3 positions showing, still no IBKR live price
- 9:01 PM scan: **DYN hit TP → 50% scaled out!** 🎉 TS now trailing at $17.27. PINS signal → PENDING
- Signals: DYN (scaled out), PINS (pending)
- Notes: First scale-out event on Day 1! DYN mean_reversion strategy working. Git push from VPS failing silently (token issue).

**Wednesday Sep 10:**
- Fixed IB Gateway read-only issue: added `ReadOnlyApi=no` to `/root/ibc/config.ini`
- IBC now auto-unchecks read-only checkbox on startup ✅
- Confirmed in logs: `Read-Only API checkbox is now set to: false`
- Fixed silent git push failure in `run_ares.sh` (was `2>/dev/null`, now logs errors)
- VPS git push synced — all 3 trades (HAFN, DYN, ABM) + PINS pending now on GitHub
- Updated README to V3: screens, project structure, schedule, roadmap, timeline
- Live price still shows "No live price" — expected, market closed at time of test (5:35 AM MYT)
- **Will verify live price works from tomorrow's 11:30 PM monitor logs**
- 1:32 PM scan: PINS executed at $20.00. Portfolio now 4/5 slots. 0 new signals.
- 11:30 PM / 1:30 AM monitors: Still "No live price" ⚠️ — IBKR gateway may have died at 11:45 PM daily restart
- 9:01 PM scan: 2 signals (DYN again — already open). 0 new pending. 1 slot remaining.
- Signals: PINS (opened)
- Notes: IBKR live price still not working during market hours. Gateway likely dies at daily restart and doesn't come back. Need to investigate `restart_gateway.sh` — it runs at 9:00 PM but gateway dies at 11:45 PM, so monitors at 11:30 PM and 1:30 AM have no gateway.

**Thursday Sep 11:**
- Upgraded VPS from 1GB → 2GB RAM ($12/mo). Zero downtime on disk.
- Added gateway restarts before each monitor (cron at :25 before :30 monitors)
- Root cause confirmed: IBKR kills gateway at 11:45 PM daily, old cron never restarted it for monitors
- New cron: gateway restart at 13:00, 15:25, 17:25 UTC → monitors at 15:30, 17:30 always have live gateway

**Verification checklist:**
- [x] IB Gateway running at 9:30 PM scan ✅ (cron logs confirm)
- [ ] Monitor shows live prices — ❌ still "No live price" (cron restart was BEFORE 11:45 PM kill)
- [x] Telegram alerts arriving ✅
- [x] RAM >300MB free ✅ (1.5GB free after 2GB upgrade)
- [x] Swap usage low ✅ (0B used)

- 9:30 PM scan: 4/5 slots, 0 new signals
- 11:30 PM / 1:30 AM monitors: No live price (gateway dead from 11:45 PM kill)
- 5:00 AM scan: 4/5 slots, 0 signals
- Signals: none
- Notes: Dashboard timestamps showing UTC labeled as MYT (bug found, fixed Sep 15)

**Friday Sep 12:**
- 9:30 PM scan: 4/5 slots, 0 new signals (quiet day)
- 11:30 PM / 1:30 AM monitors: No live price (same gateway timing issue)
- 5:00 AM scan: no new signals
- Signals: none
- Notes: End of Week 2. No trades closed yet. All 4 positions holding.

**Open Trades (End of Week 2):**
| Symbol | Strategy | Entry Date | Entry Price | Hold Days | SL | TS | TP | Status |
|--------|----------|-----------|-------------|-----------|----|----|-----|--------|
| HAFN | momentum_breakout | Sep 8 | $8.95 | 4 | $8.51 | $8.51 | $10.56 | Open |
| DYN | mean_reversion | Sep 8 | $17.09 | 4 | $14.76 | $17.27 | $18.80 | 50% scaled out |
| ABM | momentum_breakout | Sep 9 | $45.86 | 3 | $44.07 | $45.54 | $54.11 | Open |
| PINS | mean_reversion | Sep 10 | $20.00 | 2 | $18.83 | $18.83 | $22.00 | Open |

**Closed Trades (Week 2):**
None — all 4 trades still open.

**Weekly Summary (Week 2):**
| Metric | Value |
|--------|-------|
| Trades opened | 4 (HAFN, DYN, ABM, PINS) |
| Trades closed | 0 |
| Scale-outs | 1 (DYN 50% @ $19.17) |
| Signals triggered | ~8 |
| IBKR live price | ❌ Still not working |

---

<a id="w3"></a>
### Sep 14-18, 2026 (Monday-Friday) — Week 3

**Monday Sep 14:**
- 1:32 PM scan: 4/5 slots, 0 new signals. Dashboard holding days showing "2d" ⚠️ (bug — monitor correctly shows Day 6)
- 11:30 PM / 1:30 AM monitors: Still "No live price" — IBKR gateway issue persists despite cron restarts
- 9:01 PM scan: 3 signals — ECO (momentum_breakout, conf3, uptrend) → PENDING. NTSK + SLDE → QUEUED (4/5 slots full)
- Signals: ECO (pending), NTSK + SLDE (queued)
- Notes: First queued signals! System correctly queues when slots nearly full. IBKR live monitor still broken — need deeper investigation.
- Fixed dashboard holding days bug: was reading stale JSON field, now calculates live from entry_date
- Root cause of "No live price" found: IBKR kills gateway at 11:45 PM MYT, old cron restarted at 11:25 PM (BEFORE the kill) — useless
- Fix: moved monitor cron to AFTER the 11:45 PM kill: gateway restart at 12:00 AM, monitor at 12:10 AM MYT
- VPS upgraded from 1GB → 2GB RAM ($12/mo) to support Ares + Hermes simultaneously
- Confirmed IBKR connection works when gateway is alive (returns NaN during market closed = expected)
- Updated cron to avoid Hermes clash (shifted 16:05 → 16:10 UTC)
- Added reviewer's Claude integration design to ROADMAP.md (Phase 3)

**New cron schedule (MYT):**
| Time | Job |
|------|-----|
| 9:00 PM | Gateway restart |
| 9:30 PM | Full scan |
| 11:45 PM | IBKR kills gateway |
| 12:00 AM | Gateway restart (NEW — after kill) |
| 12:10 AM | Monitor — should show live prices ✅ |
| 1:25 AM | Gateway restart |
| 1:30 AM | Monitor |
| 5:00 AM | Full scan |

**Verification: check tomorrow's 12:10 AM and 1:30 AM monitors for live prices.**

**Tuesday Sep 15:**
- 9:32 PM scan: ECO executed at $75.93. **Portfolio now 5/5 slots FULL.** 0 new signals.
- Timezone fix confirmed working — timestamps now show correct MYT
- 12:10 AM / 1:30 AM monitors: Still "No live price"
- 5:01 AM scan: 2 signals. RVTY added to queue (now 3 queued: NTSK, SLDE, RVTY)
- Signals: ECO (opened)
- Notes: All slots full. Queue building up as designed.

**Wednesday Sep 16:**
- 🚨 **SECURITY INCIDENT**: Telegram bot token was in plaintext in public GitHub repo. Bot got hijacked — name changed to suspicious Russian/Burmese text with spam links.
- Fix: deleted compromised bot, created new bot, moved token to `/root/ares/.env` (chmod 600), added `.env` to `.gitignore`
- 🎯 **IBKR LIVE PRICE FIXED!** Root cause: missing `tzdata` package on VPS caused `ZoneInfoNotFoundError: 'US/Eastern'` which silently broke all IBKR data parsing
- Fix: `apt install tzdata` + `pip install tzdata`
- Also discovered paper account has no live streaming subscription — switched `get_live_price()` to use 1-min historical bars instead (free, works fine)
- 10:55 PM monitor: **First live prices ever!** ✅

**First Closed Trade:**
| Symbol | Entry | Exit | Hold Days | P&L % | P&L $ | Reason |
|--------|-------|------|-----------|-------|-------|--------|
| PINS | $20.00 | $18.83 | 5 | **-5.9%** | **-$8.86** | stop_loss |

**Live Portfolio (10:55 PM):**
| Symbol | Price | P&L | Day | TS | TP |
|--------|-------|-----|-----|----|----|
| HAFN | $9.76 | +9.1% | 8 | $8.78 ↑ | $10.56 |
| DYN | $17.55 | +2.7% | 8 | $17.27 | $18.80 |
| ABM | $50.40 | +9.9% | 7 | $45.54 | $54.11 |
| ECO | $85.89 | +13.1% | 1 | $77.30 ↑ | $89.59 |

- Notes: **Major milestone.** Stop-loss executed automatically for the first time. Trailing stops updating live (HAFN $8.51→$8.78, ECO $72.11→$77.30). ECO at +13.1%, approaching TP for 50% scale-out. 4/5 slots now, 1 free for queued signals.

**Lessons Learned:**
1. Never commit secrets — even to "personal" public repos. Bots get scraped within days.
2. Always check for silent library errors — the tzdata issue produced no visible error in the monitor, just NaN prices.
3. Paper accounts don't have live market data subscriptions. Historical bars are a free workaround.

**Thursday Sep 17:**
- Backfilled post-mortem on PINS: verdict `signal_failed` — never traded above $20.00 entry. RSI 29.1, range regime, confluence 3.
- Insight: a conf3 + oversold + range signal failed outright. High confluence alone is not sufficient in a range regime.
- 12:10 AM / 1:30 AM monitors: live prices working consistently ✅. Trailing stops climbing on all 4 positions.
- 5:01 AM scan: 103 screened, 0 signals. 4/5 slots, 3 still queued.

**Live Portfolio (1:30 AM):**
| Symbol | Price | P&L | Day | TS | TP | To TP |
|--------|-------|-----|-----|----|----|-------|
| HAFN | $9.77 | +9.2% | 8 | $8.80 ↑ | $10.56 | 8.1% |
| DYN | $17.82 | +4.3% | 8 | $17.27 | $18.80 | 5.5% |
| ABM | $50.91 | +11.0% | 7 | $45.82 ↑ | $54.11 | 6.3% |
| ECO | $86.25 | +13.6% | 1 | $77.72 ↑ | $89.59 | 3.9% |

**Realized to date: -$10.86** (1 closed, 0 wins)

**🐛 MAJOR BUG FOUND — Signal queue was write-only:**
- Symptom: PINS closed Sep 16 freeing a slot, but NTSK/SLDE/RVTY sat queued for days with no promotion
- Root cause: `load_queue()` was only used by `queue_signal()` (add), `expire_queue()` (drop old), and `print_scorecard()` (display). **No promotion logic existed at all.**
- Fix: added `promote_queue()` to `daily_report.py` at step [2b], right after `check_open_trades()`
- Promotion ranks by confluence DESC then age ASC, re-validates the top pick (drift ≤5%, RSI ≤90, price ≥ EMA20), promotes to pending → fills at next open
- Added `queue_max_drift_pct: 5.0` to strategy_params.json

**Dashboard fixes:**
- Open positions were showing `0.0%` — was reading `pnl_pct` which only populates on close. Now shows peak % from `peak_price`.
- Added `RECENT CLOSES` section showing last 3 closed trades with post-mortem verdict and peak %

**New feature — closed trade post-mortem:**
Auto-generated diagnosis on every close, stored in `trade['post_mortem']`:

| Exit Reason | Verdict | Meaning |
|-------------|---------|---------|
| take_profit | `as_expected` | Strategy worked as designed |
| trailing_stop | `partial_win` | Locked profit after peak |
| trailing_stop | `reversal` | Peaked but exited below entry |
| trailing_stop | `immediate_reversal` | Never moved up |
| stop_loss | `signal_failed` | Never traded above entry |
| stop_loss | `weak_follow_through` | Peaked <3% then died |
| stop_loss | `reversal_after_gain` | Peaked >3% then reversed |
| rsi_extreme | `emotional_exit` | Avoided blow-off top |

Captures `max_favorable_excursion_pct`, `entry_quality_pct`, `rsi_at_entry`, `regime_at_entry`, `confluence`, plus a `manual_note` field for own reflections.

**Known limitations of current promotion logic (pending review):**
1. Only top-ranked signal is validated — loop breaks when slots fill, so it is "first valid in rank order" not "best among all valid"
2. Ranking uses frozen `confluence` from signal time, not current data
3. Stale signals only detected at promotion time — if no slot frees, they expire silently at day 5 with no logged reason
4. **Monitors cannot promote.** PINS closed during the 10:55 PM monitor but next promotion chance was the 5:01 AM scan — a ~6 hour idle slot during live market hours

**Proposed two-level validation (awaiting reviewer opinion):**
- Level 1 (scans): validate ALL queued signals regardless of slots, drop stale with logged reason, re-rank on current data, persist to `logs/queue_ranked.json`
- Level 2 (monitors): if slot freed, re-validate top-ranked with fresh price → promote
- Main benefit is data generation: builds a record of *why* signals go stale, answering whether queued signals run away or fade. Feeds Phase 3 ML.
- Deliberately keeping ranking simple until 40-60 trades exist — avoid overfitting on a 5-trade sample.

**Git note:** push from home VPN kept failing (`RPC failed; curl 56/52`). Corporate network suspected. Workaround is to push from office, or scp the file to VPS and push from there.

**Queue redesign implemented (after review):**

Design correction: the earlier "two-level validation" framing was wrong — both levels would have run microseconds apart in the same scan process on identical data. Replaced with **two distinct passes**:

```
SCAN (9:30 PM, 5:00 AM MYT)
  [2]  check_open_trades()      closes positions, frees slots
  [2b] maintain_queue()         validate ALL → drop stale → re-rank → queue_ranked.json
       promote_queue("scan")    if slots free → promote top valid

MONITOR (12:10 AM, 1:30 AM MYT)
       close detected → promote_queue("monitor", use_live=True)
       reads ranked queue, re-validates against live IBKR price. No rescanning.
```

**Monitors can now promote** — closes the ~6h idle-slot gap. Kept deliberately narrow: monitors do not scan, they only promote from the existing ranked queue.

**Ranking (kept simple on purpose):** `confluence DESC → |drift| ASC → age ASC`. No weighted scoring formula — with 1 closed trade any weights would be fake precision. Real ranking deferred to the Phase 3 ML layer at 40-60 trades.

**Guards added:**
- **Execution-time re-validation is mandatory.** `promote_queue()` never trusts the stored ranking; it re-checks drift / RSI / EMA20 immediately before promoting. This is the real safety net, not clock heuristics.
- **Fill-window guard** — computes the actual next US regular open (13:30 UTC, skipping weekends) and skips promotion if the fill sits too far out (`pending_max_gap_hours: 48`):

| Now (UTC) | Next fill | Gap | Result |
|-----------|-----------|-----|--------|
| Wed 18:00 | Thu 13:30 | 19.5h | ALLOW |
| Thu 21:00 (= Fri 5am MYT scan) | Fri 13:30 | 16.5h | ALLOW |
| Fri 18:00 | Mon 13:30 | 67.5h | SKIP |
| Fri 21:00 | Mon 13:30 | 64.5h | SKIP |
| Sat 10:00 | Mon 13:30 | 51.5h | SKIP |

- **Queue size cap** `queue_max_size: 10`. On overflow keeps higher confluence then newer age, logs every eviction.
- **Permanent drop** on failed re-validation. A signal that ran +7% then pulled back is treated as a different setup — the scanner can rediscover it cleanly. Avoids zombie signals bouncing in and out of eligibility.

**New audit log — `logs/queue_events.jsonl`** (append-only, one JSON per line):
```json
{"timestamp":"2026-09-17T21:32:04","symbol":"NTSK","action":"dropped",
 "queued_at":"2026-09-14","confluence":2,"signal_price":41.2,
 "drift_pct":7.3,"rsi":88.1,"ema20_ok":true,"live_price":44.2,
 "drop_reason":"drifted +7.3% (max ±5.0%)"}
```
Actions tracked: `queued | kept | dropped | promoted | expired | evicted`

This is the point of the whole change. An observed queue is worth keeping; a silent queue is not. Over the next month this log answers a question the system could not previously answer: **do queued signals run away or fade?**

| Pattern | Interpretation | Action |
|---------|----------------|--------|
| Mostly drift up past +5% | Queue too slow, real opportunity cost | Consider raising `max_positions` |
| Mostly lose EMA20 / fade | Queue is protecting capital | Keep as-is, design validated |
| Mixed | Drift threshold mis-calibrated | Tune `queue_max_drift_pct` on evidence |

**Dashboard now shows the ranking**, not just symbol names:
```
📋 QUEUED (3) — ranked
  1. RVTY conf3 | drift +1.2%
  2. NTSK conf2 | drift -0.4%
  3. SLDE conf2 | drift +2.8%
```

**New params:** `queue_max_size: 10`, `pending_max_gap_hours: 48`

**Explicitly deferred:** ML ranking, multi-factor freshness scores, dynamic `max_positions` based on queue pressure. Not enough trades to justify any of it.

**Repo hygiene:** hardened `.gitignore` to block secrets (`.env`, `*.pem`, `*.key`, `config.ini`, `*token*`, `*secret*`), local docs (`docs/`, `notes/`, `PROPOSAL_*.md`) and caches. GitHub now carries code, config, README/ROADMAP/Logbook and trade history only.

**Capacity review — disk is a non-issue:**

Measured growth rates rather than guessing:

| Source | Rate | 1 year | 5 years |
|--------|------|--------|---------|
| Trade records | ~1.5 KB/trade | 100 trades = 150 KB | 750 KB |
| `queue_events.jsonl` | ~300 B/event, ~10/day | 1.1 MB | 5.5 MB |
| `data/ohlcv/` | ~4.3 KB/symbol | ~13 MB | 30 MB |
| Ares `cron.log` | ~30 KB/day | 11 MB | 55 MB |
| Hermes `cron.log` | ~100 KB/day (96 soak runs/day) | 36 MB | 180 MB |

Total ≈ **60 MB/year** against 35 GB free. Storage will never be the constraint. Trade logs in particular are the most valuable asset and cost nothing — never prune them.

Decided **against** logrotate. The only scenario it protects against is a runaway error loop spamming the log, and the dashboard now warns at `cron.log > 20 MB`, which makes that visible. No need for a background job to guard a risk that is now observable.

**🐛 BUG FOUND via the 736-file cache count — stale cache in queue validation:**

`ls data/ohlcv | wc -l` returned **736** files, far more than the ~103 currently screened. That exposed a correctness bug, not a space problem.

`load_stock()` had **no staleness check** — it only downloaded when the file was missing:
```python
if not filepath.exists():
    return download_stock(symbol)
df = pd.read_csv(filepath, ...)   # could be months old
```

`refresh_watchlist()` only refreshes currently-screened symbols. But `maintain_queue()` calls `load_stock()` on **queued** symbols. If a queued symbol dropped out of the screener, its cache went stale — so the drift / RSI / EMA20 gates would have evaluated against old data and produced wrong keep/drop decisions. This would have silently corrupted the queue dataset the whole redesign was built to collect.

**Fix:**
- `load_stock()` re-downloads when cache age > 20h (20h chosen so both the 9:30 PM and 5:00 AM scans get fresh data)
- Falls back to the stale cache with a printed warning if re-download fails, rather than crashing
- Added `prune_cache()` — deletes files that are both >30 days old **and** not screened, not held, not queued. Runs each scan after `refresh_watchlist()`.

**New feature — SYSTEM health block on dashboard:**

Reads `/proc/meminfo` and `shutil.disk_usage` directly, no shelling out. Degrades gracefully if a source is unavailable.

```
🖥️ SYSTEM
  ✅ RAM: 503/1900 MB (1400 free)
  ✅ Swap: 314 MB used
  ✅ Disk: 14/48 GB (28%)
  ✅ Cache: 736 symbols
  ✅ cron.log: 0.3 MB
```

| Metric | ⚠️ threshold | Rationale |
|--------|-------------|-----------|
| RAM available | < 300 MB | Below this the IBKR Gateway JVM starts swapping hard |
| Swap used | > 500 MB | Indicates sustained pressure, not just a past spike |
| Disk | > 80% | Standard headroom margin |
| Cache symbols | > 2000 | Signals pruning has stopped working |
| `cron.log` | > 20 MB | Runaway error-loop canary (replaces logrotate) |

**RAM is the real long-term constraint, not disk.**

Current VPS reading of 503 Mi used is suspiciously low against the expected steady state:

| Process | Expected |
|---------|----------|
| IBKR Gateway (JVM) | ~440 MB |
| Ares Python | ~100 MB |
| MT5 + Wine (Hermes) | ~200-300 MB |
| OS | ~100 MB |
| **Total** | **~840-940 MB** |

So at sample time something was idle — likely MT5 between soak cycles. Meanwhile **314 Mi of swap is in use**, which proves a genuine memory peak occurred that was never observed. Swap does not release on its own.

**Known gap:** the dashboard samples RAM only when it runs (9:30 PM, 5:00 AM). Hermes fires every 15 minutes, so the true peak almost certainly falls between samples. Two point-in-time readings per day cannot answer whether RAM is actually a problem.

Optional next step under consideration: peak RAM tracking — sample every 10 min into a small file, report the 24h maximum on the dashboard (~10 KB/day, one cron line). Deferred pending decision; it is another moving part.

Escalation options if RAM does become the binding constraint:

| Fix | Cost | Effect |
|-----|------|--------|
| Cap IBKR Gateway JVM heap (`-Xmx512m`) | Free | Stops JVM ballooning |
| Stagger cron so Ares and Hermes never overlap | Free | Partly done already (:10 vs :07) |
| `swapoff -a && swapon -a` | Free | One-time swap reclaim, run when idle |
| Upgrade 2 GB → 4 GB | +$12/mo | Real headroom |

**Lesson:** a metric collected for capacity planning (cache file count) found a correctness bug instead. Worth watching numbers even when the obvious concern turns out to be a non-issue.

**VPS memory cleanup performed:**

Disabled services a cloud droplet has no use for:
```bash
systemctl disable --now multipathd ModemManager
systemctl mask --now fwupd            # static unit — disable does not work, must mask
systemctl stop multipathd.socket      # socket kept re-triggering the service
systemctl mask multipathd.socket multipathd.service
```

Capped journald (was consuming 100 MB):
```bash
# /etc/systemd/journald.conf
SystemMaxUse=50M
RuntimeMaxUse=50M
```
`journalctl --vacuum-size=50M` freed 0 B — the 100 MB was memory-mapped runtime cache, not archived files on disk. The **restart** reclaimed it. The cap prevents future growth.

Reclaimed swap while market was closed and no cron was due:
```bash
swapoff -a && swapon -a
```

**Results:**

| Process | Before | After |
|---------|--------|-------|
| `systemd-journal` | 100 MB | **18 MB** |
| `multipathd` | 27 MB | gone |
| `fwupd` | 24 MB | gone |
| `ModemManager` | 10 MB | gone |
| Swap used | 313 MB | **0 B** |

Note: RAM *used* rose 476 → 713 Mi after the swap reclaim. That is correct and expected — the 313 MB of swapped-out pages came back into physical memory.

**Real memory footprint identified.** The largest consumer was an unidentified process named `main`. Resolved it with `readlink /proc/<pid>/exe` → `/usr/lib/wine/wine64`, i.e. **MT5 running under Wine**. Hermes is heavier than previously estimated:

| Process | RSS | Owner |
|---------|-----|-------|
| `main` (wine64 = MT5 terminal) | 245 MB | Hermes |
| `python.exe` (Wine Python MT5 bridge) | 82 MB | Hermes |
| `winedevice.exe` ×2 | ~32 MB | Hermes |
| `wineserver` | 17 MB | Hermes |
| **Hermes / MT5 subtotal** | **~376 MB** | |
| `Xvfb` | 22 MB | Ares (gateway display) |
| `systemd-journal` | 18 MB | OS |

Earlier estimate for MT5+Wine was 200-300 MB; actual is ~376 MB. Nothing to disable here — MT5 has to run.

**Projection with IB Gateway up** (it was down at sample time — no `java` process present):
```
713 Mi current + ~440 Mi JVM ≈ 1150 Mi used
1946 Mi total − 1150 ≈ ~790 Mi available
```
Above the 500 MB comfort threshold, so 2 GB should hold. Tighter than assumed, but workable.

**The 313 MB of swap was almost certainly inherited from the 1 GB era** before the upgrade — swap is never released automatically. The meaningful signal now is whether it *re-accumulates*:

| Swap over coming days | Interpretation | Action |
|-----------------------|----------------|--------|
| Stays near 0 | 2 GB genuinely sufficient | None |
| Climbs past ~200 MB | Real ongoing pressure | Cap JVM heap `-Xmx512m`, or upgrade to 4 GB |

The dashboard SYSTEM block surfaces swap daily, so this is now passively monitored rather than needing manual checks.

**Peak RAM tracking: deferred.** The projected ~790 Mi available sits above threshold, and swap is now a sufficient proxy for detecting pressure — if RAM peaks hard between dashboard samples, swap will rise and the dashboard will show it. Revisit only if swap re-accumulates.

> ⚠️ **Superseded — this reasoning was wrong.** Swap occupancy cannot serve as a proxy for peak RAM. Corrected in the *Memory pressure: PSI replaces swap occupancy* entry below. Left in place because the flawed inference is instructive: it conflated a cumulative state with an event detector.

**Still to verify:** `free -h` after tonight's 9:00 PM gateway restart, to confirm the ~790 Mi projection against a real reading with both systems up.

---

<a id="d-sep18"></a>
### Sep 18, 2026 (Friday)

<a id="b-signalloss"></a>
#### Silent signal loss: eight bugs behind one dashboard symptom

**Symptom.** Comparing two consecutive dashboards showed a pending signal simply disappear:

```
Sep 17, 09:32 PM   PENDING (1) RVTY -> next open      (no QUEUED section)
Sep 18, 05:01 AM   PENDING (1) SDGR -> next open      QUEUED (1) TMO
```

RVTY was not in the portfolio, not pending, not queued, and not in recent closes. Portfolio stayed 4/5, so it never filled. It was simply gone. Separately, the signals queued on Sep 14 — NTSK and SLDE — had also vanished with no visible explanation.

**`queue_events.jsonl` earned its keep on its first real outing.** The audit log added during the queue redesign gave a definitive answer instead of a guess:

```json
{"symbol":"NTSK","action":"dropped","queued_at":"2026-09-14","confluence":3,
 "drop_reason":"validation error: Cannot set a DataFrame with multiple columns
                to the single column vol_ratio"}
{"symbol":"SLDE","action":"dropped","queued_at":"2026-09-14","confluence":3,
 "drop_reason":"validation error: ... single column vol_ratio"}
{"symbol":"RVTY","action":"kept","drift_pct":3.95,"rsi":50.0,"ema20_ok":true}
{"symbol":"RVTY","action":"promoted","drift_pct":3.95,"live_price":145.73}
```

**The previously queued signals were dropped by a bug, not by design.** NTSK and SLDE were destroyed by an unhandled exception during validation — they were never evaluated at all, so the `drift_pct`, `rsi` and `ema20_ok` fields are `null`. RVTY validated cleanly, promoted to pending, and then evaporated in the fill path with no event of any kind.

Checked against current prices, all three were still inside both gates when they were destroyed:

| Symbol | Signal price | Price on Sep 18 | Drift | Age | Verdict |
|--------|-------------|-----------------|-------|-----|---------|
| NTSK | $17.00 | $17.21 | +1.2% | 4d | still valid — killed by bug |
| SLDE | $26.23 | $25.00 | −4.7% | 4d | still valid — killed by bug |
| RVTY | $140.19 | $146.73 | +4.7% | 3d | still valid — killed by bug |

None were legitimately filtered. Three confluence-3 signals lost to defects.

##### The unifying defect

Three separate code paths collapsed **"this signal is invalid"** and **"I could not evaluate this signal"** into the same outcome: permanent deletion. A transient yfinance hiccup was treated identically to a genuine trend break.

##### Root causes found

**1. Entries were filling at the previous session's open price.** 🔴

`daily_report.py` called `execute_pending_signals()` *before* `refresh_watchlist()`:

```python
execute_pending_signals()        # reads the cache
refresh_watchlist(all_symbols)   # refreshes it afterwards
```

`load_stock()` only re-downloads past a 20 h staleness window, but the gaps between scans are 16.5 h (21:00 → 13:30 UTC) and 7.5 h (13:30 → 21:00 UTC) — both under 20 h. So the fill never triggered a refresh, and `df.iloc[-1]['Open']` was **yesterday's open**, with nothing validating the bar date.

This is the most damaging finding: **every entry price recorded so far is one day stale**, which contaminates `entry_quality_pct` and the post-mortem verdicts for HAFN, DYN, ABM, ECO and PINS.

**2. Pending signals were silently deleted on any data failure.** 🔴 *(this killed RVTY)*

```python
df = load_stock(sig['symbol'])
if df is None or len(df) < 2:
    executed.append(sig)   # → pending.remove(sig) → gone forever
    continue
...
except Exception as e:
    print(f"❌ Execution failed — {e}")
executed.append(sig)       # ran even after the exception → gone forever
```

`download_stock()` swallows its own exceptions and returns `None`, so a single transient fetch failure permanently destroyed the signal — with no log entry, which is why the dashboard showed nothing.

**3. `load_stock()` had two return paths with different column shapes.** 🔴 *(this killed NTSK and SLDE)*

**This was a regression introduced by the stale-cache fix made earlier in this same work.**

```python
if age_h > max_age_hours:
    fresh = download_stock(symbol)
    if fresh is not None:
        return fresh          # RAW yfinance frame: MultiIndex columns
df = pd.read_csv(filepath, ...)  # flat columns
return df
```

Modern `yf.download()` returns MultiIndex columns such as `('Close','NTSK')`. On that path `df['Volume']` yields a DataFrame rather than a Series, so `add_indicators()` raised *"Cannot set a DataFrame with multiple columns to the single column vol_ratio"*.

Why only NTSK and SLDE? They were queued on Sep 14 and had since dropped out of the screener, so `refresh_watchlist()` no longer touched them and their cache aged past 20 h — the **only** condition that reaches the raw-download return. Screened symbols stay under 20 h and always took the CSV path. The first scan after the staleness fix deployed (13:31:19 on Sep 17) killed precisely the two queued-but-unscreened symbols.

A fix aimed at stale data created a new way to lose signals. The lesson is narrow and useful: **a function must return one shape.** A conditional early-return that produces a structurally different object is a trap, and here it stayed invisible because the failure was caught and converted into a routine-looking "drop".

**4. Queue validation also read stale data.** 🔴

`_validate_queued()` used the default 20 h window, so queued symbols outside the screener were judged on the previous day's close — and a permanent drop decision was made on it.

**5. "Could not check" was treated as "invalid".** 🔴

Both `return False, "no data"` and `return False, f"validation error: {e}"` fed into `maintain_queue()`, which treats every `False` as a permanent drop.

**6. The queue discarded `stdev_20`.** 🟠

`queue_signal()` never stored it and `promote_queue()` never passed it, so `open_trade()` fell back to `0.05` and **every queue-promoted trade received a generic 10% stop** instead of one scaled to its actual volatility — silently corrupting the dataset the queue redesign exists to collect.

**7. `stdev_20` fallback was a price, not a fraction.** 🟠

```python
stdev_20 = sig.get('stdev_20', entry_price * 0.05)
stop_loss = entry_price - (entry_price * stdev_20 * 2)
```
On a $100 stock the default yields `100 − 100·5·2 = −900` — a stop that can never trigger.

**8. Held/pending queue dedupe removed entries with no log.** 🟡

##### Fixes applied

| Area | Change |
|------|--------|
| `data_feed` | `download_stock()` flattens MultiIndex columns before caching |
| `data_feed` | `load_stock()` reduced to a **single parse path**, always reading the cached CSV |
| `data_feed` | Legacy MultiIndex-header CSVs cleaned by dropping NaN-`Close` rows |
| `daily_report` | `execute_pending_signals()` moved **after** `refresh_watchlist()` |
| `tracker` | Fill forces `max_age_hours=0` and asserts `df.index[-1].date() == today` |
| `tracker` | Fills retry up to `MAX_FILL_ATTEMPTS=3`, retained not discarded, every outcome logged |
| `tracker` | New `pending_max_age_days` guard (default 4) replaces unbounded pending lifetime |
| `tracker` | `_validate_queued()` prefixes fetch/exception failures `transient:` |
| `tracker` | Transient failures retained **unranked** for `MAX_CHECK_FAILURES=3` cycles — kept in `signal_queue.json` but excluded from `queue_ranked.json`, so unpromotable until a check actually passes |
| `tracker` | Queue validation reads with `max_age_hours=6`, below the 7.5 h scan gap |
| `tracker` | `stdev_20` and `vol_ratio` preserved through `queue_signal` → `promote_queue` |
| `tracker` | `stdev_20` fallback corrected to the fraction `0.05` |
| `tracker` | Held/pending dedupe now logged |

New event actions in `queue_events.jsonl`: `fill_retry`, `fill_dropped`, `check_retry`.

**Verified** the exact call chain that crashed:
```
NTSK OK 251 bars, close 17.21
SLDE OK 314 bars, close 25.0
RVTY OK 501 bars, close 146.73
```

**Decided not to restore the three lost signals.** All were still inside both gates, but there are no free slots (4 open + SDGR pending = 5/5), NTSK and SLDE expire at `queue_max_age_days: 5` the following day, TMO is already queued ahead of them, and hand-editing `signal_queue.json` is the same category of risk that produced this. The signals are gone; the bug class that ate them is not.

##### `risk_rules.json` is dead config

A grep for every key in that file returns **zero references anywhere in the codebase**. The file is headed *"YOUR RULES. Follow these when trading"* but nothing reads it, and its values contradict actual behaviour:

| Stated rule | Actual behaviour |
|-------------|------------------|
| `max_concurrent_positions: 8` | `max_positions: 5` from `strategy_params.json` |
| `max_position_pct: 0.10` | `(1000 × 0.75) / 5 = $150` = **15%** |
| `cash_buffer_pct: 0.30` | `cash_reserve_pct: 0.25` = **25%** |
| `max_weekly_loss_pct: 0.05` | **not implemented — no circuit breaker exists** |
| `stop_loss_multiplier: 2.0` | hardcoded `* 2` in tracker (matches by coincidence) |

Positions are **50% larger than the stated cap**, and there is **no weekly loss circuit breaker** despite one being written down. Academic on paper money; not academic with real capital in June 2027. A believed-in protection that does not exist is worse than no protection, because it changes behaviour.

**Resolved — file removed rather than corrected.** Two options were weighed:

| Option | Approach | Verdict |
|--------|----------|---------|
| A | Remove from the repo; `strategy_params.json` is the sole source of truth; aspirational rules move to ROADMAP | **Chosen** |
| B | Keep it, renamed `risk_rules.DRAFT.md` or `.unimplemented.json` with a `NOT LOADED BY CODE` header | Rejected — inferior |

Option B still leaves a plausible-looking risk file in the config directory. A header comment is exactly the kind of thing that gets skimmed past six months later. Removing it is unambiguous.

One correction to the plan: the suggested destination `docs/future_risk_controls.md` would have been **gitignored** — `.gitignore` blocks `docs/` as local-only. The file would have existed on one machine and silently vanished from the repo, which is close to the original failure mode. The controls went into `ROADMAP.md` instead, which is tracked.

Changes made:
- Deleted `config/risk_rules.json`
- README gained a *Configuration — single source of truth* section, with the rule for adding tunables: config key + `params.get()` default, never a hardcoded value, and anything unimplementable goes to ROADMAP rather than config
- ROADMAP gained *Unimplemented Risk Controls* — a current-vs-intended table, plus an implementation sketch for the weekly loss circuit breaker
- `stop_loss_multiplier` promoted from a hardcoded `2` to a config key. It was the only `risk_rules` value the code happened to honour, so it is now explicit and tunable. Behaviour unchanged at 2.0
- `pending_max_age_days` added to config; it had existed only as a code default, which is the same "value not visible in config" smell

**Position sizing deliberately left at 15%.** Reconciling it to the intended 10% means either raising `max_positions` to 8 or adding an explicit cap. Either changes sizing mid-dataset and splits the trade history into two incomparable regimes. With 1 closed trade the priority is a clean baseline, not optimal sizing. Recorded in ROADMAP as *before live*.

**The weekly loss circuit breaker is the one gap that genuinely matters.** Nothing currently halts trading after a losing streak. Sketched in ROADMAP with a note that a silent halt must be surfaced on the dashboard — otherwise a circuit-breaker trip is indistinguishable from a scan that found no signals, which would be its own debugging nightmare.

##### 53 lines of unreachable code in `open_trade()`

Found while wiring up `stop_loss_multiplier`: everything after `return 'pending'` was dead — the pre-pending immediate-open path, left behind when execution moved to next-bar fills.

It was not merely unused but **broken**: it referenced `entry_price`, `shares` and `commission`, none of which exist in that scope. Had control ever reached it, the result would have been `NameError`. Deleted.

Worth noting how it was found — not by reading the file, but by grepping for hardcoded `* 2` while implementing a config key. Dead code hides well from direct reading precisely because it looks plausible in isolation.

##### Repo hygiene — second credential incident

A GitHub Personal Access Token was found embedded in plaintext in the `origin` remote URL, present in `.git/config` on **all three** local repos (Ares, Athena, Hermes) and on both VPS repos (Ares, Hermes) — five copies in total.

Unlike the Telegram token this was **never published**, since `.git/config` is untracked, so exposure was local-disk only and there was no evidence of abuse. Severity was housekeeping, not emergency. But it was a live credential of unknown scope sitting in plaintext on an internet-facing box, it surfaced in ordinary command output (`git remote -v`), and a classic `ghp_` token typically carries full `repo` scope across *all* repositories — not just the one it is configured for.

**Migrated to SSH keys rather than rotating the token.** Rotation would only have reset the clock on the same design flaw; SSH removes the secret entirely, so there is nothing left to leak or rotate again.

| Step | Detail |
|------|--------|
| Laptop key | `ed25519`, comment `ricksonkang-laptop` |
| VPS key | `ed25519`, comment `ares-vps` — **separate key** |
| Remotes migrated | 5 total: 3 local (Ares, Athena, Hermes) + 2 VPS (Ares, Hermes) |
| Verified | `ssh -T git@github.com` on both machines; live `git pull` and `git fetch` on the VPS |
| PAT | Deleted at GitHub **after** both machines were confirmed working |

**Separate keys per machine matter:** the VPS can now be revoked independently if it is ever compromised, without disturbing laptop access.

**Sequencing was the one real hazard.** Revoking the PAT before SSH was verified would have broken the VPS mid-session — it auto-commits and pushes a report file on every scan, so the failure would have surfaced as silent cron errors rather than an obvious outage. Revocation was deliberately made the last step, after a successful `git pull` over SSH on the VPS.

Confirmed no residue: `grep -rln "ghp_" /root/ares/cron.log /root/Hermes/logs/ /root/ares/.env` returned nothing, so the token was never echoed into a log by a failing git command.

**Pattern across both incidents.** Two credential exposures in one project, from the same underlying habit: **pasting a secret into a config file because it was the fastest way to make something work.** The Telegram token went into `run_ares.sh` to get alerts working; the PAT went into the remote URL to avoid password prompts. Both were expedient, both created a permanent liability. The structural answer is not "be careful with secrets" but **choose mechanisms that have no secret to place** — SSH keys instead of tokens, `.env` outside the repo instead of inline values.

##### Takeaways

- **Distinguish "invalid" from "unknown".** Collapsing them into one outcome destroyed three signals across three independent code paths. Any validation that can fail for infrastructure reasons needs a third state.
- **One function, one return shape.** The `vol_ratio` regression survived only because the malformed frame was caught downstream and reported as a routine drop.
- **An audit log is worth more than the feature it audits.** `queue_events.jsonl` cost a few lines and turned "where did RVTY go?" from unanswerable into a five-second grep. The fill path had no logging, which is exactly why RVTY's disappearance was a mystery while NTSK and SLDE's was not.
- **Order of operations is silent.** Nothing errored when fills ran before the data refresh; entries were merely wrong, every single time, for as long as the system had been running.

<a id="b-psi"></a>
#### Memory pressure: PSI replaces swap occupancy

Correction arriving from the Hermes thread, which tested the assumption this logbook recorded earlier and found it does not hold.

**The flawed claim.** Peak RAM tracking was deferred on the reasoning that swap acts as a sufficient proxy: if RAM spiked between dashboard samples, swap would rise and the dashboard would report it. That inference is wrong.

**The measurement that disproves it.** On Hermes, swap sat at 284–308 MB while **1486 MB of RAM was free**. That is not memory pressure. At the default `vm.swappiness=60` the kernel evicts idle anonymous pages even with gigabytes free, and headless Wine/MT5 holds many such pages — GUI code paths, chart rendering, dialog resources that never execute. Once evicted they are never faulted back in, so swap fills and stays filled.

After a manual swap clear the refill was **two-phase**: 0 → 284 MB rapidly, then 284 → 308 MB over 21 hours, roughly 1 MB/hour. Thrashing looks like tens of MB per *minute*, not per day.

**Why the proxy fails.** Equilibrium restoration and a genuine RAM peak produce the *same occupancy curve*. Swap therefore tells you eviction happened at some point — not that pressure is happening now, and not what caused it. The old dashboard threshold (`swap > 500 MB`) would either alarm on inert history or stay silent through a real spike that resolved between two samples.

**The category error:** treating a cumulative state as an event detector.

| Signal | Measures | Catches a spike between samples? |
|--------|----------|----------------------------------|
| `MemAvailable` | State, sampled | No — structurally cannot |
| `swap_used` | Cumulative occupancy | No — cannot distinguish cause |
| `pswpout` delta | Rate | Yes, while it is happening |
| PSI `full` delta | Cumulative stall time | **Yes, even after it ends** |

**Fix — Pressure Stall Information.** `/proc/pressure/memory` reports a `full` line measuring time in which *every* runnable task was blocked on memory reclaim. Its `total=` field is cumulative since boot, which is the property that matters: a delta between two samples captures a spike that began **and ended** between them.

Dashboard now reports three distinct things instead of one ambiguous one:

```
🖥️ SYSTEM
  ✅ RAM: 913/1967 MB (1054 free)
  ✅ Swap: 298 MB (inert — 0 MB paged out in 7.5h)
  ✅ Stall: 0 ms in 7.5h
```

- **Swap** line qualified by the `pswpout` delta — high occupancy with no recent paging now reads `inert` rather than warning
- **RAM** warns only on the compound condition `swap > 200 MB` **and** `free < 400 MB`, so an inert high-water mark no longer alarms
- **Stall** warns above 1 s of full-stall time between runs
- Degrades to a no-op if PSI is unavailable; state carries in `logs/psi_state.json`

**This retires the deferred peak-RAM question properly** rather than by proxy. No extra cron job was needed — the cumulative counter does the work that per-minute sampling would have.

##### Two pieces of bad advice corrected

**1. `swapoff -a && swapon -a` was wrong and is now documented as a don't.** It force-faults every evicted page back into RAM simultaneously — a genuine spike, with the JVM resident — and the kernel then re-evicts the same idle pages over the following hours. It manufactures the very condition it appears to diagnose. If the occupancy is unwanted, `sysctl vm.swappiness=10` reduces the cause instead.

**2. Threshold shape.** Hermes traced two separate false-positive alert storms to the same root cause: **alerting on a state rather than a transition or a rate.** Anything phrased `if value < threshold` re-fires on every sample for the whole duration of a dip, rather than once on crossing. The Ares `RAM` check had exactly this shape and was the same class of defect as the swap threshold — now a compound condition, which at least requires two independent things to be true before it speaks.

##### Confirmation from the other direction

The *"invalid vs couldn't evaluate"* bug class found in Ares was checked against Hermes and **five instances were found there**, failing in the more dangerous direction — **fail-open rather than fail-closed**. The exact analogue: a broker query failure returned an empty position list, and the risk manager read `len(positions) >= max_open` → `0 >= 1` → `False` → approved. *"I could not check for open trades"* silently became *"there are no open trades."* Harmless while alert-only; it would have permitted double entry on the first day of auto-execution.

Ares failed closed (signals were destroyed), Hermes failed open (gates were bypassed). Same root cause, opposite blast radius. **Fail-closed loses opportunities; fail-open loses money.** Worth remembering which direction each system errs in.

The append-only event log recommendation was also adopted there, and immediately surfaced a three-day silent-no-signal mystery — plus fixed a latent whole-file-rewrite risk in their trade history. The audit-log pattern has now paid for itself twice in two systems.

---

<a id="d-sep21"></a>
### Sep 21, 2026 (Monday)

<a id="b-scaleout"></a>
#### A winning trade made realised P&L worse

**Symptom spotted from the dashboard**, not from the logs:

```
Sep 18, 09:32 PM   Trades completed: 1           Realized: -$10.86
Sep 19, 05:02 AM   Trades completed: 2 (W: 1/2)  Realized: -$12.15
                   ✅ DYN 0.9% (trailing_stop) partial_win | peak 12.29%
```

DYN closed as a **win** — flagged ✅, counted in `W: 1/2` — yet the realised total moved **$1.29 further into the red**. A winning trade cannot make realised P&L worse. That contradiction was the whole tell.

**Root cause: the scale-out gain was never booked.**

DYN scaled out 50% at $19.17 on Sep 9, locking in **+$9.07**. The handler reduced `trade['shares']` from 8.72 → 4.36 and stored `scale_out_price`, but the realised dollars went into a local variable that was printed and then discarded:

```python
pnl_pct = (scale_price - trade['entry_price']) / trade['entry_price'] * 100
print(f"SCALED OUT 50% at ${scale_price:.2f} (+{pnl_pct:.1f}%)")   # printed, then lost
```

`_close_trade()` therefore measured P&L on the **remaining 4.36 shares only**:

```
pnl_raw = (17.2527 − 17.09) × 4.36        = +$0.71
pnl_after_costs = 0.71 − $2.00 commission = −$1.29
```

Which is exactly the observed move. The arithmetic confirmed the hypothesis before a line was changed.

| DYN | Booked | Correct |
|-----|--------|---------|
| Scale-out tranche | **unbooked** | +$9.07 locked |
| Gross P&L | +$0.71 | +$9.77 |
| Commission | $2.00 | $3.00 |
| **Net** | **−$1.29** | **+$6.77** |
| Percent | +0.95% | **+6.56%** |

**An $8.06 error on a single trade.**

##### Why this one mattered more than its size

`scale_out: true` is enabled, so **every** winner that scaled out was being understated by precisely the amount the scale-out locked in. The mechanism working as designed was being recorded as a failure.

Left in place, the Phase 3 ML at 40–60 trades would have examined the data and concluded that scaling out destroys returns — exactly backwards. This is the most dangerous class of bug for this project: not a crash, not a lost signal, but **a silent bias in the training data**. It would have produced a confident, well-evidenced, wrong conclusion.

##### Two further bugs found alongside it

**Commission was overwritten, not accumulated.** `_close_trade()` set `total_commission = entry + exit`, discarding the scale-out commission recorded earlier. Costs under-counted by $1 on every scaled trade — partially masking the larger error above.

**Three fields gave three verdicts on the same trade.** `wins` counted gross `pnl` (+$0.71 → win), `Realized` summed `pnl_after_costs` (−$1.29 → loss), and the ✅ icon used `pnl_pct` (+0.95% → win). A trade whose gain is smaller than its commissions is not a win. Classification now uses net P&L everywhere, so the win count and the realised total cannot disagree again.

##### Fixes

| Area | Change |
|------|--------|
| Scale-out | Stores `scale_out_shares`, `scale_out_pnl`, `scale_out_pnl_pct`; log line now reports the locked dollar amount |
| `_close_trade` | Sums the sold tranche and the remainder |
| `_close_trade` | Accumulates commission instead of overwriting it |
| `pnl_pct` | Blended over the original cost basis so percent and dollars agree — identical to the old formula for trades that never scaled out |
| Dashboard + scorecard | Win/loss classified on net P&L |
| `repair_scaled_pnl.py` | Rebooks already-closed trades from stored fields; dry run by default, backs up before writing |

The repair was recoverable only because `scale_out_price` and `original_shares` were being stored even though they were unused — `sell_shares = original_shares − shares` reconstructs the tranche exactly. **Storing more than you currently consume paid off.**

##### Corrected record

```
DYN   +6.56%  +$6.77  trailing_stop  partial_win
PINS  −5.9%   −$10.86 stop_loss      signal_failed
Realised: −$4.09   (was −$12.15)
```

1 win / 1 loss with a small net loss — not the two-trade bloodbath the dashboard implied.

##### PSI validated on its first real reading

```
✅ Swap: 310 MB (inert — 0 MB paged out in 7.5h)
✅ Stall: 128 ms in 7.5h
```

Exactly the case the Hermes thread predicted: high swap occupancy, **zero** actual paging, negligible stall. The old `swap > 500 MB` threshold was heading toward a warning on inert history; the new detector correctly reports nothing wrong. 128 ms of stall across 7.5 hours is noise.

##### Takeaways

- **Arithmetic that cannot be true is the cheapest bug detector there is.** No log, trace, or debugger was needed — "a win made the total worse" is self-contradictory, and the $1.29 delta then matched the remaining-shares calculation on the first try.
- **Silent data bias is worse than a crash.** A crash announces itself. This produced plausible numbers that would have taught the ML the opposite of the truth.
- **Compute-print-discard is a recurring shape.** The value existed, was formatted for a human, and was never persisted. Same family as the earlier `risk_rules.json` problem: something that looks accounted for but isn't.
- **Store fields before you need them.** The repair was only possible because unused fields were already being written.

---

<a id="data-status"></a>
#### Data status — process validation, not strategy proof

Recording this explicitly so that no edge conclusion is drawn from the current sample later on.

**Treat the sample as mechanically improving but not yet clean enough for edge conclusions.** The scoreboard validates that the machinery works. It does not yet say anything trustworthy about whether the strategy has an edge.

Known contaminants, in order of how much damage they do to conclusions:

| # | Contaminant | Affects | Repairable |
|---|-------------|---------|------------|
| 1 | Queue-promoted trades got `stdev_20 = 0.05` instead of real volatility | **Exit behaviour** — stop distance was wrong, so the trade exited where the strategy would not have | ❌ Never — the price path cannot be re-run |
| 2 | Lost queue/pending signals (NTSK, SLDE, RVTY) | **Selection bias** — the sample is missing trades that should exist | ❌ Never |
| 3 | Stale entry-open fill path | `entry_price`, and therefore `pnl`, `pnl_pct`, `entry_quality_pct`, post-mortem verdicts | ⚠️ Measurable, not correctable |
| 4 | Scale-out P&L underbooking | `pnl`, `pnl_after_costs`, win classification | ✅ Repaired for DYN |

**Contaminant 1 is the worst and is the easiest to overlook**, because unlike the accounting bugs it changed *behaviour* rather than reporting. A wrong stop distance decides whether a trade survives a dip. No amount of recomputation recovers what would have happened under the correct stop.

**Contaminant 3 is not uniform**, which is better than first assumed. The fill read the cache written by the *previous* scan:

| Fill scan | Previous refresh | Bar it read | Result |
|-----------|------------------|-------------|--------|
| 9:30 PM MYT (13:30 UTC) | 21:00 UTC **yesterday** | yesterday's complete bar | ❌ one session stale |
| 5:00 AM MYT (21:00 UTC) | 13:30 UTC **today** | today's bar | ✅ correct |

So roughly half of the fills were right, depending on which scan executed each pending. `audit_entry_prices.py` compares each recorded `entry_price` against the true open for its `entry_date` and reports the error per trade, so the sample can be triaged on measurement rather than assumption.

**What the current data is good for:** confirming next-bar execution works, that scale-out triggers, that trailing stops fire, that the queue promotes, that monitors detect closes, that alerts deliver. All process validation — and all of it genuinely established.

**What it is not good for:** win rate, profit factor, average win/loss, strategy comparison, parameter tuning, or any ML training. The clean baseline effectively starts from the Sep 18 fix set, not from Sep 8.

**Practical consequence:** the 40–60 trade threshold for the Phase 3 ML work should count **from the first post-fix entry**, not from the first trade ever. Counting contaminated trades toward that threshold would just deliver a confident conclusion sooner, drawn from bad data.

<a id="entry-audit"></a>
#### Measuring the stale-fill damage instead of assuming it

`audit_entry_prices.py` compared every recorded `entry_price` against the true open for its `entry_date`:

| Symbol | Entry date | Recorded | True open | Error | Verdict |
|--------|-----------|----------|-----------|-------|---------|
| ABM | Sep 9 | $45.86 | $50.60 | **−9.46%** | STALE |
| ECO | Sep 15 | $75.93 | $79.70 | −4.83% | STALE |
| HAFN | Sep 8 | $8.95 | $8.81 | +1.49% | STALE |
| PINS | Sep 10 | $20.00 | $18.25 | **+9.48%** | STALE |
| DYN | Sep 8 | $17.09 | $17.08 | −0.04% | clean |
| SDGR | Sep 18 | $29.35 | $29.32 | +0.00% | clean |

**4 of 6 stale, dispersion ±9.5% — wider than the stop distance itself.**

Ruled out as a corporate-action artifact before acting: ABM's last split was 2002 and its dividend is $0.29 quarterly with none in the window. The $45.86 was genuinely Sep 8's open used for a Sep 9 fill, amplified by a real earnings gap.

##### The distortion was noise, not bias — which is why it hid

HAFN was understated (+13.1% → +14.7% once corrected) while ABM and ECO were flattered. A consistent bias would have shown up as implausibly good results. Random error in both directions just looks like ordinary variance, so nothing drew attention to it for two weeks.

##### The hypothesis about which scan was stale was wrong

Predicted the 9:30 PM scan would produce stale fills and the 5:00 AM scan clean ones. The data disagreed. Cross-referenced against the fill times in this logbook:

| Trade | Filled by | Result |
|-------|-----------|--------|
| DYN | 9:01 PM **cron** | ✅ clean |
| SDGR | cron | ✅ clean |
| HAFN | 1:30 AM **manual test** | ❌ stale |
| ABM | 1:31 PM **manual** | ❌ stale |
| PINS | 1:32 PM **manual** | ❌ stale |

Contamination tracks **manual off-schedule runs during week one**, not which cron slot. Ad-hoc runs at 1:30 PM MYT read caches written the previous night. **The production path was sound; the bad data came from testing it by hand.** Worth remembering the next time a manual run is used to "just check something" — off-schedule execution reads state the schedule never would.

##### Why the open positions had to be re-based, not merely flagged

Every field derived from `entry_price` inherited the error:

```
shares      = position_size / entry_price
stop_loss   = entry_price − entry_price × stdev_20 × sl_mult
take_profit = entry_price × (1 + tp_pct)
peak_price  initialised to entry_price
```

`stop_loss` is the one that matters, because it is **live behaviour rather than reporting**:

| ABM | Recorded | Corrected |
|-----|----------|-----------|
| Entry | $45.86 | $50.65 |
| Stop loss | $44.07 | $48.67 |
| Stop vs current price | **10.6% below** | 1.3% below |
| Unrealized | **+8.5%** | **−1.7%** |

ABM was carrying roughly **2.7× its intended risk** while the dashboard called it a winner. Accounting errors can be left flagged and excluded; a wrong stop keeps making new decisions every day the position stays open.

`repair_stale_entries.py` re-bases open positions only, preserving the stop and target as *fractions* of entry so the volatility scaling survives — only the base price moves. Trailing stops were left to their own logic, since `peak_price` is real market data and therefore uncontaminated: HAFN and ECO kept theirs because `peak × 0.90` still cleared the corrected stop, while ABM's rose to meet its new `stop_loss` floor.

**Closed trades were deliberately not touched.** DYN and PINS exited at prices the wrong stops produced. Rewriting them would invent history that never occurred.

##### The trade-off that was accepted

Re-basing makes the *risk* correct and leaves the *history* fictional — ABM now shows a 3.9% stop it never actually had. That is why every corrected trade keeps `entry_price_original` and carries `contaminated: true`. **The tag is what keeps the dataset honest, not the numbers.** Correcting data without marking it corrected would have been the worse outcome of the two.

Net effect on the dashboard: unrealized fell about $21, none of it a loss — just the removal of a number that was never real.

##### Takeaways

- **Measure contamination, don't estimate it.** The blanket assumption was "all entries suspect". The measurement said two were clean, four were not, and one had flipped sign — which changed what needed doing.
- **Distinguish wrong *records* from wrong *behaviour*.** Three of the four bugs this week were accounting and could be flagged and excluded. The stale entry reached into `stop_loss` and kept acting, so it had to be repaired.
- **Manual runs are not a safe way to test a scheduled system.** Every stale fill came from one.
- **Rule out the boring explanation first.** Checking ABM for a split cost one command and would have invalidated the entire repair had it come back positive.

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


---
