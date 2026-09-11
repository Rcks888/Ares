# Ares — Future Plans & Ideas

## Capital Planning

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

## Slot Management (When All Positions Full)

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
2. ⏳ Collect 40-60 trades baseline (currently 4/40)
3. ⏳ Establish win rate / PF without Claude (control group)
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

## Notes
- Paper trade minimum 3 months before going live
- Need 50+ closed trades for statistically meaningful data
- Shadow tracking data is critical for TP optimization
- Start real money only when profit factor > 1.3 consistently
- ML model only useful with sufficient data — never trust models trained on <50 trades
- Backtest results ≠ live results (slippage, timing, emotions) — use as guidance only
