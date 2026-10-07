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

### 2.1 The monitor also makes ENTRY decisions

`monitor_trades.py:89` calls `promote_queue(source="monitor", use_live=True)`
whenever a close freed a slot. That reaches `open_trade(signal, from_queue=True)`
with `price = checks['live']` (`tracker.py:589`).

**The monitor is not an exit-only path. It is a full admission path.** The
incident characterised it as a second exit implementation; it is a second exit
implementation *and* an unobserved entry mechanism. The recorder must cover
admission decisions, not only exits.

This also means the Oct 7 open question about EROC/ITUB pricing basis has two
possible answers depending on which cycle admits them, and the recorder must
record which.

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
need that distinction. The recorder adds `session_context` derived from the
cycle's UTC time.

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

### 3.5 Admission capture (new, from §2.1)

`promote_queue(source="monitor", use_live=True)` runs after `save_trades` when a
close freed a slot. Capture per candidate:

- `lifecycle_id`, `symbol`, `source: "monitor"`
- `admission_outcome`: `promoted` / `dropped_already_held` / `dropped_validation`
  / `deferred_transient` / `deferred_fill_window` / `open_trade_rejected`
- `validation_checks` (the `checks` dict: `live`, `drift_pct`, …)
- `price_basis`: `checks['live']` versus a daily close — the field that would
  have answered the EROC/ITUB question
- `open_trade_result`: `pending` / `opened` / other
- `free_slots_before`, `pending_max_gap_hours`, `fill_window_ok`, `fill_window_reason`

Admission records go to a **separate stream** from exit observations
(`logs/monitor_admission_v1.jsonl`). They answer a different question and mixing
them would make both harder to reason about.

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
| Separate files | `logs/monitor_observation_v1.jsonl`, `logs/monitor_admission_v1.jsonl`, `logs/monitor_heartbeat_v1.jsonl`. Never appended to `tracker_parity_v1.jsonl` — different semantics, no comparator, and mixing would corrupt the meaning of the 64 existing records. |

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

### 7.2 Manifest — explicit, versioned, and wider than the obvious five

```python
DECISION_MANIFEST_V1 = (
    "monitor_trades.py",            # monitor policy  (was unmanifested)
    "daily_report.py",              # report entry point
    "engine/tracker.py",            # report policy + shared primitives
    "engine/data_feed.py",          # price acquisition (was unmanifested)
    "engine/indicators.py",         # RSI, divergence
    "engine/exit_policy.py",        # canonical module
    "engine/tracker_compat.py",     # 2dp adapter
    "engine/parity_hook.py",        # report observation bridge
    "engine/parity_eval.py",
    "engine/parity_runner.py",
    "config/strategy_params.json",  # effective config, see 7.3
)
```

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
`strength: medium`. **`from_queue: false`** — these were admitted directly from
the scan, not promoted from the queue, which means the 13:30 report path is the
expected filler and the monitor promotion route is not implicated for these two.

`logs/queue_ranked.json` is `[]`. XP sits in `signal_queue.json` at $29.73 with
one logged event: `queued` at 2026-10-06T21:01:19, all drift/live fields `null`.

### Refined prediction, from actual signal prices

At ~$150 fixed notional: EROC ≈ 10.99 sh, ITUB ≈ 14.78 sh.

```
471.35 − 150 − 150 − 2.00 commissions ≈ $169.35   vs $250 reserve
```

**Predicted breach ≈ $80.65.** RSI 76.5 on ITUB at admission is worth noting
against the `rsi_extreme_high: 90` exit threshold — it enters already two thirds
of the way to an immediate `emotional_extreme` exit on the report path, a
condition the monitor path does not implement at all.

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

- Were both admitted, or did `max_positions`/`_fill_window_ok` intervene?
- Quantity and **pricing basis** for each: `checks['live']` versus daily close —
  the field §3.5 exists to make explicit in future
- Which cycle admitted them: 13:30 report, or a 16:10/17:30 monitor promotion
- Booked entry price, shares, commission; resulting cash
- Whether the reserve breach matched the prediction, and by how much
- Report-path parity records for the new positions: expect
  `not_computed_entry_day_skip` and `parity_action: skipped_entry_day`
- Heartbeat `attempted` — expect 5 if both filled

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
9. **The 0-of-6 monitor-exit coverage already in the record** is permanent. The
   recorder prevents recurrence; it cannot repair history.

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
