# Phase 0.5 — Inline Decision-Input Capture

**Amendment to `TRACKER_MIGRATION_PLAN.md`. Status: PROPOSED, awaiting review.**
**No code has been changed. `tracker.py` is untouched. `ARES_PARITY` remains unset.**

Corrected dependency order:

```
Phase 0 -> Phase 1 -> Phase 3 -> Phase 0.5 -> Phase 4 -> Phase 2 -> Phase 5
```

---

## 1. Why this amendment exists

Phase 4 was deployed disabled at `3267900`. Before enabling it, a
production-time measurement invalidated the premise it rested on.

| | |
|---|---|
| Claimed premise | no IB Gateway, so `get_live_price` returns `None` and the daily Close is always used |
| Measured 07:22 UTC | port 4002 refused — **consistent with the premise** |
| Measured from `crontab -l` | `restart_gateway.sh` runs 13:00, 16:00, 17:25 on weekdays; `run_ares.sh` runs **13:30 and 21:00** |
| Measured from `/tmp/ares_output.txt` | the last real cycle reported `(live)` for **all five** open positions |

13:30 UTC is 09:30 ET, US market open, thirty minutes after a deliberate
gateway restart. The 07:22 measurement was real but taken ~3:20am ET, hours
before any gateway exists. **The premise held only at the moment it was
measured, not during the cycles that make decisions.**

Consequence at `engine/tracker.py:819-821`:

```python
live = get_live_price(trade['symbol'])
daily_price = float(latest['Close'])
current_price = live if live else daily_price
```

Production exit decisions are driven by a **live IBKR 1-min bar close**. That
value is not reproducible after the fact, so Phase 4 as built cannot compare
against it.

### Why the existing stdout cannot substitute

`print_scorecard` emits the `(live)` line at `tracker.py:942`, but it makes its
**own independent** `get_live_price` call at line 931 — a *second* market
observation taken after the decision. Parsing it would compare the canonical
module against a price the inline path never evaluated. This route looks
plausible and is wrong; it is recorded here so it is not rediscovered and
adopted later.

`price_source` is already computed at `tracker.py:822` and **never used** — a
dead assignment. There is no other externally observable trace of the decision
price. **No observability path exists without modifying `tracker.py`.**

### What worked

The per-cycle premise gate refused rather than trusted the documented
assumption. Had it trusted it, parity would have compared a live 1-min close
against a re-derived daily Close and reported decision-changing mismatches
caused by **market movement**, which would have been investigated as migration
defects. The gate converted a silent false-evidence failure into a loud refusal.

---

## 2. Authorized scope

Phase 0.5 is authorized **solely** to expose the exact inline decision-input
packet. It is not authorized to change any decision, persistence, network
behaviour, or production failure handling.

### 2.1 Tracker additions — the complete permitted diff

Two additions only.

```python
# Module level. Migration telemetry only: written by check_open_trades, read by
# the parity bridge, never read back by any decision path. Not persisted.
_LAST_EVAL = {}
```

and, inside `check_open_trades`, immediately after `today` is resolved at
line 824 and **before** the entry-day check at line 825:

```python
            _LAST_EVAL[trade['symbol']] = {
                "cycle_token": _EVAL_CYCLE_TOKEN,
                "price": current_price,
                "price_source": price_source,
                "rsi": current_rsi,
                "bearish_div": bool(latest.get('bearish_div', False)),
                "bar_date": today,
            }
```

### 2.2 Why exactly these fields

Enumerated from the inline exit chain (`tracker.py:846-892`), not guessed. These
are the **only** bar-derived values the decision consumes:

| field | consumed at | role |
|---|---|---|
| `current_price` | 846, 852, 875, 881-891 | peak ratchet, scale-out trigger, stop compare, exit fill basis |
| `price_source` | — | provenance; distinguishes live from daily path |
| `current_rsi` | 881, 890 | `emotional_extreme`, `mean_reversion_complete` |
| `bearish_div` | 885 | `bearish_divergence` |
| `today` | 825, 878, 882, 887, 891 | entry-day suppression, exit/scale-out date |

Everything else the canonical module needs — `strategy`, `stop_loss`,
`trailing_stop`, `peak_price`, `shares`, `original_shares`, `take_profit`,
`scaled_out` — is **persistent trade state** already supplied by the pre-inline
clone. It must **not** be duplicated into the packet. This is a decision-input
packet, not a tracker-state dump.

### 2.3 Two placement details that matter

**Instrument before the entry-day skip, not after.** `tracker.py:825` does
`if today == trade['entry_date']: continue`. Capturing after it would leave
entry-day symbols with no packet, indistinguishable from a capture failure.
Capturing before it, and including `bar_date`, lets parity *derive*
`skipped_entry_day` from the packet. Absence of a packet then means exactly one
thing: the inline path failed before reaching the instrumentation point.

**`bearish_div` must be read eagerly.** At line 885 it sits in an `elif` chain,
so it is never evaluated when an earlier branch fires. The packet reads it
unconditionally. This is a pure read of an already-loaded DataFrame row —
no network, no I/O, no mutation, no decision change — but it is a read the
inline path sometimes skips, and that difference is recorded here deliberately.

### 2.4 Cycle identity and the stale-record hazard

A module-level dict persists across cycles. Without protection:

```
cycle N   writes a packet for SDGR
cycle N+1 fails on SDGR before instrumentation
          -> stale cycle-N packet is still present
          -> parity compares against LAST cycle's inputs and may report MATCH
```

That would be false evidence of the most persuasive kind. Required contract:

- A `_EVAL_CYCLE_TOKEN` is set once per `check_open_trades` invocation, and
  `_LAST_EVAL` is cleared at the same point.
- Every packet carries that token.
- The parity bridge reads the token **before** the inline call and accepts only
  packets bearing it.
- A symbol with no packet, or a packet with a non-matching token, classifies
  `INLINE_INPUT_NOT_CAPTURED` and **never** falls back to an older value.

Clearing is unconditional, not gated on `ARES_PARITY`. A store that is cleared
only when parity is enabled would behave differently in the two modes, which
defeats the purpose of disabled-mode equivalence.

`INLINE_INPUT_NOT_CAPTURED` naturally absorbs: inline data-load failure,
`add_indicators` failure, exceptions before instrumentation, symbols not
reached, and partial cycle failure — all of which must block comparison rather
than silently produce agreement.

### 2.5 Write-only from the tracker's perspective

Dependency direction is one-way and must be enforced structurally:

```
inline tracker resolves genuine decision inputs
        v
tracker COPIES them into _LAST_EVAL
        v
parity bridge reads them after the inline call
```

The tracker must never read `_LAST_EVAL` back. AST-based invariants (text
scanning has produced five false results in this project and is not acceptable
for structural questions):

1. `_LAST_EVAL` appears in `tracker.py` as an assignment target and a `Subscript`
   store only — never as a load feeding a comparison, branch, or call argument.
2. Exactly one write site, at the registered instrumentation point.
3. No `save_*`, `json.dump`, or file write receives `_LAST_EVAL`.
4. Only `engine/parity_hook.py` and `engine/parity_eval.py` read it.
5. No report, scorecard, dashboard, or analytics module references it.

---

## 3. Explicit exclusions

Phase 0.5 must not change: entry or exit decisions · stop or take-profit
calculation · rounding or the `tracker_v3_2dp` contract · commission behaviour ·
position persistence · market-data retrieval · **number of network calls** ·
exception handling · alerting · cache refresh · order processing · queue
behaviour · `psi_state.json` · trade logs · Phase 5 authority.

Deferred Phase 0.5 items from earlier analysis — structured evaluation status to
replace stdout marker parsing, blanket-exception observability, and the measured
`<34 bars` `add_indicators` failure — are **explicitly out of scope of this
amendment** and remain separately authorizable. This amendment is capture only.

---

## 4. Parity-side consequences: removal, not accumulation

Once parity consumes the exact inline packet, the reconstruction machinery built
for Phase 4 becomes not merely unnecessary but **misleading about which price is
authoritative**. It must be removed, not bypassed or left dormant.

| remove | reason |
|---|---|
| `_bar_fn` cache read and `make_bar` reconstruction | the packet supplies the values |
| `_bar_recheck`, `cache_mtime`, `BarStale` | nothing is reconstructed, so nothing can go stale |
| `_premise`, the four `PREMISE_*` states, `data_feed._ib_connection` | provenance now comes from `price_source` in the packet |
| `PRICE_SOURCE_NONDETERMINISTIC` as a refusal | live prices become comparable, not refused |
| `NonDeterministicPrice` refusal in `parity_eval` | superseded |

This is a net architectural simplification and reduces private dependencies from
two to one:

```
keep    tracker._load_params      -- same effective policy config
keep    tracker._LAST_EVAL        -- registered migration contract (new)
remove  data_feed._ib_connection  -- no remaining purpose
```

`price_source` must still be **recorded** on every parity record so daily-path
and live-path coverage can be counted separately. It stops being a refusal
reason and becomes a coverage dimension.

---

## 5. Rebaseline requirements

`tracker.py` changes, so every source-lineage contract must be updated
deliberately, in this order:

1. Record the outgoing hash: **`be7b60383dba5d718821e8ef52209953`**.
2. Apply the instrumentation-only change.
3. Record the new hash and update `tools/parity_baseline.json` via
   `--update-baseline`; review the diff.
4. Re-verify the marker contract against the new source.
5. Preserve rollback tag **`pre-tracker-swap` -> `d6cbd55`** unchanged, and
   confirm it still restores the original inline tracker.
6. Confirm `config/strategy_params.json` is byte-identical.
7. Confirm `logs/virtual_trades.json` (`e1ae6db0…`) and `logs/psi_state.json`
   (`de708e75…`) are unchanged by implementation.

### Required wording change

The gate `tracker_unchanged_since_tag` becomes false and must **not** be
restated as true. Replace it with:

> Tracker decision behaviour remains unchanged; tracker source differs from the
> rollback tag only through the registered Phase 0.5 decision-input
> instrumentation.

The gate must be **renamed and re-implemented** to assert that the *only* diff
against the tag is the registered instrumentation — not deleted, and not
softened into a passing boolean.

---

## 6. Validation gates before Phase 4 may be re-authorized

### 6.1 Daily-path regression — proves telemetry changed nothing

Re-run the original Phase 0 harness against the **modified** tracker:

- 20 boundary fixtures + 250 replayable historical paths = **4,730 bars**
- required: **0** decision-changing mismatches
- required: the 8,616 stored-value differences remain fully explained by
  `tracker_v3_2dp`, with no new unexplained difference
- the 43 `UNTESTED_INSUFFICIENT_WARMUP` entries remain reported as untested

### 6.2 Policy A parity

Required: **293 / 293**, with no change to dates, exit reasons, scale-outs,
shares, commissions, P&L, or holding periods.

### 6.3 Live-path equivalence — new coverage

Inject deterministic live prices through the same tracker call sequence:

```
tracker receives live price X
        v
_LAST_EVAL captures exactly X          (identity, not approximation)
        v
module_eval receives exactly X
        v
no second market-data request occurs
        v
decision and tracker-compatible state agree
```

Boundary fixtures required, each at one cent above / exactly at / one cent below:

- `effective_stop`
- the scale-out threshold
- `stop_loss == trailing_stop` (**ABM**, equality boundary -> must label
  `stop_loss`)
- inside SDGR's giveback dead band (-> must label `trailing_stop`)

Note `get_live_price` returns `round(float(bars[-1].close), 2)`, so a captured
live price is already 2dp and the comparison is exact identity. Daily prices are
full-precision `float(latest['Close'])`; the packet must carry the **unrounded**
value, since the inline decision uses the unrounded local.

### 6.4 Capture-integrity tests

| case | required classification |
|---|---|
| current-cycle packet present | evaluate normally |
| packet from a previous cycle | `INLINE_INPUT_NOT_CAPTURED` |
| symbol absent from the store | `INLINE_INPUT_NOT_CAPTURED` |
| inline failure before instrumentation | inline failure; module **not** evaluated |
| entry-day skip | `skipped_entry_day`, derived from `bar_date` |
| multiple symbols | no cross-contamination between packets |

### 6.5 Zero new network calls — structural and dynamic

The parity path must never call `get_live_price`, `_get_ib`, `load_stock`,
`download_stock`, or reach `yfinance`. Verified by AST **and** by a runtime spy
that fails if any is invoked.

---

## 7. Corrections to the record

**SDGR fixture.** Real values are `stop_loss = 25.06`, `trailing_stop = 28.23`,
`entry_price = 29.35`. Fixtures currently use `27.60`, a **recording error on my
part, not a strategy-state change**. The structural result is unaffected —
`max(25.06, 28.23) = 28.23`, attribution `trailing_stop` — but SDGR is required
coverage and its fixture must match the live position.

**Phase 0 scope.** Every statement implying Phase 0 proved general tracker
equivalence must be replaced with:

> Phase 0 demonstrated no decision-changing differences on the **daily-price
> path** within the tested population. It did **not** test the production IBKR
> live-price path. Phase 0.5 adds exact inline decision-input capture so Phase 4
> can evaluate that path without a second market observation.

To be applied in: the migration plan, Phase 0 status, Phase 4 prerequisites, the
final parity report template, and any executive summary.

---

## 8. Decision

- Phase 4 deployment is **paused**. The staged bridge stays deployed and
  disabled at `3611afc`.
- **`ARES_PARITY` remains unset.** Under the current gateway schedule it would
  correctly produce refusals and zero usable coverage.
- Phase 0.5 is authorized solely to expose the exact inline decision-input
  packet, without changing decisions, persistence, network behaviour, or
  production failure handling.
- After Phase 0.5 passes the gates in section 6, Phase 4 may be re-authorized
  for **both** the daily and IBKR live-price paths.
- Phase 5 remains prohibited. ABM and SDGR continue to exit naturally under
  inline Policy A; no administrative closure.

This is not a setback. The deployed premise gate prevented Phase 4 from
collecting persuasive but invalid evidence, and Phase 0.5 yields a strictly
better input than reconstruction ever could: the exact values the authoritative
inline tracker used.
