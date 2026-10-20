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
| [Sep 22](#d-sep22) | 🧭 **Findings** | **The backtest stopped being evidence.** Athena V1–V5 retracted for look-ahead; ~30 parity mismatches; Run A′ measures −5.58% against SPY +85.66%; momentum substitution rejected; **Run B fails and the defect proves load-bearing**. Strategy question closed. Ares untouched, `clean_v3` demoted |
| [Sep 23](#d-sep23) | 📐 Analysis | **The 10% trail cannot protect profit below +11.1%.** ECO's `trailing_stop` loss proved to be arithmetic, not a bug. HAFN closed +3.9% from a +15.53% peak. TP has never once been reached |
| [Sep 24](#d-sep24) | 🐛 Fixes | **Two unreachable post-mortem branches** — 4th string-mismatch defect. Giveback promoted from prose to fields and guarded. Clean-sample progress showed 0 while **4 of 5 slots** carried clean trades |

### Major bug index

Direct links to the significant defects, newest first.

| Date | Severity | Bug | Impact |
|------|----------|-----|--------|
| [Oct 20](#fill-before-decision) | 🔴 | Intra-session admissions filled at that session's open, which preceded the decision | **11 of 21 fills** booked at an unattainable price; 9 of 14 `clean_v3` entries affected (−0.79% to +3.49% against the decision price). Recorded, not fixed (FROZEN) |
| [Sep 21](#tmo-nearmiss) | 🟠 | Missing `stdev_20` silently replaced by 0.05 | TMO would have opened trade #1 of `clean_v3` at **3.2x intended risk** under a clean label. Caught before the fill |
| [Sep 24](#b-postmortem) | 🟠 | `_build_post_mortem` reads `rsi_extreme`; producer emits `emotional_extreme` | 2 of 5 branches unreachable; `emotional_extreme` and `mean_reversion_complete` recorded as `other` with no diagnostics. **4th string-mismatch defect** |
| [Sep 24](#b-postmortem) | 📐 | `gave_back_pct` unguarded for trades that never rose | PINS reported +5.90% "giveback" equal to its loss. Would have inflated any average — same shape as the Sep 21 scale-out bias |
| [Sep 22](#live-defects) | 🔴 | Queue gates read `RSI` and `EMA_20`; indicators emit lowercase `rsi` and no `EMA_20` | Both promotion gates **inert**. Live promotion is drift-only. Third inert-gate defect in the project |
| [Sep 22](#live-defects) | 🔴 | Swing loop stops at `len − window − 1`, live reads `len − 1` | All four divergence columns permanently **`False`** in production. Run B showed this was **load-bearing in the strategy's favour** |
| [Sep 22](#live-defects) | 🔴 | Position size from `starting_capital` as a constant, no balance check | Unfundable orders as equity declines — 5 × $149 = $745 at $427 equity. **Only defect with a hard deadline** |
| [Sep 21](#sample-split) | 📐 | Dataset split into pre_clean and clean_v3 | Official edge metrics now filter on `sample_phase`. Clean count is **0**, not 2 — both candidates failed the rules |
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

<a id="sample-split"></a>
#### Dataset split: pre-clean history vs clean sample

**Weeks 1-3 retained as process-validation history. Official edge measurement begins from the first clean scheduled fill after the Sep 18-21 fixes. Contaminated trades remain labelled and excluded from edge/ML metrics.**

No reset, no deletions, no strategy parameter changes. The history stays exactly where it is and keeps its original values; what changes is that every trade now carries a label saying whether it may enter a performance figure.

##### A trade counts as clean only if all four hold

1. Filled by the scheduled production path, not a manual off-schedule run
2. Entered after the Sep 18-21 fixes
3. Not marked `contaminated: true`
4. Uses current accounting end to end - entry, stops, scale-out, close, net P&L

##### Fields added to every trade record

| Field | Meaning |
|-------|---------|
| `sample_phase` | `pre_clean` or `clean_v3` |
| `contaminated` | boolean gate |
| `contamination_reasons` | list, e.g. `stale_entry`, `manual_fill`, `scaleout_pnl_pre_fix` |
| `fill_source` | `scheduled` or `manual`, stamped at fill time |
| `fill_detect` | `env` or `tty_inferred` - how `fill_source` was determined |
| `fill_run_ts` | timestamp of the run that produced the fill |
| `entry_price_original` | retained wherever a position was re-based |

##### Why the labelling happens at fill time, not afterwards

The audit established that a manual fill cannot be reconstructed from a completed record - `entry_date` stores a date and no time, so there is nothing to distinguish a 1:31 PM ad-hoc run from a 9:01 PM cron run. Rather than infer it later, `open_trade()` now stamps the fill context as it happens.

`ARES_SCHEDULED=1` is authoritative when set. Absent it, the fallback is whether stdin is a TTY: cron has none, an interactive shell does. **`fill_detect` records which method was used**, so an inferred classification is never silently mistaken for a verified one.

##### The queue stdev bug turned out to be detectable after the fact

The fallback was `stdev_20 = 0.05` against `stop_loss_multiplier = 2.0`, which places the stop at *exactly* 10.00% below entry. Real volatility virtually never lands there - the measured trades sit at 3.90%, 4.92% and 5.03%. So `from_queue` combined with a 10.00% stop distance is a **signature**, not a suspicion, and `queue_stdev_fallback` is assigned on evidence.

##### Consequence: the clean count is zero, not two

The working assumption had been that DYN and SDGR would survive as the clean baseline. Applying the rules as written, neither does:

| Trade | Verdict | Why |
|-------|---------|-----|
| DYN | `pre_clean` | Entered Sep 8; scaled out under the pre-fix booking; fill path unverifiable |
| SDGR | `pre_clean` | Entered **on** the fix day - the record stores no time, so a post-deployment fill cannot be proven |

`CLEAN_FROM` was therefore set to **Sep 19**, the day after the fixes, rather than Sep 18. A trade entered on the fix day itself is unprovable either way, and **unprovable is treated as contaminated.** Choosing the generous boundary would have seeded the official sample with exactly the kind of trade this split exists to keep out.

So the official scoreboard reads zero closed trades and states the date it starts from. That is the correct and honest output, and it is preferable to a win rate computed from one trade.

##### Where the filter lives

`engine/sample.py` holds the predicate, the phase constants and the metrics function. The scorecard, the dashboard, and any future ML training import it rather than reimplementing the test. Three copies of `is_clean()` would eventually disagree, and the disagreement would surface as an unexplained discrepancy between two reports months from now.

`metrics()` returns `None` for an empty sample rather than zeros, and leaves profit factor undefined when nothing has lost yet. A displayed `0.0%` win rate is indistinguishable from a real result; an absent one is not.

##### What was deliberately not done

**Closed-trade prices were not rewritten.** DYN and PINS keep the entries and exits they actually had, wrong as those were. Re-basing them would have improved the historical figures while destroying the audit trail - and those figures are excluded from edge metrics anyway, so the only thing such a rewrite could achieve is making old results look better than they were.

<a id="tmo-nearmiss"></a>
#### A diagnostic aimed at one question found a live problem elsewhere

`analyze_queue_bias.py` was written to test whether queue drift-expiry discards the fastest movers. On its own question it returned **UNDERPOWERED** - 7 queue events in three weeks, zero drift-bearing drops. Correct answer, no information.

But the drop-reason tally showed something unrelated:

```
2026-09-17T13:31  dropped   NTSK   validation error: Cannot set a DataFrame...
2026-09-17T13:31  dropped   SLDE   validation error: Cannot set a DataFrame...
2026-09-17T13:31  kept      RVTY
2026-09-17T13:31  promoted  RVTY
2026-09-17T21:01  queued    TMO
2026-09-18T13:31  kept      TMO
2026-09-18T21:01  kept      TMO
```

##### The historical part closed cleanly

Both drops were pandas MultiIndex crashes inside `_validate_queued`, dated **Sep 17** - before the fix - and they are NTSK and SLDE themselves rather than additions to the known losses. `load_stock` and `download_stock` both flatten MultiIndex now, so the mechanism is already repaired.

Worth noting RVTY was **kept and promoted successfully**, then still never became a trade. It reached `pending` and was lost there. So the week-1 signal loss was two independent failures stacked - a validation crash and a separate pending-path loss - not one bug with three victims.

##### The live part: TMO would have poisoned trade #1

TMO had been sitting in the queue since Sep 17 with `stdev_20: None`, from before `queue_signal()` stored the field. With 4/5 slots occupied and one free, tonight's scan was likely to promote it.

| TMO | Fallback | Real |
|-----|----------|------|
| `stdev_20` | 0.05 fabricated | **0.0155** |
| Stop distance | 10.00% | **3.11%** |
| Stop price | $592.49 | $637.86 |
| Risk on $149 | ~$14.90 | ~$4.60 |

**3.2x the intended risk** - and because `open_trade()` stamps `sample_phase` from fill context without inspecting the payload, it would have been labelled `clean_v3`. Trade #1 of the official sample, carrying a fabricated stop, under a clean label. Exactly the outcome the dataset split exists to prevent, arriving through a route the split did not cover.

TMO is Thermo Fisher at $658 - a low-volatility large cap. The 5% default was not merely arbitrary, it was **wrong by a factor that varies per symbol**. A fixed default cannot be conservative for every name; it is simply wrong in an unpredictable direction.

##### Fixes

`or 0.05` no longer substitutes silently. A missing `stdev_20` is an absence, not a 5% volatility stock. The fallback still applies so a fill is never lost over it, but `stdev_fallback` is recorded, a warning prints, and the trade is forced to `pre_clean`.

`sample.classify()` preserves a `stdev_fallback` recorded at fill time. The existing 10.00%-stop signature can only *infer* the fallback, and that fingerprint is erased if a trailing ratchet or a re-base later moves the stop. **Recorded evidence does not decay; inferred evidence does.** Same reasoning as retaining `entry_price_original`.

`repair_queue_stdev.py` recomputes the value through `load_stock` then `add_indicators` - the path the scanner itself uses - rather than reimplementing `Close.pct_change().rolling(20).std()` where the two could drift apart.

##### Takeaways

- **Instrumentation pays off sideways.** The diagnostic answered nothing about drift bias and still caught a live defect hours before it would have committed capital. Reason enough to build the instrument even when the question it targets is not yet answerable.
- **First bug this project caught before the damage, not after.** Every earlier one - stale fills, lost signals, scale-out underbooking - was archaeology. This was prevention, and the difference came entirely from having queue events logged.
- **A silent default is a third instance of the same pattern.** The discarded scale-out value, `risk_rules.json` wired to nothing, and now `or 0.05` fabricating volatility. All three produced plausible output and raised no error. Worth treating any `or <constant>` on missing input as suspect by default.
- **Labels applied at one layer do not protect a different layer.** `sample_phase` was stamped correctly from fill context and was still wrong, because the contamination entered through the payload. A gate is only as good as the inputs it actually reads.

---

### Sep 22, 2026 (Tuesday)

<a id="d-sep22"></a>
#### The day the backtest stopped being evidence

One audit triggered four more. By the end of it the strategy question was **closed**,
two generations of backtest were retracted, and Ares itself was left running untouched.
Full analysis lives in `ROADMAP.md`; this is the sequence and the numbers.

##### 1. Athena's profit factor was look-ahead bias

The swing detector confirmed a pivot using bars `i+1 … i+5`, then stamped the result
back onto bar `i`:

```python
for i in range(window, len(series) - window):
    # compares series.iloc[i] against i-j AND i+j for j = 1..5
    result.iloc[p_curr] = True          # back-dated onto the pivot
```

So `bearish_div` was readable on a bar whose confirmation had not happened yet — and
it was consumed as an **exit** on that same bar. Those exits were **~96% of reported
dollar profit**. PF 2.42 was not an optimistic estimate; it was profit taken on
information the system could not have had.

**After Review: V1 through V5 are retracted.** No profit-factor expectation is
currently justified in either direction.

##### 2. Worse — the backtest had never run live's rules at all

A second, independent parity audit found **~30 mismatches**, the largest being that
the simulators **reimplemented** the entry logic rather than importing it. Every
Athena run ever produced had measured a different system from the one on the VPS.

##### 3. Run A′ — the first like-for-like measurement in this project

V6 rebuilt to import live `signals.py` through an adapter, with an md5 assertion that
**fails the run** if the two ever diverge. Live's static $149 stake, no compounding,
live's drift-only queue promotion, and the broker's cash constraint modelled.

| | Universe A (130) | Universe B (98) |
|---|---|---|
| Net return | **−5.58%** | **−57.32%** |
| Max drawdown | −34.65% | −67.55% |
| Profit factor | 0.941 | 0.537 |
| Gross before commission | **+$273.21** | **−$269.85** |
| Commission | −$331.00 | — |

Benchmarks over the same window: **SPY +85.66%, QQQ +97.72%.**

Commission consumed **121.2% of gross profit** — at $149 a position, $1 each way is
**1.34% round-trip against a 1.26% gross edge.** But the deeper number is that
Universe A's gross is only **~+5.5%/yr before any cost**, against the index at
~13.4%. A **commission-free** version, on the universe most flattering to it, would
still have lost badly to buying SPY.

**2024 carried both universes (+23.70%, +37.66%) and every other year lost** — the
signature of a momentum strategy fitted to a trend that has since stopped.

##### 4. Momentum substitution test — and a gate I specified wrong

Pre-registered before the result was known: *is this an expensive way to buy momentum
exposure MTUM sells for 0.15%?*

**Answer: no.** Bootstrap `P(R² > 0.5) = 0.02`, so the replica branch is rejected at
~98%. What emerged instead was **partial momentum beta minus a cost** — β ≈0.61 on
deployed capital, with the residual negative everywhere measurable (−7.3%/yr on A,
−20.9% on B).

> Not a momentum ETF substitute; a partial-momentum process with a negative residual
> — **dominated by a cheaper passive blend with the same factor loading.**

**The registered decision rule was mine, and it was wrong.** It gated on *"is alpha
significantly negative?"* — and A's −7.27%/yr came in at p=0.197, so it **passed**,
on a margin of 0.0054 from the threshold with `P(R² > 0.3) = 0.50`. A coin flip.

−7.27%/yr is economically decisive and statistically invisible at n=60 with ~2.5
trades a month. **Testing significance under low power, then reading
failure-to-reject as "not harmful," converts noise into permission.** Three of the
four cells passed purely because a wide CI crossed zero — and the width was the
reason for caution, not grounds for a pass.

Re-scored under an economic threshold (α ≤ −3%/yr → adverse, regardless of p):
**all four cells ADVERSE.** Both verdicts are retained side by side in the results,
because the record needs to show the same data reversing under a corrected rule.

**Standing principle, now binding on all future pre-registration:** economic
magnitude first, significance second, and **low power never reads as absolution.**

##### 5. Run B — and the finding I would not have predicted

All four divergence columns are permanently `False` in production, so live *and* every
backtest had measured a **crippled** version of the design. Pre-registered question:
does repairing causal divergence move the residual to non-negative?

| Primary metric, Universe A | Run B | Threshold | |
|---|---|---|---|
| Annualised α vs MTUM | **−16.06%** | ≥ −2% | **FAIL** |
| Net excess vs SPY, deployed | **−146.17%** | ≥ 0% | **FAIL** |

α CI **[−25.43%, −5.61%]**, p=**0.0040**. Not the awkward drift to −3% that was
flagged as the temptation case — **twice as far negative, and significant.** Gross
P&L collapsed from **+$273.21 to +$8.27**, and Universe B moved the same way.

> **The defect was load-bearing.** Divergence being permanently broken was the only
> thing keeping Universe A near flat. The strategy *as designed* is worse than the
> strategy *as accidentally built*. The residual belongs to the **concept**.

Three properties make that a finding rather than an artifact: the changed variable
genuinely **fired** (28 divergence entries on A, 27 on B — a null from a change that
did nothing would be uninformative); **both universes moved the same direction**; and
the $250.63 of `bearish_divergence` exits was **not** claimed as the mechanism, since
counterfactual exits are unmeasured and the totals do not decompose additively.

**Consequence — this changes the V4 plan.** Repair is not the same as improvement in
a system understood only empirically. If the inert queue gates started working,
promotion would tighten from drift-only, and there is now direct evidence that
tightening this strategy's entry conditions destroys what little it has. **A V4 that
fixes all three defects could perform worse than V3.1.**

<a id="live-defects"></a>
##### Three live Ares defects found along the way

| # | Defect | Live consequence |
|---|--------|------------------|
| 1 | `tracker.py:400,403` read `latest.get('RSI', 50)` and `EMA_20` — but `add_indicators` emits lowercase `rsi` and **never emits `EMA_20` at all** | Both queue gates are **inert**. Promotion is `\|drift\| ≤ 5%` and nothing else |
| 2 | Swing loop stops at `len − window − 1` while live reads `len − 1` | All four divergence columns permanently **`False`** in production |
| 3 | `tracker.py:119-123` sizes from `starting_capital` as a **constant**, with **no balance check anywhere** | At $427 equity, 5 × $149 = $745 is unfundable. IBKR will reject orders Ares does not know it cannot pay for |

Defects 1 and 2 stay **dead and documented** — after Run B, repairing them is not
obviously an improvement. **Defect 3 is the exception**: a correctness bug with a real
deadline before live capital, fixed on its own merits.

This is the third instance of the **inert-gate** class in this project, after the six
dead config keys and the `hidden_*_div` key mismatch.

##### What changed in Ares: nothing

| | |
|---|---|
| Parameters | **unchanged.** Re-tuning against these numbers is how the original figure was manufactured |
| `clean_v3` | **not restarted.** A disappointing backtest is explicitly not grounds |
| `clean_v3`'s role | **demoted** to *process and integrity observation* — 40–60 trades was never powered to separate +5%/yr from +15%/yr |
| June 2027 | **review gate, not a deploy trigger.** Replaced by a written four-condition live-capital bar |
| Strategy family | still **not** recorded as dead. B is gross-negative and A is survivorship-flattered, so *no edge established* ≠ *absence proven* |

##### Closing position

> **Momentum as a factor works. This active implementation subtracts from it.**

A complete answer, obtained for **$0 of real capital**, roughly two years before the
plan would have committed $1,000 to it. Also recorded: four separate overstatements I
made during this analysis, every one leaning toward the cleaner narrative — so
structural claims from it should be discounted until they appear in a run.

##### Collection record

The audit dominated the day; one position closed.

| Event | Detail |
|-------|--------|
| **ECO closed** | Entered Sep 15, exited Sep 22. −2.0%, `trailing_stop`, verdict `reversal`, peak **+8.97%** |

##### TMO's Sep 21 fill verified in passing

Not a Sep 22 event — **TMO filled on Sep 21** — but confirmed while reviewing this
data, so recorded here. TMO is the trade that exposed the `stdev_20` fallback, and its
fill verifies the fix: entry $654.54, SL $634.25, a **3.10% stop**, implying
`stdev_20` = 1.55%. Had the `0.05` fallback fired, the stop would have sat at
**$589.09** — a 10% stop:

```
10.00% / 3.10% = 3.2x
```

Exactly the "**3.2x intended risk**" figure from the near-miss. The near-miss trade
opened at correct risk on a real stdev.

ECO's label is examined in the Sep 23 entry.

---

### Sep 23, 2026 (Wednesday)

<a id="d-sep23"></a>
#### The trailing stop cannot protect profit below +11.1%

**Symptom from the Sep 22 data:** ECO closed as `trailing_stop` with a **negative**
P&L. A trailing stop that fires at a loss reads like a contradiction, so it was worth
proving one way or the other before touching anything.

**It is not a bug. It is arithmetic.**

```python
peak_price    = trade.get('peak_price', trade['entry_price'])   # seeds AT entry
trailing_stop = peak_price * (1 - 0.10)
effective_stop = max(trade['stop_loss'], trailing_stop)
reason = 'trailing_stop' if trailing_stop > trade['stop_loss'] else 'stop_loss'
```

Because `peak` seeds at the entry price and the trail is **10% below peak**, the
ratchet only clears breakeven once the trade has gained more than `1/0.90 − 1` =
**11.1%**. That produces a dead band:

| Peak vs entry | Label | Outcome |
|---|---|---|
| < ~+6.7% | `stop_loss` | loss — trail still below the initial stop |
| **~+6.7% → +11.1%** | **`trailing_stop`** | **still a loss** — ratchet cleared the stop, not entry |
| > +11.1% | `trailing_stop` | first point profit can actually be locked |

ECO peaked at **+8.97%** — squarely in the dead band. The exit price confirms it to
the cent:

```
giveback = (1 + peak) × trail_pct = 1.0897 × 0.10 = 10.90%
exit      = +8.97% − 10.90%       = −1.93%   ≈  −2.0% reported
```

**After Review: correct code, wide parameter.** `trailing_stop_pct` at 0.10 changes
exit prices, so it is **defer-to-V4** under the change policy. Recorded, not touched.

##### The label was already right — a proposed fix withdrawn

First instinct was that `trailing_stop` needed splitting so a human could tell
profit-protection from a loss. **Wrong — the code already does this.**
`_build_post_mortem` sets `verdict` to `partial_win` when the exit is above entry and
`reversal` when it is not, and the dashboard prints both. ECO reads
`(trailing_stop) reversal`; HAFN reads `(trailing_stop) partial_win`.

Worth recording that the proposed fix was unnecessary and the existing code was better
than assumed.

##### HAFN closed — the same mechanism, above the band

| | Peak | Exit | Giveback | Predicted |
|---|---|---|---|---|
| ECO | +8.97% | **−2.0%** | 10.97% | 1.0897 × 0.10 = **10.90%** |
| HAFN | +15.53% | **+3.97%** | 11.56% | 1.1553 × 0.10 = **11.55%** |

HAFN exited at `$10.19 × 0.90 = $9.17`, exactly the trail. It peaked **+15.53%**
against a TP of **+18.03%** ($10.41) and never reached it.

**Structural note, for the record only.** Three of the four closes to date are
trailing exits and **none has ever hit TP.** `tp_momentum: 0.18` is effectively inert
in practice; the 10% trail is the real exit mechanism, and it surrenders ~11 points
from peak every time. Live is independently reproducing the exit-structure weakness
Run A′ found in simulation. Changing either value alters exit prices — **V4**.

##### Collection record

| Event | Detail |
|-------|--------|
| **HAFN closed** | +3.9%, `trailing_stop`, verdict `partial_win`, peak **+15.53%** |
| **WBD opened** | Entry $30.87, SL $29.31 — stop distance 5.05% |
| Scan | 103 screened, **0 signals** |
| Queue | NEOG held at `conf3`, drift 1.4% |
| Clean sample | still **0 closed** — ECO and HAFN both *entered* before Sep 19 |

Both closes are `pre_clean` because the phase is assigned on **entry** date, not exit.
A trade must have been opened after the fixes to count, which is working as designed.

---

### Sep 24, 2026 (Thursday)

<a id="d-sep24"></a>
#### Two unreachable post-mortem branches, and a progress metric reading zero

<a id="b-postmortem"></a>
##### The fourth string-mismatch defect

Reviewing exit handling after the ECO work, the producer and consumer disagree on a
reason string:

```python
_close_trade(trade, today, current_price, 'emotional_extreme')   # producer
...
elif reason == 'rsi_extreme':                                    # consumer — never matches
```

An RSI-extreme exit therefore fell through to `verdict: 'other'` with no analysis.
Auditing the whole function:

| Branch | Status |
|--------|--------|
| `take_profit` | **unreachable** — no full-TP exit exists. Scale-out sells 50% then `continue`s, and the remainder only ever leaves via a stop |
| `trailing_stop` | live |
| `stop_loss` | live |
| `rsi_extreme` | **unreachable** — the producer emits `emotional_extreme` |
| `else → 'other'` | silently absorbed `emotional_extreme` **and** `mean_reversion_complete` |

**Two of five branches could never fire, and two reachable exit reasons produced no
diagnostics at all.** `mean_reversion_complete` genuinely fires when a range trade's
RSI recovers above 70, and would have been recorded as `other`.

This is the **fourth** instance of this class in the project, after the six dead config
keys, the `hidden_*_div` key mismatch, and the inert queue gates reading `RSI`/`EMA_20`.
The pattern is always the same: **a string written in one place and read in another,
with no assertion tying them together.**

##### Measurement buried in prose

`locked` and `gave_back` were computed correctly — then interpolated into the
`analysis` **string** and never stored as fields. The two most informative numbers
about the exit mechanism existed only inside human-readable text.

##### Clean-sample progress showed zero while four slots carried clean trades

The dashboard reported *"No clean closed trades yet"* — true, but it only ever counted
**closed** trades. By entry date against `CLEAN_FROM = Sep 19`:

| Position | Entered | Phase |
|----------|---------|-------|
| ABM | Sep 09 | `pre_clean` |
| SDGR | **Sep 18** | `pre_clean` — **one day inside the boundary** |
| TMO | Sep 21 | **`clean_v3`** |
| WBD | Sep 22 | **`clean_v3`** |
| NEOG | Sep 23 | **`clean_v3`** |

**Three of five open positions are clean sample, and nothing said so.** The sample was
filling up while its own progress indicator read zero.

SDGR is the instructive one: it entered **Sep 18**, one day before
`CLEAN_FROM = Sep 19`, so it is excluded. That is the boundary working exactly as
intended — a fix-day fill cannot be proven post-deployment, so **unprovable is treated
as contaminated.**

<a id="correction-dates"></a>
###### Correction — these dates were initially wrong

First written by back-calculating from the dashboard's `Nd` field, assuming calendar
days. **It is holding days — trading days only.** That put every entry one to two days
late and wrongly promoted SDGR into the clean sample. Corrected against
`logs/virtual_trades.json`, which is tracked in git and was already current.

**The data was readable the whole time.** Inferring from a rendered dashboard when the
underlying record was one command away is the same mistake in miniature as
reimplementing live's rules in the backtest instead of importing them.

##### Fixes

| # | Area | Change |
|---|------|--------|
| 1 | `_build_post_mortem` | `rsi_extreme` → `emotional_extreme`, matching the producer |
| 2 | `_build_post_mortem` | Added `mean_reversion_complete` and `bearish_divergence` branches. The divergence branch carries a note that it **should not fire** in production — if it ever does, the indicator layer changed |
| 3 | `_build_post_mortem` | `locked_pct` and `gave_back_pct` promoted to top-level fields, computed once for **every** exit reason |
| 4 | `_build_post_mortem` | `gave_back_pct` **guarded to 0.0 when `mfe_pct <= 0`** — see below |
| 5 | `build_dashboard.py` | Clean sample now reports open positions in flight, with symbols |

`take_profit` is left in place. Unlike `rsi_extreme` it is not a mismatch — it is a
correctly-named consumer for an exit that does not currently exist, and would become
live if a full-TP exit were ever added.

##### Why the guard on fix 4 matters more than it looks

Unguarded, `gave_back_pct = mfe_pct − locked_pct` gives PINS — which **never traded
above entry** — a giveback of **+5.90%**, equal to its loss. It surrendered nothing;
it simply lost. Any future average over that field would have been inflated by every
no-peak loser in the sample.

That is the **same shape as the Sep 21 scale-out bug**: not a crash, not a lost
signal, but a field that reads one way and means another, quietly biasing whatever
consumes it later. Guarded to `0.0`, which loses nothing — `mfe_pct` and `locked_pct`
are both stored, so the raw distance is always recoverable.

##### Verification

Post-mortem exercised against the four real closes and three synthetic reasons:

```
ECO   trailing_stop            verdict=reversal              locked= -2.00  gave_back= +10.97
HAFN  trailing_stop            verdict=partial_win           locked= +3.97  gave_back= +11.56
PINS  stop_loss                verdict=signal_failed         locked= -5.90  gave_back=  +0.00
RSIx  emotional_extreme        verdict=emotional_exit        locked=+15.00  gave_back=  +1.00
MRc   mean_reversion_complete  verdict=target_reached        locked= +7.00  gave_back=  +2.00
bdiv  bearish_divergence       verdict=divergence_exit_loss  locked= -2.00  gave_back=  +7.00
junk  something_new            verdict=other                 locked= +0.00  gave_back=  +0.00
```

ECO and HAFN reproduce the hand arithmetic to two decimals. `emotional_extreme` now
resolves instead of falling through. PINS shows the guard working. An unrecognised
reason still degrades safely to `other`.

##### Collection record

| Event | Detail |
|-------|--------|
| **NEOG promoted** | From queue at $14.22, SL $13.75 — filled HAFN's vacated slot |
| Scan | 103 screened, **2 signals** |
| **SECZ** | `momentum_breakout`, `conf3`, uptrend → queued, portfolio full at 5/5 |
| Pre-clean | 4 closed, realised **−$5.34**, W/L 2/2 |
| Clean sample | **0 closed, 3 in flight** (TMO, WBD, NEOG) |

Queue promotion worked end to end: NEOG was held overnight at `conf3`/1.4% drift and
promoted when a slot opened. Of the 2 signals, one was SECZ; the other was a repeat on
a symbol already queued, which `queue_signal` discards in favour of the first entry.

**System:** RAM 1006/1967 MB, swap inert (0 MB paged in 7.5h), stall 84 ms, disk 27%,
cache 379 symbols. All green.

---

### Sep 25, 2026 (Friday)

First `clean_v3` close, and the portfolio returns to 5/5. Logged from the VPS cron
commits `6adad6a`…`5063f48`, pulled Sep 28.

##### Collection record

| Event | Detail |
|-------|--------|
| **NEOG closed** | `stop_loss` −3.4%, verdict `weak_follow_through`, peak **0.03%** — **first clean_v3 close**, realised **−$7.07** |
| **SECZ promoted** | From queue → filled at $14.68 (10.15 sh), SL $12.07, TP $17.33 — took NEOG's vacated slot |
| Scan | 103 screened, 0 signals at 09:33 PM; **2 signals** by 05:01 AM (SECZ `momentum_breakout` `conf3` uptrend) |
| Queue | TWST `conf3` −0.7%, AMP `conf3` +1.6%, ADT `conf2` +0.6% |
| Clean sample | **1 closed, 3 in flight** (TMO, WBD, SECZ) |
| Pre-clean | 4 closed, realised −$5.34, W/L 2/2 — ABM and SDGR still open |
| All combined | 5 closed, realised **−$12.41**, W 2/5 |

**Open positions at 05:01 AM MYT:**

| Symbol | Phase | Day | Entry | Last | Peak | SL | TS | Trail state |
|--------|-------|-----|-------|------|------|----|----|-------------|
| ABM | pre-clean | 16 | $50.65 | $49.57 (−2.1%) | $50.91 (+0.5%) | $48.67 | $48.67 | **never ratcheted** |
| SDGR | pre-clean | 7 | $29.35 | $29.10 (−0.9%) | $31.37 (+6.9%) | $25.06 | $28.23 | active, below entry |
| TMO | clean_v3 | 4 | $654.54 | $671.03 (+2.5%) | $678.39 (+3.6%) | $634.25 | $634.25 | never ratcheted |
| WBD | clean_v3 | 3 | $30.87 | $30.87 (0.0%) | $30.87 (0.0%) | $29.31 | $29.31 | never ratcheted |
| SECZ | clean_v3 | 0 | $14.68 | $15.71 (+7.0%) | $15.71 (+7.0%) | $12.07 | $14.14 | active, below entry |

##### Equity, reconciled against the trade log

`pnl` in `virtual_trades.json` is **gross**; the dashboard reports `pnl_after_costs`.
The two differ by $2 per trade except DYN, which has `scaled_out=True` and therefore
a third commission. That single dollar was the whole discrepancy:

| | gross `pnl` | commissions | net |
|---|---|---|---|
| HAFN | +5.76 | 2 | +3.76 |
| DYN | +9.77 | **3** | +6.77 |
| PINS | −8.86 | 2 | −10.86 |
| ECO | −3.01 | 2 | −5.01 |
| NEOG | −5.07 | 2 | −7.07 |
| | | | **−12.41** ✓ |

| Component | Value |
|-----------|-------|
| Starting capital | $1,000.00 |
| Realised (net of commission) | −$12.41 |
| Cash on hand | **$237.59** |
| Stock at cost (5 × $149) | $745.00 |
| Market value of open positions | $756.47 |
| Unrealised, net of entry commissions | **+$6.47** |
| **Equity** | **$994.06** |

##### Defect 3 is now measurable, and directional

Sizing reads a constant `starting_capital` of $1,000, not current equity, so the
25% cash reserve is nominal. With five slots filled the reserve target is $250 but
actual cash is **$237.59** — already $12.41 light, exactly the realised loss. The
error is the realised P&L, by construction, and it grows with every losing close.

Not yet a breach: a freed slot needs $150 and there is $237.59. It becomes one when
cash falls below $150, i.e. after roughly **−$100 more** of realised losses. Worth
watching, not yet acting on.

##### Two positions are sitting in the giveback dead band

SDGR and SECZ both satisfy the two conditions that produced Block A's worst
sub-population: the trail is **active** (`peak × 0.90 > stop_loss`, so a trail exit
is labelled `trailing_stop`), yet peak is **below +11.11%**, so `peak × 0.90` is
still under the entry price and the trail locks a *loss*.

- SDGR: trail $28.23 is **3.8% below** its $29.35 entry
- SECZ: trail $14.14 is **3.7% below** its $14.68 entry

Before the 1.34% round-trip cost. Athena Universe A found 14 such trades, 12 of
which exited `trailing_stop`, and **12 of 12 lost money**. ECO is already a completed
live instance: peak 8.97%, closed −2.00% `trailing_stop`, gave back 10.97pp.

The band was registered as peak +6.7% to +11.1%, but the lower bound is
stop-distance dependent, not universal — SDGR's trail overtakes its stop at peak
+5.1%, and SECZ's was already above its stop at entry. **The +11.11% upper bound is
exact; the lower bound is per-trade.** Correct the ROADMAP wording when convenient.

##### ABM, TMO and WBD will label `stop_loss`, not `trailing_stop`

All three have `TS == SL`. The trail never ratcheted, so `trailing_stop > stop_loss`
is false and the reason branch resolves to `stop_loss`. ABM is the live one to watch:
day 16, peak +0.5%, last $49.57, ~1.8% above its stop.

**System:** RAM 1017/1967 MB, swap inert (6 MB paged in 7.5h), stall 16 ms, disk 28%,
cache 477 symbols. All green.

**Notes:**
- Portfolio full at 5/5 with 3 queued, so the next signal is queued, not entered.
- **AMP is genuine, not a truncation** — `mean_reversion`, `price_at_signal` $485.15,
  RSI 28.1, screens `oversold_bounce` + `big_movers_down`. That is Ameriprise. My
  earlier note recording it as "AMPD" was wrong.

---

### Sep 28, 2026 (Monday)

A quiet session that matters only in hindsight: it is the last day both pre-clean
positions were open, and SDGR's fatal bar was printed on it. Logged from cron
commits `b4b468e`…`3301875`, pulled Sep 29.

##### Collection record

| Event | Detail |
|-------|--------|
| Closes | **none** — 5/5 held all session |
| SECZ ratchet | TS **$14.63 → $14.94** between 13:32 and 21:01 UTC as peak ran +10.8% → +13.1% |
| Scan | 103 screened, **1 signal** — BCE `mean_reversion` `conf2` range |
| Queue | TWST `conf3`, AMP `conf3`, ADT `conf2`, BCE `conf2` — 4 waiting, no free slot |
| Clean sample | 1 closed (NEOG), 3 in flight (TMO, WBD, SECZ) |
| Pre-clean | 4 closed, realised −$5.34, W/L 2/2 — **ABM and SDGR both still open** |
| All combined | 5 closed, realised −$12.41, W 2/5 |

**Open positions at 21:01 UTC (05:01 AM MYT Sep 29):**

| Symbol | Phase | Day | Entry | Last | Peak | SL | TS | Trail state |
|--------|-------|-----|-------|------|------|----|----|-------------|
| ABM | pre-clean | 19 | $50.65 | $49.11 (−3.0%) | $50.91 (+0.5%) | $48.67 | $48.67 | **never ratcheted — TS pinned to SL** |
| SDGR | pre-clean | 10 | $29.35 | $29.78 (+1.5%) | $31.37 (+6.9%) | $25.06 | $28.23 | active, below entry |
| TMO | clean_v3 | 7 | $654.54 | $675.45 (+3.2%) | $681.77 (+4.2%) | $634.25 | $634.25 | never ratcheted |
| WBD | clean_v3 | 6 | $30.87 | $30.86 (−0.0%) | $30.87 (0.0%) | $29.31 | $29.31 | never ratcheted |
| SECZ | clean_v3 | 3 | $14.68 | $16.16 (+10.1%) | $16.81 (+14.5%) | $12.07 | $15.13 | active, below entry |

##### The bar that closed SDGR was printed today

SDGR's session close was **$28.20** against a trail of **$28.23** — three cents
through, from `peak 31.37 × 0.90 = 28.233`. The exit was therefore already
determined by the time this session ended, but the tracker only *acts* on the
following cycle, so the position still reads open in the 21:01 UTC dashboard above.

That one-cycle lag is ordinary and not a defect. It becomes the central fact of
Sep 29 because parity collection was activated **inside that lag**.

##### ABM's equality condition was structural, not coincidental

ABM closed the day 19 sessions in with a peak of **+0.5%**. The trail is
`max(stop_loss, peak × 0.90)`, and `50.91 × 0.90 = 45.82`, far below the initial
stop of `$48.67`. The trail therefore never left the stop, and
`trailing_stop == stop_loss` exactly.

This is the signature of a position that never moved in the operator's favour, and
it is the precise input that makes the inline tie-break observable:

```python
reason = 'trailing_stop' if trailing_stop > trade['stop_loss'] else 'stop_loss'
```

`48.67 > 48.67` is false, so an exit here must be labelled `stop_loss`. Worth
recording a day early, because tomorrow it stops being a hypothetical.

---

### Sep 29, 2026 (Tuesday)

The densest day of the migration so far, and the only one on which both pre-clean
positions closed. Parity collection went live, and in the 17 minutes between
SDGR's exit and the first parity record, one registered evidence requirement was
lost permanently while the other was captured cleanly 15 hours later.

Logged from cron commits `615d173`…`5fa3eb2`, plus two manual cycles.

##### Cycle-by-cycle, because the ordering is the point

| UTC | Invocation | Parity | Outcome |
|-----|-----------|--------|---------|
| ~05:31 | manual (`run_ares.sh`) | **OFF** | **SDGR closed** `trailing_stop` — evidence not collected |
| 05:35 | — | — | repo `run_ares.sh` lands on disk (declaration deployed) |
| 05:52 | manual | **ON** | **first 4 parity records ever** (ABM, TMO, WBD, SECZ) |
| 08:00 | manual | ON | 4 records, commit `99c6b08` |
| 13:30 | **cron** | ON | first *automated* cycle — 5 records, **AMP entered** |
| 21:00 | **cron** | ON | 5 records — **ABM closed** `stop_loss`, **exit captured** |

The 05:31 run used the *external* `/root/ares/run_ares.sh`, which had no
`ARES_PARITY=1`. Cron was repointed to the repository launcher at 09:31 UTC, which
is why the 13:30 and 21:00 cycles collected automatically.

##### Two closes, seventeen minutes apart in consequence

| | SDGR | ABM |
|---|---|---|
| Entry | 2026-09-18 @ $29.35 | 2026-09-09 @ $50.65 |
| Exit | 2026-09-28 @ **$28.20** | 2026-09-29 @ **$48.62** |
| Peak | $31.37 (+6.9%) | $50.91 (+0.5%) |
| SL / TS | $25.06 / **$28.23** | $48.67 / **$48.67** |
| Reason | `trailing_stop` (trail active) | `stop_loss` (**equality**) |
| Gross / net | −$5.83 / **−$7.83** | −$5.96 / **−$7.96** |
| % | −3.91% | −4.01% |
| Parity evidence | **none — permanently unobtainable** | **captured, MATCH** |

ABM's exit record is the single most valuable row in the evidence file:

```
cycle d800900e  2026-09-29T21:00:55Z  commit 33d8106
  inline_decision   close:stop_loss      shadow_decision   close:stop_loss
  inline_stop 48.67 captured_inline_local   shadow_stop 48.67
  difference_class MATCH   differing_fields []   would_change_action False
  parity_action exit      state_hash before == after (observation wrote nothing)
  bar_date 2026-09-29   bar_price 48.62   price_source IBKR
```

It settles three things that had been arguments rather than evidence: the
equality tie-break resolves to `stop_loss` in both implementations; a position
evaluated on its closing cycle **does** produce a packet, so exits are
observable; and the staleness worry was unfounded — ABM's bar was same-day and
IBKR-sourced, and `48.62 < 48.67` is a real stop.

Had the exact-capture change not landed the day before, `inline_effective_stop`
would have been reconstructed from rounded persisted state on the one exit that
mattered.

##### AMP entered under observation from inception

AMP filled on the 13:30 UTC cycle at **$492.08** (0.30 sh), SL $472.99, TP
$541.29, taking the slot **SDGR** vacated that morning — ABM's slot did not free
until 21:00 UTC, eight hours later. It was top of a 4-deep queue (AMP, TWST, BCE,
ADT) and left three behind it.

AMP is the first position whose entire lifecycle will be observed under parity
from inception, which makes it the first live opportunity to see an entry-day
cycle recorded: no `effective_stop` is computed on entry day, so its basis should
read `not_computed_entry_day_skip` rather than `captured_inline_local`.

##### Equity, reconciled

Realised now −$28.20 across 7 closes, which reconciles exactly:

| Cohort | Trades | Net realised |
|--------|--------|--------------|
| Pre-clean | HAFN +3.76, DYN +6.77, PINS −10.86, ECO −5.01, SDGR −7.83, ABM −7.96 | **−$21.13** (W/L 2/4) |
| clean_v3 | NEOG −7.07 | **−$7.07** (W/L 0/1) |
| | | **−$28.20** ✓ |

| Component | Value |
|-----------|-------|
| Starting capital | $1,000.00 |
| Realised (net of commission) | −$28.20 |
| Stock at cost (4 × $149) | $596.00 |
| Entry commissions on open positions | $4.00 |
| Cash on hand | **$371.80** |
| Market value of open positions¹ | $608.19 |
| Unrealised, net of entry commissions | **+$8.19** |
| **Equity** | **≈$979.99** |

¹ marked at the 17:30 UTC monitor prices (TMO $674.27, WBD $30.86, SECZ $15.30,
AMP $495.88), not a session close — treat as indicative.

##### Defect 3 has doubled, and the free slot now breaches the reserve

Sizing still reads a constant `starting_capital` of $1,000 rather than equity, so
the 25% reserve remains nominal. The shortfall is the realised loss by
construction, and it has grown from **−$12.41** to **−$28.20**.

With four slots filled, cash is $371.80 against a $250 target — comfortable. But
filling the fifth slot costs $150, leaving **$221.80**, which is **below the $250
reserve** for the first time. Previously the breach was ~$100 of further losses
away; it is now one entry away. Still not acting, per V3 rules, but this is the
first session where the reserve rule and the sizing rule actually conflict.

##### Both pre-clean positions are now closed

Phase 2 clearance is therefore satisfied on its own merits: no pre-migration
position remains under the legacy tracker. That is a *different* question from
whether their evidence was collected, and the two answers now diverge —

| Symbol | Phase 2 clearance | Required parity coverage |
|--------|------------------|--------------------------|
| ABM | cleared (closed) | **satisfied** — exit captured, MATCH |
| SDGR | cleared (closed) | **unobtainable** — retired with documented cause |

Keeping those separate is the whole reason the coverage gate was rebuilt; a
`still_open` test would have reported both identically.

---

### Sep 30, 2026 (Wednesday)

The session that verified the heartbeat lineage fix — and, in hindsight, the
first of five consecutive exits that parity never saw. Logged retrospectively on
Oct 7 after the vacation.

##### Collection record

| Event | Detail |
|-------|--------|
| Heartbeat | first post-fix cycle: `production_commit cb9d92b`, `lineage_complete true`, 5/5 attempted/written, 0 write failures, `contract_valid true` |
| Records | 18 → 23 |
| Entries | TWST $188.19, 0.79 sh, SL $167.83, TP $222.06 — captured `not_computed_entry_day_skip`, `parity_action skipped_entry_day` |
| | BCE $20.22, 7.4 sh, SL $19.86, TP $22.24 (appeared Day 0 at 21:01 UTC) |
| Closes | **SECZ** at $15.13 `trailing_stop` — **announced in a 16:10/17:30 UTC monitor run** |
| Scan | 103 screened, 2 signals — GMAB `momentum_breakout` `conf3` |
| Clean sample | 2 closed (NEOG −$7.07, SECZ +$2.41), realised −$4.66, W 1/2 |

All five 13:31 records `MATCH`, `would_change_action: 0`, `price_source IBKR`,
`tracker.py` md5 unchanged. Everything we set out to verify verified.

##### The reserve rule and the sizing rule conflicted for the first time

TWST filled at `188.19 × 0.79 = $148.67`, taking cash to

```
1000 − 28.20 realised − 744.67 cost basis − 5.00 commissions = $222.13
```

against a $250 target — **$27.87 short**, almost exactly the $221.80 projected on
Sep 29. Nothing stopped it: no gate, no alert. Sizing still reads a constant
`starting_capital`. Observed only, per V3 rules.

With the portfolio at 5/5 the breach could not deepen; the next opportunity for
the defect to bite was the first entry after a slot freed.

##### A rounding-basis mismatch, logged and not acted on

TWST recorded `entry_price 188.19` with `peak_price 188.188` — peak below entry,
which is structurally impossible in a correct peak tracker. Entry is rounded to
2dp under the `tracker_v3_2dp` contract while peak retains the raw fill. Harmless
in effect; the two fields are simply not on the same rounding basis.

##### What was not noticed

SECZ's exit was announced in a **monitor** block, not a report cycle. At the time
this read as an ordinary intraday alert. It was the first instance of the
condition documented in `INCIDENT_2026-10-07_monitor_decision_path.md`: the
monitor decides exits on a path parity does not observe.

---

### Oct 1, 2026 (Thursday)

| Event | Detail |
|-------|--------|
| Heartbeats | 13:31 and 21:01, both `lineage_complete true`, 5/5, clean |
| Entries | GMAB $35.29, 4.2 sh, SL $34.16, TP $41.64 |
| Closes | **BCE** at $19.86 `stop_loss`, Day 1 — **monitor path** |
| Scan | 103 screened, 0 signals |
| Clean sample | 3 closed, realised −$9.46, W 1/3, PF 0.20 |

BCE held for one day with a peak of +0.54% and a stop just 1.78% below entry —
the tightest stop in the sample. Monitor live check at 17:30: TMO $666.07,
WBD $30.94, AMP $494.82, TWST $187.25 with the trail ratcheted to $178.50.

##### Note on `production_commit`

Each heartbeat carried a different commit — `9b40821` at 13:31, `60fba24` at
21:01. The bot commits its own logs every cycle, so HEAD advances constantly and
the field records a *log* commit. See Oct 7.

---

### Oct 2, 2026 (Friday)

| Event | Detail |
|-------|--------|
| Heartbeats | 13:31 (5/5) and 21:01 (**4/4**) |
| Closes | **GMAB** at $34.16 `stop_loss`, Day 1 — **monitor path** |
| Scan | 103 screened, 0 signals |
| Clean sample | 4 closed, realised −$16.37, W 1/4, PF 0.13 |

The drop from 5 to 4 attempted positions between the two cycles is the monitor
path becoming visible in the evidence without being visible in it: GMAB closed
between 13:31 and 21:01, and the only trace in parity is a decremented count.

GMAB turned out to be the sample's genuine whipsaw. Stopped at $34.16 with a peak
of −0.01% — it never moved favourably — then rallied to $38.48 by Oct 5.
`missed_upside_pct: 12.75`, `avoided_downside_pct: −0.09`.

---

### Oct 5, 2026 (Monday)

The weekend crossing, and the first live test of schedule-aware freshness.

| Event | Detail |
|-------|--------|
| Heartbeats | 13:31 (4/4) and 21:01 (**5/5**) |
| Entries | PCVX $87.30, 1.7 sh, **SL $65.93**, TP $103.01 |
| Closes | none |
| Scan | 103 screened, 1 signal — PCVX `momentum_breakout` `conf3` |
| System | ⚠️ stall 1218 ms over 64.5 h — flagged memory pressure, benign |

##### The weekend gap behaved as designed

The Fri 21:00 → Mon 13:30 interval is **64.5 hours**. The collector reported
normally throughout and freshness stayed valid. A fixed 36h or 48h staleness rule
would have raised a false alarm here; the schedule-aware design was built for
exactly this interval and has now been exercised once in production.

##### PCVX's stop was placed 24.5% from entry

`65.93 / 87.30 = 0.7552`. That is by far the widest stop in the sample, and it
sits on the same ~$150 fixed notional as every other position. The implication
was not drawn until Oct 7.

TWST meanwhile ran to peak +8.9%, trail ratcheting $180.64 → $181.51 → $184.50
across the monitor runs.

---

### Oct 6, 2026 (Tuesday)

| Event | Detail |
|-------|--------|
| Heartbeats | 13:31 (5/5) and 21:01 (**3/3**) |
| Closes | **TWST** at $188.10 `trailing_stop`, **PCVX** at $65.93 `stop_loss` — **both monitor path** |
| Scan | 103 screened, 0 signals at 13:32; 1 signal at 05:01 MYT — **WBD, already held at Day 14** |
| Clean sample | 6 closed, realised **−$57.25**, W 1/6, PF 0.04 |
| Pending | EROC, ITUB → next open. Queue: XP |

##### One trade is two thirds of the clean sample's loss

| Trade | Entry | Booked exit | Net | Day | Reason |
|-------|-------|-------------|-----|-----|--------|
| TWST | $188.19 | $187.91 | −$2.22 | 7 | trailing_stop |
| **PCVX** | **$87.30** | **$65.86** | **−$38.66** | **1** | **stop_loss** |

PCVX alone is **67.5%** of −$57.25. Expectancy −$9.54 and PF 0.04 therefore
describe one outlier, not a process. Median net is near −3.3%. At n=6 with this
much concentration the mean is retained as descriptive only; no strategy-validity
claim is made or implied.

##### TWST's trail was correct ex-post

The trail surrendered 11.1 points of peak to close at −0.1%, which is the
giveback weakness Block A documented. But `avoided_downside_pct: 11.14` — TWST
fell to $166.97 after exit. The giveback was real and the alternative was worse.
Both halves belong in the record.

##### WBD signalled while held

The 05:01 MYT scan listed WBD as a fresh `momentum_breakout conf3` while WBD was
open at Day 14. It did not re-enter. *Why* it did not — which guard fired — has
not been read from the logs and is not assumed.

---

### Oct 7, 2026 (Wednesday) — the monitor path is a second exit implementation

No trading observations of note. This session was diagnosis, and it invalidates a
claim the migration had been resting on since Phase 0.5.

Full record: **`INCIDENT_2026-10-07_monitor_decision_path.md`**.

##### What was found

Verification of the vacation evidence showed heartbeat continuity intact — 12
heartbeats, no missing cycle, weekend correctly absent, `write_failures 0`,
`contract_valid true` throughout — and **exit coverage of 1 in 6**:

```
decisions: hold 52, state_update 11, close:stop_loss 1
exit records: [('ABM', '2026-09-29', 'stop_loss')]
```

The cause is not a missing flag. `monitor_trades.py` never calls
`check_open_trades`. It implements **its own exit loop** over tracker primitives
using IBKR live prices, writes production state directly, and has done since
commit `03c3c50` on Sep 17 — before parity activation.

The originally planned repair — `export ARES_PARITY=1` in `run_monitor.sh` —
would have had **no effect**, because that variable gates a function the monitor
does not call. Verifying the call chain before editing was the instruction that
caught it.

##### Ten divergences, two critical

`scale_out: True`, so reaching take-profit sells **half** the position on the
report path and **all** of it on the monitor path — selected by which cron slot
the price happens to touch TP in. The monitor also has **no entry-day skip**,
implements 2 of 5 exit reasons, and performs **no stop evaluation at all** when
IBKR returns no price.

##### Claims withdrawn

- **"The inline tracker is the sole production authority"** is withdrawn. It is
  sole authority *on the report path*. An unchanged `tracker.py` md5 no longer
  demonstrates unchanged production exit behaviour.
- **The 64 MATCH records remain valid for the report path only.** They do not
  establish whole-system exit equivalence.
- **`phase4_operational_ready: true` is too broad** and splits into *collector
  healthy on registered paths* (true) and *all decision paths registered and
  observed* (false).

Five exits — SECZ, BCE, GMAB, TWST, PCVX — are **unobtainable and blocking**,
keyed by lifecycle identity. They are explicitly **not** retired by analogy with
SDGR: one pre-activation closure and five systematic omissions are different
findings. `trade['shadow']` was checked as a possible partial recovery and is
post-exit price tracking only — no decision context, no recovery.

##### Shared defect, both paths

Both discard the observed price and book exits at the stop level, then apply a
flat 0.1% haircut (`slippage_pct` absent from params, in-code default). The
monitor holds `live` at line 68 for the trigger and throws it away at line 70 —
**the most diagnostic value in the system is computed and discarded on every
monitor exit.** Had it been captured, PCVX's gap question would be settled; it is
currently unanswerable from persisted data.

##### Corrections of record

Seven claims made during the session were wrong or overstated, including "zero
slippage, ever" (slippage *is* applied), "PCVX gapped" (unfounded), and a second
wrong inference from `trough_after_exit` in the opposite direction. All are
tabulated in the incident document rather than quietly dropped.

##### The governing question has changed

`exit_policy` was built to replace `check_open_trades`, and Block A's Policy A is
defined by that function. But most live exits came from the other implementation.
So before *"is Policy A worth modelling?"* comes:

> **Which production exit policy are we trying to preserve or replace?**

Phase 5 and AI modelling remain prohibited.

---

### 2026-10-07 — EROC and ITUB filled; monitor caught writing state

Both pendings filled on the 13:30 UTC report cycle. Portfolio to 5/5.

- EROC 11.81 @ $12.61, SL 11.14, TP 14.88
- ITUB 14.72 @ $10.12, SL 9.32, TP 11.94

Cash fell to **$171.46** against a nominal $250 reserve — a **$78.54 breach**.
The pre-cycle prediction of $171.35 was wrong by $0.11, caused entirely by
`shares` being **floored** to 2dp rather than rounded, so each fill consumes
slightly less than `position_size`. `position_size` is the pre-floor notional,
not the outlay. Booked value stands; the method was not adjusted to recover the
prediction.

The reserve is a sizing constant against `starting_capital`, never a runtime cash
check. Documenting this breach does not authorise the entries that caused it.

**The incident was demonstrated end to end.** At 16:10 and 17:30 UTC the monitor
ratcheted EROC's trailing stop 11.14 → 11.66 → 11.76 and its peak to 13.07, with
**no evidence written**; the 21:00 report cycle then read those values. An
unobserved path mutated state the observed path depends on. The only artefact is
Telegram text.

The monitor ratcheted at **10%**, confirming `trailing_stop_pct` resolved from
config at both sites and that the 0.08/0.10 default divergence remains masked and
not firing — `currently_active: false` stands. Not to be fixed in the recorder.

Also observed: EROC was Day 0, and the monitor has no entry-day skip, so it acted
on a position the report path would have skipped. WBD returned "No live price" on
all four monitor checks across two days → `continue`, no stop evaluation, no
trace.

New finding: EROC filled 7.6% below its $13.64 signal price. The queue enforces
`queue_max_drift_pct`; the pending path appears not to. Flagged to verify, not
asserted, not repaired.

`peak_price` has two precisions — 2dp when the monitor has ratcheted it, full
unrounded float when it has not. ITUB's retains the discarded unrounded entry
`10.120109656333922`.

Parity 64 → 84, exactly 4 cycles × 5 positions. `stdev_fallback` false and
`sample_phase: clean_v3` on both fills, as predicted. `initiating_source` is
`unknown` for both and permanently so.

Design correction found by observation: `queue_events.jsonl` has **no record** for
either admission, so it cannot serve as the sole admission-evidence stream. §3.5
amended.

Recorded in `RECONCILIATION_2026-10-07_fill_cycle.md`. No runtime change.

### 2026-10-08 — no new entries; near-miss on EROC

5/5 slots, so BSP signalled and queued rather than admitted. Scan 103 screened.
EROC traded to $11.92 against a trailing stop of $11.76 — within $0.16, no exit.
TMO to $641.83 against $634.25. WBD again returned no live price on both monitor
checks.

BSP queued at −4.4% drift on Oct 8 and was absent from the queue by Oct 9 with no
stated reason. To check against `queue_max_drift_pct` and `queue_max_age_days`.
Dashboard also reported "2 signals" while listing one — same count/list mismatch
seen on Oct 7.

### 2026-10-09 — reconciliation closed; recorder built and reviewed, not activated

**Reconciliation.** Booked cash **$171.46**, reserve breach **$78.54**. The
pre-cycle prediction of $171.35 was wrong by **$0.11**, caused entirely by
`shares` being floored to 2dp: EROC `149/12.61 = 11.8160` was booked as
**11.81**, so each fill consumes slightly less than `position_size`, which is the
pre-flooring notional and not the outlay. Legacy basis confirmed at
`745.1608 − 297.8905 = 447.2703`. Booked value stands; the method was not
adjusted to recover the prediction. Recorded in
`RECONCILIATION_2026-10-07_fill_cycle.md`.

**Recorder built** on branch `recorder/monitor-observer-v1` (`7681d9c`,
`ac73363`). `engine/monitor_observer.py` plus 120 lines in `monitor_trades.py`:
three manifests that fail on a missing path, per-site effective configuration, an
exact 33-field schema, a path-scoped heartbeat, and a loop that writes raw values
only with every derivation moved after it.

**Critical finding — merging is deployment.** Both launchers run
`git pull --rebase --autostash` whenever the bare push fails, which a new `main`
commit guarantees. Pushing code to `main` therefore lands it in the VPS working
tree at the next cycle. There is no separate deploy step and never was; every
documentation commit this week propagated the same way. The recorder was
committed to a branch for exactly this reason, and activation must be a
deliberate merge between cycles.

**Controls: 22/22 laptop, 22/22 VPS, exit 0.** Run through a `git worktree` in
`/tmp` so the live tree was never switched, then removed. A stdlib-only runner
was written because pytest is absent from the VPS venv and installing it would
change the production environment to test an observation-only module.
Mutation-tested three ways; each mutation fails only its own control.

**Two defects found by the recorder's own controls.** `flush` applied the
derivation twice, so a partial record reported `record_complete: true` — the
exact failure mode the recorder exists to prevent. And a legitimately skipped
symbol reported incomplete, which would have made every WBD cycle
indistinguishable from a loop that died. Both fixed and regression-guarded.

**Not activated. Nothing deployed. `main` is clean and the recorder is absent
from it.** Admission capture is still outside scope: `queue_events.jsonl` is
empty for a direct scan admission and needs its own registered diff covering both
routes. The governing question — which exit policy is authoritative — remains
open, the five lost monitor exits remain blocking, and the recorder cannot be
called a fix for recurrence until every path is instrumented and verified.

Phase 5, policy unification and AI modelling remain prohibited.

### 2026-10-09 (session) — no fills, no closes; EROC $0.14 above the monitor-written stop

Source: Telegram dashboards at 21:32 MYT (13:32 UTC) and 05:01 MYT Oct 10
(21:01 UTC), plus two monitor live checks between them — 16:10 and 17:30 UTC
according to the crontab, since the monitor messages carry no timestamp. Not
cross-checked against `virtual_trades.json`: the laptop's cached `origin/main`
predates the session and was not re-fetched.

**Portfolio 5/5, unchanged across the session.** No admissions, no exits. Scan:
103 screened, 0 signals on both cycles. Since nothing filled or closed, the
booked cash of **$171.46** and the **$78.54** reserve breach still stand. That
is inferred from no activity, not re-read.

| | Entry | SL | TS | TP | Peak | Held | 16:10 | 17:30 |
|---|---|---|---|---|---|---|---|---|
| TMO | 654.54 | 634.25 | 634.25 | 772.36 | 4.6% | 18d | 657.18 | 659.71 |
| WBD | 30.87 | 29.31 | 29.31 | 36.42 | 0.4% | 17d | no price | no price |
| AMP | 492.08 | 472.99 | 472.99 | 541.29 | 1.9% | 10d | 500.47 | 500.92 |
| EROC | 12.61 | 11.14 | 11.76 | 14.88 | 3.6% | 2d | 12.03 | 11.90 |
| ITUB | 10.12 | 9.32 | 9.32 | 11.94 | 0.0% → 0.3% | 2d | 10.00 | 9.97 |

**EROC closed the 17:30 check $0.14 (1.2%) above its effective stop.** That stop
is $11.76, the trailing value the monitor wrote without trace on Oct 7. The
configured stop is $11.14. Any print between those two levels would close EROC
**only because of unobserved state**. Oct 8 came within $0.16, so this is the
second consecutive near-miss. While the HOLD stands, a monitor exit on Monday
would go unrecorded and add a sixth lost monitor exit. That is a factual
consequence of the HOLD and not, on its own, a reason to lift it.

**The monitor wrote no stop or peak changes on Friday.** This is inferred from
arithmetic, not observed, because nothing records it. No live price exceeded a
recorded peak; AMP came closest, at 500.92 against a peak of at least 501.18. No
`live × 0.90` exceeded the current trailing stop; EROC came closest, at
10.83 against 11.76.

**ITUB's peak moved 0.0% → 0.3% at 21:00 UTC**, yet both monitor prices were
below entry, so the monitor did not set it. The likely source is the report
path's own bar evaluation, which is unverified. The trailing stop was
unaffected: 10.15 × 0.90 = 9.135, below 9.32. Still to check: whether
`peak_price` is now stored at 2dp or still holds the unrounded
`10.120109656333922`, which is the dual-precision finding from Oct 7.

**WBD returned no live price on both checks again.** By this logbook's count
that is eight consecutive monitor checks over four sessions: Oct 7's entry
covers four checks over two days, and Oct 8 and Oct 9 add two each. For eight
checks the monitor has evaluated no intraday stop for WBD, leaving the report
path as the only evaluator. The cause remains opaque: four distinct causes all
collapse to `None`.

**XP left the queue between cycles with no stated reason.** The queue went from
3 to 2 entries. XP was at +3.7% drift at 13:30. This is the second unexplained
departure after BSP, so it goes on the same check against
`queue_max_drift_pct` and `queue_max_age_days`. INSM moved −1.0% → +0.1% and
MANE −1.8% → +3.4%, both still conf3.

**Sample statistics unchanged.** Clean: 6 closed, 1 win, −$57.25, expectancy
−$9.54, PF 0.04, −6.40% ± 7.89%; the CI includes zero, so this is not evidence of
edge. Pre-clean: 6 closed, −$21.13. All history: 12 closed, −$78.38.

**System nominal.** RAM 916 → 1026 / 1967 MB; swap 318 MB with nothing paged
out; stall 434 ms over 16.5 h, then 96 ms over 7.5 h; disk 28%; cache
847 → 857 symbols; `cron.log` 0.5 → 0.6 MB.

**The 21:00 cycle opened the 64.5 h weekend window, which lapsed unused.** The
recorder was on HOLD pending independent review, so nothing was deployed.
Monday's 16:10 UTC monitor cycle runs the unmodified `monitor_trades.py`.

### 2026-10-12 — recorder repair round 2 staged for independent review

**Correction to the 2026-10-09 entry: merging is *not* deployment.** That entry
said a push to `main` lands on the VPS at the next cycle. Reading the launchers
disproves it. `run_ares.sh` runs python at line 16 and its git block at
lines 40–41; `run_monitor.sh` runs python at line 7 and its git block at the end.
Python therefore always runs *before* any pull, so a cycle that pulls new code
has already executed the old code. And the pull happens only when the push
fails. A cycle with nothing to commit pushes trivially, skips the pull, and the
code never arrives. Explicit deployment is mandatory. The branch-only discipline
was right, but for a different reason than the one recorded.

**Review returned deployment on HOLD with six required repairs.** The verifier
had passed three defective candidates, and coverage could report complete over
an incomplete record. All six repairs are done. Second-round scrutiny exposed
two further latent defects:

- **The registry pinned shape, not multiplicity.** A doubled
  `_MONITOR_EVAL["attempted"] += 1`, which would corrupt population
  reconciliation, passed. Each registered statement now carries an exact
  occurrence count.
- **The rollback check spanned the whole repository.** Once the bot has
  committed logs, `git diff <PRE> HEAD` is never empty, and the check invited
  reverting operational state. It is now scoped to deployment-owned paths, with
  `logs/` explicitly preserved.

**The bundle was not independently runnable as first built.** Rerun from the
archive alone, it scored 42/44 and 12/13 because files named in the manifests,
such as `signals.py` and `screener.py`, were missing. This is the same gap that
blocked the first review. The failure was fail-closed, as designed. Every
manifest path is now included.

**Controls: 44 observation, 14 verifier-negative, 13 containment, 71/71, exit 0.**
These ran from a fresh extraction of the bundle. The verifier reports 47
accepted, which is 46 registered additions plus 1 registered replacement. Tests
wrote nothing to `logs/`, and none of the three recorder output files exist.

**Staged on `recorder/monitor-observer-v1`; not committed, pushed or activated.**
Staged diff sha256 `a4d9c480…d79a36`. Bundle:
`review_bundle_monitor_recorder_v2.tgz`. The HOLD remains until the independent
rerun and source review are complete.

Known limitations remain labelled rather than fixed:

- A `SIGKILL` leaves no completion heartbeat.
- Runtime acquisition counts are unmeasured.
- Launcher success cannot be inferred from `tee`'s exit status.

Phase 5, policy unification and AI modelling remain prohibited.

### 2026-10-12 (later) — corrections and new findings from git, parity and VPS logs

Sources: bot commits on `origin/main` up to `432818c`, `logs/tracker_parity_v1.jsonl`
(94 records), `logs/queue_events.jsonl`, VPS `/var/log/syslog.2.gz`,
`/root/ares/cron.log`, and `data/ohlcv/*.csv`. All reads; nothing was changed on
the VPS or in production state.

**Corrections to earlier entries.** The original text is left as written.

- **Oct 7, "the only artefact is Telegram text":** wrong about stored state.
  `run_monitor.sh` commits `logs/` whenever it changes. Commits `c3abed5`
  (16:10) and `708e016` (17:30) record EROC's trailing stop and peak before and
  after each write. What is genuinely unrecorded is the decision inputs (the live
  price seen, effective stop, config) and any hold or skip, which changes no file.
- **Oct 8, BSP "no stated reason":** dropped at 21:01 with
  `drifted -7.5% (max ±5.0%)`. The reason is in `queue_events.jsonl`; the
  dashboard just doesn't show it.
- **Oct 9 session, XP:** dropped at 21:00:54 with `drifted +8.4% (max ±5.0%)`.
- **Oct 9 session, "the monitor wrote nothing":** confirmed. There is no monitor
  commit on Oct 8 or Oct 9.
- **Oct 9 session, ITUB peak:** set to `10.15`, at 2dp, by the 21:00 report from
  an after-hours IB price. See finding F.
- **Oct 9 session, "the report path is WBD's only evaluator":** wrong. See
  finding A.
- **Oct 9 reconciliation, "parity 64 → 84 = 4 cycles × 5 positions":** this
  counted records, not successful evaluations. Five of those 20 records were
  not MATCH.

**A. WBD has had no working stop since Oct 5.**

| Period | Report path | Monitor |
|---|---|---|
| Oct 6 13:31 → Oct 7 13:31 | evaluated against a stale Oct 5 bar (30.95) | no live price |
| Oct 7 21:01 → Oct 9 21:00 | `BOTH_EVALUATIONS_FAILED` ×5, `'NoneType' object is not subscriptable` | no live price |

`data/ohlcv/WBD.csv` holds one row (Oct 5, `Open 0.0`, `Volume 40`), and
`load_stock` returns `None` below two rows. Both price sources failed for WBD at
about the same time, which suggests a symbol-level cause; this is unverified.
Parity recorded the failure every cycle, while the dashboard showed WBD as a
normal position. No current price has been compared with the $29.31 stop since
Oct 5.

**B. Parity: 94 records, 86 MATCH, 8 non-MATCH.** The 8 are WBD ×5 (A) and ITUB
×3 `DECISION_CHANGING_MISMATCH`, on Oct 8 13:31, Oct 8 21:01 and Oct 9 13:31.

The ITUB three are the dual-precision peak. Inline stored `10.120109656333922`;
the shadow under `tracker_v3_2dp` normalised it to `10.12` and returned
`state_update` against inline `hold`. Stops and exit decisions were identical,
and the mismatch cleared once the peak became `10.15` (record 94). The
classifier calls these decision-changing. Whether a representation-only
difference counts is a review decision and has not been reclassified.

**C. Oct 1: the 17:30 monitor run lasted about 3½ hours and overlapped the
report.**

| Time (UTC) | Event |
|---|---|
| 16:10:17 | monitor promotes GMAB from queue to pending after BCE's close |
| 17:30:01 | cron starts `run_monitor.sh`, the only monitor start until 21:00. TMO priced at 666.07, within the session range |
| 17:30 → ~21:01 | stalled, most likely on WBD's price request, holding IB client ID 10 |
| 21:00:01 | `run_ares.sh` starts. Its first IB call (TMO) gets `Error 326: client id already in use` and falls back to daily data |
| 21:01:10 | monitor exits. Telegram message and the "monitor" commit `38910a5` land in the same second |
| 21:01:18 | report gets IB prices identical to the monitor's: WBD 30.94, AMP 494.82, TWST 187.25 |

- **`38910a5` contains the report path's GMAB fill.** `execute_pending_signals`
  is called only from `daily_report.py:54`; the monitor's `git add logs/` picked
  it up. A commit's label identifies the launcher, not the writer.
- **No damage occurred, by luck.** The monitor saves only `if updated:`. Had it
  closed or ratcheted anything, it would have saved the list it loaded at about
  17:30 and could have overwritten GMAB's fill.
- AMP's price equalled the day's close and TWST's was below the session low, so
  the monitor's later prices were after-hours.
- `Error 326` appears only once in the retained `cron.log`.

**D. Gateway and connection.**

- Every Ares process uses `clientId=10` (`data_feed.py:27`), so the monitor and
  the report cannot both be connected at once.
- Only `ib.connect` has a timeout. `qualifyContracts` and `reqHistoricalData`
  are called without one.
- `restart_gateway.sh` restarts nothing unless `pgrep` finds no gateway process
  at all. It never clears a gateway that is running but wedged.

**E. Partial-bar daily fallback.** On Oct 1 at 21:01, TMO was evaluated at
`672.34`, close to the open (672.39) and not the close (652.51). A bar cached
early in the session is reused for up to `CACHE_MAX_AGE_HOURS = 20`. AMP falls
back to daily data at almost every 13:31 cycle.

**F. After-hours prices at 21:00.** `get_live_price` uses `useRTH=False`, and the
21:00 cycle runs after the 20:00 close, so its IB prices are after-hours trades.

**G. Smaller items, flagged and unverified.**

- **GMAB's promotion** at 16:10:17 fell inside the monitor cycle. This is
  circumstantial evidence of a monitor-initiated promotion; `queue_events` has
  no source field.
- **`"rsi": 50.0`** appears on every queue re-check examined (BSP, GMAB, XP,
  INSM, MANE), against XP's `rsi_at_signal` of 85.5. It may be a placeholder.
- **The five unobtainable exits** (SECZ, BCE, GMAB, TWST, PCVX) each have a
  monitor commit holding the state before and after the close. Their status is
  unchanged: incident §7 forbids reconstruction, and those commits can help
  diagnosis only.
- **VPS log retention:** the journal holds about 2 days, syslog uses ISO
  timestamps and rotates weekly, and the bot's log is `/root/ares/cron.log`
  (`logs/cron.log` is Hermes's).

**Decision: pause, checkpoint, audit, simplify.** The work has become too heavy
to validate with the time available. New work stops at a checkpoint, followed by
a full audit of everything done so far, then a cleanup of the repository and VPS
down to the code that is actually required. The recorder stays on HOLD and
undeployed. None of the findings above is to be repaired before that audit.

Phase 5, policy unification and AI modelling remain prohibited.

### 2026-10-12 (later) — checkpoint, audit and cleanup

**Checkpoint.**
- Recorder repair round 2 committed to `recorder/monitor-observer-v1` as
  `0cde474`, byte-identical to the reviewed staged diff (`a4d9c480…`), and pushed
  to that branch only. The branch was already on origin at `2a022f4` from the VPS
  worktree review; the earlier statement that it had never been pushed was wrong.
  It has never been in `main` and has never run on the VPS.
- Tag `checkpoint-2026-10-12` was created at `445a22f`, before the cleanup.

**Audit** (report kept outside the repo, `Olympus/Ares_audit/`).
- **VPS:** at `432818c`, identical to git, no local edits, recorder absent.
- **Repo size:** trading code is 16 files and about 2,800 lines. Parity runtime
  is about 1,750 lines, parity tests and tools about 11,400, documents about
  9,600.
- **Tests:** all 16 tests on `main` exit 0 (they are plain scripts, not pytest).
- **GitHub workflow:** disabled (`if: false`) since Sep 3, so not a writer.
- **New findings:** SSH password login is enabled, with outside attempts in the
  log; the gateway was not running at 04:54 UTC; 38 tracebacks in `cron.log` are
  not yet classified.

**Cleanup.** No trading or parity code was changed.
- **Removed:** the 8 one-off repair, backfill and analysis scripts, and the
  disabled workflow. All are kept at the tag.
- **Moved to `archive/`:** the incident, the Oct 7 reconciliation, and the
  recorder design.
- **Added:** `STATUS.md` as the one-page entry point.
- **Unchanged:** the parity plans stay at the top level, because engine comments
  point to them.
- **Decided:** parity is kept and frozen; the recorder is shelved. Consequence:
  the monitor's decisions remain unrecorded. WBD is still decision pending.

Phase 5, policy unification and AI modelling remain prohibited.

### 2026-10-12 (later) — WBD resolved: cash acquisition, closed by hand

**The cause of finding A (Oct 12) was a delisting, not a bot fault.** WBD was
acquired by Skydance Corporation (NYSE: SKYD, formerly Paramount Skydance). The
company's press release of 2026-10-06 says:

> "Under the terms of the agreement, WBD shareholders received an amount in cash
> equal to $31.01666668 per share."
>
> "WBD shares have ceased trading on NASDAQ, effective today."

stockanalysis.com shows WBD as "Inactive", last trade Oct 5 at $30.95. Yahoo
keeps only a stub row for Oct 5 (`Open 0.0`, `Volume 40`) and has dropped WBD's
earlier history. That explains the 1-row cache file, the parity
`BOTH_EVALUATIONS_FAILED` records and the monitor's "No live price".

**The stop was never at risk.** The $31.02 cash price is well above the $29.31
stop, and WBD traded at about the deal price from Oct 5 onwards. The real cost
was a blocked slot for a week.

**Booking (decided 2026-10-12).** This was a one-off script run by hand
(`close_wbd_acquired.py`, kept outside the repo under `Olympus/Ares_audit/`),
which writes through the bot's own `_close_trade` and `save_trades`.

| Field | Value |
|---|---|
| exit price | $31.01666668 cash, less the standard 0.1% slippage → **$30.99** |
| exit date / reason | `2026-10-06` / `acquired` (post-mortem verdict `other`) |
| P&L | **+$0.56** gross, **−$1.44** after $2.00 commission |
| holding days | 14 (Sep 22 → Oct 6) |
| sample | **excluded**: `contaminated`, reason `corporate_action_acquisition`, phase `pre_clean` |
| shadow tracking | off (`inactive_reason: delisted`) |
| provenance | `exit_source: manual`, plus a `corporate_action` block holding the source URL |

**Verification.**
- Before applying, the script was tested on a disposable copy of `main`: dry
  run, apply, a second run (refused), undo (byte-exact), and the dashboard
  render.
- On the VPS, the file's md5 before (`e9b78e05…`) and after (`d547805f…`)
  matched the test exactly.
- The committed diff changes WBD (19 fields) plus only `holding_days` on the four
  open positions. `save_trades` refreshes those on every run.
- Backup: `/root/ares/backups/virtual_trades.json.20261012T061658.pre_wbd_close`.
- Commit `22142a4`, pushed from the VPS.

**Effect.**

| | Before | After |
|---|---|---|
| Official clean sample | 6 closed, −$57.25 | unchanged |
| Open positions | 5 | **4** (TMO, AMP, EROC, ITUB); one slot free |
| Pre-clean history | 6 closed, −$21.13 | 7 closed, −$22.57 |
| All history | 12 closed, −$78.38 | 13 closed, −$79.82 |

**Registered, not repaired:** the bot has no way to detect a delisting (now on
`STATUS.md`).

Phase 5, policy unification and AI modelling remain prohibited.

### 2026-10-12 → 13 — recorder accepted (v3 → v7); MANE in, 5/5 slots

**Trading (the bot ran unchanged, all four cycles).**
- **21:32 MYT report** (`138ccf0`): 4/5 slots, MANE pending for the next open,
  INSM queued (conf 3), 103 screened.
- **00:10 / 01:30 MYT monitor** (`00e35c9`, `879d7d8`): no exits.
  - EROC −5.0% / −4.8% at $11.98 / $12.01, against a $11.76 trailing stop.
  - AMP's peak rose to 2.2%.
- **05:01 MYT report** (`0fe8f3b`):
  - MANE filled at **$130.12** (1.15 shares, entry 2026-10-12), SL $118.49,
    TP $153.54. **5/5 slots.**
  - INSM is still queued; it cannot enter while the slots are full. New signal:
    DHT, momentum_breakout, conf 3.
- Official clean sample unchanged: 6 closed, −$57.25, CI includes zero.
- System:
  - swap 171 → 250 MB (79 MB paged out in 7.5 h; still labelled "inert")
  - stall 402 ms. Monday's ⚠️ 1200 ms covered the 64.5 h weekend gap.

**Recorder review: accepted after five delta rounds.** Every finding was in the
inspection tool or the procedure, never in the recorder. Recorder code is
unchanged since v2.

| Round | Finding (reviewer) | Fix |
|---|---|---|
| v3 | cycles grouped by `invocation_token` (1 in every process); `&& … \|\| echo ok` checks pass on git errors | `tools/inspect_monitor_cycles.py`: commit-delimited cycles, matched by `cycle_id`, fail-closed |
| v4 | prepended evidence accepted; identity reused from history accepted; minimal forged record accepted | byte-prefix append check; identity seeded from full history; independent record contract |
| v5 | null lineage / bad timestamps PASS on a zero-position cycle; `inf` accepted | lineage = ran-on commit, digests = committed files; timestamps parsed and ordered; non-finite rejected |
| v6 | manifests holding only an unrelated file PASS | exact registered path set per manifest, pinned to the recorder |
| v7 | **accepted**: "no additional blocker found" | — |

- **Evidence at acceptance:**
  - inspection controls 31/31, with each of the tool's 31 checks
    mutation-tested and none missed
  - observer 44/44, verifier 14/14, containment 13/13; isolation verifier
    47 accepted
  - `main` 16/16
  - bundles in `Olympus/review_bundle_monitor_recorder_v3…v7`
- **Producer limitation (open, non-blocking):** the recorder itself writes
  `Infinity` with `record_complete: true`. The inspection rejects it.

**Activation (main-flow step 1), 2026-10-13 ~13:40 MYT, inside the window.**
- **Laptop:**
  - `main` fast-forwarded to `0fe8f3b` (= PRE)
  - `git merge --squash recorder/monitor-observer-v1` → **`5fd13e9`**: one
    parent, 15 files, +5554 / −3, no `logs/`
  - its tree `9336de7` equals the dry run; the recorder files equal the
    accepted `28ae5f0`
- **Rollback pre-test (§3.5), in a throwaway clone:**
  - the inverse applies cleanly
  - deployment-owned paths restored exactly
  - `monitor_trades.py` back to `1f81383a…`
  - the test revert was not pushed
- **VPS:**
  - clean, `--ff-only` `0fe8f3b..5fd13e9`, HEAD = `5fd13e9`
  - md5 `monitor_trades.py` `22a9da16…`, `engine/monitor_observer.py` `059f3f82…`
  - Python 3.12.3
  - on the live tree: 44/44, 14/14, 13/13, 31/31, isolation rc 0
  - tree still clean afterwards
- **Rollback (VPS):**
  `git checkout 0fe8f3b -- monitor_trades.py && rm -f engine/monitor_observer.py`
- **First recorder cycles:** 00:10 and 01:30 MYT, Oct 14 (16:10 and 17:30 UTC,
  Oct 13). Inspect in the morning with
  `tools/inspect_monitor_cycles.py <R> <M1> <M2>`, where `<R>` is the 21:30 MYT
  report commit.
- **Nothing is to be pushed to `main` until that inspection is done.** A push
  makes the bot's `pull --rebase` re-parent its monitor commits, which breaks
  the commit chain the inspection checks.

Phase 5, policy unification and AI modelling remain prohibited.

### 2026-10-13 → 14 — EROC and MANE closed; DHT and INSM in; first recorder night

**21:32 MYT report (Oct 13).**
- **EROC closed by the report path: trailing_stop, −6.8%** (peak 3.65%,
  reversal). The TS was $11.76; the last monitor reading was $12.01.
  - Clean sample: −$57.25 → −$69.43 (−$12.18).
  - This happened before the first recorder cycle, so EROC is correctly absent
    from recorder evidence.
- 4/5 slots, with DHT pending for the next open. INSM queued (conf 3, drift
  −2.4%). Scan: 103 screened, 0 signals.
- Clean sample: 7 closed, W 1/7, −$69.43, net/trade −6.66% ± 6.69%, CI includes
  zero.

**00:10 / 01:30 MYT monitor: the first two cycles with the recorder active.**
- **MANE closed by the monitor at $118.49, stop_loss (−9.0%)**, one day after
  entry at $130.12 (peak −0.0%, signal_failed).
  - Clean sample: −$69.43 → −$84.94 (−$15.51).
  - **Recorded split (first real evidence):**

    | Field | Value |
    |---|---|
    | `observed_live` | **$118.29** (−9.09%) |
    | `close_price_argument` | $118.49 (the stop) |
    | `booked_exit_price` | **$118.37** (= stop × (1 − 0.1%)) |

    - The price had already gone $0.20 through the stop when observed.
    - The booked fill is $0.08 per share *better* than the observed price,
      because slippage is applied to the stop, not to the live price.
    - Here the effect is about $0.09, but for a gap-through it is unbounded
      (the PCVX question).
    - Realized −$15.51 = (118.37 − 130.12) × 1.15 − $2.00 in costs.
  - **The first recorded close, so the inspection will check the close path,
    not just holds.**
- One Live Check message was received (TMO $663.35 +1.3%, AMP $492.69 +0.1%,
  ITUB $9.88 −2.4%). Which cycle closed MANE is for the inspection to show.

**05:01 MYT report (Oct 14).**
- **DHT** entered at $26.17 (5.7 shares), SL $24.74, TP $30.88.
- **INSM** entered at $98.28 (1.5 shares), SL $93.05, TP $108.11.
- **5/5 slots.** IFF queued (momentum_breakout, conf 3, uptrend).
- Clean sample: **8 closed, W 1/8 (12%), −$84.94**, expectancy −$10.62, PF 0.03.
- Net/trade **−7.13% ± 5.86%**, i.e. about −13.0% to −1.3%.
  - **The CI no longer includes zero, and the dashboard dropped its CI line
    instead of saying so.**
  - That is a wording gap; register it, don't repair it (FROZEN). At n=8 this is
    not a conclusion about edge.
- All history: 15 closed, W 3/15, −$107.51.
- System:
  - RAM 873/1967 MB
  - ⚠️ swap 295 → **457 MB** (168 MB paged out in 7.5 h); stall 744 ms
  - Earlier in the 16.5 h to 21:32: swap 295 MB, 51 MB paged out, stall 935 ms
  - The swap rise overlaps the first recorder cycles and the Hermes soak on the
    VPS.

**Recorder inspection, run 2026-10-15 on the VPS: PASS, rc 0.**
- Command: `inspect_monitor_cycles.py d654695 4e0458c 1a1dd73`
- Cycle 1 (`4e0458c`, 16:10 UTC): 4 records (AMP, ITUB, MANE, TMO),
  `cycle_id b8495edb`. **MANE was closed in this cycle.**
- Cycle 2 (`1a1dd73`, 17:30 UTC): 3 records (AMP, ITUB, TMO),
  `cycle_id e0752c44`. Its population is correctly anchored on cycle 1's
  result, without MANE.
- The first live close passed the full close-path contract (requested,
  completed, booked price present, save attempted and completed).

### 2026-10-14 → 15 — quiet; DHT and ITUB near their stops; swap recovered

**21:32 MYT report (Oct 14).**
- 5/5 slots, no closes. IFF ranked (conf 3, drift −0.1%). Scan: 103 screened,
  0 signals.
- Clean sample unchanged: 8 closed, −$84.94, −7.13% ± 5.86%.
- System:
  - RAM 1014 MB
  - **swap 457 → 175 MB** (inert, 0 MB paged out in 16.5 h).
    - *Corrected 15 Oct:* this was **not** swappiness. Swappiness does not
      drain swap, and the Hermes snapshot at 08:37 UTC already showed 174 MB.
    - A ~280 MB drop means a process that held swapped-out pages exited. It is
      consistent with the 265 MB `main` process started at ~06:22 UTC (see the
      host note below).
  - ⚠️ stall **1253 ms in 16.5 h**, flagged as a "memory pressure event" even
    though swap was inert.
  - Disk 12/47 GB.

**00:10 / 01:30 MYT monitor (recorder cycles 3 and 4).** No exits.

| | 00:10 | 01:30 | stop | gap to stop at 01:30 |
|---|---|---|---|---|
| TMO | $673.78 (+2.9%) | $673.26 (+2.9%) | $634.25 | 5.8% |
| AMP | $492.54 (+0.1%) | $491.72 (−0.1%) | $472.99 | 3.8% |
| ITUB | $9.64 (−4.7%) | $9.74 (−3.8%) | $9.32 | 4.3% |
| DHT | $25.12 (−4.0%) | $25.06 (−4.2%) | $24.74 | **1.3%** |
| INSM | $97.67 (−0.6%) | $97.14 (−1.2%) | $93.05 | 4.2% |

- TMO's live +2.9% is below its recorded 4.6% peak, so no ratchet and no peak
  advance.
- DHT is the nearest to its stop.

**05:01 MYT report (Oct 15).**
- 5/5 slots, no closes. IFF still ranked (drift +1.6%).
- Scan: 103 screened, 2 signals; SKYD shown (mean_reversion, conf 3, range).
- Clean sample unchanged. All history: 15 closed, −$107.51.
- System: RAM 1089 MB, swap 210 MB (inert, 35 MB in 7.5 h), stall 410 ms.

**Recorder inspection, run 2026-10-15 on the VPS: PASS, rc 0.**
- Command: `inspect_monitor_cycles.py 00f80af 79aa70a eea639d`
- Cycle 1 (`79aa70a`): 5 records (AMP, DHT, INSM, ITUB, TMO), `cycle_id ca5e4cb5`.
- Cycle 2 (`eea639d`): 5 records, the same five, `cycle_id fb769ed7`.

**Main-flow step 2 done: CHECKPOINT.**
- History since activation is one linear chain:
  `5fd13e9 → d654695 → 4e0458c → 1a1dd73 → f75731b → 00f80af → 79aa70a → eea639d → 3e45d03`,
  with no rebases and no manual commits.
- Four recorder cycles, 16 records, one close and 15 holds, all PASS.
- **Still open:**
  - the producer can write non-finite values marked complete (the inspection
    rejects them)
  - the inspection checks consistency, not price correctness
  - a cycle without a commit cannot be inspected

**Host note: swap and stall, 12–14 Oct** (from the Hermes thread's analysis).
- **Swap-out bursts:** 25k pages on 12 Oct and 43k pages on 13 Oct, both within
  20:52–21:07 UTC.
  - Both overlap `run_ares.sh` at 21:00 UTC (21:00:01–21:01:29).
  - They were eviction bursts, not thrashing: very few pages came back in,
    about 1 GB stayed free, and load was 0.06.
- **The Ares dashboard samples inside that burst.** `build_dashboard.py` runs at
  the end of the 21:00 run, so:
  - the 05:01 MYT reading of 457 MB / ⚠️ is mid-burst
  - the tail of the burst lands in the next window, a likely source of the
    21:32 MYT ⚠️ stall (1253 ms)
- **The "Stall" figure** is the PSI `full` total delta against
  `STALL_WARN_MS = 1000`. 1.25 s in 16.5 h is about 0.002% of wall time, so
  "memory pressure event" overstates it, like the word "thrashing". Register,
  don't repair (FROZEN).
- **Environment change, 14 Oct:** `vm.swappiness` 60 → 10, persisted in
  `/etc/sysctl.d/99-swappiness.conf`, applied by the Hermes thread.
  - It is host-wide and touches no Ares code or config, so the freeze holds.
  - **Swap and stall readings before and after 14 Oct are not comparable.**
- **The 265 MB `main` process is identified (15 Oct): it belongs to Hermes, not
  Ares.**
  - PID 876512: exe `/usr/lib/wine/wine64`, cwd `/root/Hermes`,
    cgroup `cron.service`.
  - Launched by a Hermes cron job at 06:22:17 UTC on 14 Oct, now reparented to
    PID 1, RSS 227 MB.
  - It has its own `Xvfb` (PID 876392, started 06:21:58).
  - The Ares gateway is not involved: its `Xvfb` (PID 1241) has been up since
    11 Sep, and `pgrep -f ibgateway` does not match PID 876512.
  - Only two Xvfb exist, so there is no display leak.
  - The ~280 MB swap release is consistent with the previous wine/MT5
    instance exiting at that restart.
  - Root cause (Hermes, 15 Oct): unattended-upgrades updated libx11 at
    06:21:57, and needrestart restarted `cron.service` and `xvfb.service`
    (Hermes :99). At its next connection the MT5 terminal installed build
    6259 and relaunched itself. This has happened 6 of 6 times since 11 Sep,
    always in the 05:40–07:00 UTC upgrade window, outside every Ares cron time.
  - Ares exposure: none. `cron.service` has `KillMode=process`, so the
    cron-launched gateway survives a cron restart. Ares :1 is not a systemd
    unit, so needrestart only flags its old libx11 and never restarts it.
- **swappiness=10, first night (14 Oct 21:07 UTC):** swap was flat at 210 MB
  and there was no alert. Hermes confirms this over the week.
- **Pending host reboot** (kernel 6.8.0-139 running; libc6 and kernels
  142/146/147 installed). It is not automatic. A joint weekend test is
  planned; Ares owns choosing the slot.
  - There is no `@reboot` entry or systemd unit for Ares. The only automatic
    recovery is `restart_gateway.sh` on weekdays at 13:00 UTC, which starts
    `Xvfb :1` and the gateway 30 minutes before the first `run_ares.sh`.
- **Restart-path check (15 Oct 05:07 UTC, no reboot): PASS.**
  - The gateway was down, which is normal at that hour. `restart_gateway.sh`
    started it, it listened on `*:4002` within minutes, and the redundant
    `Xvfb :1` launch exited harmlessly (only PID 1241 remains).
  - Gateway JRE: `/opt/i4j_jres/.../17.0.16.0.101-zulu/bin/java`, the IB
    install4j bundle. It is neither the system openjdk nor
    `/root/jdk-17.0.12`, so the 23 Sep openjdk-17/21 upgrade does not
    affect it.
  - The gateway was stopped again at about 05:10 UTC, restoring the normal
    state for the 13:00 UTC cron. Both Xvfb (:1 and :99) were untouched.
  - The VPS fast-forwarded to `abfb784` (checkpoint-2026-10-15) and the tree
    is clean.
  - Side effect: the manual start overwrote `/root/ares/gateway.log`
    (`start_gateway.sh` uses `>`), so the log from the 14 Oct instance is
    lost.
  - The weekend reboot test stays open. Use the same checks, then stop the
    gateway again afterwards.
- **Reboot test agreed with Hermes: Sat 17 Oct, 00:00–04:00 UTC (08:00–12:00
  MYT).** Fallback is Sat 24 Oct.
  - Final plan (Hermes, 15 Oct): reboot at 01:00 UTC (09:00 MYT). Both
    stacks' checks should be done by about 01:30 UTC. Ares's steps were
    accepted as written.
  - The swappiness decision now uses the post-reboot 21:07 UTC samples from
    19–23 Oct. The 14–16 Oct samples are supporting evidence only.
  - Hermes-owned, no Ares sign-off needed: excluding `xvfb.service` from
    needrestart, and a soak alert for MT5 connects over 10 s or a build
    change.
  - Hermes-side artefact: the manual Saturday soak record shows a meaningless
    negative swap/stall delta and raises no alert. Its Mon 00:07 UTC reading
    is valid.
  - Run the Ares gateway check before about 03:30 UTC, ahead of IB's nightly
    reset at about 23:45 ET (03:45 UTC).
  - Known artefact: the reboot resets `/proc` PSI and vmstat counters.
    `build_dashboard.py` clamps the negative delta to 0, so Monday 19 Oct's
    21:30 MYT dashboard will falsely show 0 ms stall and 0 MB paged out over a
    window of about 64 h. The next reading (Tue 05:01 MYT) is valid again.
  - Final confirmation: inspect Monday's monitor cycles with
    `tools/inspect_monitor_cycles.py`. The expected result is PASS.
- **Next week, only if the 21:07 UTC swap-out persists at swappiness 10:**
  observe the memory peak of the 21:00 run with a host-side sampler outside
  the repo (Ares stays frozen).
  - One hypothesis to test: the price cache grew from 866 to 943 symbols in
    four days.

### 2026-10-15 → 16 — DHT stopped out; SKYD in; small 21:00 swap burst returns

**21:32 MYT report (Oct 15).**
- **DHT closed by the report path: stop_loss, −5.6%** (peak −0.01%,
  signal_failed), one day after entry at $26.17.
  - The last monitor reading was $25.06, 1.3% above the $24.74 stop.
  - Clean sample: −$84.94 → −$95.22 (−$10.28). This matches
    (24.74 × 0.999 − 26.17) × 5.7 − $2.00 = −$10.29.
  - It closed before the night's monitor cycles, so DHT is correctly absent
    from recorder cycles 5 and 6.
- 4/5 slots, SKYD pending for the next open. Ranked queue: IFF (conf 3, drift
  +2.8%), BSP (conf 3, drift −4.7%). Scan: 103 screened, 1 signal (SKHY,
  mean_reversion, conf 2).
- Clean sample: **9 closed, W 1/9 (11%), −$95.22**, expectancy −$10.58, PF 0.02.
  Net/trade −7.10% ± 5.17% (about −12.3% to −1.9%); the CI line is still
  missing (the wording gap registered on 14 Oct).
- All history: 16 closed, W 3/16, −$117.79.
- System: RAM 1049 MB, swap 160 MB (inert, 4 MB in 16.5 h), stall 925 ms
  (below the 1000 ms flag). This window held the 05:40–07:00 UTC upgrade
  window and the 05:07 UTC manual gateway start.

**00:10 / 01:30 MYT monitor (recorder cycles 5 and 6).** No exits.

| | 00:10 | 01:30 | stop | gap to stop at 01:30 |
|---|---|---|---|---|
| TMO | $691.29 (+5.6%) | $692.88 (+5.9%) | $634.25 | 8.5% |
| AMP | $495.62 (+0.7%) | $495.82 (+0.8%) | $472.99 | 4.6% |
| ITUB | $9.62 (−4.9%) | $9.58 (−5.3%) | $9.32 | **2.7%** |
| INSM | $99.13 (+0.9%) | $98.12 (−0.2%) | $93.05 | 5.2% |

- TMO's peak advanced (live $692.88), but the 10% trail from it is $623.59,
  below the $634.25 SL, so the TS stays at the SL. The trail only lifts the
  TS once the peak passes $704.72 (+7.67% over entry).
- ITUB is now the nearest to its stop.
- Not inspected; the next required inspection is Monday's cycles after the
  reboot.

**05:01 MYT report (Oct 16).**
- **SKYD** entered at $7.07 (21.1 shares), SL $6.18, TP $7.77.
  - The TP is +9.9% but the SL is −12.6%, so reward:risk is below 1 at entry.
    Register, don't repair (FROZEN).
- **5/5 slots.** Peaks: TMO 4.6% → 6.4%, INSM −0.0% → 0.9%; TS unchanged.
- Ranked queue: IFF (drift +2.4%), BSP (drift +4.3%). Scan: 103 screened,
  2 signals; BSP shown (momentum_breakout, conf 3, uptrend).
- Clean sample unchanged: 9 closed, −$95.22.
- System:
  - RAM 1074 MB, swap 160 → **202 MB (42 MB paged out in 7.5 h)**
  - ⚠️ stall **1055 ms in 7.5 h**, just over the 1000 ms flag
  - cache 987 symbols (866 on 12 Oct)
- **Attribution: the 21:00 UTC Ares run, not a Hermes restart.**
  - The window is 13:32–21:01 UTC on 15 Oct. It does not contain the
    05:40–07:00 UTC upgrade window, where every needrestart/MT5 restart has
    occurred.
  - It ends with `run_ares.sh` at 21:00 UTC, and `build_dashboard.py` samples
    inside that run, the same pattern as 12 and 13 Oct.
  - **Hermes event, 15 Oct 19:07 MYT (11:07 UTC):** "MT5 connection lost,
    skipping cycle (1st failure)". The terminal restarted at the next Hermes
    cycle. This was not the upgrade-window restart.
    - It falls in the *previous* window (21:01–13:32 UTC, read at 21:32 MYT):
      stall 925 ms, 4 MB paged out, no flag. It is not inside the flagged
      window.
    - Possible indirect link, unproven: a freshly relaunched terminal has
      idle pages that the kernel evicts at the 21:00 run peak. Hermes's
      15 Oct 21:07 UTC soak sample can show whether those pages were MT5's.
    - This is the second MT5 connection loss in 3 days (13 Oct 11:52 UTC,
      15 Oct 11:07 UTC). Hermes's "a single occurrence needs no action" no
      longer applies. The item belongs to Hermes; Ares has no exposure.
    - *Checked 17 Oct 00:5x UTC, before the reboot:* Hermes Xvfb :99 is PID
      937657, `ActiveEnterTimestamp` Thu 15 Oct 06:07:53 UTC, NRestarts=0.
      - So a 7th upgrade-window `xvfb.service` restart happened on 15 Oct.
        It is the usual needrestart pattern, five hours **before** the
        11:07 UTC connection loss. The connection loss is a separate event,
        like 13 Oct's.
      - `journalctl -u xvfb.service` shows no entries, so the journal cannot
        confirm what triggered it. The Hermes upgrade logs can.
- **swappiness=10, second night:** the burst returned but was smaller,
  +42 MB against +99 MB (12 Oct) and +163 MB (13 Oct); 14 Oct was flat.
  - Two nights, mixed. Hermes's 15 Oct 21:07 UTC soak sample gives the page
    count.
  - No action. The decision stays with the post-reboot 19–23 Oct samples.
  - The cache-growth hypothesis is still open (+121 symbols in four days).

### 2026-10-16 → 17 — no exits; ITUB within 1% of its stop; swap burst +95 MB

**21:31 MYT report (Oct 16).**
- 5/5 slots, no closes. Clean sample unchanged: **9 closed, W 1/9, −$95.22**.
- Peaks: TMO 6.4% → 6.5%, INSM 0.9% → 1.1%, SKYD −0.0% → 1.1%.
- **SKYD TS $6.18 → $6.44**, its first trail lift: the 10% trail from a peak
  of about $7.15 is $6.44, above the $6.18 SL. This shows the trail has no
  activation level (same as EROC's 11.76 at a 3.6% peak).
- Queue: IFF only (drift +2.4%). **BSP was dropped by design**:
  `queue_events.jsonl` 13:31:03 UTC, "drifted +7.3% (max ±5.0%)" (signal
  $53.46, live $57.38). Scan: 103 screened, 0 signals.
- System: RAM 996 MB, swap 205 MB (inert, 3 MB in 16.5 h), stall 449 ms,
  cache 1008 symbols.

**00:10 / 01:30 MYT monitor (recorder cycles 7 and 8).** No exits.

| | 00:10 | 01:30 | stop (TS) | gap to stop at 01:30 |
|---|---|---|---|---|
| TMO | $684.98 (+4.7%) | $687.90 (+5.1%) | $634.25 | 7.8% |
| AMP | $502.80 (+2.2%) | $497.92 (+1.2%) | $472.99 | 5.0% |
| ITUB | $9.46 (−6.5%) | $9.41 (−7.0%) | $9.32 | **1.0%** |
| INSM | $97.17 (−1.1%) | $96.64 (−1.7%) | $93.05 | 3.7% |
| SKYD | $7.19 (+1.7%) | $6.86 (−3.0%) | $6.47 | 5.7% |

- The monitor ratcheted SKYD: a new peak of $7.19 × 0.9 = $6.47 (TS $6.44 →
  $6.47). This is the known monitor-writes-state behaviour; the morning report
  carried it through (peak 1.7%, TS $6.47).
- ITUB at $9.41 is the closest any open position has come to its stop this
  week, $0.09 above it.
- Cycles 7 and 8 have not been inspected (not required; the next required
  inspection is Monday's cycles).

**05:01 MYT report (Oct 17).**
- 5/5 slots, no fills, no closes. Clean sample unchanged.
- Queue: IFF (drift +2.8%). Scan: 103 screened, 1 signal, **AVTR**
  (momentum_breakout, conf 3, uptrend), not shown in the queue.
  - **Display lag, not a defect.** AVTR *was* queued (`queue_events.jsonl`
    21:01:26 UTC; in `signal_queue.json` at `f83d400`).
  - The dashboard shows only the ranked queue, `queue_ranked.json`, which is
    rebuilt at the next report. At `f83d400` it holds IFF alone.
  - The same thing happened on 15 Oct at 05:01 MYT. BSP and SKYD were in
    `signal_queue.json` at `3e45d03` but not on the dashboard; BSP showed up
    ranked at the next report.
  - Expect AVTR in the ranked queue at Mon 19 Oct 21:30 MYT. The dashboard
    wording gap (unranked additions not shown) is registered, not repaired
    (FROZEN).
- System:
  - RAM 1049 MB, swap 205 → **296 MB (95 MB paged out in 7.5 h)**
  - stall 445 ms (no flag)
  - cache 1029 symbols (+163 since 12 Oct)
- **swappiness=10, third night:** the 21:00 UTC burst is back at about the
  pre-change size. The nights so far: 14 Oct flat, 15 Oct +42 MB, 16 Oct
  +95 MB, against +99 MB (12 Oct) and +163 MB (13 Oct) at swappiness 60.
  - Low stall each night (≤ 449 ms) means it is still eviction, not pressure.
  - No action. The reboot reset swap, so the deciding 19–23 Oct samples are
    a clean baseline. The cache-growth hypothesis stays open.

### 2026-10-17 (Sat) — joint host reboot test: Ares PASS

**Before the reboot (00:49 UTC).**
- Kernel 6.8.0-139, up 35 days. HEAD `f83d400` (Ares V3 report
  2026-10-16_21:01), clean tree.
- crontab md5 `cb91ed9c51ee0ca9eb83388df640347c`.
- Gateway stopped. Xvfb :1 PID 1241, Hermes :99 PID 937657.

**Reboot at about 01:02 UTC (09:02 MYT)**, run once by the user.

**After the reboot (01:05 UTC).**
- Kernel **6.8.0-147-generic**, so the pending kernel and libc6 are now active.
- cron is active, and the crontab md5 is unchanged.
- The clock is synchronized and NTP is active, so the inspection tool's 2 s
  clock slack holds.
- HEAD `f83d400` is unchanged and the tree is clean.
- Only Xvfb :99 came back (PID 778, systemd). Ares :1 was absent, as
  expected, because Ares has no boot autostart.

**Gateway start test (01:06–01:07 UTC): PASS.**
- `restart_gateway.sh` reported "DOWN — restarting...", then "restarted
  successfully" at 01:07:26.
- java (PID 1419) is listening on `*:4002`. Xvfb :1 was started by the script
  (PID 1382).
- The gateway was then stopped with `pkill -f ibgateway`, which confirmed
  "gateway stopped". Both Xvfb (:1 PID 1382, :99 PID 778) stay up, and the
  tree is still clean.
- This is the normal weekend state for the Mon 13:00 UTC cron.

**Hermes side (reported by the Hermes thread): FAIL, then PASS after a fix.**
- xvfb.service and the rpyc bridge recovered by themselves.
- The MT5 terminal installed pending build 6271, which refused to start on
  Wine 9.0 ("A debugger has been found running in your system"). Any terminal
  restart would have hit this; the reboot only triggered it.
- Fix: Ubuntu wine packages removed, WineHQ repo added, winehq-stable 11.0
  installed. The terminal connected at 01:50 UTC.
  - The install pulled in avahi-daemon; it is disabled and stopped, and UDP
    5353 is closed.
  - WineHQ is not in the unattended-upgrades origins, so Wine won't upgrade
    itself.
  - Prefix backup: `/root/wine_prefix_backup_20261017.tgz`.
- **Ares exposure: none.** No Ares code, config, Java, gateway or Xvfb :1 was
  touched. Wine is Hermes-only.

**Still to confirm:**
- Mon 19 Oct, 13:00 UTC: the cron gateway start.
- Mon 19 Oct, 21:30 MYT dashboard (window starts at the reboot):
  - paged-out should be about 0;
  - stall may show ⚠️, because the window holds about 2.7 s of stall from the
    Hermes maintenance (01:32–01:50 UTC: tar, apt, wineboot).
  - Either way it is a reboot/maintenance artefact, not a reading.
- Tue 20 Oct: run `inspect_monitor_cycles.py` on Monday's two monitor cycles;
  expect PASS.

Phase 5, policy unification and AI modelling remain prohibited.

### 2026-10-19 → 20 — Finviz screener broken (0 candidates); reboot confirmed; swap +171 MB (7.5 h window total)

**Reboot test closed.**
- Mon 13:00 UTC: the cron gateway start worked. Both reports ran, and both
  monitor cycles got live prices.
- The 21:32 MYT dashboard showed "0 MB paged out, 0 ms stall in 64.5 h". This
  is **not a reading**:
  - `build_dashboard.py` clamps negative deltas to zero
    (`max(0, now − prev)`).
  - The reboot reset the kernel counters, so the delta against Friday's
    baseline was negative.
  - The ~2.7 s Hermes maintenance stall was hidden, not absent.
  - The 05:01 window (both readings after the reboot) is the first valid one.

**21:32 MYT report (Oct 19).**
- 5/5 slots, no fills, no closes. Clean sample unchanged: **9 closed, W 1/9,
  −$95.22**. Peaks unchanged.
- Queue: **AVTR ranked #1** (drift −0.2%), as predicted on 17 Oct.
- **Scan: 3 screened, 0 signals** (normally about 103). See the Finviz entry
  below.
- System: RAM 965 MB, swap 138 MB, cache 1074 symbols.

**00:10 / 01:30 MYT monitor (recorder cycles 9 and 10).** No exits, no
ratchets.

| | 00:10 | 01:30 | stop (TS) | gap to stop at 01:30 |
|---|---|---|---|---|
| TMO | $677.09 (+3.4%) | $677.60 (+3.5%) | $634.25 | 6.4% |
| AMP | $484.46 (−1.5%) | $483.36 (−1.8%) | $472.99 | 2.1% |
| ITUB | $9.46 (−6.5%) | $9.41 (−7.0%) | $9.32 | **1.0%** |
| INSM | $95.78 (−2.5%) | $96.02 (−2.3%) | $93.05 | 3.1% |
| SKYD | $6.71 (−5.1%) | $6.71 (−5.1%) | $6.47 | 3.6% |

- **Inspection: PASS, rc 0.** Command:
  `inspect_monitor_cycles.py 59c71c6 63b1b4f ff5b3cb`.
  - Cycle `dbd6fc0c` (`63b1b4f`) and cycle `0d0c4c97` (`ff5b3cb`).
  - 5 records each: AMP, INSM, ITUB, SKYD, TMO.
  - The recorder is unaffected by the reboot.

**05:01 MYT report (Oct 20).**
- 5/5 slots, no changes. AVTR still ranked #1 (drift −0.8%). Scan: 3 screened,
  0 signals.
- **Cache flat at 1074 symbols**, the first night without growth. This is a
  second symptom of the empty screener.
- System:
  - RAM 972 MB, swap 138 → **308 MB (171 MB paged out in 7.5 h)**
  - ⚠️ **stall 1386 ms**, above the 1000 ms flag
- **swappiness=10, first deciding sample (19 Oct 21:07 UTC):** the largest
  burst so far. *(Corrected 20 Oct: this is a 13:31–21:01 UTC window total,
  not a 21:00 burst. See the next entry.)*
  - The setting survived the reboot: `vm.swappiness = 10` and
    `/etc/sysctl.d/99-swappiness.conf` both confirmed.
  - This night's Ares 21:00 run did *less* work than usual: 3 symbols
    screened, cache flat. That weakens the hypothesis that Ares's price cache
    growth drives the burst.
  - Hermes owns the swappiness decision (19–23 Oct samples). Ares only
    supplies these figures.

**Finviz screener broken, cause found (read-only diagnosis, 20 Oct).**
- `cron.log`:
  - Fri 13:32 and 21:01 UTC worked (102 and 455 unique candidates).
  - Mon 13:32 and 21:01 UTC: all five screens returned `0 stocks` with **no
    `error` line**.
  - "3 screened" is just SPY/QQQ/IWM, which `daily_report.py` always appends.
- `last_scan_summary.txt`: `candidates_screened:3`, `signals_found:0`.
- Library: `finvizfinance` **1.5.0** in `venv`, which is also the newest on
  PyPI, so no upgrade fixes it. `Overview().screener_view()` returns `None`.
- Cause: **Finviz changed its site over the weekend.**
  - `/screener.ashx` now gives 301 → `/screener` → `/screener/stocks`.
  - The final page is a real screener page (HTTP 200, 357 KB, title "Stock
    Screener - Overview").
  - The page has 0 `quote.ashx?t=` links; ticker links are now `/quote?t=`
    (40 on the page).
  - The page has **no `<table>` elements**. The library parses a table, finds
    none and returns `None` silently.
- **Not reboot-related.** It broke between Fri 21:01 and Mon 13:32 UTC, and
  the reboot falls in that window only by coincidence.
- Impact:
  - No new candidates or signals until it is fixed, so the clean sample stops
    growing. It is not contaminated: no trade was made on a wrong universe.
  - Still normal: open positions, stops (report path and the monitor's live
    IB prices), queue validation (AVTR), the cache (prune only > 30 days) and
    the recorder.
- **Registered gap (silent failure):**
  - `_run_screen` turns an empty or `None` result into `[]` without raising.
  - So the fixed-watchlist fallback in `daily_report.py` never fires, and the
    dashboard shows no warning; "3 screened" is the only visible sign.
- **Decision (user, 20 Oct): Option A, wait.**
  - No code, config or venv change while FROZEN.
  - Rejected for now:
    - fixed-watchlist fallback: changes the universe and the sample
      definition
    - own parser for the new page: a code change that needs review
  - Revisit at the weekly checkpoint if upstream has not fixed it.

Phase 5, policy unification and AI modelling remain prohibited.

### 2026-10-20 — Hermes host data (sysstat): swap growth is in the gateway windows, not 21:00; dashboard window clarified

No action. Observation only; Ares stays FROZEN.

**What the dashboard swap/stall figures cover** (answer to Hermes Q4, from
`build_dashboard.py` `memory_pressure()`):
- Each value is a delta between two consecutive `build_dashboard.py` runs:
  `pswpout` from `/proc/vmstat` and PSI `full total` from
  `/proc/pressure/memory`. Negative deltas are clamped to 0. The baseline is
  `logs/psi_state.json`, rewritten on every run.
- Only `run_ares.sh` calls it (`run_monitor.sh` does not). So the windows are:
  - 05:01 MYT dashboard: 13:31 → 21:01 UTC (7.5 h)
  - 21:3x MYT dashboard, Tue–Fri: 21:01 → 13:31 UTC (16.5 h)
  - 21:3x MYT dashboard, Mon: Fri 21:01 → Mon 13:31 UTC (64.5 h)
- So the 20 Oct 05:01 figures (171 MB paged out, 1386 ms stall) are **totals
  for 13:31–21:01 UTC Monday**. That window contains the 16:00–17:30 UTC
  gateway session and the tail of the 13:30 run. The dashboard cannot place
  an event within its window.

**Correction to earlier entries.** The figures logged as "21:00 burst" were all
7.5 h window totals, never 21:00-slot measurements:
- 15→16 Oct: +42 MB
- 16→17 Oct: +95 MB
- 19→20 Oct: +171 MB

Only Hermes's sysstat and soak data can place swap growth in time.

**Hermes sysstat, Mon 19 Oct (UTC):**
- 21:00–21:10 slot: flat (316 MB from 20:40 to 21:20). The 21:07 soak stall
  was 113 ms.
- Swap growth that day (38 → 316 MB):
  - 13:30–13:40: +105 MB (Ares 13:30 run, gateway up)
  - 16:00–17:30: +167 MB (gateway up, heaviest 17:10–17:30)
- Largest stall: 309 ms per 15 min, about 1 GB RAM free. No impact.
- The 21:00–21:10 slot over time:

| Night | swappiness | 21:00–21:10 swap |
|---|---|---|
| 13 Oct | 60 | +163 MB |
| 14 Oct | 10 | +36 MB |
| 19 Oct | 10 | ~0 (only 3 symbols screened) |

**Consistency check on Hermes's figures:**
- **171 MB:** agrees. Hermes's soak swap-outs for 16:07–21:07 total about
  168 MB, and the dashboard window covers that span.
- **1386 ms:** by the code this is a delta from the Mon 13:31 UTC baseline,
  not a total since the reboot. Hermes's reading, "≈ cumulative since reboot
  (~1.45 s)", fits only if boot → Mon 13:31 had about 65 ms of full stall.
  That conflicts with Hermes's 17 Oct note of ~2.7 s of maintenance stall
  (01:32–01:50 UTC). Most likely one of the figures is a different PSI line
  (`some` or `full`) or a different counter. **Asked Hermes which.**
- **14 Oct:** the table gives +36 MB for 21:00–21:10. Hermes's 15 Oct summary
  said swap was "unchanged at 210 MB" at the 14 Oct 21:07 soak. These could
  be sysstat versus soak point samples. **Asked Hermes to reconcile.**

**What this changes:**
- Hypothesis update: Monday's growth followed the **gateway-up windows**
  (Java), not the Ares 21:00 run or price-cache growth. The earlier
  hypothesis that the 866 → 1074 symbol cache drives the burst is weakened
  further.
- **Swappiness verdict: still open, and blocked under Option A.** Separating
  swappiness from workload needs a 21:00 run with normal screening. While
  Finviz is broken there is no such night, so the 19–23 Oct samples will be
  confounded the same way as 19 Oct.
- If host sampling is ever needed (observation only), target 13:25–13:45
  and 16:00–17:35 UTC, not 21:00.

**Hermes-side items, no Ares impact:**
- 19 Oct 19:19–19:25 UTC: a 5.8 min MetaQuotes broker outage caused the
  19:22 UTC alert. Not host-related.
- Hermes `d288a0f` fixes its own alert state: the failure count now persists
  across cron runs.
- The 14 Oct 06:22 UTC MT5 relaunch was unattended-upgrades → needrestart →
  `xvfb.service`. This matches the 15 Oct entry.
- swappiness stays at 10. No host changes proposed.

<a id="fill-before-decision"></a>
### 2026-10-20 — step 3 started: admission routes audited; 11 of 21 fills booked at an open that preceded the decision

No action. Recorded, not fixed; Ares stays FROZEN. Full audit:
`archive/AUDIT_2026-10-20_admission_routes.md` (input to step 3,
`DESIGN_admission_capture_v1.md`).

**Admission routes, all 21 trades reconstructed.** Method: the first commit
whose `logs/pending_signals.json` holds the trade's symbol and `signal_date`,
cross-checked against `queue_events` `promoted` timestamps.

| Route | Call site | Trades |
|---|---|---|
| R1 direct scan | `daily_report.py:119` `open_trade(s)` | 10: HAFN, DYN, ABM, PINS, ECO, SDGR, SECZ, PCVX, EROC, ITUB |
| R2 queue → scan | `daily_report.py:64` `promote_queue(source="scan")` | 6: TMO, AMP, TWST, MANE, DHT, SKYD |
| R3 queue → monitor | `monitor_trades.py:280` `promote_queue(source="monitor")` | 5: WBD, NEOG, BCE, GMAB, INSM |

For the 11 queue trades, the commit subject and the `promoted` timestamp agree
11/11. **EROC and ITUB were R1, admitted by the 10-06 21:01 report.** That
closes `initiating_source: unknown` in the 10-07 reconciliation (§2 item 6).

This was reconstructed by inference, not read from evidence. It works only because
every cron run commits the pending file, and R1 shows up only as the absence
of queue events. The admission recorder is still needed.

**🔴 Finding: same-session fill at a pre-decision open.**
`execute_pending_signals` fills at `df.iloc[-1]['Open']` as soon as the last bar
is today's (`tracker.py:120-130`). A pending created *during* a session is
therefore filled by the 21:00 run at **that same session's open**, which
happened before the admission decision. The intended rule is next-bar
execution, "signal day N → buy at day N+1 open" (Sep 8–12 entry;
`daily_report.py:121`, `tracker.py:629`).

The Sep 18 fix (fill after refresh, plus the bar-date guard) stopped fills
using **yesterday's** open. It did not check that the bar's open came
**after** the admission. Both defects come from one missing check: nothing
compares the fill bar's time with the decision time.

| Admitted by | Trades | Decision after the open by |
|---|---|---|
| 13:3x UTC report | DYN, TMO, PCVX, MANE, DHT, SKYD | ~1–2 min |
| 16:10 UTC monitor | NEOG, BCE, GMAB, INSM | ~2 h 40 min |
| 17:30 UTC monitor | WBD | ~4 h |

Unaffected (admitted at 21:01 or pre-market, filled next session): HAFN,
ABM, PINS, ECO, SDGR, SECZ, AMP, TWST, EROC, ITUB.

Booked entry against the price the decision saw (queue trades, where
`queue_events` holds the decision price):

| Trade | Decision price | Booked entry | Diff |
|---|---|---|---|
| NEOG | 13.74 | 14.22 | +3.49% |
| GMAB | 34.70 | 35.29 | +1.70% |
| MANE | 128.08 | 130.12 | +1.59% |
| SKYD | 6.99 | 7.07 | +1.14% |
| DHT | 26.03 | 26.17 | +0.54% |
| WBD | 30.80 | 30.87 | +0.23% |
| INSM | 98.20 | 98.28 | +0.08% |
| TMO | 655.46 | 654.54 | −0.14% |
| BCE | 20.38 | 20.22 | −0.79% |

- The booked price was not attainable when the decision was made.
- 7 of 9 are *worse* (higher), so the defect does not flatter the sample.
- Scope: **9 of the 14 `clean_v3` trades** (TMO, NEOG, BCE, GMAB, PCVX, MANE,
  DHT, INSM, SKYD), plus pre-clean DYN and WBD. Every official clean-sample
  figure includes these entries.
- DST: after US DST ends (1 Nov), 13:30 UTC is 08:30 ET, pre-market. The
  bar-date guard will then defer 13:30 admissions to the 21:00 run, at that
  session's open. The R3 cases (16:10 / 17:30) are unaffected by DST.
- Decision deferred to the unfreeze review. The admission design will record
  the decision time and price, plus the fill bar and open, so the gap is measured
  per trade, not reconstructed.

**Smaller findings from the same audit:**
- Queue promotion re-bases the signal: `signal_date` / `signal_price` hold
  the promotion date and validation price (`tracker.py:588-589`). The scan's
  date survives only as `queued_at` in `queue_events`.
- `strength` is lost on the queue route: all 11 `from_queue` trades are
  `unknown`.
- AMP was admitted by a manual 05:35 UTC run and filled by the scheduled
  13:30 run. It is `clean_v3`, because contamination is judged at the fill
  only. Policy question for the design.
- R3 runs after any monitor state save, including a peak advance with no close
  (`monitor_trades.py:218`). It is not close-only, as the comment at `:278`
  implies.
- The two `queued` producers (scan with slots full; pending demoted at fill)
  are indistinguishable. `duplicate` outcomes are never logged.
  `_log_queue_event` drops write errors silently.
- `promote_queue` falls back to the unranked queue when `queue_ranked.json` is
  empty (`tracker.py:543-544`). Promotion still re-validates, but this bypasses
  ranking.

### 2026-10-20 (later) — admission capture design drafted; review bundle v1 built

No code. Ares stays FROZEN. Design: `archive/DESIGN_admission_capture_v1.md`
(DRAFT, not authorised).

- **Records.** Three kinds go to a new `logs/admission_v1.jsonl`, plus one
  heartbeat per run:
  - `admission`: one per `open_trade` call.
  - `promotion_pass`: one per `promote_queue` call, early returns included.
    It keeps the original queue date and price.
  - `fill`: one per pending checked, nine explicit outcomes, with the bar
    date and open, so A2 is measured per trade.
- **Linkage:** natural key `(symbol, signal_date)`. No id is written into
  production state.
- **Inspection:** a new tool reconciles git state diffs (pendings added and
  removed, trades added) against the records, independently of the recorder's
  own claims.
- **New defect, recorded only:** two silent pending discards at fill time
  (`tracker.py:88-91` already open; `:82-86` slots full and already queued).
  They contradict the docstring at `:63`.
- **Cost:** registration grows to `tracker.py`, `daily_report.py` and
  `monitor_trades.py`. The accepted monitor diff gains additions; its existing
  digests are unchanged.
- **Review bundle:** `review_bundle_admission_capture_v1` (+ `.tgz`, sha256
  `9e5596d9…`). The reproduction script reruns the audit from the extracted
  evidence and agrees: 21/21, 11/11, 11/21, 9/14.
- **Method correction found while building it:** run time must be the commit
  **author** date. `run_ares.sh` rebases before pushing, which rewrites the
  committer date (4 commits, including TMO's admission).
- **Housekeeping:** the completed monitor-recorder bundles v2–v7 were deleted
  from `~/Olympus`. The final state is on `main` (`5fd13e9`).

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
