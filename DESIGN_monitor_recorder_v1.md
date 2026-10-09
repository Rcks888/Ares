# Design — Monitor Decision-Path Recorder v1

**Status:** DRAFT — design only. No runtime file is modified by this document.
**Date:** 2026-10-07
**Predecessor:** `INCIDENT_2026-10-07_monitor_decision_path.md`
**Authorises:** nothing. Implementation and deployment require separate approval
after the 2026-10-07 13:30 UTC cycle completes and is preserved.

---

## 1. Purpose and strict scope

Make the monitor decision path **observable**, so that the evidence base covers
both production exit implementations rather than one.

### What this design does

- Records, at the decision site, the values the monitor actually used.
- Records monitor decisions and their booked outcomes.
- Registers every production decision path, its schedule and its coverage.
- Makes freshness path-aware, so a healthy report cycle cannot conceal a missing
  monitor cycle.

### What this design explicitly does NOT do

| Not in scope | Why |
|---|---|
| Comparator, `difference_class`, `MATCH`/`MISMATCH` labelling | There is no agreed reference policy to compare against. The two implementations differ by design-accident, and labelling a difference "MISMATCH" would assert a correctness claim nobody has authorised. |
| Unifying the two exit policies | Separate preregistration. Observation must not become silent modification. |
| Changing monitor trading behaviour | Any behaviour change invalidates the incident-to-repair boundary. |
| Recreating the five lost exits | Impossible. They remain unobtainable and blocking. |
| Fixing fixed-notional sizing or reserve enforcement | Separate operational-risk decision. |
| Resolving fill realism | Needs the trigger price this recorder will begin capturing, plus a separate preregistration. |
| Authorising tonight's EROC/ITUB entries | **Documentation is not a safety control.** This design observes; it does not gate. |

Phase 5 and AI modelling remain prohibited throughout.

---

## 2. Five findings since the incident that change the design

These emerged while reading the call chain for this draft and were **not** in the
incident document. Two of them widen the scope of what is unobserved.

### 2.1 The monitor initiates admissions but cannot complete them

`monitor_trades.py:89` calls `promote_queue(source="monitor", use_live=True)`
whenever the loop set `updated`. That reaches `open_trade(signal,
from_queue=True)` with `price = checks['live']` (`tracker.py:589`).

**Correction to the first reading of this finding.** `open_trade` does **not**
open a position. It appends to `pending_signals.json` and returns `'pending'`
(`tracker.py:635-658`). No price is filled, no shares are sized, no stop is
computed. The fill happens in `execute_pending_signals()`, which is called from
**`daily_report.py:54` only** — the report path.

### 2.1.1 The three-stage entry pipeline — canonical description

This replaces every earlier description of the monitor as an entry-execution
path, in this document and in the incident record.

```
STAGE 1  candidate selection
         scan (daily_report, report path)  OR  promote_queue (monitor path)
                ↓
STAGE 2  open_trade  →  appends to pending_signals.json, returns 'pending'
         no price, no shares, no stop
                ↓
STAGE 3  execute_pending_signals  →  the FILL
         entry_price = today_open * (1 + slippage_pct)
         position_size = 149.00, shares, stop_loss, take_profit
         daily_report.py:54 ONLY — report path
```

So the correct characterisation is:

| Path | Stage 1 select | Stage 2 pending | Stage 3 fill |
|---|---|---|---|
| monitor | **yes**, unobserved | yes | **no** |
| report | yes | yes | **yes** |

The monitor is an **admission-initiating** path, not an admission-completing one.
That is narrower than "a full admission path" and the distinction matters: the
monitor decides *which* candidate becomes pending, using a live-price
re-validation (`_validate_queued(use_live=True)`) that no evidence records, but
it cannot set entry price, size or stop.

Consequence for the recorder: admission capture is still required, but its
subject is **candidate selection**, not entry. And `price_basis` on the monitor
path is the *validation* price, which never becomes the entry price.

**This settles the EROC/ITUB *fill* question in §7A, but not their origin.**
Stage 3 is report-path-only, so whichever path selected them, the fill must come
from `execute_pending_signals()` on a report cycle. Their **initiating source is
`unknown`** and no record field establishes it — see §3.5 on why `from_queue` is
not a proxy.

### 2.2 `get_live_price` cannot fail visibly

`data_feed.py:61-62` catches **every** exception and returns `None`. A dead
gateway, an unqualified contract, a timeout and a genuine absence of bars are
all indistinguishable at the call site.

Consequence for the monitor: `if not live:` at line 45 silently skips **all stop
evaluation** for that position, and nothing anywhere records *why*. A gateway
outage during a monitor window produces a cycle that looks normal in the
operator output and leaves no trace.

The recorder therefore cannot infer the reason, and **must not pretend to**. It
records `live_price_absent: true` with `absence_reason: "indistinguishable"`.
Making the reason distinguishable requires editing `data_feed.py`, which is a
decision-path change and is deferred to its own approval.

### 2.3 The monitor loop has no per-trade exception guard

`tracker.check_open_trades` wraps each position in `try/except` and continues.
The monitor does not. An exception on position 2 of 5 aborts the loop, leaving
positions 3–5 **unevaluated for that cycle**, with `save_trades` never reached —
so the ratchet updates for positions already processed are also lost.

This makes `attempted` versus `written` genuinely load-bearing on this path, and
requires a `cycle_aborted` field with the index reached. It is also a real
robustness divergence; it is recorded, not repaired here.

### 2.4 `useRTH=False` — the 21:00 report price is an after-hours print

`data_feed.py:56` requests bars with `useRTH=False`. The 21:00 UTC report cycle
runs **after** the 20:00 UTC US close, so `live` at that cycle is an
extended-hours bar, while the 13:30, 16:10 and 17:30 cycles are inside regular
hours.

Not a defect, and not this design's problem to solve — but `price_source: "IBKR"`
is currently too coarse to distinguish them, and any later fill-realism work will
need that distinction.

**Corrected after review.** An earlier draft proposed deriving `session_context`
from the cycle's scheduled UTC time. That is wrong: `useRTH=False` *permits*
extended-hours bars, it does not establish the session of any particular returned
bar, and a cron slot is not evidence about a price. Deriving session from the
schedule would manufacture provenance.

`get_live_price` returns `round(float(bars[-1].close), 2)` and discards
`bars[-1].date`, so the actual bar timestamp is **not available** at the decision
site today. Therefore:

```json
"price_timestamp": null,
"price_timestamp_available": false,
"session_context": "unknown",
"session_basis": "bar_timestamp_unavailable"
```

`session_context` is recorded as `unknown`, never inferred. Preserving the real
bar timestamp requires returning it from `data_feed.get_live_price`, which is a
decision-path file and therefore its own narrowly registered diff — not something
the exit recorder may absorb. Evaluation time and source are recorded separately
and are not substitutes for it.

### 2.5 Resolved: why WBD did not re-enter

Logged here because the incident left it open and explicitly unassumed.
`promote_queue` drops already-held symbols at `tracker.py:562-564`:

```
if symbol in held:   # open ∪ pending
    print(f"    - {symbol}: already held or pending — removed from queue")
```

WBD was screened, ranked, then dropped at promotion time with a logged
`dropped` queue event. The guard worked. **Closes open question 6.**

A side effect worth noting: there is already a queue-event log
(`_log_queue_event`) carrying admission decisions. It is partial evidence for
entries that predates this design, and it is a preservation target tonight.

---

## 3. Contract 1 — exact decision-site capture

### 3.1 Why a module-boundary wrapper cannot work

The two most diagnostic values in the monitor exist only inside the loop body and
are destroyed before it ends:

| Value | Where | Why unrecoverable afterwards |
|---|---|---|
| `live` | `monitor_trades.py:38` | Held for the comparison at line 68, discarded at line 70. `_close_trade` books `effective_stop`, never the observed price. Re-reading it later yields a *different* price. |
| `effective_stop` (unrounded) | line 65 | `max(stop_loss, trailing_stop)` where `trailing_stop` may be the **unrounded** ratchet from line 61, while line 62 persists only `round(x, 2)`. A ratchet to `188.104` books an exit at that value and stores `188.10`. No function of stored state recovers it. |

This is the same class of problem Phase 0.5 solved for the report path, and it
has the same answer: capture must happen **at the decision site**, not at a
boundary.

### 3.2 Mechanism: reuse the proven `_LAST_EVAL` pattern

`tracker.check_open_trades` already carries an in-module telemetry store
(`_LAST_EVAL`) that is written unconditionally with plain dict assignments and
read only by the parity bridge. Phase 0.5 tests already cover its
enabled/disabled equivalence. **Reuse it rather than invent a second pattern.**

Monitor-side design:

```python
# monitor_trades.py — module level
_MON_EVAL = {"packets": {}, "results": {}, "cycle_token": 0}
_MON_CYCLE_SEQ = 0
```

Inside `monitor()`, before the loop: increment the sequence, **clear the store,
then publish the token** — in that order, for the reason already documented in
`tracker.py`: publishing first leaves a new token beside prior-cycle packets,
which a reader validating `packet["cycle_token"] == store["cycle_token"]` would
wrongly accept as current evidence. Clearing first leaves an old token with an
empty store, which cannot be accepted.

Inside the loop, the only added code is **dict assignment of already-computed
values**. No imports, no I/O, no function calls that can fail, no price
requests. Serialisation happens once, after the loop, outside the decision path.

**Unconditional, not env-gated.** The store must behave identically whether or
not observation is enabled, or disabled-mode equivalence stops covering this code
path — the exact reasoning `tracker.py` records for `_EVAL_CYCLE_SEQ`.

### 3.3 Capture points

Five points per position. Points A–C are plain assignments; D reads back
post-`_close_trade` state; E is the skip path.

| Point | Location | Captures |
|---|---|---|
| **A** pre-state | loop top, before the ratchet block mutates `trade` | `state_before`: entry_price, shares, original_shares, stop_loss, trailing_stop, peak_price, take_profit, scaled_out, entry_date, strategy |
| **B** inputs | after `live` resolves, **before** the `if not live` branch | `live_price`, `live_price_absent`, `price_source`, `trailing_pct`, `rsi_extreme`, `cycle_utc`, `session_context` |
| **C** derived | after line 65 | `peak_after`, `trailing_stop_unrounded`, `trailing_stop_persisted`, `effective_stop_unrounded`, `ratcheted` (bool), `trigger_margin` = `live − effective_stop` |
| **D** decision | after the branch resolves | `decision`, `exit_reason`, `exit_price_requested`, and post-close booked values: `exit_price`, `exit_slippage`, `pnl`, `pnl_pct`, `pnl_after_costs`, `total_commission`, `exit_date`, `exit_date_basis` |
| **E** skip | the `if not live: continue` path | `decision: "skipped_no_live_price"`, `evaluation_performed: false`, `absence_reason: "indistinguishable"` |

Point B sits **before** the absence branch deliberately: an absent packet then
means the loop failed before reaching the position at all, which is a different
fact from a position that was reached and could not be evaluated. Same
distinction the report path draws, and the reason the report path can tell
`not_computed_entry_day_skip` from a missing record.

### 3.4 Decision vocabulary — deliberately NOT the report path's

| Monitor value | Meaning |
|---|---|
| `hold` | evaluated, no threshold crossed |
| `state_update` | ratchet moved, position retained |
| `close:stop_loss` | `live <= effective_stop`, trail not above initial stop |
| `close:trailing_stop` | `live <= effective_stop`, trail above initial stop |
| `close:take_profit` | `live >= take_profit` — **sells the full position**, no scale-out |
| `skipped_no_live_price` | reached, not evaluated |
| `not_reached` | loop aborted before this position (see §3.5) |

There is **no `skipped_entry_day`** value, because the monitor has no entry-day
skip. A day-0 position is evaluated for exit on the monitor path and skipped on
the report path. The vocabulary makes that visible rather than smoothing it over.

`close:take_profit` is flagged in-schema with `full_exit_on_tp: true` so the
scale-out divergence is queryable, not buried in prose.

### 3.5 Admission capture — scoped out of this recorder

**Revised after review.** Admission decisions are made inside `promote_queue`,
`_validate_queued` and `open_trade` — all in `engine/tracker.py`. They are **not
visible in the monitor loop's locals**. Capturing them from the exit recorder
would require reaching into tracker primitives, and that must not be implied by
or smuggled into an exit-capture design.

Therefore admission capture is **split out** into its own narrowly registered
diff, reviewed separately. What follows is the requirement it must satisfy, not a
change this document authorises.

#### Required admission chain

```
candidate → eligibility result → price used → quantity
          → accepted / rejected / dropped → persistence outcome
```

#### What existing queue events already cover

`_log_queue_event` (`tracker.py:270-291`) is append-only and already records
`timestamp`, `symbol`, `action` (`queued|kept|dropped|promoted|expired|evicted|
fill_dropped|fill_retry`), `queued_at`, `confluence`, `signal_price`,
`drift_pct`, `rsi`, `ema20_ok`, `live_price`, `drop_reason`.

That covers **candidate, eligibility result, and price used**. Reuse it; do not
duplicate it.

#### What it does not cover

| Missing | Why it matters |
|---|---|
| `source` (`scan` vs `monitor`) | `promote_queue` takes `source=` and prints it, but never records it. **The one field that would distinguish a monitor-initiated admission from a scan-initiated one is printed to stdout and lost.** |
| `use_live` | Whether the validation price was intraday IBKR or a daily close |
| `open_trade` return value | `duplicate` / `queued` / `pending` — the persistence outcome |
| quantity | Not knowable at this stage; set at fill, in `execute_pending_signals` |
| lifecycle linkage | No key ties a queue event to the trade it eventually became |

`source` is the highest-value, lowest-risk addition in the entire repair: one
field, already in scope as a parameter, currently discarded.

#### Quantity belongs to the fill, not the admission

`open_trade` computes no quantity. Sizing happens at
`tracker.py:132-135`. So "quantity" in the required chain is a **report-path
fill** property, and admission evidence must link to it rather than claim it.

#### Minimum additional fields, for the separate diff to justify

`source`, `use_live`, `open_trade_result`, `free_slots_before`,
`fill_window_ok`, `fill_window_reason`, and a lifecycle linkage key. Each must be
argued for in that diff on its own merits.

Admission evidence stays in `logs/queue_events.jsonl` — the existing stream —
rather than a new file, so one admission does not produce two partial records in
two places.

> **Corrected 2026-10-09 by observation.** This is **wrong as stated**, and the
> 2026-10-07 reconciliation proved it. `grep -E 'EROC|ITUB' logs/queue_events.jsonl`
> returned **nothing**: both admissions were `from_queue: False`, never entered
> the queue, and produced **no queue event at all**.
>
> `queue_events.jsonl` covers **queue-routed admissions only**. For a direct scan
> admission it is not merely insufficient — it is empty. A design that reuses it
> as the sole admission stream would have recorded nothing for the only two
> admissions observed in this period, while reporting full coverage.
>
> The separately registered admission diff must therefore cover **both routes**,
> and the coverage contract must treat an absent direct-admission record as a
> **gap**, not as an absence of admissions. See
> `RECONCILIATION_2026-10-07_fill_cycle.md` §6.

#### `source` is one field but still a record-contract change

**Revised after review.** "One field, already a parameter, currently discarded"
understated it. `queue_events.jsonl` is an append-only evidence stream with
existing consumers, and adding a key changes the contract for every reader. It
must be registered before it is added, not alongside it:

| Contract element | Specification |
|---|---|
| Destination | `logs/queue_events.jsonl`, existing stream, append-only |
| Field name | `initiating_source` — **not** `source`, which is already an overloaded word in this codebase |
| Allowed values | `scan`, `monitor`, `unknown` — closed set; an unrecognised value is a validation failure, not a pass-through |
| Schema version | `queue_event_schema_version: 2`; absence of the key means version 1 |
| Historical behaviour | Records written before the change have **no** key. Readers must treat absent as `unknown`, never as `scan`. |
| Backfill | **None.** No historical record is edited. |
| Validation | A version-2 record missing `initiating_source` is invalid; a version-1 record missing it is valid. Version-aware, as with the parity record schema. |

#### `from_queue` is not a proxy for the initiating process

An earlier draft leaned on `from_queue: false` to conclude EROC and ITUB were
scan-initiated. That inference is **too strong and is withdrawn.**

`from_queue` records whether `open_trade` was reached via `promote_queue` (queue
origin) or called directly. It distinguishes **queue-originated from
non-queue-originated**, which is not the same as identifying the initiating
process:

- `promote_queue` is called from **both** `daily_report.py:64` (`source="scan"`)
  and `monitor_trades.py:89` (`source="monitor"`), and both produce
  `from_queue: true`.
- So `from_queue: true` is **ambiguous** between the two paths — precisely the
  distinction being sought.
- `from_queue: false` means the direct `daily_report.py:119` call, which is
  report-path **on current code**. That is a fact about today's call graph, not a
  property the record asserts, and it would silently stop being true if any other
  caller were added.

For EROC and ITUB the conclusion is therefore: `from_queue: false` is
**consistent with** scan initiation, and no record field establishes it.
Correct status is `initiating_source: unknown`, with the call-graph reading noted
as an inference. Every pre-change record is permanently `unknown`.

### 3.6 Lifecycle identity

Exit records key on lifecycle, not symbol, per the incident:

```
lifecycle_id = f"{symbol}@{entry_date}#{entry_price:.2f}"
```

Deterministic from existing fields and stable across cycles. **Known limitation:**
it cannot distinguish two entries in the same symbol on the same date at the same
price. A persistent `trade_id` minted at entry would be correct, but that writes
production state and is therefore out of scope here. The limitation is recorded
in-schema as `lifecycle_id_basis: "derived_v1"` so a later `trade_id` can
supersede it without rewriting history.

---

## 4. Contract 2 — observation isolation

### 4.1 The five invariants

A recorder failure must not change:

1. **Monitor decisions** — which positions close, at what price, for what reason.
2. **Production state** — `virtual_trades.json`, pending, queue, ranked, psi_state.
3. **Operator output** — stdout byte-identical, so the Telegram `grep` for
   `CLOSED` / `📊❌✅` and the `head -5` / `head -10` truncation behave identically.
4. **Exception propagation** — a genuine monitor exception must surface exactly
   as it does today, with the same traceback and the same partial-cycle effect.
5. **Market-data acquisition** — no additional `get_live_price` call, ever. A
   second request would both perturb IBKR pacing and record a price the decision
   did not use.

### 4.2 Structural guarantees

| Guarantee | How |
|---|---|
| Loop body cannot raise | Only dict assignment of already-bound locals. No calls, no I/O, no imports. |
| Serialisation cannot raise into the monitor | Single call site after the loop, fully wrapped in `try/except Exception`, returning a written/not-written flag. Never re-raises. |
| No stdout contamination | The recorder prints **nothing** on success. On failure it prints one line to **stderr**, which `tee` does not capture into `/tmp/ares_monitor.txt` and the Telegram `grep` never sees. |
| No import-time risk | Recorder imported lazily inside the post-loop `try`, not at module top. An unimportable recorder leaves the monitor fully functional. |
| Write atomicity | Append-only JSONL, one `write()` of a newline-terminated serialised line, same discipline as `parity_runner.append_record`. |
| Separate files | `logs/monitor_observation_v1.jsonl` and `logs/monitor_heartbeat_v1.jsonl`. Never appended to `tracker_parity_v1.jsonl` — different semantics, no comparator, and mixing would corrupt the meaning of the 64 existing records. Admission evidence is **not** written by this recorder; it stays in `logs/queue_events.jsonl` under the separate diff of §3.5. |

### 4.3 The early-return problem

`monitor_trades.py:22-25` returns before the loop when there are no open
positions:

```python
if not open_trades:
    print("  No open positions to monitor.")
    disconnect_ib()
    return
```

A recorder placed after the loop never runs on this path. The result would be a
cycle that **ran correctly and produced no heartbeat** — indistinguishable from a
cycle that never ran, which is precisely the failure mode path-aware freshness
exists to eliminate.

**Design:** emit the heartbeat from a `try/finally` wrapper around the `monitor()`
body, so it is written on the early-return path, the normal path, and the
exception path alike:

```python
if __name__ == "__main__":
    try:
        monitor()
    finally:
        _emit_observation()      # never raises
```

A zero-position cycle writes `attempted: 0`, `written: 0`,
`skip_reason: "no_open_positions"`. That is a healthy heartbeat, and it is
distinguishable from absence.

The `finally` placement also covers §2.3: an aborted loop still emits a heartbeat
carrying `cycle_aborted: true`, `positions_reached: N`, and the exception type —
turning a currently-silent partial cycle into recorded evidence.

#### 4.3.1 Zero open positions also means zero admissions attempted

Raised in review, and confirmed in source. The early return at line 22 precedes
the loop **and** the `if updated:` block at line 85, so
`promote_queue(source="monitor", use_live=True)` is never reached. Worse,
admission on this path is gated on `updated` at all — so a monitor cycle that
evaluates positions and changes nothing also attempts no admission.

| Monitor cycle | Exit evaluation | Admission evaluation |
|---|---|---|
| no open positions | not attempted (early return) | **not attempted** |
| open, nothing changed | attempted | **not attempted** (`updated` false) |
| open, ratchet or close | attempted | attempted |

A heartbeat reporting `no_open_positions` would therefore be truthful about exits
and **silent about admissions**, which is the same conflation at a smaller scale
as one heartbeat covering two paths. Note the first row's consequence: with the
portfolio empty, five slots free and candidates queued, the monitor path cannot
admit anything. That is a behaviour fact, recorded; **the recorder must not
change the early return**, and this design proposes no change to it.

Exit and admission evaluation are therefore accounted **separately**, each with
its own attempted / skipped / aborted state:

```json
"exit_evaluation":      {"attempted": 0, "written": 0, "skipped": 0,
                         "not_reached": 0, "aborted": false,
                         "skip_reason": "no_open_positions"},
"admission_evaluation": {"attempted": false,
                         "skip_reason": "early_return_no_open_positions",
                         "gated_on": "updated"}
```

`admission_evaluation.attempted` is a boolean, not a count, because at this layer
the monitor either reached `promote_queue` or did not. Candidate-level counts
belong to the separate admission diff in §3.5.

#### 4.3.2 `finally` must preserve the original failure

A `finally` block that raises **replaces** the in-flight exception, and one that
returns **discards** it. Either would convert a genuine monitor failure into a
recorder failure — destroying the evidence the recorder exists to produce, and
doing it precisely when it matters most.

Requirements:

- `_emit_observation()` is wholly wrapped in `try/except Exception` internally and
  **cannot raise**.
- The `finally` block contains **no** `return`, `break`, or `continue`.
- Recorder write failure is recorded in its own field (`write_failures`), not
  raised, and surfaces at evaluation time per §4.4.
- The original traceback must be unchanged, not chained or re-raised.

On an aborted cycle, classification must stay honest about what did and did not
persist:

- Positions processed before the abort: ratchet updates were **computed but not
  persisted**, because `save_trades` at line 86 is never reached. Recorded as
  `state_update_computed_not_persisted` — not as `state_update`.
- Positions after the abort point: `not_reached`. **Never `hold`.** A hold is a
  decision; not-reached is the absence of one.

This distinction is the whole point. An aborted cycle currently looks like a
quiet cycle.

### 4.4 Fail-closed vs fail-open, and where each applies

These pull in opposite directions and the split must be explicit:

- **Observation is fail-open.** A recorder fault must never stop the monitor from
  trading. Trading is production; observation is not.
- **Evidence interpretation is fail-closed.** A cycle with no heartbeat, a
  malformed record, or `attempted != written` must never contribute to a green
  readiness state. Missing evidence reads as *missing*, never as *fine*.

The failure is therefore absorbed at write time and surfaced at evaluation time.
This is the same split the report path already uses; stating it here prevents a
future reader from "fixing" the fail-open recorder into a fail-closed one that
can halt trading.

---

## 5. Negative controls

Each must fail if the guarantee it protects is broken. These are the acceptance
criteria for implementation; none require the VPS.

### Isolation

1. **Decision equivalence.** Same fixture, recorder enabled vs disabled →
   identical `virtual_trades.json` byte-for-byte.
2. **Stdout equivalence.** Same fixture, both modes → captured stdout identical
   byte-for-byte, including emoji and spacing.
3. **Telegram-extraction equivalence.** Run the launcher's actual `grep "CLOSED"
   | head -5` and `grep "📊\|❌\|✅" | head -10` over both outputs → identical.
4. **Recorder raises → monitor unaffected.** Monkeypatch the serialiser to raise;
   assert decisions, state and stdout unchanged, exit code unchanged.
5. **Recorder unimportable → monitor unaffected.** Make the import fail; same
   assertions.
6. **No extra price request.** Count `get_live_price` invocations in both modes →
   equal, and equal to the open-position count.
7. **No production write by the recorder.** Assert the recorder opens only its
   three log paths; assert `save_trades` call count is unchanged.
8. **Exception transparency.** Force an exception mid-loop; assert the same
   exception type propagates, the same positions remain unevaluated, and the
   partial-state outcome matches disabled mode.

### Capture correctness

9. **Unrounded effective stop is preserved.** Fixture ratcheting to `188.104`:
   assert `effective_stop_unrounded == 188.104` while persisted state holds
   `188.10`, and assert the two are recorded in different fields.
10. **Trigger price is preserved.** Assert `live_price` is recorded and differs
    from `exit_price_requested` whenever the position closes below the stop —
    the value destroyed at line 70 today.
11. **Absent price does not become a hold.** `get_live_price → None`: assert
    `decision == "skipped_no_live_price"` and `evaluation_performed is False`.
    Assert it is **not** recorded as `hold`, and that no exit evaluation occurred.
12. **Zero-position cycle still emits a heartbeat.** Assert `attempted == 0`,
    `skip_reason == "no_open_positions"`, and that the heartbeat exists.
13. **Aborted loop is visible.** Exception on position 2 of 5: assert
    `cycle_aborted is True`, `positions_reached == 2`, `attempted == 5`,
    `written == 2`, and that positions 3–5 appear as `not_reached`.
14. **Day-0 position is evaluated.** Assert a position with
    `entry_date == today` produces a real evaluation on the monitor path and that
    no `skipped_entry_day` value exists in the monitor vocabulary.
15. **Take-profit records a full exit.** Assert `close:take_profit` carries
    `full_exit_on_tp: true` and that `shares` went to zero, versus the report
    path's half-sale on the same fixture.

### Anti-vacuity

This class has produced two real defects already — the `phase3_gate` lookup that
returned `{}` and passed because `all({})` is `True`, and the heartbeat key no
producer emitted. Controls, not vigilance:

16. **Empty observation set cannot read as covered.** Assert an empty
    `monitor_observation_v1.jsonl` yields `coverage: false`, not vacuous true.
17. **Every schema key has a producer.** Enumerate schema v1 keys, run a fixture
    cycle, assert each key is emitted by some path or is explicitly registered as
    conditional with the condition named.
18. **Heartbeat lineage is non-null when expected.** Assert `decision_code_version`
    is non-null and that a null value sets `lineage_complete: false` rather than
    being silently tolerated.
19. **A report-path heartbeat cannot satisfy a monitor cycle.** Assert that
    feeding only report heartbeats leaves every monitor cycle unsatisfied.
    This is the §6 claim, pinned as a test.

### Added in review (§4.3.1, §4.3.2, §2.4)

20. **`finally` preserves the original exception.** Force a monitor exception
    *and* a recorder serialisation failure in the same cycle. Assert the
    **monitor's** exception type, message and traceback propagate unchanged, and
    the recorder's failure appears only as `write_failures: 1`.
21. **No control-flow statement in `finally`.** Static assertion over the AST of
    the wrapper: no `return`/`break`/`continue` inside the `finally` body.
22. **Unpersisted ratchets are classified as such.** Abort after position 2 of 5
    with a ratchet on position 1. Assert position 1 records
    `state_update_computed_not_persisted`, and that `virtual_trades.json` is
    byte-identical to its pre-cycle state.
23. **Zero open positions records admission as not attempted.** Assert
    `exit_evaluation.skip_reason == "no_open_positions"` **and**
    `admission_evaluation.attempted is False` with
    `skip_reason == "early_return_no_open_positions"`. Assert the heartbeat is not
    treated as admission coverage.
24. **An unchanged cycle records admission as not attempted.** Open positions,
    no ratchet, no close → assert `admission_evaluation.attempted is False` with
    `gated_on: "updated"`, while exit evaluation is `attempted` and complete.
25. **Session is never inferred from the schedule.** Assert
    `session_context == "unknown"` and `session_basis ==
    "bar_timestamp_unavailable"` for every record, and that no code path derives
    a session value from `cycle_utc`. A future `data_feed` diff that supplies a
    real timestamp must flip `price_timestamp_available` rather than
    reinterpreting old records.
26. **Early-return behaviour is unchanged.** Zero-position fixture: assert
    `promote_queue` is called **zero** times in both modes, and that stdout is
    byte-identical. The recorder must not "fix" the early return.

---

## 6. Contract 3 — path-aware coverage and freshness

### 6.1 The registry replaces launcher-grep coverage

Current coverage asks "does `run_ares.sh` export `ARES_PARITY=1`?". That question
is satisfiable by a launcher that invokes code which never reads the variable —
which is exactly what the originally-planned Step 1 repair would have produced.
Replace it with a registry of **decision paths**, keyed by path, not launcher.

```python
DECISION_PATHS = {
    "report": {
        "launcher":        "run_ares.sh",
        "entry_point":     "daily_report.py",
        "decision_call":   "engine.parity_hook.observed_check_open_trades"
                           " -> engine.tracker.check_open_trades",
        "observation":     "engine.parity_runner.observe_cycle",
        "activation":      {"kind": "env", "name": "ARES_PARITY", "value": "1"},
        "schedule_utc":    ((13, 30), (21, 0)),
        "evidence":        "logs/tracker_parity_v1.jsonl",
        "heartbeat":       "logs/parity_heartbeat_v1.jsonl",
        "decides":         ("exit", "entry", "scale_out"),
        "observed":        True,
    },
    "monitor": {
        "launcher":        "run_monitor.sh",
        "entry_point":     "monitor_trades.py",
        "decision_call":   "monitor_trades.monitor (inline loop, own policy)",
        "observation":     "engine.monitor_recorder.emit",
        "activation":      {"kind": "unconditional"},
        "schedule_utc":    ((16, 10), (17, 30)),
        "evidence":        "logs/monitor_observation_v1.jsonl",
        "heartbeat":       "logs/monitor_heartbeat_v1.jsonl",
        "decides":         ("exit", "entry"),
        "observed":        False,   # True only after deployment
    },
}
```

`activation: unconditional` is deliberate. The report path's env gate exists
because parity wraps a function that must also run ungated; the monitor recorder
is a pure in-module store with no alternative call path, so a gate would add a
silent-off failure mode for no benefit. The absence of a gate is the reason
control 1 (decision equivalence) is mandatory.

`"observed": False` is the initial committed value. Flipping it to `True` is part
of the deployment change, not of this design, and the snapshot must read the
registry rather than assume.

### 6.2 Schedule contract expands from two cycles to four

`PARITY_CYCLE_SCHEDULE_UTC = ((13, 30), (21, 0))` currently describes the report
path only. Instrumenting the monitor without expanding this closes one blind spot
and opens another: monitor records would land on cycles the freshness gate cannot
see, so a monitor collector that stopped would read as healthy.

| UTC | Path | Local (MYT/SGT) | Decides |
|---|---|---|---|
| 13:30 | report | 21:30 | exit, entry, scale-out |
| 16:10 | monitor | 00:10 +1 | exit, entry |
| 17:30 | monitor | 01:30 +1 | exit, entry |
| 21:00 | report | 05:00 +1 | exit, entry, scale-out |

Weekdays and the 90-minute grace carry over unchanged; both were validated
against the 64.5h weekend gap on Oct 5. `restart_gateway.sh` at 13:00, 16:00 and
17:25 is a **dependency, not a decision path** — it is registered as such so a
future reader does not mistake it for one, and so a gateway restart failure can
later be correlated with the `live_price_absent` cases from §2.2.

### 6.3 Per-path freshness, never collapsed

The current implementation takes a single latest heartbeat. With four cycles
across two paths, that is actively misleading: the report path runs last each day
at 21:00, so a monitor collector that died at 16:10 would be masked by the 21:00
report heartbeat for the entire following period.

```
for path in DECISION_PATHS:
    last_due[path]  = most recent scheduled cycle for THAT path's schedule
    last_hb[path]   = most recent heartbeat in THAT path's heartbeat file
    covers[path]    = last_hb[path] >= last_due[path] - grace
    state[path]     = ARMED_NOT_STARTED | ARMED_NO_HEARTBEAT
                    | ACTIVE_VALID | ACTIVE_STALE | ACTIVE_INVALID
```

Aggregate is **conjunctive over registered paths**, and empty is rejected:

```
all_paths_fresh = bool(DECISION_PATHS) and all(covers[p] for p in DECISION_PATHS)
```

The `bool(...)` guard is not defensive noise — `all([])` returning `True` is the
exact mechanism of two prior defects.

### 6.4 Readiness splits into two measures

`phase4_operational_ready: true` was too broad because it answered a narrower
question than its name implied. It becomes:

| Measure | Question | Current value |
|---|---|---|
| `collector_healthy_on_registered_paths` | Is every path marked `observed` reporting on schedule? | **true** |
| `all_decision_paths_observed` | Is every path that can change positions actually observed? | **false** — monitor is registered but unobserved |
| `phase4_operational_ready` | Both of the above | **false** |

`phase4_operational_ready` becoming `false` on first evaluation is the correct
outcome and the point of the exercise. It must not be relaxed to restore a green
reading; the green reading was the defect.

The report path's existing 64 records stay valid under their own scope, which the
snapshot states explicitly rather than leaving to inference:

```json
"evidence_scope": {
  "report_path": {"records": 64, "exits_observed": 1, "comparator": "parity_v1"},
  "monitor_path": {"records": 0, "exits_observed": 0, "comparator": null,
                   "exits_lost": 5, "note": "unobtainable, blocking, not retired"},
  "claim": "64 MATCH records establish report-path implementation agreement only"
}
```

---

## 7. Contract 4 — evidence identity and completeness

### 7.1 Three separate identities

`production_commit` is a **log** commit: the bot commits its own logs every
cycle, so HEAD advanced on all twelve vacation heartbeats while no decision code
changed. It still identifies a repository tree containing the executing code, so
it is retained — but it cannot group unchanged code epochs, which is what
evidence grouping needs.

| Field | Meaning | Stability |
|---|---|---|
| `repository_commit` | repo state at execution | changes every cycle |
| `decision_code_version` | fingerprint of the decision implementation | changes only when decision code or effective config changes |
| `observation_code_version` | fingerprint of the recorder itself | changes only when observation changes |

Keeping the last two apart matters because a recorder change must not look like a
decision change, and vice versa.

### 7.2 Three manifests, separated by responsibility

**Revised after review.** One manifest conflated three different questions. Each
fingerprint is independent, each **fails loudly if any required path is missing**,
and none may silently skip an absent entry — the failure mode caught at §7.2's
`config/params.json` error, where a wrong path would have hashed nothing and
still produced a stable-looking fingerprint.

| Manifest | Governs | Changes mean |
|---|---|---|
| `DECISION_MANIFEST_V1` | admissions, fills, exits | production behaviour changed |
| `CLASSIFICATION_MANIFEST_V1` | clean/contaminated sample membership | what counts as evidence changed |
| `OBSERVATION_MANIFEST_V1` | recorder, parity comparison, evidence validation | how we watch changed |

Keeping them apart means a recorder change cannot look like a decision change, and
a change to sample-membership rules cannot masquerade as either.

```python
CLASSIFICATION_MANIFEST_V1 = (
    "engine/sample.py",             # CLEAN_FROM, classify, is_clean, metrics
)

OBSERVATION_MANIFEST_V1 = (
    "engine/monitor_recorder.py",   # proposed, not yet existing
    "engine/parity_hook.py",
    "engine/parity_eval.py",
    "engine/parity_runner.py",
    "engine/parity_compare.py",
    "engine/tracker_compat.py",
    "tools/pre_parity_snapshot.py",
)

DECISION_MANIFEST_V1 = (
    "monitor_trades.py",            # monitor policy  (was unmanifested)
    "daily_report.py",              # report entry point
    "engine/tracker.py",            # report policy + shared primitives
    "engine/data_feed.py",          # price acquisition (was unmanifested)
    "engine/indicators.py",         # RSI, divergence
    "engine/exit_policy.py",        # canonical module
    "engine/signals.py",            # candidate generation
    "config/strategy_params.json",  # effective config, see 7.3
)
```

The parity modules move to the observation manifest: they compare, they do not
decide.

#### Membership follows runtime authority, not filename

`engine/tracker_compat.py` is in the observation manifest **because it currently
serves only the non-authoritative parity branch** — not because of what it is
called or where it sits. If Phase 5 ever makes it part of live decision
execution, it must **also** enter the decision manifest. Conditional, and
recorded as such:

```python
# Membership is a statement about runtime authority at a point in time.
# Re-audit on any change to what calls these modules.
CONDITIONAL_MEMBERSHIP = {
    "engine/tracker_compat.py": {
        "observation": "always — adapts state for comparison",
        "decision":    "IF a production decision path calls it (Phase 5 cutover)",
        "today":       "observation only",
    },
    "engine/exit_policy.py": {
        "role":        "REFERENCE decision implementation",
        "authority":   "none — no production decision path calls it",
        "today":       "decision manifest by role, not by authority",
    },
}
```

**Manifest membership does not imply live authority.**
`engine/exit_policy.py` is the **reference decision implementation** — the
canonical expression of Policy A, against which the two production
implementations can be compared. It is distinct from the **currently
authoritative production implementations**, which are:

| Implementation | Authority today |
|---|---|
| `tracker.check_open_trades` | authoritative, report path |
| `monitor_trades.monitor` | authoritative, monitor path |
| `engine/exit_policy.py` | **reference only, no authority** |

It is in the decision manifest because a change to the reference changes what
"Policy A" means, which is a decision-semantics change. That is a different claim
from saying it decides anything.

Together the two entries prevent a filename rule being inferred: one module is
decision-manifest **without** authority, the other observation-manifest and may
**acquire** authority later. Membership tracks role and runtime authority, which
are two axes, not one.

A manifest-placement audit is therefore required whenever the set of callers
changes — the same event that would invalidate the §7.2 `sample.py` audit.

#### `engine/sample.py` — call-chain audit, as required before placement

Audited rather than assumed. Every production reader of `sample`:

| Call site | Uses | Decides? |
|---|---|---|
| `tracker.py:168` | `fill_context()` → `fill_reasons` | **no** — computed *after* sizing (132-135) and stop (153); the fill happens regardless |
| `tracker.py:172,208` | `PRE_PHASE`/`CLEAN_PHASE` → `sample_phase`, `contaminated` written to the record | **no** — labels only |
| `tracker.py:1010-1050` | `clean`, `excluded`, `metrics`, `net_pnl` | **no** — `print_scorecard` |
| `tracker.py:1167` | `sample_phase`, `contaminated` as CSV columns | **no** — `export_csv` |
| `build_dashboard.py:252-306` | `clean`, `excluded`, `metrics`, `net_pnl` | **no** — display |
| `backfill_sample_phase.py`, `repair_stale_entries.py` | `classify`, `STALE_ENTRY_TOL_PCT` | **no** — offline tools |

**No decision path reads `sample_phase` or `contaminated`.** Placement in the
classification manifest is confirmed.

One boundary worth stating precisely, because it looks like a counterexample: a
missing `stdev_20` both **changes the stop** (`tracker.py:151` substitutes `0.05`,
roughly doubling stop distance) and **sets a contamination label**. The
substitution is a decision and lives in `tracker.py`; only the labelling is
classification. `sample.FALLBACK_STOP_FRAC` and `FALLBACK_TOL` are used solely by
`used_fallback_stdev()` to *detect* that substitution after the fact. The split
holds.

**Verified, not assumed.** The config path is
`config/strategy_params.json` (`tracker.py:28`); `config/params.json` does not
exist. A manifest listing the wrong path would hash nothing and still produce a
stable-looking fingerprint — a fingerprint that silently covers no config at all.
Implementation must therefore assert every manifest path exists and fail loudly
if one does not, rather than skipping absent entries.

`monitor_trades.py` and `engine/data_feed.py` were **not** in any prior manifest.
That is not incidental: the file implementing most live exits and the file
acquiring every decision price were both outside the fingerprint. A manifest that
omits the decision code is a fingerprint of the wrong thing.

Fingerprint = SHA-256 over canonical serialisation of `(path, sha256(bytes))`
pairs, sorted by path, UTF-8, no whitespace variance. **The manifest list itself
is recorded in every record**, so a later manifest change is detectable rather
than silently retrospective.

### 7.3 Effective configuration, not the file

`slippage_pct` and `commission_per_trade` are **absent** from `params.json` and
come from in-code defaults (`0.001`, `1.00`). Fingerprinting the file alone would
miss a change to those defaults, and the values materially affect every booked
exit on both paths.

So `config_fingerprint` covers the **resolved** values actually used, with
provenance per key:

```json
"effective_config": {
  "trailing_stop_pct":    {"value": 0.10, "source": "params.json"},
  "slippage_pct":         {"value": 0.001, "source": "code_default"},
  "commission_per_trade": {"value": 1.00,  "source": "code_default"},
  "scale_out":            {"value": true,  "source": "params.json"},
  "max_positions":        {"value": 5,     "source": "params.json"}
}
```

`source: "code_default"` is itself the finding — it marks every parameter that can
change without any config diff.

### 7.3.1 Effective-parameter inventory — completed 2026-10-07

Enumerated from all `params.get(...)` call sites against
`config/strategy_params.json` (23 keys). Read-only; independent of tonight's
cycle.

**Exactly two keys resolve from a code default:**

| Key | Resolved value | Origin | Affects |
|---|---|---|---|
| `slippage_pct` | `0.001` | **code default** | every booked entry and exit price, both paths |
| `commission_per_trade` | `1.00` | **code default** | `position_size`, every exit's `total_commission` |

All 21 others — `cash_reserve_pct`, `starting_capital`, `max_positions`,
`stop_loss_multiplier`, `tp_momentum`, `tp_reversal`, `trailing_stop_pct`,
`scale_out`, `scale_out_pct`, `rsi_extreme_high`, `rsi_length`, `min_confluence`,
`sma_trend_length`, `sma_slope_threshold`, `disable_trend_continuation`,
`queue_max_age_days`, `queue_max_drift_pct`, `queue_max_size`,
`pending_max_age_days`, `pending_max_gap_hours` — are present in the config file.

So the two parameters that can change with no config diff are precisely the two
that set every booked price. That is the whole of the exposure, and it is
narrower than feared.

**To verify at implementation, not claimed here:** `rsi_oversold`, `min_vol_ratio`
and `version` are present in the config but matched no `params.get` site in this
scan. They are either accessed by another pattern (likely in `engine/signals.py`)
or unused. Not asserted either way.

### 7.3.2 Defaults differ per call site — a latent divergence

**New finding, and the reason "resolved value and origin" is not quite
sufficient.** `trailing_stop_pct` is read with **two different defaults**:

| Call site | Default |
|---|---|
| `monitor_trades.py:29` | **0.08** |
| `engine/tracker.py:873` | 0.10 |
| `engine/exit_policy.py:106` | 0.10 |
| `engine/tracker_compat.py:92` | 0.10 |
| `daily_report.py:111,143` | 0.10 |
| `repair_stale_entries.py:47` | 0.10 |

The config currently supplies `0.10`, so **both paths trail at 10% today and the
divergence is invisible.** Remove, rename or typo that key and the monitor would
trail at 8% while the report path trails at 10% — a silent exit-policy
divergence pre-wired into the defaults, triggered by a config edit that touches
no code.

This is an **eleventh divergence**, in the defaults layer rather than the logic
layer, and it is currently masked.

Consequence for the contract: recording one global `source` per key is not
enough. The effective-config record must carry the **per-call-site default**, so
a masked divergence is visible in evidence before a config change unmasks it:

```json
"trailing_stop_pct": {
  "value": 0.10,
  "source": "config",
  "defaults_by_site": {"monitor_trades.py:29": 0.08,
                       "engine/tracker.py:873": 0.10,
                       "engine/exit_policy.py:106": 0.10,
                       "engine/tracker_compat.py:92": 0.10,
                       "daily_report.py:111": 0.10,
                       "daily_report.py:143": 0.10},
  "defaults_agree": false,
  "masked_divergence": true,
  "currently_active": false
}
```

`currently_active: false` is the fifth field and the one that keeps the record
honest in both directions: the disagreement is real, and it is not firing. A
record showing only `masked_divergence: true` would read as an active defect; one
showing only `value: 0.10` would read as no defect at all. Both would be wrong.

`defaults_agree: false` with `source: "config"` is the signature of a divergence
that exists but cannot currently fire, and a control must assert the combination
is reported rather than normalised away.

#### The recorder must not fix this

The temptation is one line — make `monitor_trades.py:29` read `0.10`. **Out of
scope, and prohibited here.**

Stated precisely, because the obvious objection is "it changes nothing": while
the config supplies `0.10`, that edit would **not change current behaviour** —
both paths already resolve to 0.10. What it would change is **missing-key
behaviour**, which is the only circumstance in which either default is ever read.

So the edit is not a no-op dressed as a fix; it is a change to the system's
behaviour under a *different* configuration, invisible under the present one. It
would alter exactly the latent condition this record exists to preserve, and do
so inside an observation diff reviewed for something else.

Which default is *correct* is also undecidable until the governing question is
answered: unifying on 0.10 presumes the report path is authoritative, which is
exactly what has not been established. Separate review, on its own merits.

Required instead:

- **Control 27** — set `trailing_stop_pct` absent in a fixture config. Assert the
  two paths then resolve to **0.08** and **0.10** respectively, that
  `currently_active` flips to `true`, and that the evidence names both sites.
  The control *demonstrates* the exposure; it does not remove it.
- **Review gate** — a change to `trailing_stop_pct` in
  `config/strategy_params.json`, including deletion or rename, requires explicit
  review against this record before acceptance. The key is load-bearing for
  policy equivalence in a way its name does not suggest.

---

## 7A. Pre-cycle preservation record — 2026-10-07, before 13:30 UTC

Captured read-only. Nothing in this section was edited.

### State

| Item | Value |
|---|---|
| `engine/tracker.py` | `3c23d191b4bccea221678502bd82f30a` |
| `monitor_trades.py` | `1f81383aa6acad939b5c946eeeb76fd7` |
| `engine/data_feed.py` | `28216583761fdbc7ea9c0c5dae6b4721` |
| `logs/virtual_trades.json` | `cf7412f6647def8948538360c0080d75` |
| Parity records | 64 |
| Parity heartbeats | 12 |
| Trades | 3 open, 12 closed |

### Open positions

| Symbol | Entry date | Entry | Shares | SL | TS | TP |
|---|---|---|---|---|---|---|
| TMO | 2026-09-21 | $654.54 | 0.23 | 634.25 | 634.25 | 772.36 |
| WBD | 2026-09-22 | $30.87 | 4.83 | 29.31 | 29.31 | 36.42 |
| AMP | 2026-09-29 | $492.08 | 0.30 | 472.99 | 472.99 | 541.29 |

Open cost basis **$447.27**. Realised after costs, all history, **−$78.38**.

```
cash = 1000 − 78.38 − 447.27 − 3.00 entry commissions = $471.35
```

### Pending (`logs/pending_signals.json`, verbatim fields)

| Symbol | Signal price | Conf | RSI | vol_ratio | stdev_20 | from_queue |
|---|---|---|---|---|---|---|
| EROC | $13.64 | 3 | 50.7 | 1.72 | 0.0585 | false |
| ITUB | $10.15 | 3 | 76.5 | 1.61 | 0.0395 | false |

Both dated 2026-10-06, `momentum_breakout` / `breakout` / `uptrend`,
`strength: medium`, `from_queue: false`.

The fill must come from a **report** cycle regardless of origin, because Stage 3
exists only there (§2.1.1). Their **initiating source is `unknown`**:
`from_queue: false` is consistent with scan initiation on the current call graph
but does not establish it (§3.5).

`logs/queue_ranked.json` is `[]`. XP sits in `signal_queue.json` at $29.73 with
one logged event: `queued` at 2026-10-06T21:01:19, all drift/live fields `null`.

### The sizing mechanism, read rather than assumed

`execute_pending_signals` (`tracker.py:132-135`):

```python
portfolio         = params.get('starting_capital', 1000)
available_capital = portfolio * (1 - cash_reserve_pct)   # 1000 * 0.75 = 750
position_size     = (available_capital / max_positions) - commission
shares            = position_size / entry_price
```

This refines the defect statement. **A reserve is not missing — it is computed
against a constant.** `cash_reserve_pct = 0.25` holds back 25% of the *initial*
$1000 permanently, giving a fixed `position_size` of **$149.00** per slot and a
fixed total deployment of $745 across 5 slots. Actual cash is never consulted at
any point.

So as realised losses accumulate, deployment stays constant while cash falls, and
the nominal $250 reserve is breached without any code noticing — because the code
reserved 25% of a number that stopped being true after the first loss.

`entry_price = today_open * (1 + slippage_pct)`, i.e. the **next session's open**,
not the signal price. Share counts therefore cannot be predicted from the $13.64
and $10.15 signal prices; the dollar outlay can.

### Prediction — labelled as a prediction, pending actual fills

| Quantity | Predicted |
|---|---|
| Cash before | $471.35 |
| Outlay per fill | $149.00 position + $1.00 commission = $150.00 |
| Cash after two fills | **≈ $171.35** |
| Nominal reserve | $250.00 |
| Predicted breach | **≈ $78.65** |

Supersedes the earlier figure of $169.35, which double-counted commission: the
$149 `position_size` already has the $1 commission deducted.

**This remains provisional.** The arithmetic is consistent *assuming each fill
consumes exactly $149.00 + $1.00*, which is what the code computes but not yet
what the record shows. It is not confirmed until actual `shares`, `entry_price`,
`position_size` and `entry_commission` are reconciled from
`virtual_trades.json`, per §8.3. `shares` is rounded to 2dp at
`tracker.py:187-188`, so `shares × entry_price` will not land on exactly $149.00
and the residual must be read, not assumed.

Two fill risks could falsify it outright: `_pending_age_days` (both pendings are
1 day old against a 4-day maximum, so this should pass), and the `bar_date !=
today` retention guard at `tracker.py:122` — if the daily bar for Oct 7 has not
appeared when the 13:30 cycle runs, both are **retained, not filled**, and the
fill lands on the 21:00 cycle instead. AMP and TWST both filled at 13:30, so the
bar normally exists by then, but this is an observation to make rather than
assume.

### A policy asymmetry visible at admission

ITUB is admitted with `rsi: 76.5`, below the configured
`rsi_extreme_high: 90`.

The narrow finding: **the report path can apply an RSI exit; the monitor path
cannot.** Which path acts on any position first depends on its inputs and
schedule, not solely on when price moves — the monitor runs at 16:10 and 17:30
on IBKR live prices, the report path at 13:30 and 21:00 on a daily bar with a
live override, and either may reach a threshold first.

Two earlier formulations are withdrawn: "two thirds of the way to an immediate
exit" (not a defined measure), and "depends on which cycle its price moves in"
(too narrow — inputs and schedule both matter).

### 7.4 Completeness accounting

Per cycle: `attempted` (open positions at loop start), `written`,
`skipped` with per-reason counts, `not_reached`, `cycle_aborted`,
`positions_reached`, `write_failures`.

`attempted == written + skipped + not_reached` is an **asserted invariant**, not
a hope. A cycle violating it is recorded with `accounting_valid: false` and reads
as incomplete at evaluation time.

### 7.5 No retrospective rewriting

The first two report heartbeats carry `production_commit: null` and no
`lineage_complete`, because they predate the fix. They stay that way. Monitor
records begin at deployment and carry no synthetic history. The five lost exits
are registered as obligations, not reconstructed.

Where historical reconstruction could support diagnosis, it is labelled
`reconstructed: true` in a separate stream and never merged into contemporaneous
evidence. **Reconstruction can inform; it cannot observe.**

---

## 8. The 2026-10-07 13:30 UTC cycle — preservation protocol

Runtime stays unchanged tonight. The cycle is observed with the instrumentation
that exists, and its outcome preserved for later comparison.

### 8.1 Before (read-only, no edits)

Capture and commit, without modifying any of it:

- `logs/virtual_trades.json` — hash plus the 3 open positions and their state
- pending EROC and ITUB records, verbatim, including admission basis fields
- queue/ranked state including XP
- cash and reserve position as computed by the documented method
- `logs/` queue-event entries for EROC, ITUB, XP — the partial admission
  evidence identified in §2.5
- record and heartbeat counts; `tracker.py` md5; `monitor_trades.py` md5

### 8.2 Predicted (stated before the fact, so it can be wrong)

| Quantity | Prediction |
|---|---|
| Cash before | ≈ $471 |
| Two entries at fixed notional | ≈ 2 × $150 + $2 commission |
| Cash after | ≈ **$169** against a $250 reserve |
| Reserve breach | **yes**, by ≈ $81 |
| Anything that prevents it | **nothing** — sizing reads `starting_capital` |

Writing the prediction down first is what makes the observation a test rather
than a narrative fitted afterwards.

### 8.3 After (capture, do not act)

Read-only. Fixed list, so the capture cannot drift toward whatever happened to
be interesting.

| # | Capture | Source |
|---|---|---|
| 1 | **Pending before and after** — full records, both states | `logs/pending_signals.json`, pre-state in §7A |
| 2 | **Per-symbol status**: filled / retained / expired / rejected / queued | `queue_events.jsonl` actions, stdout |
| 3 | **Actual fill price, quantity, commission** — `entry_price`, `entry_slippage`, `shares`, `original_shares`, `position_size`, `entry_commission` | `virtual_trades.json` |
| 4 | **Bar date used by the fill guard** — the `bar_date` vs `today` comparison at `tracker.py:120-127`, and whether either symbol was retained on it | stdout, `fill_retry` events |
| 5 | **Resulting cash and reserve comparison** — recomputed, against the $250 nominal and the provisional $171.35 | derived |
| 6 | **Executing path and source evidence where available** — which cycle filled, and `initiating_source` recorded as `unknown` for both unless evidence exists | heartbeat timestamps, `from_queue`, §3.5 caveat |

Supporting observations, same read-only pass:

- Report-path parity records for any new position: expect
  `inline_effective_stop_basis: not_computed_entry_day_skip` and
  `parity_action: skipped_entry_day`
- Heartbeat `attempted` — 5 if both filled, 4 if one, 3 if neither
- `stdev_fallback` on each fill: EROC carries `stdev_20: 0.0585` and ITUB
  `0.0395`, so both should be `false`; a `true` would mean the pending lost the
  field between admission and fill
- `sample_phase` on each fill: expect `clean_v3`, since `ARES_SCHEDULED=1` is
  exported by the launcher and `fill_context()` reads it as authoritative

### 8.3.1 Reconciliation rules

Four rules, binding on the reconciliation, each closing a way the capture could
be over-read.

**1. Do not infer initiation from `from_queue`.** `promote_queue` is called from
both paths and both yield `from_queue: true`; the field is ambiguous exactly
where the distinction is wanted. Record `initiating_source: unknown` for both
symbols unless a record field establishes otherwise. It will not.

**2. Compute cash from booked values, not from the model.** Use actual `shares`,
`entry_price` and `entry_commission` read from `virtual_trades.json`:

```
cash = 1000 − Σ realised_after_costs
            − Σ (shares × entry_price)       ← booked, not position_size
            − Σ entry_commission
```

`shares` is rounded to 2dp, so `shares × entry_price ≠ 149.00`. Report the
residual rather than absorbing it. If the reconciled figure differs from the
provisional $171.35, **the prediction was wrong and the booked value stands** —
do not adjust the method to recover the prediction.

**3. A retained pending is not a failed fill.** Three distinct outcomes that must
not collapse:

| Outcome | Evidence | Meaning |
|---|---|---|
| `retained_date_guard` | `fill_retry`, `last bar X != today Y` (`tracker.py:122`) | the session's bar had not appeared; pending **survives**, fill deferred |
| `retained_no_data` | `fill_retry`, `fill_attempts` incremented | data fetch returned nothing; pending survives until `MAX_FILL_ATTEMPTS` |
| `fill_dropped` | `fill_dropped` event | aged out past `pending_max_age_days`, or data exhausted — pending **gone** |

A retention is the guard working. Only `fill_dropped` is a loss.

**4. Preserve unavailable provenance as `unknown`.** Never substitute a default,
a plausible value, or an inference presented as a reading. Specifically:
`initiating_source`, `price_timestamp`, `session_context`, and any field whose
producer did not run. An `unknown` that is accurate is worth more than a value
that is merely present.

### 8.4 What the capture cannot establish

Stated now so the reconciliation is not over-read later:

- **Which path initiated** EROC and ITUB. No record field carries it (§3.5).
- **Whether the fill price was executable.** `today_open × 1.001` is a model, and
  the same unresolved fill-realism question as PCVX.
- **Whether the reserve breach is harmful.** It establishes that the computed
  reserve does not track cash. Whether that matters is a separate risk decision.

### 8.4 Explicit non-authorisation

This protocol **does not authorise the EROC/ITUB entries**, and observing a
predicted breach is not the same as controlling it. Documentation is not a safety
control. If the breach warrants prevention, that is an operational-risk decision
with its own preregistration — sizing basis, reserve enforcement point, and
behaviour when they conflict — and it is **not** a recorder change. The recorder
would only have made the breach visible after the fact.

---

## 9. What this design leaves unresolved

Listed so none of it is mistaken for handled.

1. **Which exit policy is authoritative.** The governing question. This design
   observes two policies; it does not choose between them. Deliberately so: a
   recorder that assumed an answer would prejudge it.
2. **Fill realism.** Capturing `live` and `trigger_margin` will make the question
   answerable for *future* exits. It does not answer it for PCVX, and capturing a
   trigger price still does not prove an executable fill.
3. **Fixed-notional sizing and reserve enforcement.** Separate preregistrations.
4. **`get_live_price` opacity.** Making outage distinguishable from absence
   requires editing a decision-path file.
5. **No per-trade exception guard on the monitor.** Recorded by this design,
   repaired by none of it.
6. **Monitor exit-date basis.** The monitor stamps `datetime.now()` (naive, UTC on
   the VPS); the report path uses the bar date. Captured as `exit_date_basis`,
   not reconciled.
7. **Disposition of the five lost exits.** They remain blocking pending explicit
   review. This design does not retire them.
8. **`MIN_CLOSED_SAMPLE`.** Still unregistered, still correctly blocking.
9. **The 0-of-6 monitor-exit coverage already in the record** is permanent and
   cannot be repaired. Nor does this design yet prevent recurrence: a design
   prevents nothing. Observation loss stops only once every relevant path is
   instrumented, deployed and **verified against live cycles** — which is a state
   this document does not reach and does not authorise reaching.
10. **Whether `cash_reserve_pct` should be computed against equity** rather than
   `starting_capital`. Identified precisely in §7A; repairing it is an
   operational-risk decision with its own preregistration.

---

## 10. Review checklist, then sequence

Before implementation, review this design against **both** paths and confirm:

- [ ] Every value the monitor decides on is captured at its decision site
- [ ] No captured value is re-derived or re-requested
- [ ] All four cycles and both paths are registered, with per-path freshness
- [ ] A report heartbeat cannot satisfy a monitor cycle (control 19)
- [ ] `attempted == written + skipped + not_reached` is asserted
- [ ] No comparator, no `MATCH`, no `difference_class` anywhere in the schema
- [ ] Observation is fail-open; evidence interpretation is fail-closed
- [ ] `phase4_operational_ready` is permitted to go false
- [ ] Manifest includes `monitor_trades.py` and `engine/data_feed.py`
- [ ] Effective config records `code_default` provenance
- [ ] No schema key lacks a producer (control 17)
- [ ] No empty collection can read as covered (control 16)

Then:

```
Draft recorder design                     ← this document
        ↓
Preserve the 13:30 UTC cycle's outcome    ← §8, tonight
        ↓
Review design against both paths          ← §10 checklist
        ↓
Implement and test offline                ← §5 controls, laptop only
        ↓
Deploy between cycles under separate approval
        ↓
Separately preregister: policy unification, sizing, fill realism
```

Phase 5 and AI modelling remain prohibited. The goal is complete observation of
the existing two-policy production system — not unifying it, and not changing it.

---

## 11. Checklist walk — 2026-10-07, pre-cycle

Walked against current source and the §7A preserved state. **No implementation
or deployment is authorised by this review.** Seven items pass; five required
amendment, all now applied above.

| # | Item | Result |
|---|---|---|
| 1 | Every decided value captured at its decision site | **AMEND** → §3.5 |
| 2 | No captured value re-derived or re-requested | **AMEND** → §2.4 |
| 3 | Four cycles, both paths, per-path freshness | **AMEND** → §4.3.1 |
| 4 | Report heartbeat cannot satisfy a monitor cycle | PASS (control 19) |
| 5 | `attempted == written + skipped + not_reached` asserted | **AMEND** → §4.3.1 |
| 6 | No comparator, no `MATCH`, no `difference_class` | PASS |
| 7 | Observation fail-open; interpretation fail-closed | **AMEND** → §4.3.2 |
| 8 | `phase4_operational_ready` permitted to go false | PASS |
| 9 | Manifest includes `monitor_trades.py`, `data_feed.py` | PASS, path corrected |
| 10 | Effective config records `code_default` provenance | PASS, widened |
| 11 | No schema key lacks a producer | PASS (control 17) |
| 12 | No empty collection reads as covered | PASS (control 16) |

### Item 1 — exits pass, admissions do not

Exit capture is sound: every value the monitor's branches read is bound in the
loop, and §3.3 captures each at its site. Admission decisions are **not** in the
loop's locals — they are inside `promote_queue`, `_validate_queued` and
`open_trade`. The original §3.5 proposed capturing them anyway, which would have
meant an exit recorder reaching into tracker primitives under cover of a design
reviewed for something else.

**Resolved:** admission capture split into its own registered diff. §3.5 now
states the requirement and the existing `queue_events.jsonl` coverage, and
authorises nothing. The single highest-value field is `source` — already a
parameter of `promote_queue`, printed to stdout, never recorded.

### Item 2 — session provenance was being inferred

The draft derived `session_context` from the cycle's scheduled UTC time. A cron
slot is not evidence about a price, and `useRTH=False` permits extended-hours
bars without establishing the session of any given bar.

**Resolved:** `session_context: "unknown"`, `price_timestamp: null`,
`session_basis: "bar_timestamp_unavailable"`. The real `bars[-1].date` is
discarded inside `get_live_price`; recovering it is a `data_feed` diff. Control
25 pins that no code path derives session from `cycle_utc`.

### Items 3 and 5 — the conflation reappeared one level down

Confirmed in source: the early return at `monitor_trades.py:22` precedes both the
loop and the `if updated:` block, so `promote_queue` is unreachable on that path —
and admission is gated on `updated` even when positions exist. A
`no_open_positions` heartbeat is truthful about exits and silent about
admissions. Same shape as one heartbeat covering two paths, one level down.

**Resolved:** separate `exit_evaluation` and `admission_evaluation` accounting
(§4.3.1), controls 23, 24 and 26. The early return is **not changed**; control 26
asserts `promote_queue` is called zero times in both modes.

### Item 7 — `finally` could have destroyed the evidence it exists to produce

The draft specified `try/finally` for heartbeat emission but never stated that
`finally` must not raise or return. A raising `finally` replaces the in-flight
exception; a returning one discards it. The failure mode is exactly inverted:
the recorder would erase the monitor's exception at the moment that exception is
most diagnostic.

**Resolved:** §4.3.2 — no control flow in `finally`, recorder cannot raise,
original traceback unchanged and unchained. Plus two classification rules that
were missing: pre-abort ratchets are
`state_update_computed_not_persisted` (because `save_trades` at line 86 is never
reached), and post-abort positions are `not_reached`, **never `hold`**. Controls
20–22.

### Item 9 — manifest widened again

`config/params.json` → `config/strategy_params.json` (`tracker.py:28`), already
corrected. Review adds a candidate: `engine/sample.py`, whose `fill_context()`
sets `PRE_PHASE` vs `CLEAN_PHASE` at fill time (`tracker.py:168-172`). It governs
**evidence classification**, not decisions, so it belongs in a classification
manifest rather than `DECISION_MANIFEST_V1`. Flagged for the implementation
review to place deliberately rather than by default.

### Item 10 — more parameters resolve from code defaults than recorded

Beyond `slippage_pct` and `commission_per_trade`, the fill path reads
`cash_reserve_pct`, `max_positions`, `starting_capital`,
`stop_loss_multiplier`, `tp_momentum`, `tp_reversal`, `pending_max_age_days`
and `pending_max_gap_hours`, each via `params.get(key, default)`.

Which of these are present in `config/strategy_params.json` and which fall
through to a code default is **not asserted here** — it must be enumerated at
implementation and recorded per key, because a parameter resolving from a code
default can change with no config diff. §7.3's `source` field already carries the
shape; the key list widens.

### Findings this walk added to the record

1. The monitor **cannot complete an admission** — `open_trade` only writes a
   pending; `execute_pending_signals` is report-path only. Corrects §2.1 from
   "full admission path" to "admission-initiating path".
2. **`source` is discarded.** `promote_queue(source=...)` prints it and records
   nothing, so no evidence distinguishes a monitor-initiated pending from a
   scan-initiated one. The retrospective question "which path initiated this
   admission?" is unanswerable for every existing entry.
3. **The reserve is computed against `starting_capital`**, so it reserves 25% of a
   number that stopped being true after the first loss. `position_size` is a flat
   $149.00 and actual cash is never read.
4. **Admission is gated on `updated`** — a monitor cycle that changes nothing
   attempts no admission, even with free slots and queued candidates.
5. With **zero open positions the monitor cannot admit at all**, because the early
   return precedes `promote_queue`.

### Second pass — 2026-10-07, both prerequisites closed

Item 9 and item 10 are now resolved, read-only and independent of the cycle.

**Manifests split three ways** (§7.2) by responsibility: decision,
classification, observation. `engine/sample.py` placed in the classification
manifest **after** the call-chain audit confirmed no decision path reads
`sample_phase` or `contaminated` — six production readers enumerated, all
labelling, display, export or offline tooling. `tracker_compat.py` and the parity
modules moved to observation; `engine/signals.py` added to decision. Every
fingerprint must fail on a missing path rather than skip it.

**Parameter inventory complete** (§7.3.1): of 23 keys, **exactly two** resolve
from code defaults — `slippage_pct` (0.001) and `commission_per_trade` (1.00).
Narrower than feared, and they are precisely the two that set every booked price.

**`source` capture re-scoped** (§3.5): registered as a record-contract change
with destination, field name `initiating_source`, closed value set
`{scan, monitor, unknown}`, schema version 2, absent-means-unknown for history,
and no backfill. The earlier "one field" framing understated it.

**`from_queue` inference withdrawn** (§3.5). `promote_queue` is called from
*both* paths and both yield `from_queue: true`, so the field is ambiguous exactly
where the distinction is wanted. `from_queue: false` is consistent with scan
initiation on the current call graph but asserts nothing. EROC and ITUB are
`initiating_source: unknown`.

### Finding added by the second pass — an eleventh divergence, currently masked

`trailing_stop_pct` is read with **0.08** at `monitor_trades.py:29` and **0.10**
at all six other sites. The config supplies `0.10`, so both paths trail at 10%
today and the divergence cannot fire.

Remove, rename or mistype that one config key and the monitor trails at 8% while
the report path trails at 10% — an exit-policy divergence pre-wired into the
defaults, unmasked by a config edit that touches no code and passes every
existing check.

This is why "resolved value and origin" needed extending: a single global
`source` per key would record `"config"` and look clean. The contract now carries
`defaults_by_site`, `defaults_agree` and `masked_divergence`, and
`defaults_agree: false` with `source: "config"` is the signature to watch for.

### Next

Prerequisites closed. Tonight's 13:30 UTC cycle runs **unchanged** — about
6h15m from 15:15 SGT. Then §8.3 is captured read-only against the fixed list,
§8.4 bounds what it cannot establish, and predicted-versus-actual is reconciled
as a separate step before any implementation diff is presented.

Nothing in this document authorises a runtime change. In particular, the recorder
must not quietly repair admission gating, reserve sizing, exception handling, the
defaults divergence, or any exit-policy difference. **Observe first.**

---

## 12. Implementation review outcome — 2026-10-09

Built on branch `recorder/monitor-observer-v1` (`7681d9c`, `ac73363`).
**Deliberately not on `main`.** Review only; activation not authorised.

### 12.1 Merging IS deployment

Found while preparing the commit, and it changes the activation procedure.
`run_monitor.sh` and `run_ares.sh` both contain:

```sh
if ! git push -q 2>/dev/null; then
    if git pull -q --rebase --autostash && git push -q; then ...
```

A new `main` commit **guarantees** the bare push fails, which triggers the pull,
which brings `main` into the VPS working tree — and the next cycle runs it. There
is no separate deploy step and never was.

Consequences:

- Every documentation commit this week was propagated to the VPS the same way.
  Harmless for markdown; **not** harmless for code.
- Activation must be an explicit merge performed **between cycles**, with gateway
  state known, not whenever convenient.
- Code under review must live on a branch. The bot pushes `main` only, and
  `pull --rebase` targets its tracked upstream, so a side branch cannot reach the
  live tree.

### 12.2 What was implemented

`engine/monitor_observer.py` (new) — three manifests whose fingerprints report
`complete: false` on a missing path; effective configuration recording resolved
value and origin with **per-site** defaults; an exact 33-field record schema;
a path-scoped heartbeat in its own file; deterministic serialisation with
`fsync`; `flush` that never raises and is idempotent.

`monitor_trades.py` (+120 lines) — a plain dict store mirroring
`tracker._LAST_EVAL`. The loop writes **raw values only**: no imports, no
arithmetic, no formatting, no I/O. Unconditional, with no env gate, because a
store that behaves differently when observation is off means the observed path is
not the path that runs. The per-trade record is appended **before any branch**
and mutated in place, so a loop that dies mid-trade leaves a partial record
rather than none. Guarded import; `finally` flush; original exception preserved
by a bare `raise`.

### 12.3 Recorded and fixed nowhere

The divergent 0.08 trailing-stop default · the absent entry-day skip · the
missing per-trade exception guard · the two-precision `peak_price` · the naive
exit date · `get_live_price` collapsing four causes into `None` · the apparent
absence of a drift guard on the pending path.

### 12.4 Controls

22 controls, **22/22 on the laptop and 22/22 on the VPS**, exit 0.

`tests/test_monitor_observer.py` is the pytest form.
`tests/run_monitor_observer_controls.py` is a stdlib-only runner, added because
pytest is absent from the VPS venv and installing it would change the production
environment in order to test an observation-only module. Both are kept and must
not drift.

Verified on the VPS through a `git worktree` in `/tmp`, so the live tree was
never switched, then removed.

Mutation-tested to show the controls can fail and are not over-coupled:

| Mutation | Controls that fail |
|---|---|
| defaults forced to agree | 18, 19 — nothing else |
| `decision_path` relabelled | 04 — nothing else |
| schema given an unproduced key | 08 — nothing else |

Control 00 is a harness self-test: a harness that cannot report a failure proves
nothing.

### 12.5 Two defects found by the recorder's own controls

1. **`flush` applied the derivation twice.** The second pass saw the keys the
   first had filled with `None`, so a **partial record reported
   `record_complete: true`** — the recorder would have claimed complete evidence
   for an aborted loop, which is the exact failure mode it exists to prevent.
2. **A legitimate skip reported incomplete.** WBD returns no live price on every
   cycle, so it would have been indistinguishable from a loop that died.
   Completeness is now judged against what the outcome can produce, and the skip
   itself is never reported as missing evidence.

Both fixed, both regression-guarded (controls 09 and 08).

### 12.6 Qualifications on the review

- Control 13 verified manifest completeness against the **worktree** checkout,
  not the live tree. The two differ only in the recorder files, so the
  decision-manifest paths are confirmed present — but a digest comparison against
  the running tree is a separate check belonging to the merge decision.
- **Admission capture is not included.** `queue_events.jsonl` is empty for a
  direct scan admission, so that work needs its own registered diff covering both
  routes. The recorder observes exits and state updates only, and that boundary
  must be stated rather than discovered later from a gap in the records.
- The recorder makes the monitor path **observable**. It does not make the two
  paths reconciled, and it cannot be described as preventing recurrence until
  every decision path is instrumented **and verified**. The five lost monitor
  exits remain blocking.
- The governing question — which production exit policy is authoritative — is
  untouched by this work, deliberately.
