# Ares — Future Plans & Ideas

## Status — 2026-09-22

| | |
|---|---|
| **ARES** | V3.1 running. Strategy frozen. `clean_v3` open 2026-09-19 |
| **Clean sample** | 1 open (TMO), **0 closed**. No edge measurement exists yet |
| **Pre-clean history** | 6 trades retained, labelled, excluded from edge metrics |
| **ATHENA** | Unaudited. V3 parameters are frozen against its output |
| **HERMES** | **Running, unaudited, by decision** — mid data-collection, needs several more weeks. Not frozen |

**HERMES is not frozen.** A freeze was proposed and declined: it is collecting
its own sample and pausing would cost weeks. The consequence accepted in
exchange is that its data is unaudited, so **the date of its eventual checklist
pass becomes its clean-data boundary**, the same way 2026-09-19 became ARES's.
Recorded now because that boundary is far cheaper to know than to reconstruct.

Sections below marked *(historical intent)* predate V3.1 and are kept for the
reasoning they contain. They are **not** the current active plan — where they
disagree with the Status table or the Change Policy, those win.

## Capital Planning *(historical intent — pre-V3.1)*

> Written when per-position sizing was assumed to be ~$200 / 20%. Actual sizing
> is `(1000 × 0.75) / 5 = $149`, i.e. ~15%. See *Unimplemented Risk Controls*
> for the reconciliation, and note that per-trade **dollar risk** varies 4.7x
> because size is fixed while stops are volatility-scaled.

### Starting Capital: $1,000
- 5 positions max at ~$200 each (20% per trade)
- Keep 25% cash reserve ($250)
- Commission impact: IBKR ~$1/trade = 0.5% cost on $200 position
- Revisit position sizing when capital grows

### Scaling Plan
| Capital | Max Positions | Per Trade | Cash Reserve |
|---------|--------------|-----------|-------------|
| $1,000 | 5 | $150-200 | $250 |
| $2,500 | 6 | $300-350 | $750 |
| $5,000 | 8 | $500 | $1,500 |
| $10,000+ | 8-10 | $1,000 | $3,000 |

---

## Slot Management (When All Positions Full) *(historical intent — pre-V3.1)*

> Solutions A and B were implemented: the watchlist queue with drift validation
> and expiry, and scale-out at TP. Solution C (replace weakest) was not, and is
> not planned — it would change which trades are held mid-sample.

### Problem
With max 5-8 positions and 2-4 week holds, system will be fully loaded
and ignore new signals for weeks. Missed opportunities.

### Solution A: Watchlist Queue
- When all slots full, log signal as "queued" with timestamp
- When a trade closes, check queue for still-valid signals
- Auto-enter the strongest queued signal (re-verify RSI, regime, confluence)
- Queue expires after 5 days (signal may no longer be valid)
- Never miss an opportunity — just delay it

### Solution B: Scale Out (Partial Exit)
- When TP hits: sell 50% of position, lock in profit
- Let remaining 50% ride with trailing stop
- Frees up ~50% of capital for new trades
- Example:
  - Buy 10 shares at $100 ($1,000 position)
  - TP hits at $112: sell 5 shares (+$60 profit locked)
  - Remaining 5 shares ride with trailing stop
  - $500 freed for next signal

### Solution C: Replace Weakest
- New signal with confluence 4 appears, all slots full
- Compare with weakest open trade (lowest confluence, worst P&L)
- If new signal significantly stronger: close weakest, enter new
- Risk: closes a trade that might have recovered
- Only for advanced phase

### Recommended Implementation Order
1. Watchlist Queue (Phase 3) — low risk, high value
2. Scale Out (Phase 4) — when auto-executing trades via IBKR
3. Replace Weakest (Phase 5) — needs AI judgment (Claude)

---

## Phase 3: Claude AI Signal Validation

> **Core Rule:** Claude validates and contextualizes Ares signals. Claude does NOT generate signals.

### Decision Hierarchy

| Priority | Layer | Can force a buy? | Can block a buy? |
|----------|-------|-------------------|-------------------|
| 1 | Ares hard rules | ✅ (source of signals) | ✅ |
| 2 | Risk manager (slots, size, stops) | ❌ | ✅ |
| 3 | ML score (later) | ❌ | ✅ / rank only |
| 4 | Claude thesis | ❌ | ✅ (soft/hard caution flags) |

### Next-Bar + Thesis Flow

```
Day N close
  Ares signal → pending
  Claude thesis generated and stored

Day N+1 open
  Revalidate:
    1. Ares conditions still acceptable?
    2. Gap / extension acceptable?
    3. Claude risk flags clear?
    4. Slot/risk available?
  If all pass → enter with planned SL/TP/TS
  Else → skip / expire pending
```

### Claude Output Structure (per pending signal)

| Field | Type | Example |
|-------|------|---------|
| `thesis_summary` | 2-4 lines | "Shipping sector momentum, no earnings within 14 days..." |
| `catalyst_flags` | list | `["earnings_proximity", "elevated_iv"]` |
| `risk_level` | low / medium / high | "medium" |
| `action` | allow / caution / avoid | "allow" |
| `why_avoid` | string (if any) | "Earnings in 2 days, gap risk" |
| `confidence` | low / medium / high | "medium" |

### Deterministic Policy on Claude Output

| Claude Verdict | System Action |
|----------------|---------------|
| `allow` | Normal Ares execution |
| `caution` | Reduce size or require stronger ML score |
| `avoid` | Skip trade |

### Exit Rules — NO Claude Involvement
Exit remains 100% rule-based:
- Stop-loss
- TP scale-out (50%)
- Trailing stop (10%)
- RSI extreme (>90)

### Hybrid Architecture

```
Ares       = permission (generates signals)
ML ranker  = priority (scores/ranks signals)
Claude     = context referee (validates/flags)
Risk engine = final gate (slots, sizing, stops)
```

### Prerequisites
1. ✅ Ares V3 running stable
2. ⏳ Collect 40-60 **clean closed** trades (currently **0**, sample opened 2026-09-19)
   - the earlier "4/40" counted pre-clean trades, which are excluded from edge
     metrics and cannot serve as a control group
3. ⏳ Establish win rate / PF without Claude (control group) — needs item 2 first
4. Then add Claude layer and compare performance

### Estimated Cost
- ~$0.01-0.03 per signal validation
- ~$0.15-0.30/day ($5-9/month)

### Claude Could Also (Future)
- Analyze shadow tracking data → suggest optimal TP levels
- Review weekly performance → suggest parameter adjustments
- Explain WHY a trade worked or failed (pattern recognition)
- Identify sector rotation trends across multiple signals

---

## Phase 4: Automated Execution (IBKR)

### Order Types
- Limit buy at signal price or slightly below
- Stop-loss order placed immediately after fill
- Trailing stop order (8% from peak)
- Scale-out: sell 50% at TP, adjust trailing stop for remainder

### Safety Guards
- Max daily orders: 3
- Max position size: 20% of portfolio
- Kill switch: stop all trading if portfolio down 5% in a week
- Paper trade for 3 months minimum before going live

---

## Phase 5: Portfolio Optimization

### Ideas
- Correlation check: don't hold 3 tech stocks at once
- Sector balancing: max 2 positions per sector
- Volatility adjustment: reduce position size for high-vol stocks
- Dynamic trailing stop: tighter for range trades, wider for momentum
- Backtest engine: test parameter changes on historical data before applying

---

## Parameter Tuning Ideas (After Collecting Data)

| Parameter | V3 Current | Consider |
|-----------|------------|----------|
| RSI period | 21 | Test 14 vs 21 vs 28 |
| RSI source | OHLC4 | Test Close vs OHLC4 vs HLC3 |
| Min confluence | 2 | Increase to 3 for higher win rate? |
| Trailing stop | 10% | Test 8% vs 10% vs 12% |
| TP momentum | 18% (scale out 50%) | Shadow data will tell us |
| TP reversal | 10% (scale out 50%) | Shadow data will tell us |
| Max positions | 5 | Fixed for $1,000 capital |

### Decision Rule
Only change parameters after 50+ closed trades with shadow data.
Never optimize based on 5-10 trades — too small a sample.

---

## Dual Timeframe Strategy (Future)

### Daily (current)
- Signal generation: RSI, divergence, regime, confluence
- Runs at market open + close

### Intraday (future with IBKR)
- Hourly candles for better entry timing
- "Daily says BUY → wait for hourly pullback → enter"
- Could improve entry price by 1-2%
- Needs IBKR streaming data subscription ($1.50/month)

---

## Machine Learning Roadmap

### Architecture: Hybrid AI (3 Layers)

```
Layer 1: Ares V3 (Technical Analysis)
  → RSI, divergence, regime, confluence
  → Filters 5000 stocks → 5-10 signals

Layer 2: Custom ML Model (Pattern Scoring) ← NEW
  → Trained on historical + live trade data
  → Scores each signal: win probability, optimal TP/SL
  → "Trades like THIS historically win 70%"

Layer 3: Claude API (Contextual Analysis)
  → News, earnings, sector outlook
  → Final approve/reject on ML-scored signals
```

### ML Phased Approach

| Phase | When | Data | Model | What It Does |
|-------|------|------|-------|-------------|
| **ML-1** | Now | Backtested trades (500+) | Random Forest | Win/loss prediction, feature importance |
| **ML-2** | Month 2 | 50+ real paper trades | XGBoost/LightGBM | Improved scoring with real data |
| **ML-3** | Month 3 | 200+ trades + Claude | Ensemble | Combines technical + sentiment features |
| **ML-4** | Month 6+ | 500+ trades | LSTM / RL | Time-series prediction, optimal execution |

### ML Model Features (Inputs)

| Feature | Type | Source |
|---------|------|--------|
| RSI at entry | Numeric | Ares indicators |
| Regime (uptrend/range) | Categorical | Ares regime detection |
| Confluence count | Numeric | Ares signals |
| Volume ratio | Numeric | Ares indicators |
| MACD histogram slope | Numeric | Ares indicators |
| Distance from 52w high | Numeric | yfinance |
| Distance from SMA 50 | Numeric | Ares indicators |
| Sector | Categorical | Finviz |
| SPY RSI (market condition) | Numeric | yfinance |
| Day of week | Categorical | Date |
| VIX level | Numeric | yfinance |
| Finviz screen type | Categorical | Screener |

### ML Model Outputs (Predictions)

| Output | Type | Example |
|--------|------|---------|
| Win probability | 0-100% | 72% |
| Expected P&L % | Numeric | +8.5% |
| Optimal TP | Numeric | +15% |
| Optimal SL | Numeric | -5.5% |
| Expected holding days | Numeric | 12 days |
| Confidence | Low/Medium/High | Medium |

### Separate Project: Athena (Backtesting Engine)

The backtesting engine lives in a separate repo: **Athena**
- Named after the Greek goddess of wisdom and strategy
- Simulates Ares V2.1 on 2 years of historical data
- Generates 500+ simulated trades for ML training
- Shares the same indicator/signal logic as Ares
- Outputs training data CSV for ML models

### Athena → Ares Integration

```
Athena (backtest)           Ares (live)
  ├── Generate trades ──→ ML model training
  ├── Feature analysis ──→ Parameter tuning
  └── Optimal TP/SL    ──→ Update strategy_params.json
```

---

## Change policy during the observation phase

The clean sample measures one fixed system. Any change that alters **which trades
are taken, at what price, or in what size** makes the trades before and after it
non-comparable, so it must wait for a deliberate version boundary with its own
sample phase (`clean_v4`). Reviewed and agreed externally.

| Fix now | Defer to V4 |
|---------|-------------|
| Measurement, labelling, logging, docs | The trade population |
| Accounting correctness for *already-defined* rules | Entry / exit prices |
| Crashes and silent data corruption | Position sizing and risk model |
| Ops reliability (push retries, alerts) | New filters that accept or reject setups |

The line is whether the change corrects **how the system is measured** or changes
**what the system does**. Fixing an unbooked scale-out is the former: the rule
always said P&L spans the whole position, and the code failed to implement it.
Adding a gap filter is the latter: no rule ever said gapped fills are declined.

Forced exception: a defect that mis-executes a *stated* rule, or damage severe
enough to end the observation, is fixed immediately regardless of sample cost.

## Deferred to V4 — reviewed, accepted as known behaviour

### 1. No price-gap guard on pending fills

`execute_pending_signals()` checks slots, duplicates, pending age, data
availability and session freshness, then fills at `today_open × (1 + slippage)`
**without comparing that open to the signal price.** The queue path does check
drift (±`queue_max_drift_pct`, 5%), so a signal blocked by full slots is
guarded while one about to commit capital is not.

Arguments both ways, both judged real:

- **For a guard:** filling 8-12% above signal price buys a different setup. RSI,
  extension and volume context have all moved, so the confluence that justified
  the trade may no longer hold.
- **Against:** a signal gaps up *because the stock moved as predicted.*
  Rejecting gaps discards the fastest movers, which for `momentum_breakout` may
  be discarding the trades that work. This is the same selection-bias mechanism
  already flagged for queue drift expiry — adding the filter introduces
  deliberately what we are watching for accidentally.

**Decision: leave open for `clean_v3`, documented as known behaviour.** The
clean sample must not be described as if a gap guard exists.

When designed at V4, prefer **re-validating confluence and regime at the open**
over a blunt drift cut, which is momentum-hostile. Testable in Athena using
signal-day close to next open, applying the filter only on information available
at the open. Look-ahead traps to avoid: using the same-day close after the open;
using future bars to set filter parameters; and **tuning the threshold on the
same 1060 trades then reporting those trades as proof.**

No new instrumentation is required. `signal_price` is already stored on every
trade, so gap-at-fill is derivable retroactively and the V4 decision can be made
from data already being collected. Measured so far: TMO −0.24%, DYN +0.37%. The
−9.46% and +9.30% figures on ABM and PINS measure the since-fixed stale-data
bug, not real gaps.

### 2. Per-trade dollar risk varies 4.7×

Position size is fixed at ~$149 regardless of stop distance, while stops are
volatility-scaled with no cap. Observed across five concurrent positions:

| Symbol | Stop distance | Position | Dollar risk |
|--------|---------------|----------|-------------|
| TMO    | 3.10%         | $149     | $4.62       |
| ABM    | 3.91%         | $149     | $5.82       |
| HAFN   | 4.88%         | $149     | $7.27       |
| ECO    | 5.03%         | $149     | $7.49       |
| SDGR   | 14.62%        | $149     | $21.88      |

SDGR's wide stop is correct (~7.3% daily stdev, volatile biotech), not a
fallback. But the most dangerous trade receives the same capital as the safest,
and five SDGR-like positions would risk ~10.9% of the portfolio at once.

Risk parity would size by `shares = (capital × risk_pct) / (entry − stop)`,
making TMO large and SDGR small.

**Reviewed as the highest-priority risk item for live readiness**, ahead of the
portfolio circuit breaker, correlation caps and time-stops. Still deferred:
choosing `risk_pct` sensibly requires the empirical stop-distance distribution
this sample is producing, and $1K paper across 5 slots is not ruin-scale.

Related friction: at $149 with $1 commission each way, round-trip cost is ~1.5%
of position value. Against TMO's 3.10% stop, costs are nearly half the risk
budget. Volatility-scaled stops tighten on quiet stocks while fixed commissions
do not shrink with them. This argues for larger notional when live, a minimum
stop distance, or fewer and larger positions — as **live sizing design**, not a
mid-sample patch.

### 3. Signal generation is once daily — accepted as correct

Only the 21:00 UTC pass finds signals; the 13:30 UTC pass structurally cannot,
because the daily candle barely exists at the opening bell. See README. Reviewed
conclusion: the morning pass is a legitimate operations pass, and moving signal
generation intraday would be a different system with a different edge definition
and greater data dependency. Throughput is signal-limited, not scan-limited.

## Removed inert configuration keys

Six keys in `strategy_params.json` were read by no code. Three shadowed
hardcoded literals, so editing them silently did nothing:

| Key | Reality |
|-----|---------|
| `rsi_source: ohlc4` | RSI does use OHLC4 (`indicators.py:16`), but the key does not control it |
| `rsi_overbought: 70` | `current_rsi > 70` hardcoded, tracker.py |
| `rsi_midline: 50` | `40 <= rsi <= 50` hardcoded, signals.py |
| `macd_threshold: 0` | comparison hardcoded |
| `rsi_extreme_low: 10` | unused entirely |
| `lookback_days: 252` | unused entirely |

Deleted rather than wired. Wiring all six would have added surface area for a
future silent divergence without changing any behaviour, and the same reasoning
retired `risk_rules.json`: a config that looks authoritative but is inert is
worse than no config, because it invites decisions based on settings that do not
apply. If any becomes a real knob later, it gets added when the code reads it.

Remaining lower-priority instance of the same shape: the pending record still
defaults `rsi` and `vol_ratio` to `0`, values that are impossible in
practice and so indistinguishable from real readings. Lower stakes than the
`stdev_20` case because neither feeds sizing, but changing them requires
checking every format site that would receive `None`.

## Athena audit — completed 2026-09-22, result: V1-V5 contaminated

One-line summary for the logbook:

> Athena's reported edge was materially driven by look-ahead divergence
> labelling; V1-V5 are contaminated. Ares continues as a fixed live process under
> `clean_v3`. Next step is Athena V6 Run A (as-live, causal), not parameter churn.

### The defect

`engine/indicators.py:74-102` — `_find_swing_highs` / `_find_swing_lows` compare
`series.iloc[i]` against `series.iloc[i + j]` for `j = 1..5`, then write the
result onto bar `i`. So `bearish_div[i] == True` asserts that bar `i` is the
highest close of the surrounding 11 days — unknowable until bar `i+5`. The
simulators use that flag as an **exit on bar `i`**, selling at a confirmed local
top with hindsight.

| Run | `bearish_divergence` exits | Avg P&L | Contribution |
|---|---|---|---|
| Athena V3 (829 trades) | 260 (31%) | +12.53% | ~89% of per-trade edge |
| Athena V5 "realistic" (209) | 74 (36%) | +12.25% | **96% of dollar P&L** |

The true edge under causal rules is **unknown and materially lower**. It is not
recoverable by re-pricing those exits: removing an exit changes hold times,
capital occupancy and which later trades get funded.

### Why it matters more to Ares than it first appears

`Ares/engine/indicators.py` and `Athena/engine/indicators.py` are
**byte-identical** (md5 `f2cf8af8`). The same function runs live — but live the
effect inverts.

The loop is `range(window, len(series) - window)`, so the highest index that can
ever receive a flag is `len-6`. Live code reads `latest`, which is index
`len-1`. Therefore:

**`latest['bearish_div']`, `latest['bullish_div']` and both `latest['hidden_*_div']`
are structurally always `False`.**

Confirmed empirically:

| | divergence triggers | divergence exits |
|---|---|---|
| Athena V2 backtest (930 trades) | 6 | 228 |
| Athena V5 backtest (209 trades) | 1 | 74 |
| **Ares live (7 trades)** | **0** | **0** |

Consequences in live code, all currently **inert**:
- `tracker.py:851` — the `bearish_divergence` exit can never fire
- `signals.py:66,81` — `bullish_div` never contributes mean-reversion confluence
- `signals.py:27,39` — the `hidden_bull_div` trigger never fires
- `signals.py:111,113` — the two divergence *rejection* filters never reject

**The exit that produced ~96% of backtested profit cannot occur live at all.** The
frozen parameters were tuned against an effect the live system is structurally
incapable of reproducing.

### Decision: document, do not repair

Leaving it inert, deliberately. Repairing the detector would change which trades
exit and when — that is the trade population, so it belongs at the V4 boundary.
This is **not** the same class as the unbooked scale-out: that corrected the
measurement of an already-stated accounting rule, whereas enabling a working
causal divergence exit changes what the system does.

Reviewed externally and endorsed: *"Leave it. Document it. Do not repair
mid-sample."*

### Supporting findings

- **Zero holdout.** V1/V2/V3 all ran `2024-01-01 → 2026-09-01` on the same 130
  symbols; parameters were chosen by re-running identical data and keeping the
  better number. That window **excludes 2022**, the only losing year in the
  5-year sample.
- **Ares runs Athena V2, not V3.** The README crowns V3 (PF 3.00), which requires
  `disable_tp: true`. Ares has `tp_momentum: 0.18` — that is V2, PF 2.42. The
  figure carried in this repo as "2.41 over 1060 trades" conflated V5's profit
  factor with V1's trade count.
- **Max drawdown computed wrong** (`portfolio_sim.py:405`) — measures only the
  drawdown after the global equity peak. True V4 = −17.8%, V5 = −20.2%, against
  −5.0% and −15.9% reported. Understated ~3.6x.
- **`hidden_bullish_div` vs `hidden_bull_div` key mismatch** in all three
  simulators — the confluence gate is inoperative, always exactly 3, so
  `min_confluence: 2` never binds. Ares' `signals.py` uses the correct names, so
  live and backtest evaluate different entry rules.
- **The V5 "no friction" control never ran** — dead branch at
  `portfolio_sim_v5.py:319`. The "friction costs ~10% annual" claim compares two
  different engines.
- **Stop distance from `Close.std()` of dollar price levels**, not returns. Ares
  live uses the fractional `pct_change().rolling(20).std()`. A live↔backtest
  mismatch independent of the look-ahead.
- **Not reproducible** — `data/ohlcv/` is empty and gitignored, and yfinance
  adjusts retroactively.
- **Verified clean:** scale-out tranche booking (better than Ares was), slippage
  direction, commission accumulation, cash solvency, conservative
  stop-before-target ordering, V5's next-bar *entry*, and the trailing indicator
  set — RSI, MACD, SMA slope, regime — which is properly causal. The defect is
  confined to one function pair reused in four places.

### What this does and does not change

| | |
|---|---|
| Live divergence entries and exits | structurally inert, documented, unrepaired |
| Ares as a defined process | intact — every other rule operates as written |
| `clean_v3` validity | **intact.** It measures this live system, which is causal end-to-end |
| "Parameters validated by Athena" | **retracted everywhere** |
| Need to change Ares today | **no** |

Replacement language, to be used consistently: not *"Athena-optimized
parameters"* but *"parameters selected under a backtest later found contaminated
by look-ahead; the live sample is the out-of-sample test."*

**`clean_v3` is not restarted.** The sample is 1 open trade and 0 closed, which
makes restarting look cheap, and that is precisely the trap. Live Ares is now the
only uncontaminated evidence stream in the project. A restart is justified only by
a deliberate V4 change after honest measurement — never by the discovery that
Athena was wrong.

### Is the strategy family dead?

**Not proven either way, and that distinction matters.** Without the look-ahead,
V5's remaining exits are trailing stop +3.82% and stop loss −6.38%, which is not
obviously an edge. But that autopsy comes from the same contaminated engine, so
it is not reliable evidence of absence. The honest finding is *"Athena's
published edge was largely an artifact; residual edge is unknown"* — not
*"abandon the family."* **Run A did not settle this** — it measured entry
predicates that differ from live in 88% of cases, so it is not the autopsy it
looks like. Abandonment becomes rational only if Run A′ and the clean
live sample both show no usable expectancy after costs.

## Athena V6 — the first honest backtest

Scope agreed and deliberately narrow. Passes the rule for new work: it measures
something `clean_v3` cannot measure for years — five years including the 2022
bear market — and changes zero live trades.

Must-fix before any run:

| # | Fix | Location |
|---|---|---|
| 1 | Confirm swings at `i+window`; never back-date onto bar `i` | `indicators.py:74-102` |
| 2 | Book stop exits at `Close`, not at the unreachable stop price | `backtester.py:89`, `portfolio_sim.py:164`, `v5:222` |
| 3 | Correct the `hidden_*_div` key names | all three simulators |
| 4 | Max drawdown from the running peak | `portfolio_sim.py:405`, `v5:392` |
| 5 | `peak_after_exit` / `missed_upside_pct` never touch parameter selection | `backtester.py:111` |
| 6 | Stop distance from returns stdev, matching live Ares | all three simulators |
| 7 | Make V5 exits next-bar too — currently entries fill at the next open while **every exit uses the signal day's close**, a one-sided hindsight on all exits | `portfolio_sim_v5.py:222-239` |

### Secondary defects — fix during V6, none change the headline alone

These were found in the same audit and are recorded so they are not rediscovered.
They matter for correctness and for trusting the V6 output, but unlike items 1-7
none of them individually moves the reported figure much.

| Location | Defect | Effect |
|---|---|---|
| `portfolio_sim_v5.py:151-154` vs `:251` | cash is charged the $1 entry commission but `total_invested = original_shares * entry_price` omits it, so reported P&L beats the real cash result on every trade | inflates ~0.5-1% per trade |
| `portfolio_sim_v5.py:239` vs `:254` | scale-out commission leaves cash but `total_returned` adds back the **gross** proceeds, so scaled winners gain another $1 — while the printed `Commissions: $X` line makes it look accounted for | inflates |
| `portfolio_sim.py:185` | `pos['scale_out_pnl']` and `scale_out_date` are computed and never written to the trade record, so the scale-out leg is unauditable from the CSV. V5 also drops `scale_out_price` and `remaining_shares`, which is why the two defects above cannot be detected from the output files | audit blindness |
| `backtester.py:43`, `portfolio_sim.py:275,329`, `v5:304` | volatility fabricated as `Close * 0.05` when fewer than 20 prior bars exist — the same silent-default class as the live `stdev_20` bug, bounded here to each window's warm-up | inflates |
| `data_feed.py:12-26` | yfinance MultiIndex columns are stored verbatim then re-read with `header=0`, so ticker rows become data rows. On a **cache miss** the raw MultiIndex frame is returned and `df['Close']` yields a DataFrame, not a Series — so the first run differs from every later run. `signals.py:192`'s bare `except` swallows it per symbol | nondeterminism |
| `data_feed.py:19`, `backtester.py:22,28` | symbols are skipped silently on download failure or insufficient bars, and the count of symbols *attempted* versus *contributing* is never persisted anywhere — a partial download silently yields a smaller "clean" backtest | unquantified |
| `run_backtest_v5.py:39` | `UNIVERSE_B` contains `"FIVERR"`, which is not a ticker (`FVRR` is already listed), and duplicates `RIVN` and `ROKU`. 100 entries, 99 unique, only 82 produced trades | overstated universe |
| `run_backtest_v4_compare.py:52,82` | the base `config/strategy_params.json` is overwritten in place and restored **outside** any try/finally, so a crash mid-loop leaves $10K/15-slot parameters in the file every other run reads | latent corruption |
| Athena `config/*.json` | the same **six dead keys** as Ares had — `rsi_source`, `rsi_overbought`, `rsi_midline`, `rsi_extreme_low`, `macd_threshold`, `lookback_days` — each shadowing a hardcoded literal. Also `disable_tp` is read **only** by `backtester.py:85`; both portfolio sims ignore it and always apply the scale-out TP, so V5's claim to run "V3 logic" is false | tuning does nothing |
| `backtester.py:64` | `spy_rsi` is hardcoded to `0` — a dead market-context feature, constant in every CSV, and a useless ML column | dead feature |
| Athena `engine/_check_entry` | omits live's 52-week-high gate (`signals.py:105`, `pct_from_high >= -0.01`) and `near_sma_support`, and adds an `rsi > 50` filter live lacks. **88% of Run A trades are unreachable live.** Fixed in Run A′, not retrofitted | measures the wrong rule |
| snapshot / bootstrap | Yahoo returns HTTP 429 to yfinance's default session from this egress and serves a browser-shaped one normally; unpatched, the snapshot produced **zero rows for every symbol** — the silent-skip class, recurring during its own repair. Requires a curl_cffi Chrome-impersonating session, and the row-count check is now load-bearing: zero rows fails loudly rather than skipping | silent total failure |
| `vendor/` pins | `pandas_ta 0.3.x` no longer exists on PyPI, so live↔backtest parity was one version away from unreachable. Pins (numpy 2.2.6, pandas 3.0.5, pandas_ta 0.4.71b0, yfinance 1.6.0) are now **load-bearing for comparability**; Ares' versions must not drift without re-snapshotting Athena | unhedged dependency |

### Run A — complete 2026-09-22

Must-fixes 1-7 implemented and verified by 16 checks in `validate_v6.py`,
including a truncation test proving that flags at bar `i` do not move when the
future is removed. OHLCV snapshot committed — 217/227 symbols, with a manifest
recording library versions, download date and per-symbol spans. V1-V5 simulators
left byte-identical; their six runners now `raise SystemExit`. No Ares files
touched. Frozen parameters, no re-optimisation, no alternative values tried.

Period 2021-09-01 → 2026-09-01 (4.92y), Universe A (130), divergence out of the
decision set:

| | |
|---|---|
| $1,000 → | **$843.37** |
| CAGR | **−3.40%** |
| Max drawdown (running peak) | −30.23% |
| Trades / win rate | 156 / 26.9% |
| Profit factor (dollar) | **0.853** |
| Expectancy per trade | −$1.04 (−0.41%) |

Exit structure: `stop_loss` 89 trades, −$873.21, **0% win rate**; `trailing_stop`
59 trades, +$520.22, 59.3%. The trailing stop cannot cover the stops — the shape
the audit predicted from V5's residual exits.

**Not a friction problem.** At zero commission with every decision held fixed,
A = +$183.78 (≈+3.5%/yr against SPY's +13.39%). Commission turns weak into losing;
removing it does not create an edge.

**It bleeds in ordinary conditions.** 2022 was −22.78%, but 2025 was −18.28% —
and 2025 was not a bear market.

### Run A measured the wrong entry rule — read before concluding anything

Found during Run A: only **12.2% (18/148)** of its `momentum_breakout` entries
satisfy live Ares' 52-week-high gate at `signals.py:105`
(`pct_from_high >= -0.01`). Athena's `_check_entry` never implemented that gate,
and adds an `rsi > 50` filter live does not have. The range branch omits live's
`near_sma_support` confluence term, which 75% of Run A's 8 `mean_reversion`
entries would have satisfied.

So Run A is causal and internally valid, but **88% of its trades are entries live
Ares would refuse.** It measures the rule Athena implements. It is *not* the
as-live benchmark this section asked for.

Deliberately not fixed in place: changing an entry predicate changes the trade
population, which belongs at a declared boundary.

Two further confidence limits: the 11 excluded symbols are all corporate actions,
putting a **≥4.8%/5yr floor on survivorship bias** — which flatters, so the true
result is likely worse; and the 5-slot cap rejected **95% of signals** (3,135
generated → 156 filled, queue fired 0 times), so the exact figures rest on
slot-arrival luck.

**Honest conclusion, and the only one supported:** the rule Athena implements
loses money causally. **The live rule has never been measured.** This is not
"the strategy family is dead" — that claim would require measuring the actual
live predicates on a survivorship-corrected universe without a binding slot cap.

### Structural finding — account size, not parameters

Independent of entry rule, universe and divergence, and therefore the most
durable result of the whole exercise:

At $1,000 with 5 slots, **95% of generated signals are unreachable**, and
commission over the period was **$346 — 34.6% of starting capital**, roughly
7%/yr of drag. Beating SPY's 13.4% would require >20% gross. Even a genuine edge
could not be *expressed* at this account size.

Not a parameter change and not an argument to widen the slot cap. It is a
viability observation that belongs in the June 2027 live-capital plan, where the
same $1,000 figure appears.

### Open — Run A′, now above Run B

**Run A′:** same V6 engine, live Ares' actual entry predicates — the 52-week-high
gate added, the phantom `rsi > 50` removed, `near_sma_support` restored to the
range branch. This is a correctness fix, and it would be **the first time live
Ares has actually been measured**.

**Run B** — divergence repaired at `i+5` — drops below it.

### Parity audit 2026-09-22 — the mismatch is ~30 items, not 3

Independent read-only audit of V6 against live Ares, run because the V6 thread's
own report disclosed three entry mismatches and the lesson of this project is that
author-side parity checking is the control that already failed. It found roughly
thirty, plus a new causality defect introduced in V6.

**Root cause is structural.** `portfolio_sim_v6.py` imports only `data_feed` and
`indicators` and **reimplements** entry logic in its own `_check_entry`. Athena's
`engine/signals.py` is a near-copy of Ares' and **nothing imports it** — dead
code. Every Athena run ever made measured a hand-written parallel implementation
of the strategy. Patching individual predicates leaves the defect class intact;
the fix is to **call the live predicates**, not re-express them.

#### The four that move the number

| | Live Ares | Athena V6 | Effect |
|---|---|---|---|
| **Position sizing** | `tracker.py:123` — static **$149, non-compounding**, no cash tracking at all | `:304` — `min(cash − equity×0.25, equity×0.20)`, **compounding** | V6 positions start ~33% larger and grow with the curve. Run A's CAGR was produced under sizing Ares does not use |
| **Queue promotion** | `_validate_queued` — promotes on `|drift| ≤ 5%`, `rsi ≤ 90`, `price ≥ EMA_20`. Never re-requires the signal | `:415` — requires a **full fresh `_check_entry` signal** to reoccur | **~2,900 signals live would have promoted expired unentered.** 3,135 generated → 156 filled, queue fired 0 times. The largest population difference in the run |
| **Scale-out vs exit order** | `:814-839` — scale-out checked **first**, then `continue`, so exits are skipped on a TP bar | `:365` — exits checked first, scale-out only if no exit fired | An `emotional_extreme` (rsi > 90, common exactly at TP) closes the **whole** position where live banks 50% and rides. Hits winners specifically |
| **Missing `stdev_20`** | `tracker.py:136` — substitutes `0.05`, flags `contaminated`, **fills** | `:313` — refuses the entry | Live's real population contains wide-stop trades the backtest excludes |

#### New defect introduced in V6

`portfolio_sim_v6.py:300` sizes the position from **today's Close** for an order
filling at **today's Open** — a genuine look-ahead, and `validate_v6.py` does not
catch it.

#### Entry predicate differences beyond the known three

`pct_from_high` and `sma_50` are loaded into `_NUM_COLS` and never read.
`vol_ratio` and `macd_hist` use `>` where live uses `>=`/`<` (boundary-only,
backtest stricter). V6 rejects NaN bars; live passes them and emits a signal.
V6 gates momentum on `min_confluence` where live has no confluence test for that
branch and hardcodes `confluence: 3` — no effect at `min_confluence: 2`, but at 3
the backtest emits **zero** breakouts while live emits many. `bearish_div` and
`hidden_bear_div` are **hard vetoes live** but **positive confluence credit** in
V6 — a sign inversion. Trigger strings differ (live joins all satisfied terms,
V6 emits one token), so `trigger` columns are not comparable across systems.

#### Exit and lifecycle differences beyond sizing and ordering

Live suppresses all exit logic on the entry bar (`tracker.py:788`); V6 can decide
an exit from the entry bar, permitting 1-bar trades live cannot produce. Live's
`bearish_div` `elif` swallows `mean_reversion` positions so they never reach
`mean_reversion_complete`; V6 folds the strategy test into the condition and falls
through. V6 adds an `end_of_sim` forced liquidation with no live analogue. Live's
pending-order lifecycle — `pending_max_age_days: 4`, `MAX_FILL_ATTEMPTS: 3`, the
48-hour weekend gap guard — is not modelled; V6 keeps a pending entry one bar and
drops it silently. `queue_max_size: 10`, confluence/age eviction, symbol
de-duplication and the `(-confluence, |drift|, date_added)` ranking key are all
unmodelled. V6 refuses positions under $20; live has no such floor.

#### validate_v6.py is weaker than 16/16 implies

About nine checks are tautological or unreachable: several recompute `_book`'s own
expression from `_book`'s own fields; "stop exits do not fill at the stop price"
passes for *any* open-based fill including a look-ahead one; the pivot-lag check is
pure algebra testing no code; the reconcile check is unreachable because
`run_sim` raises in `reconcile()` first. The causality truncation test is genuine
but narrow — one symbol, `bearish_div` only, last 8 hits — and irrelevant to Run A
because divergence is disabled. Genuine and load-bearing: running-peak drawdown,
entry-after-signal, column absence, and `reconcile()` itself.

#### Verified correct and matching live

All indicator formulas and `detect_market_regime` (byte-identical), the
trailing-stop ratchet and its seeding, `rsi_extreme_high: 90`,
`mean_reversion_complete > 70`, **the exit chain order**, the fractional
`stdev_20` stop formula and `2.0` multiplier, must-fixes 1-5 and 7 as claimed,
`data_feed`'s single flatten-and-raise path with a load-bearing row count,
`universe.py` (130/98, 11 known-unavailable), and `snapshot_data.py`.
**Must-fix 6 is partial** — the formula matches, the missing-stdev behaviour does
not.

#### Two operational items

**Nothing is committed.** `portfolio_sim_v6.py`, `universe.py`, all 217 snapshot
CSVs, the manifest and every result file are untracked. The reproducibility claim
is unmet until they are — one `git clean` loses the run.

`indicators.load_params()` reads `config/strategy_params.json`, which still holds
V2.1 values (`trailing_stop_pct 0.08`, `tp_momentum 0.12`), not
`strategy_params_v6.json`. The three keys it uses agree today, so no numeric
error — one config change from biting.

### Consequence for Run A′ — resolved, see below

Not a patch. `portfolio_sim_v6.py` needed to **call live's predicates** and adopt
live's sizing, queue admission and scale-out ordering. That restructure was done;
Run A′ is recorded in the next section. **Outcome: the parity audit's mismatches
moved the result in both directions and did not cancel, exactly as expected.**

## Athena V6 Run A′ — complete 2026-09-22, Athena `1804721`

**The first measurement in this project where the backtest and the live system are
the same system.** `portfolio_sim_v6.py` now imports live Ares' `signals.py`
through an adapter, with an md5 `ParityDrift` assertion at import that fails the
run if the two ever diverge. No local `_check_entry` survives, and
`validate_v6.py` asserts its absence. 33 validation checks, all passing.

Fixed stake $149, non-compounding, matching live. Period 2021-09-01 → 2026-09-01.
Frozen parameters, no re-optimisation, no alternative values tried. CAGR is not
reported and the field is deleted, because a fixed-stake system does not compound.
`return_basis: fixed_stake_non_compounding` and `comparable_to_run_a: false` are
recorded in the summary so Run A and Run A′ cannot later be tabulated together.

### The funded variant is the result

Live Ares tracks no cash balance, but it trades through IBKR, **which does**. An
order live cannot fund is rejected by the broker whether or not Ares knows it, so
modelling that constraint completes the model rather than departing from live. The
first A′ pass had no funding gate and ran Universe B insolvent for 309 of 1241 days
at a minimum of −$356; it is preserved under
`results/superseded_runA2_no_cash_gate/` with a `SUPERSEDED.md`.

Gate is `cash >= stake + commission` — commission included because it is part of
the debit; gating on the stake alone leaves cash at −$1.00 per fill.

| | A′ ungated *(superseded)* | **A′ funded (final)** |
|---|---|---|
| Universe A (130) | −8.46% | **−5.58%** |
| Universe B (98) | −43.89% | **−57.32%** |
| Max drawdown | −38.01% / −59.75% | **−34.65% / −67.55%** |
| Profit factor | 0.922 / 0.702 | **0.941 / 0.537** |
| Entries refused, no cash | — | **16 / 305** |

Minimum cash moved from −$105.15 and −$356.00 to **+$10.20 and +$0.14**, zero
overdrawn days, asserted daily rather than only on the final bar.

**Universe B got worse, not better.** The constraint bit hardest exactly where
equity had already collapsed to ~$340 — denying new entries while existing losers
continued to run. A funding constraint does not protect a losing system; it
removes its chance to recover while leaving its damage in place. Worth
remembering: it is the same asymmetry a real account would experience.

### The headline — costs exceed the edge, and the edge loses to passive anyway

| Universe A (130) | |
|---|---|
| Gross P&L before commission | **+$273.21** |
| Commission (145 trades) | **−$331.00** |
| Net | **−$57.79** |
| Commission as % of gross profit | **121.2%** |
| Gross edge vs cost per trade | **+$1.88 vs −$2.28** |

At $149 per position, $1 each way is **1.34% round-trip against a 1.26% gross
edge**. Commission alone flips 2.1% of trades from winner to loser — win rate
32.4% before costs, 30.3% after. `pnl_before_commission` and
`win_before_commission` are now trade-level fields, so the split is auditable from
the CSV rather than the summary.

Universe B is **gross-negative at −$269.85**, so **no edge is established**.
Universe A is the survivorship-flattered mega-cap list, which is where a positive
gross figure would be expected to appear whether or not the strategy works.

**And the deeper finding, which the commission story can obscure:** Universe A's
gross P&L is +$273.21 on $1,000 of notional over 4.92 years — roughly **+5.5% a
year before costs**, against **SPY +85.66% and QQQ +97.72%** over the same window,
about 13-14% a year. So even a **commission-free** version of this strategy, run on
the universe most flattering to it, would have lost badly to simply buying the
index. Removing every cost does not produce a competitive system. That is a more
fundamental problem than the cost structure, and it is the finding to carry
forward.

Year shape: **2024 was solidly profitable on both universes (+23.70%, +37.66%) and
every other year lost.** That is the signature of a momentum strategy fitted to a
trend that has since stopped — and explicitly **not** an invitation to re-fit on
2024.

### Three live Ares defects found by Athena — recorded, not fixed

All three are reproduced in the backtest rather than corrected, because fixing live
behaviour inside the backtest is how the two systems drifted apart originally.
Recorded here because they are **live** defects and Athena is not where Ares' owner
would find them.

**1. Queue promotion gates are inert.** `tracker.py:400,403` read
`latest.get('RSI', 50)` and `latest.get('EMA_20', 0)`, but `add_indicators`
produces lowercase `rsi` and **never produces `EMA_20` at all**. So the RSI check
always sees 50 and can never reject, and the EMA-20 check always compares against
0 and can never reject. **Live queue promotion is `|drift| <= 5%` and nothing
else.** Third instance of the inert-gate class in this project, after the six dead
config keys and the `hidden_*_div` key mismatch.

**2. Structural divergence blindness.** Already documented above: the pivot loop
stops at `len - window - 1` while live reads `len - 1`, so all four divergence
columns are permanently `False` in production.

**3. Unfunded constant sizing.** `tracker.py:119-123` computes position size from
`starting_capital` as a **constant**, and live performs **no balance check
anywhere**. As the account declines the static $149 becomes a growing fraction of
equity, and live will attempt orders IBKR rejects — at $427 equity, 5 × $149 =
$745 is unfundable. This has real consequences before live capital arrives in June
2027 and was not in the parity audit's list either.

### What this does and does not change for Ares

| | |
|---|---|
| Parameters | **unchanged.** Run A′ does not license a change, and re-tuning against these numbers is how the original figure was manufactured |
| `clean_v3` | **not restarted.** Still the only uncontaminated live evidence; a disappointing backtest is explicitly not grounds |
| "Strategy family is dead" | **still not recorded.** Universe B is gross-negative and A is survivorship-flattered, so the honest statement is that no edge is established — not that its absence is proven |
| June 2027 capital plan | **materially affected.** See below |

**The viability question is now measured rather than asserted.** At $1,000 with 5
slots and $1/trade, this trade frequency costs ~1.34% round-trip per position, and
121% of gross profit went to commission. Combined with a gross return far below
passive, the honest conclusion is that **this configuration cannot work at this
account size**, and that raising the account size fixes the cost ratio without
creating an edge. Both would have to change.

Earlier in this thread I twice over-read a structural number before it was
measured — first claiming the commission drag was entry-rule-independent, then
predicting live's trade count would be an order of magnitude higher when live's
52-week-high gate actually cut signal volume from 3,135 to 935. **Structural
claims from this project should not be trusted until they appear in a run.**

## Strategic reassessment — 2026-09-22, after Run A′

Reviewed externally. The conclusion is a change of **role**, not a panic rewrite.

### What Run A′ settles and what it leaves open

**Settled:** this frozen configuration is not a credible index-beater at $1,000
scale. Gross is below SPY on the *flattering* universe, costs turn it negative, and
the control universe is worse.

**Not settled:** whether any momentum process can work. Survivorship is present and
unquantified in both universes, so −5.58% is not a precise estimate of a true edge.

### `clean_v3` is demoted, not deleted

It targets 40-60 closed trades. With ~30% win rate, fat tails and regime
dependence, **that sample cannot separate +5%/yr from +15%/yr** — hundreds of
independent closes would be needed, often 200-500, and even then path dependence
and regime clustering remain. At ~10-30 trades a year that is not reachable on any
practical calendar.

So `clean_v3` was always under-specified for the claim *"beats SPY."* Its new label:

> **Process and integrity observation — not edge certification.**

Keep it running: cost is near zero, it is the only live causal stream, and it keeps
surfacing implementation defects. Stop treating it as the validation path, because
doing so manufactures the feeling of progress.

### The structural constraint

At $1,000 across 5 slots a position is $149; $1 each way is **1.34% round-trip**; at
~30 trades/yr that is **~7%/yr of drag**, so beating SPY needs roughly **20%/yr
gross**. That is a property of **account size and turnover**, not of momentum
breakouts. Changing strategy while keeping $1,000 / 5 slots / ~30 trades a year
mostly re-runs the same cost experiment.

### Three corrections to the analysis itself

**1. A structural sweep would repeat the original sin.** Sweeping account size,
frequency, commission and slot count across one window and two hindsight universes
is a four-dimensional search on one dataset — the same pattern that manufactured
PF 2.42, with better manners. Separate them properly:

| Question | Method |
|---|---|
| Cost drag vs size, frequency, commission | **Closed form arithmetic.** No sim, no sweep |
| Slot count and which trades get funded | **Sim** — one **pre-registered** dimension |

Never grid-search and crown the best cell.

**2. The sim pays 0% on idle cash, and that is not neutral.** Maximum deployment is
5 × $149 = $745 of $1,000, and actual deployment is well below that most days. Over
2021-2026, with T-bills at 4-5% for much of the window, idle cash should have
earned plausibly **$60-80** against a net result of −$57.79. Universe A is therefore
**closer to flat than to losing.** Still behind SPY — not a rescue — but the
description was wrong.

Related: comparing a partly-invested strategy to 100%-invested SPY is not
apples-to-apples. At ~50% average exposure, 5.5% gross is ~11% on **deployed**
capital against SPY's 13.4%. Still behind; **"loses badly" was an overstatement.**

**3. Three overstatements in one analysis, all in the same direction.** The
commission-independence claim, the trade-count prediction, and now the
exposure-unadjusted benchmark comparison. Each overreached toward the cleaner
narrative. **Structural claims from this analysis should be discounted until they
appear in a run**, and the error bars deserve more weight than the story.

## PRE-REGISTERED TEST — momentum factor substitution

**Registered 2026-09-22, before the result is known.** Highest-leverage next step
and it runs before any structural work.

**Motivation:** 2024 carried both universes (+23.70%, +37.66%) and every other year
lost. 2024 was a strong momentum year. So:

> **Is this strategy an expensive, high-maintenance way to buy momentum factor
> exposure that an ETF provides for 0.15%?**

**Data:** `results/v6_runA2_*_equity.csv` for strategy returns; MTUM, SPY, QQQ over
2021-09-01 → 2026-09-01. Monthly returns (~59 observations; annual gives only 5 and
is unusable).

**Method:** regress strategy monthly excess return on a momentum proxy — MTUM
excess return, and separately `QQQ − SPY`. Report R², beta and alpha with standard
errors.

**Decision rule, fixed in advance:**

| Outcome | Conclusion | Action |
|---|---|---|
| **R² > 0.5 and alpha not significantly positive** | Strategy is momentum beta | **Question closes.** Own the factor or the index; do not operate a costly replica. No structural study |
| **R² < 0.3 and alpha not significantly negative** | Something idiosyncratic may exist | Proceed to the **one** pre-registered slot study |
| **Anything in between** | Inconclusive | Report as inconclusive. **Do not proceed to sweeps** and do not re-cut the test to resolve it |

**Caveats to state with the result:** ~2.5 trades/month makes monthly returns lumpy;
partial investment attenuates beta, so a beta of 0.4 at ~50% exposure implies ~0.8
on deployed capital; and the universes remain hindsight-selected, which flatters any
momentum loading.

**If it correlates, that is not failure — it is substitution.** A definite answer
obtained cheaply is the best outcome available here.

### Mandatory discipline for any future strategy work

From the first line, not retrofitted: explicit **SPY total-return benchmark**; a
realistic cost model tied to the intended account size; **holdout or walk-forward**,
never tune-on-full-sample; **causal features only**; **import live entry/exit code
into the sim** rather than reimplementing it; snapshot the OHLCV; report net of
costs **and** excess return versus benchmark; and open a new `clean_vN` phase on any
rule change.

Reuse the **infrastructure** — measurement, labelling, parity assertions. Do **not**
reuse the parameter set, and do not assume the strategy family is validated.

### The role of each layer, restated

| Layer | Role |
|---|---|
| **Core wealth** | Index and ESPP discipline — the real edge, on savings rate |
| **Ares** | Paper research platform and integrity lab. Live only if the structural economics change |
| **Optional satellite** | A small live sleeve later, **only** if a configuration clears cost and benchmark bars in V6-class tests — never *"because June 2027"* |
| **Hermes** | Optional side learning, not the mainline |

**June 2027 is a review gate, not a deployment promise.** Deploy $1,000 live only if
by then a specified configuration shows credible excess return versus SPY after
costs in honest tests, **or** the sleeve is explicitly accepted as tuition and
telemetry rather than wealth optimisation. Otherwise paper continues and capital
stays on the index path.

## Momentum substitution test — RESULT, 2026-09-22

Executed without modification to the registered spec. Reviewed externally.

| Universe / proxy | R² | β | α annualised | α p | Registered verdict |
|---|---|---|---|---|---|
| **A / MTUM** *(primary)* | 0.2946 | +0.391 | **−7.27%** | 0.197 | POSSIBLE_IDIOSYNCRATIC |
| A / QQQ−SPY | 0.0431 | +0.345 | −4.17% | 0.526 | POSSIBLE_IDIOSYNCRATIC |
| B / MTUM | 0.1909 | +0.471 | **−20.93%** | 0.0147 | INCONCLUSIVE |
| B / QQQ−SPY | 0.0205 | +0.356 | −17.57% | 0.063 | POSSIBLE_IDIOSYNCRATIC |

Bootstrap **P(R² > 0.5) = 0.02** on the primary proxy. The substitution branch —
*"it is a momentum replica, own the ETF instead"* — is **rejected at ~98%**.
Momentum explains a fifth to a third of monthly variance, not most of it. HAC(3)
errors changed no verdict (A/MTUM α p 0.218 vs 0.197).

Data catch made before any coefficient was read: an unfiltered resample gave 60
observations, not 59, because the equity curve ends 2026-09-01 while factors run to
2026-09-21 — September compared **one day** of strategy against twenty-one of MTUM.
Partial months excluded; both variants recorded; all four verdicts identical either
way.

### The recorded conclusion

Not a replica, and not an idiosyncratic edge either. β on deployed capital ≈**0.61**
(A, t=4.88) and ≈**0.81** (B, t=3.67), with the residual **negative everywhere
measurable** — −7.3%/yr and −20.9%/yr.

> **Not a pure momentum ETF substitute; a partial-momentum process with a negative
> residual — dominated by a cheaper passive blend with similar factor loading.**

A static ~39% MTUM / ~61% T-bill mix matches the beta **without** the negative
residual, the code, the commissions or the operational risk. That is **economic
dominance**, not a statistical identity — R² ≈ 0.29 means most variance is not MTUM,
so *"it is MTUM"* cannot be claimed.

### The decision rule was correctly executed and badly specified

My error, recorded in full. The gate read *"R² < 0.3 and alpha not significantly
negative → proceed."* A's alpha is **−7.27%/yr at p=0.197**, so it passed and
returned POSSIBLE_IDIOSYNCRATIC — on a margin of **0.0054** from the threshold,
with bootstrap CI [0.131, 0.488] and **P(R² > 0.3) = 0.50**. A coin flip.

The substantive defect is worse than the margin. **Testing "is alpha significantly
negative?" under low power, then reading failure-to-reject as "not harmful,"
converts noise into permission.** −7.27%/yr is economically decisive and
statistically invisible at n=60 with ~2.5 trades a month. This is the same
low-power error identified for `clean_v3`'s 40-60 trade target earlier in the same
session, written into the rule anyway — **fourth instance of the bias in one
analysis, every one leaning toward the cleaner narrative.**

### CORRECTED ALPHA CONDITION — binding on all future pre-registration

Choose one primary and write it down **before** running. Preferred here is **Option
A**, an economic threshold:

| Option | Rule |
|---|---|
| **A — economic threshold** *(preferred)* | Annualised α ≤ **−3%/yr** on the primary universe and proxy → **adverse**, regardless of p-value. Report the CI; do not require p < 0.05 |
| **B — interval** | 90% CI for annualised α entirely below 0 → adverse. CI covers 0 but point estimate ≤ −X%/yr → **inconclusive adverse**, never green. Benign only if point estimate ≥ −X *and* CI not entirely negative |
| **C — equivalence** | Pre-define an indifference band, e.g. α ∈ [−2%, +2%]. Below → adverse; inside → practically zero; above → interesting |

**The standing principle: economic magnitude first, significance second, and low
power never reads as absolution.**

Under Option A at −3%/yr, Universe A returns **adverse**, not
POSSIBLE_IDIOSYNCRATIC.

### Slot study — SKIPPED

Three reasons stacking:

1. The decision margin on the factor test was noise-level.
2. **The mechanism does not exist in the code.** `signals.py:126` hardcodes
   `'confluence': 3` for every `momentum_breakout` signal, and queue eviction ranks
   by `(-confluence, date_added)`, so with a constant it degenerates to **date
   order**. Cutting 5 slots to 2 concentrates into *arbitrary* signals, not stronger
   ones.
3. `mean_reversion` does compute real confluence, so the objection weakens in
   proportion to its share of the population — but the share is not yet known.

Revisit only after confluence becomes a real ranking axis with genuine variance,
and then as a single pre-registered hypothesis.

## PRE-REGISTERED — Run B, concept versus defect

**Registered 2026-09-22 before execution.** Legitimate but easy to abuse, so the
fail branch is fixed first.

**Question:** does repairing causal divergence — confirm at `i+window`, fire on the
**confirmation bar only** — move the residual from economically negative to
non-negative?

The motivation is real: all four divergence columns are permanently `False` in
production, so `bullish_div` and `hidden_bull_div` never reach confluence. Live
*and* every backtest have measured a **crippled** version of the designed strategy.
Whether the −7.3% belongs to the concept or to the defect is genuinely open.

| Item | Fixed in advance |
|---|---|
| Implementation | Causal confirmation only. **No other parameter changes** |
| Primary universe | **A** — declared now, not after seeing results |
| Primary metric | Annualised α versus MTUM, plus net excess versus SPY on deployed capital |
| **Success** | α ≥ **−2%/yr** *and* net excess vs SPY ≥ **0** on Universe A |
| **Failure** | Anything else → **no strategy-continuation case arises from Run B** |
| Ban | No re-optimisation of TP, trailing stop or confluence on this window |

**Explicitly barred:** running Run B until something looks less bad, then continuing.
**If α moves from −7% to −3%, that is *less bad*, not *worth continuing*.**

Default if Run B is not run: divergence stays dead and documented in live, and is
**not repaired mid-`clean_v3`**.

## The Finviz gap — real, weak as a rescue

The owner's objection: the study cannot condemn Ares because live uses a Finviz
screen and confluence filters not represented in it. Adjudicated:

| Component | Status |
|---|---|
| Confluence and all entry filters | **Already in the study.** V6 imports live `signals.py` under an md5 parity assertion, so these are priced into the −7.3%, not missing from it |
| Divergence columns `False` | **Real live-versus-design gap.** This is the concept-versus-defect question Run B addresses |
| Finviz dynamic candidate pool | **Genuinely unmeasured.** No point-in-time screen history exists without paid data |

But Universe A is already hindsight-friendly — NVDA, PLTR, SMCI are there because
they went up. **A blind live screen is more likely to be harder than that list, not
easier.** So *"Finviz might save it"* is a weak prior, not a plan. Recorded as
**unquantified and likely non-rescuing** unless point-in-time screen history is ever
purchased.

## LIVE CAPITAL BAR — falsifiable, replaces the calendar

Paper research platform is the **default state**. Live capital requires a written bar
cleared, never a date reached. **All four must hold:**

1. Honest sim, V6-class with live code imported: **net excess versus SPY ≥ 0** over
   a pre-specified window, after costs, with the cash-interest policy stated up
   front.
2. Residual versus the MTUM blend: **annualised α ≥ −2%/yr**, by economic rule, not
   p-value games.
3. At the intended size and frequency, cost drag leaves room for (1).
4. `clean_v3` or a later phase shows no operational contradiction — a **process**
   gate, not edge proof.

**If the bar is never cleared, indefinite paper plus an index and ESPP core is the
rational shape. That is a completed insight, not a failed project.**

## Side items — both suspicions confirmed

**Idle cash.** +$50.14 on Universe A, so net **−$57.79 becomes −$7.65** — five
years, $1,000, **essentially flat rather than losing**, exactly as the raw figures
failed to say. B: +$35.66 → −$560.19. Reported as an adjustment rather than credited
inside `run_sim`, because paying interest would relax the funding gate and supersede
the A′ population again for a second-order reason.

**Exposure.** Mean **64.0%** and 58.4% — higher than the ~50% assumed at
registration, so deployed-capital betas are ≈0.61 and ≈0.81 rather than ≈0.8. A's
gross on deployed capital is **+42.68% versus SPY +85.55%** — behind by **half**,
not by the ~91 points the raw framing implied, and still not clearing the benchmark.
**Third consecutive instance of the raw framing overstating the gap.**

## Run B — RESULT: FAILURE, and the most informative run in the sequence

Executed 2026-09-22 under the pre-registered rule, Athena `5d44cce`.

| Primary metric, Universe A | Run B | Threshold | |
|---|---|---|---|
| Annualised α vs MTUM | **−16.06%** | ≥ −2% | **FAIL** |
| Net excess vs SPY on deployed capital | **−146.17%** | ≥ 0% | **FAIL** |

α CI **[−25.43%, −5.61%]**, p=**0.0040** — entirely negative and significant. The
anticipated awkward case, a drift to somewhere between −7% and −2%, **did not
arise.** Alpha moved **twice as far negative** and became significant.

### The defect was load-bearing

Repairing causal divergence made the strategy **substantially worse.** Gross P&L
collapsed from **+$273.21 to +$8.27**, and Universe B moved the same direction
(−20.93% → −27.40%).

> **The residual belongs to the concept, not the defect. The permanently-`False`
> divergence columns were the only thing keeping Universe A near flat. The strategy
> as designed is worse than the strategy as accidentally built.**

Three properties make this a finding rather than an artifact:

1. **The changed variable genuinely fired** — 28 divergence-triggered entries on A
   (−$111.74) and 27 on B (−$223.12). A null from a change that did nothing would be
   uninformative; this one had power.
2. **Both universes moved the same direction**, so it is not a single-universe
   accident.
3. **The mechanism was not over-claimed.** The +$250.63 of `bearish_divergence` exits
   was *not* asserted as the cause: counterfactual exits are unmeasured and the
   totals do not decompose additively. Declining to explain a result that cannot be
   decomposed is what makes the headline credible.

Exactly one variable changed, **asserted rather than trusted** — the driver diffs the
Run B config against A′'s and exits if anything else differs. The detector was
already causal, so Run B simply stops A′'s masking. Parity md5 on `signals.py`
stayed active and passed; `indicators.py`'s intended divergence is recorded in
`parity.py` with both hashes pinned and scope enumerated — **reported, not asserted**,
since this divergence is deliberate.

**Per the rule: no strategy-continuation case arises.** Divergence stays dead in
live and is **not** repaired mid-`clean_v3`.

### CONSEQUENCE — defect repair is not improvement, and the V4 plan changes

A known defect was **protecting** the system. That reaches further than Run B.

The three live defects were recorded as things to repair at a declared V4 boundary.
Run B demonstrates that **repair is not the same as improvement** in a system whose
behaviour is only understood empirically. If the inert queue gates started working,
promotion would tighten from drift-only — and there is now **direct evidence that
tightening this strategy's entry conditions destroys what little it has.**

**A V4 that fixes all three defects could perform worse than V3.1.** Every repair
must be measured against the same bar Run B just failed, never assumed beneficial.

The one exception is **defect 3, unfunded constant sizing.** That is a correctness bug
with an external deadline, not a behavioural tuning knob, and it is fixed on its own
merits.

## Task 1 — strategy split, slot study permanently closed

| | momentum_breakout | mean_reversion |
|---|---|---|
| Universe A (145) | **114 (78.6%)** | 31 (21.4%) |
| Universe B (148) | **123 (83.1%)** | 25 (16.9%) |

`momentum_breakout` confluence observed as **[3] only** — the `signals.py:126`
hardcode confirmed **empirically**, not just by reading the source. So eviction by
`(-confluence, date_added)` degenerates to **date order for ~80% of the
population.** `mean_reversion` spans [2,3], two levels across 17-21% of trades.

**The objection does not weaken. The slot study is permanently closed** — there is no
signal-quality axis to concentrate along for four trades in five.

## Task 2 — all four verdicts ADVERSE under the corrected gate

Both verdicts retained side by side; neither overwrites the other, because the point
on record is that the same data yields opposite conclusions under the two rules and
the first rule was mine.

| Universe / proxy | α ann. | α 95% CI | α p | Originally registered | **Option A** |
|---|---|---|---|---|---|
| **A / MTUM** *(primary)* | −7.27% | [−17.50%, +4.10%] | 0.197 | POSSIBLE_IDIOSYNCRATIC | **ADVERSE** |
| A / QQQ−SPY | −4.17% | [−16.26%, +9.49%] | 0.526 | POSSIBLE_IDIOSYNCRATIC | **ADVERSE** |
| B / MTUM | −20.93% | [−34.63%, −4.64%] | 0.0147 | INCONCLUSIVE | **ADVERSE** |
| B / QQQ−SPY | −17.57% | [−33.00%, +1.05%] | 0.063 | POSSIBLE_IDIOSYNCRATIC | **ADVERSE** |

**Three of the four passed the original gate purely because a wide CI crossed zero —
and the width was the reason for caution, not grounds for a pass.** That is the
clearest statement of the specification error available.

## Closing position on the strategy

| Question | Status |
|---|---|
| Momentum replica, own the ETF instead? | **No** — rejected at ~98%, P(R² > 0.5) = 0.02 |
| Residual: concept or defect? | **Concept.** Answered by Run B, both universes |
| Slot study | **Permanently closed** — no quality axis for ~80% of trades |
| Finviz objection's one legitimate branch | **Closed.** The gap was real and contains no rescue |
| Alpha under a correctly specified gate | **All four ADVERSE** |

> **Momentum as a factor works. This active implementation subtracts from it.**

That is a complete answer, obtained cheaply, and it is not a failure. The
`clean_v3` live sample cannot overturn it — it was never powered to, which is why it
was demoted to process and integrity observation.

### Open items, in priority order

1. **Nothing.** No sweep, no slot study, no Run C, no new strategy. The research
   question on this configuration is **closed**.
2. Ares V3.1 continues untouched. `clean_v3` runs as process observation.
3. **Defect 3, unfunded constant sizing** — the only repair justified on its own
   merits, before live capital ever arrives. Defects 1 and 2 stay dead and
   documented, because Run B showed repairing them is not obviously improvement.
4. Cost and account arithmetic in closed form, if ever needed.
5. The **LIVE CAPITAL BAR** above stands. This configuration cannot clear it.
4. Credit idle cash at a T-bill proxy in V6, or report the omission explicitly
   alongside every net figure.
5. Nothing in Ares. It continues on V3.1 untouched, `clean_v3` demoted but running.
6. **Run B** — divergence repaired at `i+5` — remains optional and must not be used
   to manufacture a nicer number.
7. The three live defects stay **documented and unrepaired** until a declared V4
   boundary. Defect 3 — unfunded constant sizing — is the only one with a hard
   external deadline.

**The honest measurement stack is the asset this project has produced.** It is not
the asset it set out to build, and that is a successful audit outcome rather than a
failed plan. It answers a V4 question — repair the detector or
delete the dead code — and is explicitly **not** required to judge whether the
live collection is meaningful. If effort is limited, Run A only. Run B must not
become a stealth re-fit toward a nicer story.

Two disciplines, both non-negotiable:
- **No re-optimisation.** The output is *"what the frozen parameters actually
  do,"* not *"here are better parameters."* Re-tuning on the same window is how
  the original figure was manufactured. Any future re-fit needs a real holdout —
  fit 2021-2024, test 2024-2026, report only the test result — and would open its
  own sample phase.
- **Snapshot the OHLCV data.** V1-V5 are unreproducible because
  `data/ohlcv/` is gitignored and empty, and yfinance adjusts retroactively.

Survivorship is the one item that cannot be fixed without paid point-in-time
index membership. V6 states it as a known, unquantified limitation and retracts
the "disproven" claim rather than pretending to have controlled for it.

Priority if forced to choose one destination for effort: **Athena V6 Run A** >
shadow module > Hermes work.

Findings become knowledge, not an immediate re-tune. Correcting a parameter
mid-sample would fragment the data; corrections belong at the V4 boundary with
its own phase label.

## Defect checklist — derived from ARES, reusable

Eleven defects surfaced in ARES over three weeks. None announced itself; every
one produced plausible numbers. They were not ARES-specific — they came from
authoring habits, so the same classes are likely wherever the same hands wrote
the same idioms. This is the scope for the ATHENA audit, and the audit gate for
HERMES — which is running rather than frozen, so the gate marks where its data
becomes trustworthy rather than where it restarts.

Read as: **pattern** → *how it appeared here* → **what to check elsewhere**.

Keep the pass short. The point is a reusable artifact, not a second project.

### A. Fabrication and absence

**1. Silent default on a missing input.** *`stdev_20 = sig.get('stdev_20') or
0.05` turned an absent volatility reading into a 5% one, producing a 10% stop
where the truth gave 3.1% — 3.2x the intended risk.* Grep every
`.get(k, <constant>)` and `or <constant>` on a value that feeds a
calculation. Ask whether the constant is a legitimate value for that field. If it
is, you cannot distinguish absence from data.

**2. Fabricated sentinels.** *Pending records default `rsi` and `vol_ratio` to
`0` — both impossible in practice, both indistinguishable from a reading.* Any
numeric default that is a physically possible value is a future silent error.
Prefer `None`.

**3. A gate reading pre-sanitised input.** *The absence guard for `stdev_20`
could never fire, because one function earlier the pending record had already
replaced the absence with `0.05`. The gate was correct and useless.* For every
validation, trace where its input was last written. A gate only protects the
inputs it reads.

**4. Empty results reported as zero.** *Metrics over an empty sample must return
`None`, not zeros, and profit factor must be undefined rather than 0 when
nothing has lost yet.* An absent result must never be readable as a real one.

### B. Value computed then lost

**5. Compute, print, discard.** *Scale-out P&L was calculated, printed to the log,
and assigned to a local that went out of scope. Every scaled winner was
understated by exactly the amount the scale-out locked in — $8.06 on one trade.*
Grep for values that appear only inside an f-string. If a number is worth
printing it is usually worth storing.

**6. Partial exits not booked.** *P&L was measured on `trade['shares']`, which
holds only the remainder after a scale-out, so the tranche already sold was
invisible.* Wherever a position can be reduced, confirm P&L spans the original
size, and that percentage and dollar figures share one cost basis.

**7. Field overwritten instead of accumulated.** *`total_commission` was set to
entry+exit at close, dropping the scale-out commission.* Any running total that
is assigned rather than incremented.

### C. One quantity, several answers

**8. The same fact measured three ways.** *Win count used gross P&L, the realised
total summed net, the dashboard icon used percent — three verdicts on one trade,
so the win count disagreed with the realised figure.* Pick one definition,
centralise it, and have every consumer import it.

**9. Labels that do not travel.** *`trades_report.csv` omitted
`sample_phase` and `contaminated`, so the file most likely to reach a
spreadsheet or a model invited aggregating contaminated rows into an edge
figure.* Every export must carry the fields that determine whether a row is
usable.

### D. Ordering, shape, and retries

**10. Action before refresh.** *Fills ran before the data update, so an entry
could take the previous session's open. 4 of 6 entries were stale, dispersion
±9.5% — wider than the stop distance itself.* For any read-then-act sequence,
confirm the read happened after the most recent write.

**11. Library shape assumptions.** *yfinance returns MultiIndex columns; `df['Volume']`
then yields a DataFrame, and indicator assignment raised
"Cannot set a DataFrame with multiple columns to the single column ...",
permanently dropping signals.* Normalise external data shape at every ingress,
not at first use.

**12. Retry budgets against deterministic faults.** *`MAX_CHECK_FAILURES = 3`
assumes failure is transient. A code defect fails identically every attempt, so
the retry only postponed the same permanent drop by three scans.* Distinguish
transient from permanent before deciding to retry; the budget protects against
network flakiness, not logic errors.

### E. Configuration and operations

**13. Config that looks authoritative but is inert.** *`risk_rules.json`
declared a 10% position cap and a 5% weekly loss limit; no code read either, and
actual sizing was 15%. Six keys in `strategy_params.json` were likewise unread,
three of them shadowing hardcoded literals.* Diff every config key against the
source. Delete or implement; never leave a third state.

**14. Silent publish failure.** *`git push` with no preceding pull was rejected
non-fast-forward, the commit stayed local, the error went to a log nobody reads,
the script exited 0 and the notification reported success. Ten failures
accumulated before anyone noticed.* Any step that ships data must fail loudly
where the failure will be seen, and must not report success on a non-zero path.

**15. Provenance inferred rather than stamped.** *Whether a fill came from the
schedule or a manual run cannot be reconstructed from a finished record, so it is
stamped at fill time with a second field recording how it was determined.*
Anything you will later need to know about how a record was produced must be
written when it is produced. Recorded evidence does not decay; inferred evidence
does.

### Applying it

For ATHENA specifically, items 1, 5, 6, 10 and 11 are the highest yield: a
backtest defect is worse than a live one because no broker contradicts it.
Findings become knowledge, not an immediate re-tune.

For HERMES, add item 15. It is running while unaudited by decision, so the date
of its checklist pass becomes its `CLEAN_FROM` boundary — everything before that
is process-validation history, exactly as ARES weeks 1-3 became. Knowing the
boundary now is cheaper than reconstructing it later.

## Rule for new work during the observation phase

> New code is justified only if it measures something `clean_v3` cannot measure
> **and** does not change which official trades are taken.

Agreed externally after three weeks in which bug-hunting was genuinely
productive — which is precisely why it then feels productive beyond the point
where it is. The default action is to let the sample accumulate.

Priority order from 2026-09-22:

| Rank | Action | Why |
|------|--------|-----|
| 1 | Keep ARES V3.1 running, no strategy changes | the official sample only grows this way |
| 2 | Defect checklist (above) — artifact only | cheap, reusable, no code |
| 3 | ATHENA read-only audit | the frozen parameters sit on unaudited ground |
| 4 | Shadow module, capacity-only | answers V4 questions without breaking `clean_v3` |
| 5 | Everything else | later |

Cut first if forced: near-miss confluence tracking. Cut second: delay the shadow
module until the ATHENA audit is done. Never delay *running* ARES.

## Unimplemented Risk Controls

These were previously written in `config/risk_rules.json`, a file no code ever read.
They are recorded here as **future work**, not as configuration, so they cannot be
mistaken for active protections. See the Configuration section of the README.

| Intended control | Stated value | Current reality | Priority |
|------------------|-------------|-----------------|----------|
| Max position size | 10% of capital | **15%** — `(1000 × 0.75) / 5 = $150` | Before live |
| Max concurrent positions | 8 | 5 (`max_positions`) | Low — 5 is deliberate at $1K |
| Cash buffer | 30% | 25% (`cash_reserve_pct`) | Low — reconcile wording |
| Weekly loss circuit breaker | 5% | **Not implemented** | **Before live** |
| Stop loss multiplier | 2.0× stdev | ✅ now `stop_loss_multiplier` in `strategy_params.json` | Done |

### Position sizing reconciliation

Sizing is currently `(starting_capital × (1 − cash_reserve_pct)) / max_positions`,
which yields 15% per position — 50% above the intended 10% cap. Two ways to reconcile:

- Raise `max_positions` to 8 (aligns with the original intent: `0.75 / 8 ≈ 9.4%`)
- Or add an explicit `max_position_pct` cap applied after the division

Deliberately deferred during the observation phase: changing sizing mid-dataset would
split the trade history into two incomparable regimes, and with only 1 closed trade the
priority is a clean baseline, not optimal sizing.

### Weekly loss circuit breaker

The most important gap. Nothing currently halts trading after a losing streak.

Sketch:
- On each scan, sum `pnl_after_costs` for trades closed within the trailing 7 days
- If that sum ≤ `−(starting_capital × max_weekly_loss_pct)`, skip new entries for the
  remainder of the week — exits and monitors must keep running
- Surface the halt state on the dashboard, since a silent halt is indistinguishable
  from a scan that simply found no signals
- Log the trip to `queue_events.jsonl` so the pause is auditable after the fact

Prerequisite: enough closed trades for a 7-day window to be meaningful. Revisit
alongside the Phase 3 ML work at 40-60 trades, and **implement before any real capital
is deployed in June 2027.**

---

## Notes
- Paper trade minimum 3 months before going live
- ~~Need 50+ closed trades for statistically meaningful data~~ — **false, and
  quantified on 2026-09-24.** At n=45 the 95% CI half-width on mean per-trade is
  ±3.16%, wider than the +3.0% needed to match SPY. 50 trades cannot distinguish an
  edge from a loss. See "how the `clean_v3` live sample may be read" below; a validity
  claim needs n ≥ 100
- Shadow tracking data is critical for TP optimization
- Start real money only when profit factor > 1.3 consistently
- ML model only useful with sufficient data — never trust models trained on <50 trades
- Backtest results ≠ live results (slippage, timing, emotions) — use as guidance only

## PRE-REGISTERED — how the `clean_v3` live sample may be read

**Written 2026-09-24, before the first `clean_v3` trade closed.** Registered in
advance for the same reason the alpha gate was: the reading rule must exist before the
number, or a lucky mean gets read as vindication.

### The arithmetic, from Run A′'s 145 trades

```
mean per-trade  −0.27%      SD per-trade  10.82%
```

| Closed trades | 95% CI half-width on mean per-trade |
|---|---|
| **45** | **±3.16%** |
| 60 | ±2.74% |
| 100 | ±2.12% |
| 449 | ±1.00% |

Each trade deploys $149 of $1,000 = 14.9% of capital, at ~29.5 trades/yr. To match
SPY at ~13.4%/yr:

```
29.5 × 0.149 × x = 13.4    ->    x ≈ +3.0% per trade
```

### The consequence, stated plainly

**At 45 trades the confidence interval (±3.16%) is wider than the entire effect being
tested (+3.0%).** A observed mean of +1% yields roughly [−2.2%, +4.2%], an interval
containing both "loses money steadily" and "beats SPY."

The sample is inconclusive **by construction**, and this was knowable before any trade
closed. The 40-60 target was never a statistical threshold; it was a round number.

### What may and may not be concluded

| At n ≈ 45-60 | Available? |
|---|---|
| Implementation sound, or implementation broken | **YES.** A live mean of −8% against a sim mean of −0.3% is a bug and will show |
| Exit-reason mix matches the sim | **YES.** Categorical splits converge far faster than means |
| Operational failure — fills, sizing, crashes | **YES** |
| **Strategy has an edge** | **NO** |
| **Strategy beats SPY** | **NO** |

**Binding rule:** a positive mean whose CI includes zero is **not** evidence of edge
and must not be recorded as one. Any claim about strategy validity requires
**n ≥ 100** (≈3.5 years at the current rate). Report the CI every time the mean is
reported; a mean without its interval is not a result.

### The one genuinely unmeasured difference

Live screens Finviz dynamically; every backtest used a fixed list. That screen has
never been measured and cannot be with free data — there is no point-in-time Finviz
history. Live is the only place it is observable.

But n=45 cannot see it either, and the prior is not neutral: Universe A contained
hindsight winners (NVDA, PLTR, SMCI), so a blind live screen more plausibly does
**worse**. This is a reason to keep collecting, not a reason to expect good news.

## V4 candidates from the FriesTrader ablation

The ablation (Athena `3b6469b`, ADVERSE on both arms) found that system's rules have
no edge, but two of them are better-designed than Ares' and are worth **measuring** as
V4 candidates — against the bar Run B failed, not assumed beneficial:

1. **Trail engages only after the first take-profit tier.** FriesTrader references the
   stop to `average_cost` until +15% is reached, then switches to a trailing high. It
   is therefore structurally incapable of trailing a position into a loss. Ares seeds
   `peak_price` at `entry_price` on day one, which is the direct cause of the
   +6.7% → +11.1% dead band where `trailing_stop` books a loss.
2. **Tiered partial take-profit** at +15/+30/+50%, 25% each, versus Ares' single 50%
   scale at +18% that has never once fired in live.

Both change the trade population, so both are **V4 boundary** work and neither may be
applied mid-`clean_v3`.

## Athena's remaining parity gap — highest-value audit available

Entry predicates are imported from live `signals.py` under an md5 assertion. **Exit
logic is not**: `portfolio_sim_v6.py:187 _decide_exit()` is a reimplementation of
`tracker.py`. Every Ares exit that has ever fired is a stop or a trail, so the one
part of the sim not under parity is the part deciding every outcome — the same shape
as the Run A → Run A′ failure, still open in the codebase.

Closing it costs **zero** live sample and should precede any V4 measurement.

## De-duplicate the launcher scripts

`/root/ares/run_ares.sh` is what cron executes. `Ares/run_ares.sh` is the copy
under version control. They are currently identical, having been re-synced by
hand on Sep 21 after the tracked copy was found to be missing two changes.

**Why this matters:** only one of the two runs, and the failure mode is silent.
A fix applied to the tracked copy would show up correctly in git, read correctly
in review, and have no effect on the running system. That is the same shape as
the `risk_rules.json` problem and the discarded scale-out value: something that
looks accounted for but is not wired to anything.

**Intended fix:** reduce the outside copy to a thin wrapper that sources
`/root/ares/.env`, activates the venv, and delegates to the repo copy, so there
is exactly one place where the logic lives.

Deliberately deferred past Sep 21: cron fires the first run under fully correct
accounting that evening, and changing how the launcher resolves paths hours
beforehand risks a silent failure at the worst moment. Low urgency now that the
two are in sync, but it will drift again.


## PRE-REGISTERED — Ares V4 Block A: exit-policy replay (exploratory)

Written 2026-09-25, before any replay code or replay result exists.

### Status of this experiment

The exit-policy replay is **exploratory and cannot authorize replacement of the
production exit policy**. Policy A remains the control regardless of
development-period performance. An alternative may be recorded as a candidate
for future confirmation only.

Production selection requires an untouched confirmation period containing **at
least 20 relevant mechanism triggers in each universe separately**, together
with all other pre-registered economic and consistency requirements.

This constraint is derived from data, before results:

| | dev n (<2024-09) | val n | +18% triggers in val | Support band |
|---|---|---|---|---|
| Universe A | 85 | 60 | **14** | 10-19 = exploratory only |
| Universe B | 107 | 41 | 10 | 10-19 = exploratory only |

**Corrected 2026-09-25**, once instrumentation emitted real `mfe_pct`. Universe A
was first recorded as 16 using `scaled_out` as a "+18% reached" proxy. That proxy
is wrong: `take_profit` is `tp_momentum` 0.18 for momentum strategies but
`tp_reversal` **0.10** for mean-reversion, so the 41 Universe A tier hits mix two
thresholds. The registered conclusion strengthens — fewer qualifying triggers, not
more. Universe B's 10 was correct.

Neither universe confirms even the *existing* +18% mechanism in the untouched
period. The +30% and +50% mechanisms necessarily have equal or lower support.

**The trigger threshold will not be reduced, pooled across universes, or
replaced with an aggregate count after results are observed.**

No production change to Ares V3.1. `clean_v3` continues untouched.

#### Downstream gate

Blocks B-D are specified for continuity but are **not authorized to begin** by
this pre-registration.

Because the untouched validation period contains fewer than 20 relevant
mechanism triggers in each universe, Block A cannot confirm a replacement exit
policy. Policy A therefore remains the production control, but **retention as
control is not evidence that it is an economically worthwhile modelling
target**.

After Block A:

- If no alternative clears the development-period economic and consistency bars,
  Blocks B-D remain deferred and the exit-policy family may be rejected.
- If an alternative clears the development bars but lacks confirmation support,
  it is recorded as a future confirmation candidate. Blocks B-D remain deferred.
- If a future untouched confirmation period supplies at least 20 relevant
  triggers per universe and the candidate clears every registered bar, the
  policy may be frozen for label generation and Blocks B-D may open.
- Policy A may open Block B only under a separate, explicit decision that
  modelling the current control has standalone research value despite its
  documented exit weaknesses. Such work cannot be presented as development of
  the intended successor policy.

**Default outcome: complete Block A and stop.**

#### Work order and authorization

- **Block A** - canonical exit implementation and exploratory exit-policy
  replay. **Authorized now.**
- **Block B** - reconstructable-universe dataset and frozen-policy label
  generation. Deferred pending the downstream gate.
- **Block C** - linear ranking test and one-time tree challenger. Deferred
  pending successful completion of Block B.
- **Block D** - entry-only chronological portfolio simulation and final
  benchmark gate. Deferred pending successful completion of Block C.

Completion of one block does not automatically authorize the next. Each block
opens only when its stated evidence gate is cleared. Prior investment in design
is not an argument for continuation.

### Retraction: the +18% take-profit is not inert

Previously recorded as "the +18% momentum take-profit has never been reached."
That was true of the five-trade live history available at review, where maximum
observed MFE was +15.53% (HAFN). **It does not generalize to the audited
backtest populations.** In Run A' the +18% level was reached by:

- **37 of 145** Universe A trades (25.5%)
- **27 of 148** Universe B trades (18.2%)

(First recorded as 41 and 30. Those were *tier hits*, which include mean-reversion
positions scaling out at `tp_reversal` +10%. Measured `mfe_pct >= 18%` gives 37
and 27. The retraction's substance is unaffected — the +18% level is reached
routinely — but the figures were conflated.)

Consequences:
1. Policy A's +18% tier is **not** structurally inert in historical replay.
2. Policy C may not be justified as a correction to an allegedly dead mechanism.
   It is replayed as a pre-specified design alternative only.

This is the **fifth** logged instance of a structural claim stated more broadly
than its evidence supported, and it leaned the same direction as the previous
four. Future structural claims discount accordingly.

### Block A prerequisites (all must pass before replay)

1. **Canonical exit module.** One implementation called by Ares paper
   operation, Athena exit replay, candidate-label generation, and final
   portfolio simulation.
2. **Exit-reason reconciliation.** `sum(exit_reason_counts) == total_trades`.
   Verified clean at draft time: A = 70+70+4+1 = 145; B = 73+73+1+1 = 148.
3. **Structural invariant test.** `scaled_out=True => exit_reason != stop_loss`.
   Impossible by construction: peak >= 1.18x entry implies
   `trail = peak*0.90 >= 1.062x entry`, always above a sub-entry stop. The
   observed 70/70 and 73/73 symmetry is coincidence on a sound mechanism, not a
   labelling defect. Resolved 2026-09-25; retained as a regression test.
4. **Instrumentation.** Emit peak_price, peak_date, mfe_pct, trough_price,
   mae_pct, initial_stop, trail_activated, trail_activation_date,
   highest_trailing_stop, scale_out fields, locked_pct, gave_back_pct,
   commissions, slippage, holding_days, time_stop_bound, exit_policy_version.
   Currently `mfe_pct` and `peak_price` are absent from saved output, so the
   proposed exit-quality metrics are uncomputable.
5. **MFE disambiguation.** Report separately: MFE before any partial exit, MFE
   over the whole trade, MFE after the first tier, return retained at final
   close, total return including partial exits. A tiered policy's exit quality
   is misstated if the final remainder is compared against the full-position
   peak.
6. **Giveback, split two ways.** `mfe_pct` is price-path based while
   `net_return_pct` includes commissions, so a single metric conflates market
   movement with friction - a policy with identical exit prices but more
   partial-exit commissions would appear to have worse trailing behaviour.
   Store both:

   ```
   price_giveback_pct    = max(0, mfe_pct - gross_price_return_pct)
   economic_giveback_pct = max(0, mfe_pct - net_return_pct)
   ```

   The first evaluates exit mechanics; the second evaluates what the account
   retained after costs. Underlying price and return fields are retained so
   both can be independently recomputed rather than trusted.
7. **Path-level confusion report.** Stored exit reason versus canonical replay
   exit reason, every mismatch explained.

### Maximum holding period

60 days is **rejected** as an administrative censor: it binds on 26.2% of
Universe A and 14.2% of Universe B.

Registered value: **H = 170 trading days, `holding_days >= H` convention.**

| H | A `>=H` | B `>=H` |
|---|---|---|
| 60 | 26.2% | 14.2% |
| 165 | 5.5% (fails) | 2.0% |
| 166 | 4.83% | 2.03% |
| **170** | **4.1%** | **2.0%** |

166 is the smallest value clearing 5% in both universes, but sits 0.17pp from
the threshold. 170 is registered for margin, on the precedent of the momentum
test landing 0.0054 from its own cutoff.

The convention must match the canonical module's event ordering. **The horizon
will not be increased after replay results appear.** Bind rate is reported per
universe with the P&L, MFE, MAE, strategy family, and regime of bound trades.

### Replay design

Four frozen policies, no grid search:

- **A (control)** 50% scale-out at +18%, 10% trail from entry onward
- **B** trail activates only after first take-profit tier
- **C** tiers 25% at +15%, +30%, +50%; remainder on activated trail.
  **Registered as having an unsupported top tier.** Measured on the full five-year
  sample, +30% is reached by 15 (A) and 11 (B) trades and +50% by **4 and 4** —
  below the 10-trigger "unsupported mechanism" floor in every period, including
  development. A three-tier policy whose top tier fires four times in five years
  cannot be evaluated; it will be replayed for completeness but no result from its
  +30% or +50% tiers may be reported as evidence, and it is not eligible for
  candidate status on the strength of those tiers.
- **D** current 50% scale-out at +18%; the 10% trail activates only once peak
  reaches **+11.2%**, with the initial stop controlling before activation.
  The threshold is derived, not chosen: `trail = peak x 0.90 >= entry` requires
  `peak >= +11.11%`, the minimum peak at which the trail can lock any gross
  profit. Activating there eliminates the documented dead band exactly. The
  value will not be adjusted from replay results.

#### AMENDMENT 2026-09-25 — Policy C removed from the replay

Recorded **before any B, C or D replay code or result exists.** Policy A
instrumentation was complete; no alternative-policy outcome was known.

Policy C is **removed from implementation**, not disproven. Grounds:

1. Its distinguishing +30% and +50% tiers structurally fail the already-registered
   20-trigger support threshold in every period and both universes. Full five-year
   sample: +30% reached by 15 (A) and 11 (B), +50% by **4 and 4**. Development
   period alone: +30% by 7 and 8, +50% by 2 and 3. No future confirmation period
   drawn from this universe can reach 20.
2. Implementing C requires generalising Athena's scale-out accounting from one
   tranche to N. `scale_out_price`, `scale_out_shares` and `scale_out_proceeds`
   are single-valued, the fill guard is `not pos['scaled_out']`, and six of the 33
   validation checks cover exactly that path — scale-out proceeds net of
   commission, audit-field persistence, per-trade pnl reconciliation, and the
   realised-plus-unrealised identity. Rewriting the accounting core to replay a
   policy that cannot reach candidate status would also put Policy A's
   byte-identity guarantee at risk, and that guarantee is what makes Block A valid.

C is **not reclassified as disproven or unpromising.** Its tier structure remains
untested. Should a materially larger sample ever exist, C returns as a registered
candidate with no prejudice from this amendment.

**Retained: A (control), B, D.** The replay question is therefore narrow: does
delaying trail activation — until the first tier (B), or until the trail can lock
a gross profit at +11.2% (D) — improve on a trail live from entry (A)?

#### Affected-subset recovery hurdle

Not a feasibility bound, and explicitly not an upper limit on what B or D can
achieve. Policy A's recorded peaks are **truncated at A's exit**, so under B or D
the same trades run longer and may exceed the old peak, reach the +18% tier, fall
to the initial stop, or bind the 170-day limit. This states only what B and D
**must** deliver on the trades they can alter to clear the +0.50pp isolated bar.

| Policy | Trades alterable (A) | Their mean P&L | Losses | Mean peak at A's exit | Required per affected trade |
|---|---|---|---|---|---|
| B — activate at first tier | 34 / 145 | −6.06% | 31/34 | +8.98% | **+2.13pp** |
| D — activate at peak +11.2% | 24 / 145 | −7.67% | **24/24** | +7.00% | **+3.02pp** |

Universe B: 44 and 29 trades, requiring +1.68pp and +2.55pp.

Arithmetic: `0.50pp x 145 / 24 = 3.02pp`.

**Registered prior, before replay.** Because `effective_stop = max(initial_stop,
trailing_stop)`, a trade labelled `trailing_stop` exited *above* its initial stop.
Suppressing the trail therefore removes a higher protective exit and exposes the
trade to the lower one. B and D exchange a known better exit now for a possible
recovery later, on subsets that had already stopped running at +7–9% peaks. B and
D are therefore **expected** to fail the bar. This is a prior, not a result: a
small number of these trades could recover sharply after crossing the old trail
level, and only the replay distinguishes a plausible structural argument from an
observed counterfactual. The prior will not be revised after seeing which trades
recovered.

#### Policy A giveback, stratified

"Profitable at peak" counts every favourable tick, including excursions that never
cleared friction — live NEOG peaked at +0.03%. Stratified by economic
significance (round-trip cost 1.34% at $149):

| Stratum | Universe A | Universe B |
|---|---|---|
| MFE > 0 and net loss | 72 (49.7%) | 66 (44.6%) |
| MFE ≥ 1.34% (cost) and net loss | 57 (39.3%) | 61 (41.2%) |
| MFE ≥ 3% and net loss | 49 (33.8%) | 47 (31.8%) |
| **MFE ≥ 5% and net loss** | **32 (22.1%)** | **36 (24.3%)** |
| MFE ≥ 10% and net loss | 9 (6.2%) | 13 (8.8%) |
| **MFE ≥ 18% (tier) and net loss** | **0 (0.0%)** | **0 (0.0%)** |

Of Universe A's 72 MFE>0 losses, **15 never cleared round-trip cost** and are noise
rather than surrendered profit. The defensible statement is therefore not "half the
trades gave back everything they gained" but: **22.1% of Universe A trades achieved
a favourable excursion of at least 5% and still closed at a net loss, surrendering
a mean 13.24pp from peak at a mean −5.78%.**

**Finding in Policy A's favour, which the aggregate hid — stated on the correct
basis.** The scale-out threshold is **not** uniform: `take_profit` is
`tp_momentum` +18% for momentum but `tp_reversal` **+10%** for mean-reversion. So
"reached the tier" and "MFE >= 18%" are different populations and must be reported
separately.

| Basis | Universe A | Universe B |
|---|---|---|
| MFE >= 18% and net loss | 0 | 0 |
| **Tier executed** and net loss | **0** | **2** |

The two Universe B violations are `TOST` (−0.94%) and `CLF` (−0.05%), both
mean-reversion trades that scaled out at +10%. Scaling 50% out at +10% locks only
about +5% on half the position; a third commission plus the remainder falling to
the stop can erase it.

Corrected conclusion: **the +18% momentum tier is protective — 0 violations in 54
tier executions across both universes. The +10% mean-reversion tier is not — 2
violations in 17.** The giveback problem is concentrated below the momentum tier,
and the mean-reversion tier is itself marginal. Any replacement policy must
preserve the momentum-tier property; it has less to preserve at +10%.

#### Trigger-count reconciliation

Registered `tier_exec` counts (41 A / 30 B) and measured `mfe_pct >= 18%` counts
(37 A / 27 B) differ by 4 and 3. Fully reconciled — every mismatch is a
mean-reversion trade that executed its own +10% tier without ever reaching +18%:

- Universe A: `GILD` (MFE 12.58%), `AMT` (12.03%), `ABBV` (12.58%), `LIN` (17.39%)
- Universe B: `FVRR` (17.80%), `TOST` (11.47%), `CLF` (13.11%)

Canonical definition going forward: **a tier hit is an executed scale-out event
recorded by the exit module**, not an inference from rounded MFE. Both counts are
reported. Two invariants tested rather than assumed, and both PASS in both
universes:

```
tier_executed  =>  mfe_pct >= that trade's own TP threshold
mfe_pct >= TP  =>  tier_executed          (holds bidirectionally, because
                                           scale-out is evaluated first and
                                           short-circuits the exit chain)
```

#### Where B and D actually intervene

| Peak region | Policy A | Policy B | Policy D |
|---|---|---|---|
| below +11.2% | trail active | **trail inactive** | **trail inactive** |
| +11.2% to below +18% | trail active | **trail inactive** | trail active |
| +18% and above | scale-out + trail | scale-out + trail | scale-out + trail |

**B is the broader intervention**, suppressing the trail across the entire
sub-tier population; **D suppresses it only below gross breakeven**, where the
trail cannot lock a profit anyway. B therefore carries more exposure to giving up
Policy A's higher protective exit, which the affected counts confirm: B touches
34 (A) and 44 (B) trades against D's 24 and 29.

D tests the narrow question — does suppressing a structurally loss-making trail
help? B tests the broad one.

**Stage 1 - isolated paired replay** on the original 145 (A) and 148 (B)
entries. Primary statistic `delta_i = return_candidate_i - return_A_i`.
Report mean, median, **date-block bootstrap** interval (not trade-level:
overlapping trades understate uncertainty), % positive deltas, count of zero
deltas, count of trades where policies differ, and contribution of the largest
1/3/5/10 deltas.

**Stage 2 - chronological portfolio replay** on the **complete timestamped
signal stream**, including signals originally rejected for occupied slots,
unavailable cash, queue expiry, or losing to a higher-ranked candidate.
Different exits change slot occupancy and funding, which changes which later
signals enter at all. If the full stream cannot be reconstructed, state:
*the portfolio replay changes exit timing on the original trade population but
cannot reconstruct counterfactual admissions; it is not a complete portfolio
counterfactual.* Missing entries are not inferred.

#### Absolute economic bar

Registered before code exists. Anchored to Run A' measurements: Universe A net
was -$57.79 over 145 trades = $0.399/trade = **0.268% of a $149 position**, so
+0.27pp/trade is exact breakeven.

**Isolated replay bar** - all four required, per universe separately:

1. Mean paired delta versus Policy A **>= +0.50pp per trade** (breakeven plus
   margin; ~1/20th of the 10.8% per-trade SD, so not noise-chasing)
2. Date-block bootstrap 90% interval **lower bound > 0**
3. **>= 50%** of non-zero paired deltas positive
4. Support and concentration gates both passed

**Portfolio replay bar** - all four required, per universe separately:

1. Net return after friction **not lower** than Policy A
2. Maximum drawdown **not worse by more than 5pp** than Policy A
3. Risk-free-relative result **not below** Policy A's
4. Trade count and commission total reported; no improvement accepted whose
   source is reduced turnover alone unless that is stated as the mechanism

**Portfolio-level absolute floor.** No policy may be **recorded as a future
confirmation candidate** unless at least one achieves **net funded return >= 0%**
in Universe A. Run A' measured -5.58%; a policy that merely narrows the loss has
not cleared the bar. If every policy including A remains below this floor, the
registered result is *no policy cleared the economic bar*.

The nonnegative funded-return floor applies to **Universe A only**. Universe B is
the adverse consistency test: it must show positive paired improvement and clear
every registered portfolio-relative bar, but it is **not** required to recover
from Run A' -57.32% baseline to nonnegative funded return in this exploratory
replay.

**The two bars measure different things and neither implies deployability.**
+0.50pp per trade is the isolated paired-replay bar, asking whether an
alternative materially improves exit capture on the same entry paths. Net funded
return >= 0% is the portfolio-level floor, asking whether that improvement
survives changed holding periods, slot occupancy, funding, commissions, and
counterfactual admissions. The per-trade breakeven arithmetic above motivates
the first bar only; it is not a justification for the second.

This is a **recording floor for identifying a future confirmation candidate, not
a deployment threshold**. Clearing it would show that an exit policy merits
further confirmation; it would not establish entry edge, competitive return, or
superiority to SPY, MTUM, or any risk-matched passive alternative. Those
comparisons remain reserved for the final Block D deployment gate.

For scale: +0.50pp/trade across 145 trades at $149 is roughly **+$108**, taking
Universe A trading net from -$57.79 to about +$50 - near **+2%/yr** over the
window against **SPY +85.66%**. A policy passing every registered gate still
loses heavily to passive.

**Selection rule (conjunctive).** A policy is eligible only if:
1. Paired improvement over A is economically positive in **both** universes
2. Direction of improvement is consistent across both
3. Universe A clears the portfolio-level absolute floor; **both** universes clear
   their paired, support, concentration, and portfolio-relative bars
4. Mechanism triggers >= 20 **per universe**, not pooled
5. Chronological portfolio replay clears the bar in **both** universes

A large Universe A gain may not cancel a Universe B loss. Trigger counts are
reported per mechanism per universe, with per-tier P&L attribution and a
concentration gate:

```
concentration_10 = sum(10 largest positive paired deltas)
                   / sum(all positive paired deltas)
```

**A policy is unsupported if `concentration_10 > 50%`.** The denominator is
gross positive improvement, not net: a near-zero net denominator makes
concentration ratios unstable. Reported per universe.

**Valid outcomes:** retain A; record a future confirmation candidate;
inconclusive; reject the policy family. **"Least bad" is excluded** - the
experiment is not required to produce a winner. If A-D all lose, the result is
"no policy cleared the economic bar," not "B was best."

### Label contract (Block B)

```
label = net realized return under frozen exit policy
        - risk-free return over the identical holding interval
```

Net of all friction. Risk-free source, duration matching, and compounding
convention frozen before labelling, consistent with Athena's cash-interest
treatment. Per-trade counterfactual is **not investing**, because the floor is
designed to return zero candidates in risk-off regimes.

Diagnostics only, never the target: SPY-relative, MTUM-relative,
forward_excess_5d / 10d / 20d. Every label stamped with `exit_policy_version`.

Portfolio-level comparison against SPY, MTUM, a risk-matched passive blend, and
cash remains the final deployment gate.

### Inference

Exit-replay primary HAC lag: **A = 29, B = 17** trading days, from
current-policy median holds established before replay.

Later ranker IC HAC lag equals the median holding period produced by the
**frozen** policy on the **development** period, rounded up to the next whole
trading day. That rule is fixed now; the value is determined later without
reference to validation results.

Non-overlapping sampling uses the same frozen-policy lag, with **every**
deterministic offset reported as a distribution - never the best offset.

**When full-frequency HAC and non-overlapping evidence conflict, the
non-overlapping evidence governs.** Both positive and stable: supported. HAC
positive, non-overlapping unstable: inconclusive. Concentrated in one offset or
year: inconclusive. Both weak or negative: reject.

### Model stage (Block C)

Six-feature linear model is the default. Feature formulas frozen with the
budget - windows, estimators, and sector mapping source all specified before
results. Model order: deterministic baseline, single factor, six-feature
linear (ridge), tree challenger on the same six features.

The tree is promoted only if it clears **every** pre-registered condition on
first evaluation. If it fails any, the linear model ships. Failed criteria will
not be changed, removed, or reweighted, and the tree will not be rerun under
revised acceptance rules on the same data. A revised hypothesis requires a new
research version, a new untouched period, and a written justification unrelated
to the failed result.

Winner's-curse calibration uses **only out-of-sample observations that the full
daily selection process would actually have selected**. Insufficient selected
observations means the confidence gate is unavailable and must not be replaced
by an arbitrary score threshold - the safe result is no deployment.

### Research-leakage boundary

Exit-policy development: universe start to 2024-08-31.
Exit-policy validation: 2024-09-01 onward, opened once, no revision after.
The ranker's final evaluation period must not have participated in exit-policy
selection. Given the trigger counts above, this boundary is recorded but the
validation period is **not** sufficient to confirm a replacement.

### Finviz

Removed entirely from the V4 entry path. Training and live use the identical
reconstructable population, making universe parity testable by construction.
Finviz continues as a **shadow research feed**: log symbols and screen
categories, compute the same features, generate shadow scores, simulate frozen
exit-policy outcomes, **no live or paper entry authority**. This accumulates the
point-in-time Finviz evidence that does not currently exist.

### Version lineage

Every result identifies all six: `exit_policy_version`, `universe_version`,
`label_version`, `feature_set_version`, `model_version`,
`evaluation_contract_version`. This prevents a later policy, label, or universe
change from being compared as though only the model changed.

### What this project cannot claim

- The replay cannot establish entry edge. It is conditional on the opportunities
  Run A' generated.
- The current validation sample cannot confirm a replacement exit policy.
- Better exit capture does not demonstrate benchmark-beating portfolio results.
- Attractive development-period results do not override insufficient trigger
  support.
