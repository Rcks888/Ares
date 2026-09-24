# ARES — System Overview

*Generated 2026-09-24. Reflects V3.1 as deployed, after the Sep 22 audit sequence.*

---

## 1. Daily pipeline — screen to fill

```mermaid
flowchart TD
  CRON["cron on VPS<br/>run_ares.sh"] --> FINVIZ["Finviz screens<br/>5 screens"]
  FINVIZ --> UNIV["~103 symbols<br/>dynamic, not fixed"]
  UNIV --> OHLCV["yfinance OHLCV<br/>cached 379 symbols"]
  OHLCV --> IND["indicators.py<br/>rsi, macd, sma_50<br/>stdev_20, 52w high"]

  IND --> DIV["divergence columns<br/>bullish / bearish<br/>hidden bull / bear"]
  DIV -.->|"always False<br/>DEFECT 2"| DEAD(["never fires<br/>load\-bearing"])

  IND --> REGIME{"detect_market_regime<br/>causal"}
  REGIME -->|uptrend| MOM["momentum_breakout<br/>52w high gate<br/>vol, macd"]
  REGIME -->|range| MR["mean_reversion<br/>oversold, vol<br/>sma support"]
  REGIME -->|downtrend| NONE(["no signal"])
  REGIME -.->|disabled| TC(["trend_continuation<br/>off in V3"])

  MOM --> CONF3["confluence<br/>hardcoded 3"]
  MR --> CONFR["confluence<br/>computed 2\-3"]

  CONF3 --> SLOT{"slot free?<br/>max 5"}
  CONFR --> SLOT
  SLOT -->|yes| PEND["pending_signals<br/>next\-bar fill"]
  SLOT -->|no| QUEUE["signal_queue<br/>max 10, age 5d"]

  QUEUE --> VAL{"promote?<br/>drift <= 5%"}
  VAL -.->|"RSI + EMA_20 gates<br/>DEFECT 1: inert"| INERT(["never reject"])
  VAL -->|pass| PEND
  PEND --> FILL["open_trade<br/>stake fixed \$149"]
  FILL -.->|"no balance check<br/>DEFECT 3"| UNFUND(["unfundable as<br/>equity declines"])

  classDef defect fill:#3a1f1f,stroke:#c0504d,stroke-width:2px,color:#f0d0d0
  classDef off fill:#2a2a2a,stroke:#666,stroke-width:1px,color:#999
  class DEAD,INERT,UNFUND defect
  class NONE,TC off
```

---

## 2. Position lifecycle — fill to measurement

```mermaid
flowchart TD
  OPEN["open position"] --> ENTRYBAR{"today == entry_date?"}
  ENTRYBAR -->|yes| SKIP(["all exits suppressed<br/>on entry bar"])
  ENTRYBAR -->|no| PEAK["update peak_price<br/>seeds AT entry"]

  PEAK --> TRAIL["trailing_stop<br/>= peak x 0.90"]
  TRAIL --> EFF["effective_stop<br/>= max(stop_loss, trail)"]

  EFF --> TP{"price >= TP?"}
  TP -->|yes| SCALE["scale out 50%<br/>book locked gain"]
  SCALE --> CONT(["continue<br/>rest rides"])
  TP -->|no| STOP{"price <= eff stop?"}

  STOP -->|yes| CLOSE["_close_trade<br/>fill at eff stop"]
  STOP -->|no| RSI{"rsi > 90?"}
  RSI -->|yes| CLOSE
  RSI -->|no| MRX{"mean_rev<br/>and rsi > 70?"}
  MRX -->|yes| CLOSE
  MRX -->|no| HOLD(["hold"])

  CLOSE --> PM["_build_post_mortem<br/>verdict, mfe_pct<br/>locked_pct, gave_back_pct"]
  PM --> PHASE{"sample.classify<br/>entry >= 2026\-09\-19?"}
  PHASE -->|yes| CLEAN["clean_v3<br/>OFFICIAL"]
  PHASE -->|no| PRE["pre_clean<br/>excluded"]

  CLEAN --> STORE["virtual_trades.json"]
  PRE --> STORE
  STORE --> GIT["git add/commit/push<br/>Telegram alert on fail"]
  GIT --> DASH["build_dashboard<br/>Telegram"]

  classDef official fill:#1f3a2a,stroke:#4ca050,stroke-width:2px,color:#d0f0d8
  classDef off fill:#2a2a2a,stroke:#666,stroke-width:1px,color:#999
  class CLEAN,STORE official
  class SKIP,CONT,HOLD off
```

**The trail dead band.** Because `peak` seeds at entry and the trail sits 10% below it,
a trailing exit cannot clear breakeven until the trade has gained more than **\+11.1%**:

| Peak vs entry | Label | Outcome |
|---|---|---|
| \< \+6.7% | `stop_loss` | loss — trail still under the initial stop |
| **\+6.7% to \+11.1%** | **`trailing_stop`** | **still a loss** — ECO, peak \+8.97% |
| \> \+11.1% | `trailing_stop` | first point profit can lock — HAFN, peak \+15.53% |

---

## 3. The evidence layer — what makes a number trustworthy

```mermaid
flowchart LR
  subgraph LIVE["ARES — live, frozen"]
    SIG["signals.py"]
    TRK["tracker.py"]
    SMP["sample.py<br/>CLEAN_FROM"]
  end

  subgraph SIM["ATHENA V6 — measurement"]
    ADPT["parity adapter<br/>md5 assertion"]
    RUNA["Run A prime<br/>like\-for\-like"]
    RUNB["Run B<br/>divergence repaired"]
  end

  subgraph GATE["Governance"]
    POL["change policy<br/>fix\-now vs defer\-V4"]
    PREREG["pre\-registration<br/>economic alpha gate"]
    BAR["live capital bar<br/>4 conditions"]
  end

  SIG -->|"imported, not copied"| ADPT
  ADPT --> RUNA
  ADPT --> RUNB
  SIG -.->|"drift breaks<br/>the run"| ADPT

  RUNA -->|"\-5.58% vs SPY \+85.66%"| PREREG
  RUNB -->|"alpha \-16.06%<br/>FAIL"| PREREG
  PREREG --> VERDICT{"edge<br/>established?"}
  VERDICT -->|no| BAR
  BAR -->|"not cleared"| PAPER(["paper only<br/>index core"])

  TRK --> SMP
  SMP -->|"clean_v3<br/>0 closed, 3 in flight"| OBS["process observation<br/>NOT edge proof"]
  POL --> TRK
  POL --> SMP

  classDef live fill:#1f2f3a,stroke:#4a90c0,stroke-width:2px,color:#d0e8f0
  classDef gov fill:#3a2f1f,stroke:#c0a050,stroke-width:2px,color:#f0e8d0
  class SIG,TRK,SMP live
  class POL,PREREG,BAR gov
```

---

## 4. Where it stands

| Layer | State |
|---|---|
| **Strategy** | Frozen at V3.1. Parameters byte\-identical to V3.0 |
| **Research question** | **Closed.** Momentum works as a factor; this implementation subtracts from it |
| `clean_v3` | **0 closed, 3 in flight** (TMO, WBD, NEOG). Demoted to process observation |
| **Athena V1\-V5** | Retracted — look\-ahead bias, ~96% of profit came from future\-dated exits |
| **Athena V6** | Trustworthy. Imports live code under checksum assertion |
| **Live capital** | Behind a written 4\-condition bar. June 2027 is a review gate, not a deploy date |

### Defect ledger

| # | Defect | Status |
|---|---|---|
| 1 | Queue gates read `RSI` / `EMA_20`; indicators emit `rsi`, no `EMA_20` | Documented, **unrepaired** |
| 2 | Divergence columns permanently `False` | Documented, **unrepaired** — Run B proved repair makes it worse |
| 3 | Constant sizing, no balance check | **To fix** — only one with a real deadline |
| 4 | Post\-mortem read `rsi_extreme`, producer emits `emotional_extreme` | **Fixed** Sep 24 |

Four instances of one class: **a string written in one place and read in another, with
nothing asserting they match.**

### What has never happened

| | |
|---|---|
| A `clean_v3` trade closing | 0 of 40 target |
| `momentum_breakout` reaching TP | 0 of 2. HAFN's \+15.53% is the closest to \+18% |
| An exit other than stop, trail, or scale\-out | 0 |
