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
*"abandon the family."* Abandonment becomes rational only if Run A and the clean
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

**Run A — "as-live"** is mandatory and comes first: divergence removed from the
decision set entirely, matching what Ares structurally does today. This is the
honest benchmark for the system currently collecting.

**Run B — "divergence repaired"**, flags confirmed at `i+5` and firing five bars
late, is optional and secondary. It answers a V4 question — repair the detector or
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
- Need 50+ closed trades for statistically meaningful data
- Shadow tracking data is critical for TP optimization
- Start real money only when profit factor > 1.3 consistently
- ML model only useful with sufficient data — never trust models trained on <50 trades
- Backtest results ≠ live results (slippage, timing, emotions) — use as guidance only

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

