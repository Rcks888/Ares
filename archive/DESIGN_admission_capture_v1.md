# Admission capture v1 — design (DRAFT for review)

Status: **DRAFT, not authorised.** No code exists. Ares stays FROZEN. This is
step 3 of the plan in `STATUS.md`. The evidence behind it is
`archive/AUDIT_2026-10-20_admission_routes.md` (cited below as "the audit").
Line references are to `e4dd97c`.

---

## 1. Purpose and strict scope

### What this design does

It records every admission decision and every fill outcome on all three
routes, so that:

1. The admitting route (R1, R2, R3) is **recorded, not inferred** from git
   history or from the absence of queue events.
2. Each fill **joins exactly one admission** without inference from timestamps.
3. The gap between the decision and the price booked (audit finding A2) is
   **measured per trade**.
4. A run that admitted or filled without leaving a record is detected as a
   **gap**, not read as "no admission".

### What this design explicitly does NOT do

- It does not fix A2 (same-session fill at a pre-decision open). It records
  the inputs that measure it.
- It does not fix the lost `strength`, the signal re-base on promotion, the
  unranked fallback, or the silent pending discards (§2.2).
- It does not change `queue_events.jsonl`, its fields or its writer.
- It does not change any decision, printed output, exception path or
  production state file.
- It does not touch `engine/monitor_observer.py` or the monitor recorder's
  contract.
- It does not decide whether manual admissions belong in the clean sample
  (§9 Q5).

---

## 2. Findings this design rests on

### 2.1 From the audit (summary)

| # | Finding | Design consequence |
|---|---|---|
| A1 | All 21 routes can be reconstructed today, only by inference from git | Explicit record per admission (§4) |
| A2 | 11 of 21 fills booked at an open that preceded the decision | Record decision time and price, plus fill bar date and open (§4.4) |
| A3 | Queue promotion overwrites `signal_date` / `signal_price`; `strength` lost | Record the original queue fields next to the promotion values (§4.3) |
| A4 | Manual admission not labelled (AMP) | Record `run_kind` at admission, not only at fill (§4.1) |
| A5 | `queued` events conflate two producers; `duplicate` never logged | Every `open_trade` outcome recorded (§4.2) |

### 2.2 New in this design: pending signals discarded silently at fill

The docstring of `execute_pending_signals` says "A pending is only discarded on
a logged, explicit reason — never silently" (`tracker.py:63`). Two branches
contradict it:

- **Already open** (`tracker.py:88-91`). The pending is removed with a print
  and no event.
- **Slots full, already queued** (`tracker.py:82-86`). `queue_signal` returns
  early with no event when the symbol is already queued (`tracker.py:325-327`).
  The pending is then removed, so the demotion leaves no trace at all.

Recorded here, fixed nowhere. The fill-pass record (§4.4) gives both an
explicit outcome.

### 2.3 All admission and fill code lives in `engine/tracker.py`

Every call site, confirmed by grep at `e4dd97c`:

| Call | Site | Process |
|---|---|---|
| `execute_pending_signals()` | `daily_report.py:54` | report |
| `promote_queue(source="scan")` | `daily_report.py:64` | report |
| `open_trade(s)` | `daily_report.py:119` | report |
| `promote_queue(source="monitor", use_live=True)` | `monitor_trades.py:280` | monitor |
| `open_trade(signal, from_queue=True)` | `tracker.py:595` | either |

`open_trade` has exactly two callers. `from_queue=False` occurs only at
`daily_report.py:119`. So R1 is identified by an **explicit parameter value**,
not by absence. R2 and R3 are identified by `promote_queue`'s `source`
argument.

---

## 3. Mechanism

The monitor recorder pattern is reused unchanged.

- **Raw store in the decision code.** A plain dict, `_ADMISSION_EVAL`, sits in
  `engine/tracker.py`, alongside the existing `_LAST_EVAL`. Capture sites only
  assign raw values into it: no imports, formatting, arithmetic or I/O.
- **Observer module.** A new `engine/admission_observer.py` does all
  derivation, validation and serialisation after the run. Its `flush(store)`
  never raises and is idempotent.
- **Flush in each entrypoint's `finally`.**
  - `daily_report.py` `__main__`: a new `try/finally` (one registered
    replacement, as `monitor_trades.py` had).
  - `monitor_trades.py`: one added call inside the existing `finally`, after
    the monitor observer flush. **This changes the registered diff of an
    accepted recorder** (§9 Q2).
- **Invocation reset.** At the start of `generate_report()` and `monitor()`,
  using the same token-plus-cycle-id design as the monitor recorder.
  Records are matched by `cycle_id`, never by token.

### 3.1 Why capture inside `tracker.py`, not at the call sites

- **Inside `open_trade`:** every call produces a record, whoever the caller
  is. A future third caller cannot bypass it.
- **At the call sites:** `daily_report.py` would need a new import of the
  store. The `promote_queue` context (`source`, `use_live`, the checks) is
  only visible inside `promote_queue`, which is in `tracker.py` anyway.

The cost is a registered diff on `tracker.py`, the file both paths share
(§6).

---

## 4. Records

One stream holds all records: `logs/admission_v1.jsonl`. Each record has a
`kind`. One heartbeat per process run goes to
`logs/admission_heartbeat_v1.jsonl`. Both files are re-included in
`.gitignore`, as the monitor recorder's files are.

### 4.1 Envelope (every record)

| Field | Source |
|---|---|
| `schema_version`, `kind` | constant |
| `cycle_id`, `invocation_token` | invocation store |
| `decision_path` | `report` \| `monitor` (set by the flushing entrypoint) |
| `run_kind`, `run_kind_detect` | `sample.fill_context()`, called by the observer at flush (same process, same environment): `scheduled\|manual`, `env\|tty_inferred` |
| `timestamp` | UTC, tz-aware, taken **at the decision**, not at flush |
| `record_complete`, `missing_fields`, `contract_violations` | derived in the observer |

### 4.2 `kind: admission` — one per `open_trade` call

Captured inside `open_trade` (`tracker.py:612-658`). The record is appended
on entry and completed before each of its four returns. A raise leaves it
incomplete, never absent.

| Field | Raw value |
|---|---|
| `symbol` | `signal['symbol']` |
| `route` | `scan_direct` if `from_queue` is False; otherwise the enclosing promotion pass's `source` |
| `from_queue` | parameter |
| `signal_date`, `decision_price` | `signal['date']`, `signal['price']` |
| `strategy`, `confluence`, `strength`, `stdev_20` | from `signal` (`strength` is absent on the queue route; recorded as absent) |
| `open_positions_before`, `pending_before`, `max_positions` | as read by `open_trade` |
| `outcome` | `duplicate_open` \| `queued` \| `duplicate_pending` \| `pending` |
| `queue_signal_logged` | `queued` outcome only: whether `queue_signal` appended or returned early (`tracker.py:325-327`) |

The two `duplicate` returns (`:623`, `:633`) are separate outcomes because
they have different causes.

### 4.3 `kind: promotion_pass` — one per `promote_queue` call

Captured in `promote_queue` (`tracker.py:527-610`). It is written even when
the pass returns early: an early return is evidence that no promotion was
attempted.

| Field | Raw value |
|---|---|
| `source`, `use_live` | parameters |
| `free_slots` | `:537` |
| `ranked_count`, `ranked_basis` | `ranked` \| `unranked_fallback` \| `empty` (`:542-546`) |
| `candidates_examined` | entries reached before the loop's `break` (`:559`) |
| `fill_window_ok`, `fill_window_reason` | `:548` |
| `early_return_reason` | `no_free_slots` \| `queue_empty` \| `fill_window_closed` \| null |
| `candidates[]` | one entry per ranked symbol examined, in order |

Each `candidates[]` entry:

| Field | Raw value |
|---|---|
| `symbol` | |
| `queued_at`, `price_at_signal` | `q['date_added']`, `q['price_at_signal']`, the **original** scan values (A3) |
| `held` | `:562` |
| `valid`, `reason`, `checks` | `_validate_queued` return value, with `checks.live` as the decision price |
| `open_trade_result` | `:595` |
| `outcome` | `held_dropped` \| `deferred_transient` \| `dropped_invalid` \| `promoted` \| `dropped_open_trade` |

The loop's `break` on `free_slots <= 0` (`:559`) means later ranked entries
are **not examined**. `candidates_examined` and `ranked_count` are both
recorded, so an unexamined tail is visible and not mistaken for a rejection.

A `promoted` candidate and its `admission` record (§4.2) must agree on
`symbol`, `decision_price` and `signal_date`. That is an inspection check,
not a reason to drop either record.

### 4.4 `kind: fill` — one per pending examined by `execute_pending_signals`

Captured in `execute_pending_signals` (`tracker.py:59-249`), one entry per
iteration.

| Field | Raw value |
|---|---|
| `symbol`, `signal_date` | `sig['symbol']`, `sig['date']`, the **join key** (§5) |
| `from_queue` | `sig.get('from_queue')` |
| `pending_age_days`, `fill_attempts` | as computed |
| `bar_date` | `:120` |
| `bar_open`, `entry_price`, `slippage_pct` | `:129-130` |
| `shares`, `stop_loss`, `take_profit`, `stdev_fallback` | as booked |
| `fill_source` | `:168` |
| `outcome` | see below |

`outcome` values:

| Outcome | Branch |
|---|---|
| `demoted_to_queue` | `:82-86` (with `queue_signal_logged`, §2.2) |
| `discarded_already_open` | `:88-91` (§2.2) |
| `dropped_aged` | `:93-100` |
| `retained_no_data` / `dropped_no_data` | `:105-118` |
| `retained_bar_not_today` | `:120-127` |
| `filled` | `:129-231` |
| `retained_exception` / `dropped_exception` | `:232-243` |

A **pass-level** record is also written, with `pending_seen` (`len(pending)`
at `:65`) and `open_count_before`. A pass with no pending writes a pass record
with `pending_seen: 0`. It is not silent.

**A2 derivation (observer only, never in the loop).**
`same_session_fill` is true when `bar_date` equals the admission's decision
date and the decision timestamp is at or after that session's 09:30
`America/New_York` open, converted to UTC with `zoneinfo`. The rule and its
basis are recorded as named fields: `session_open_basis: "NYSE 09:30
America/New_York"`, plus `decision_after_open_seconds`. The DST change on
1 Nov is covered by a control (§7).

### 4.5 Heartbeat — one per process run

Fields: `decision_path`, `cycle_id`, `run_kind`, `started`, `completed`,
`report_completed` or `monitor_completed`, `lineage_complete`,
`repository_commit`, manifests (§6.2), `write_failures`, and counts:

- report: `fill_pass_written`, `pendings_seen`, `fill_records`,
  `promotion_pass_written`, `signals_seen`, `admission_records`
- monitor: `promotion_called`, `promotion_pass_written`, `admission_records`

Coverage is complete only when every count reconciles:

- `fill_records == pendings_seen`
- `admission_records == open_trade calls` (scan signals plus candidates whose
  `outcome` is `promoted` or `dropped_open_trade`)
- `promotion_pass_written` equals whether `promote_queue` was called

---

## 5. Linkage: natural key, not a new field in production state

**Recommended: join on `(symbol, signal_date)`.**

- `open_trade` refuses a second pending for a symbol (`:631-633`), so at most
  one pending per symbol exists at any time.
- A `fill` record joins the **latest earlier** `admission` record with
  `outcome: pending` for the same `(symbol, signal_date)`.
- The inspection requires **exactly one** such join, with no other `filled`,
  `dropped_*` or `discarded_*` fill for that key in between.
- The trade record's `signal_date` is the pending's `date` (`:181`), so a trade
  joins its fill on the same key, plus `entry_date == fill run date`.

**Rejected alternative: carry an `admission_id` in the pending and the trade.**
That writes a new key into `pending_signals.json` and `virtual_trades.json`.
It would break the invariant that the recorder never mutates production state
(the monitor recorder's control 26). It would also change the trade schema
that `export_csv`, `build_dashboard.py` and `sample.py` read. Raise again only
if the natural key turns out to be ambiguous (§9 Q1).

---

## 6. Isolation and registration

### 6.1 Invariants (same five as the monitor recorder)

1. No market data is acquired. No new `load_stock` or `get_live_price` call;
   only values already computed are recorded.
2. No production state is mutated. `pending_signals.json`,
   `virtual_trades.json`, `signal_queue.json`, `queue_ranked.json` and
   `queue_events.jsonl` must be byte-identical with and without the recorder.
3. Exception propagation is unchanged, including inside `open_trade`, whose
   raise must still reach `generate_report`.
4. Operator output is unchanged: no new `print`. `run_monitor.sh` builds its
   Telegram message by grepping stdout, so this is load-bearing.
5. A recorder failure is contained: it shows in a health line and never stops
   trading.

### 6.2 Registration scope grows to three files

`tools/verify_recorder_isolation.py` compares one before/after pair today.
v1 needs three pairs with one registration table each:

| File | Change | Registered |
|---|---|---|
| `engine/tracker.py` | store, capture assignments in `open_trade`, `promote_queue`, `execute_pending_signals` | additions only |
| `daily_report.py` | reset in `generate_report`; `try/finally` flush in `__main__` | additions + 1 replacement |
| `monitor_trades.py` | reset; one flush call in the existing `finally` | additions to an already-registered diff |

Pre-activation fixtures:
`tests/fixtures/{tracker,daily_report}_preadmission.py`. The existing
`tests/fixtures/monitor_trades_preactivation.py` stays the base for
`monitor_trades.py`. Its registration table gains entries; none of its
existing digests change.

Manifests: same three-way split (decision, classification, observation).
`engine/admission_observer.py` joins the observation manifest. `tracker.py`
and both entrypoints are already in the decision manifest.

---

## 7. Planned controls

Same harness style as `tests/run_monitor_observer_controls.py`: real code,
scratch `logs/`, anti-vacuity first.

**Isolation**
- State files byte-identical, recorder on vs off, over a scripted report run
  and a monitor run (all five files, §6.1 item 2).
- stdout byte-identical.
- An `open_trade` raise still propagates; the record is incomplete, not
  absent.
- Recorder unimportable or unwritable: trading continues and the health line
  is written.
- No new data-feed call (patched `load_stock` / `get_live_price` call counts
  unchanged).

**Capture: one control per outcome**
- `admission`: `pending`, `queued` (both `queue_signal_logged` values),
  `duplicate_open`, `duplicate_pending`.
- `promotion_pass`: each early return; `unranked_fallback`; and each
  candidate outcome, including an unexamined tail after `break`.
- `fill`: all nine outcomes in §4.4, including both silent discards (§2.2).

**Coverage**
- A signal with no admission record makes the run incomplete.
- `promote_queue` called with no pass record makes the run incomplete.
- A pass record with a missing `fill` entry makes the run incomplete.

**Linkage**
- Exactly one join succeeds.
- Zero joins, two joins, or an intervening drop each fail.

**A2 derivation**
- Admission at 16:10 UTC with same-day fill: `same_session_fill` is true.
- Admission at 21:01 UTC: false.
- Admission at 13:30 UTC on Mon 2 Nov (after DST ends, 08:30 ET): false.

**Anti-vacuity**
- A harness that cannot fail is detected.

**Inspection: `tools/inspect_admission_cycles.py`, mutation-tested like v7**
- **State-diff reconciliation**, independent of the recorder's own claims:
  - every pending **added** between two commits has an `admission` record
    with `outcome: pending`;
  - every pending **removed** has a `fill` record;
  - every trade **added** has a `filled` record.
- Cross-checks:
  - a promoted candidate agrees with its admission record;
  - a monitor run that saved state (monitor heartbeat
    `trade_state_save_completed: true`) has `promotion_called: true` and a pass
    record.
- Everything the v7 tool established: append-only prefix, cycle identity
  uniqueness, exact key sets, finite numbers, lineage, manifests and the time
  window.

---

## 8. Activation constraints

- The Finviz screener is broken, so R1 cannot occur live until it recovers.
  R1 is therefore covered by controls only. Activation is not blocked: R2 and
  R3 still run off the queue.
- Activation goes by the same route as `5fd13e9`: squash on `main`, one parent;
  the revert is tested before the push; suites rerun on the VPS; deploy on a
  weekday 05:00–13:00 UTC, outside the cron times.
- First inspection: the next report and monitor cycles, with the new tool,
  alongside `inspect_monitor_cycles.py`.

---

## 9. Open questions for review

| # | Question | Draft position |
|---|---|---|
| Q1 | Natural key `(symbol, signal_date)` or a carried `admission_id`? | Natural key. The id mutates production state (§5). |
| Q2 | Is amending the accepted monitor recorder's registered diff acceptable? | Yes, if existing digests are unchanged and only additions are registered. The alternative, a flush from inside `monitor_observer.flush`, changes a reviewed module instead. |
| Q3 | Should the observer duplicate fields already in `queue_events`? | Yes, so each record is self-contained. The inspection cross-checks the overlap; `queue_events` stays untouched. |
| Q4 | Should A2 be derived in the observer or only in the inspection tool? | Observer, with the basis named. The inspection recomputes it independently. |
| Q5 | Does a manually *admitted*, scheduled-*filled* trade (AMP) belong in `clean_v3`? | Not decided here. Recorded via `run_kind`; policy belongs to the unfreeze review. |
| Q6 | The 13:30 scan reads a session bar about one minute old (`signal['date']` = today). Is that in scope? | No. Recorded through `signal_date` and `timestamp`; strategy question. |

---

## 10. Sequence

1. External review of this draft.
2. Implement on branch `recorder/admission-v1`: fixtures first, then
   capture, observer, controls, inspection tool and mutation test.
3. Review bundle (same structure as monitor recorder v7).
4. Activation, after acceptance and on your go-ahead.
