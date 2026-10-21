# Admission capture v1 — design (r5, accepted for implementation review)

Status: **r5 accepted for implementation review (2026-10-22); not authorised
for activation.** No code exists. Ares stays FROZEN. Review history: r1–r4
DRAFT/HOLD, r5 accepted; bundles `review_bundle_admission_capture_v1`–`v5`. This
is step 3 of the plan in `STATUS.md`. The evidence behind it is
`archive/AUDIT_2026-10-20_admission_routes.md` (cited below as "the audit").
Line references are to `e4dd97c`.

### Revision r2 — response to the r1 review (2026-10-20)

| r1 finding | Resolution | Where |
|---|---|---|
| 1. Natural key not proven unique; "latest earlier" hides ambiguity; `demoted_to_queue` missing as a terminal | Pending **episode** model: one open episode per symbol, explicit terminal outcomes, exactly-one match or FAIL; the key is a fail-closed candidate key, not an identity | §5 |
| 2. Decision-time capture had no mechanism under the stated restrictions | Restrictions restated as an enforceable statement whitelist that permits exactly one clock read; decision time is an **interval** bracketing each decision; A2 is three-valued | §3.2, §3.3, §4.4 |
| 3. `queue_signal_logged` conflated insertion, logging attempted and logging succeeded | Split into queue insertion, queue save and queue-event write outcome; write success captured **after** the write, failure in the existing `except` | §4.2 |
| 4. No persistence semantics | Every record separates selected → in-memory mutation → save attempted → save completed; outcome is "persisted" only on save completed. Two new non-atomic save findings | §2.2.1, §4.2–§4.4 |
| 5. Coverage by record-count equality | Each call site has **expected / reached / completed** states captured on both sides of the call; a call-site inventory in the verifier rejects any new caller | §4.5, §6.2 |
| Audit script not fail-closed; A2 used commit time | Reproduction v2: fail-closed, event time as decision time, R1 bounded, 21 controls, 15 mutations. Corrected figures in the audit | bundle `repro/`, audit §2–§3 |
| Q2: digests alone insufficient | The monitor entrypoint's registered replacement is **re-registered**, not left unchanged; its containment controls are rerun | §6.2 |

### Revision r3 — response to the r2 review and source review (2026-10-21)

Every source finding was reproduced against `e4dd97c` before this revision.

| r2 finding | Resolution | Where |
|---|---|---|
| 1. Cross-recorder cycle pairing undefined | Each recorder keeps its own `cycle_id`. Pairing uses the monitor store's existing `cycle_started_at`; producer, owner and propagation are named, and a missing peer is INCONCLUSIVE | §3.4 |
| 2. Admission "completed" was the branch marker | `branch`, save facts and `returned` are separate fields; completion is `returned` only; a failed save is its own call state | §4.2, §4.5 |
| 3. Episode closure conflated pending removal with trade persistence | Each episode has two independent facts, **pending end** and **trade link**. Vanished fill and trade-while-pending are named combinations | §5 |
| 4. A2 bracket did not name the event it bounds | Two named events with named owners: admission (`open_trade`) and price observation (scan or validation). The mixed `[t_validate_start, t_exit]` is removed | §3.3, §4.4 |
| Source 1. Fill exception after `trades.append` keeps the trade and the pending | New finding §2.2.1; `trade_appended` and `executed_marked` captured; combination represented, behaviour unchanged | §2.2.1, §4.4, §5 |
| Source 2. A queued candidate can be evicted by its own insertion | `evicted[]` drives the derived queue outcome; new `inserted_then_evicted` | §2.2.1, §4.2 |
| Audit: `promoted` event is post-admission; slot proximity is not a start | Audit v3: the event is an upper bound only; the lower bound is the process start, verified from the cron log when supplied, otherwise labelled `schedule_assumed`. 31 controls, 22 mutations | audit correction block |
| Stale §2.1 summary | Rewritten to the corrected audit | §2.1 |

### Revision r4 — response to the r3 review (2026-10-21)

All three findings were confirmed against `e4dd97c`. The truncation was
reproduced with the `save_pending` body on a scratch file: `TypeError` raised
and an unparseable prefix left on disk. r4 changes one principle rather than
patching three tables: **the recorder records what happened in memory and
whether each save returned. It never claims what is on disk. Disk effects are
read by the inspection from the committed state files.**

| r3 finding | Resolution | Where |
|---|---|---|
| 1. A failed save does not establish `unchanged_on_disk` | `unchanged_on_disk` removed. A save fact is `not_attempted` \| `completed` \| `raised` and implies nothing about the file. Disk membership comes from the run's commit; an unparseable file is `unreadable` and every claim touching it is INCONCLUSIVE | §3.5, §4.4 |
| 2. Trade-effect table not exhaustive (`trade_appended`, not `executed_marked`, pending saved, trades save failed) | One-dimensional effect enums replaced by two independent per-file checks, each a total function over (intent, observed, owning save). The episode and the trade link are read from the observed dimension only. The reviewer's case is `trade_not_persisted` with the pending retained: episode open, no link. A control enumerates every combination | §3.5, §4.4, §5, §7 |
| 3. Queue outcomes overlap on self-eviction plus save failure | Split into `queue_memory` (`kept` \| `inserted_then_evicted` \| `already_queued`, from memory facts only) and observed queue membership from the commit. `save_failed` is no longer an outcome value. A non-completed owning save is the only admissible explanation of a mismatch, and only when the observed file equals the baseline | §3.5, §4.2 |
| Audit: a cron launch near the commit does not identify the producing process | Audit v4: a cron match changes the lower-bound **basis** to `cron_matched`, with `run_association: assumed`. It never yields `yes_established`, which is removed. Historical A2 is bounded and conditional, whatever the cron log shows | audit correction block |

### Revision r5 — response to the r4 review (2026-10-22)

Both findings were checked against `e4dd97c`. Finding 1 was probed with the
unmodified `expire_queue` and `maintain_queue` bodies
(`repro/probe_queue_handoff.py`, result in `results_v5.txt`).

| r4 finding | Resolution | Where |
|---|---|---|
| 1. "Only the owning save can persist a mutation" is false for queue maintenance | **Accepted: the statement was false.** `maintain_queue` does not reload; it saves (`:485`, `:488`) a list derived from `expire_queue`'s return value (`:434`). **The failure path in the finding is not reachable at `e4dd97c`:** `save_queue(active)` (`:379`) is outside the per-entry `try` (`:367-377`), so a raise at any point in it, including on close after a complete dump, leaves `expire_queue` without returning and `maintain_queue` never reaches `:485` (probe H1, both failure points). r5 replaces the single-owner rule with a registered **save chain**: the owning save plus the one registered carrier. The carrier is reachable only if the owner completed or was not attempted. The verifier now checks the data flow of every save site, so a new hand-off fails review | §3.5, §6.2, §7 |
| 2. Existing trade membership treated as a new trade link | **Accepted.** The trade baseline was fixed at "absent". r5 reads it from the parent commit like every other baseline. Trade membership is a **count** per key, the intent is baseline + `trade_appended`, and a link is an observed **increment** of exactly one. A `discarded_already_open` entry over an existing trade is consistent and adds no link; it closes an episode that already holds its link from the earlier entry | §3.5, §4.4, §5, §7 |
| Found while checking finding 1: silent queue removal in expiry | `expire_queue`'s `except Exception: pass` (`:376-377`) drops an entry from `active` with no event, for example a malformed `date_added` (probe H2). Recorded as §2.2.3 and captured; not fixed | §2.2.3, §4.2 |
| Found while checking finding 1: incomplete writer inventory | r4 listed seven save sites. There are thirteen: `save_trades` also at `:947`, `:1142` and `monitor_trades.py:276`, and `save_ranked` at `:436`, `:488`, `:610`. None of these six changes trade or queue membership, but all are now in the inventory, and trade key fields must be unchanged across commits | §3.5, §6.2 |
| Verification: attachments arrived as file-not-found placeholders | The reproduction inputs also ship as one self-contained file, `repro_pack.py`, which writes the audit, tests, mutation script, probe and evidence to a scratch directory and runs them | README |

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
| A1 | Routes are recoverable only by inference from git, and not always: 20 of 21 admitting runs resolve; HAFN's is ambiguous (two episodes for one key) | Explicit record per admission (§4); episodes (§5) |
| A2 | 11 fills were booked at the open of the session in which the pending was created, with the open before that admission **if** each admitting run started at its scheduled slot. 0 are established. A cron-log match would change only the basis of the lower bound, not this: it cannot show which process made the commit (audit v4) | Record admission and observation brackets, plus fill bar date and open (§3.3, §4.4) |
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

### 2.2.1 New in r2: two non-atomic saves

Found while specifying persistence (§4). Recorded, fixed nowhere.

- **Fill pass** (`tracker.py:248-249`): `save_pending` runs before
  `save_trades`. If `save_trades` raises after `save_pending` succeeded, a
  filled pending is removed from disk while its trade is never written: the
  fill vanishes. The exception propagates, so the run fails visibly, but the
  state on disk has lost the position.
- **Promotion pass** (`tracker.py:607-610`): `open_trade` has already saved
  the pending (`:657`). If `save_queue` then raises, the symbol is both
  pending and queued. The next pass drops it as "already held or pending",
  with an event whose reason is misleading.

### 2.2.2 New in r3, from the source review: two partial outcomes

Both reproduced by the reviewer with controlled dependencies and confirmed by
reading `e4dd97c`. Recorded, fixed nowhere.

- **Fill exception after the trade is appended** (`tracker.py:226-231`).
  `trades.append(trade)` and `open_count += 1` run before the `EXECUTED`
  print and `executed.append(sig)`. An exception in between (the print's
  formatting, a closed stdout) enters the retry handler with the trade still
  appended. Both saves then run: the trade **and** the pending persist, with
  `fill_attempts` incremented and a `fill_retry` event. The next pass
  discards the pending as already open. At `MAX_FILL_ATTEMPTS` the same path
  books the trade **and** logs `fill_dropped`.
- **Queued candidate evicted by its own insertion** (`tracker.py:343-354`).
  `queue_signal` appends, logs `queued`, then evicts the tail. A new entry
  with lower confluence can be the one evicted. The save completes and the
  candidate is absent from the saved queue. On the demotion path
  (`tracker.py:82-86`) this also removes the pending, so the signal is gone.

### 2.2.3 New in r5: silent removal in queue expiry

Found while checking the r4 review's finding 1, reproduced by
`repro/probe_queue_handoff.py` (H2). Recorded, fixed nowhere.

`expire_queue` (`tracker.py:366-377`) wraps each entry's age check in
`try: ... except Exception: pass`. An entry whose `date_added` does not parse
as `%Y-%m-%d`, or that lacks `date_added` or `symbol`, or whose `[Expired]`
print raises, is not appended to `active`. `len(active) != len(queue)` then
saves the shorter list (`:378-379`). The entry leaves the queue with no event
and no print. It is the queue-side counterpart of §2.2. `expire_queue` runs
from two sites: `maintain_queue` (`:434`, report) and `check_open_trades`
(`:953`, report, only when a slot is free; the return value is only printed).

Capture (additions only): one registered statement in that `except` appends
`(len(active), 'expiry_parse_failed', None)` to
`_ADMISSION_EVAL['queue_events']`, the same list and tuple shape as §4.2. It
carries `len(active)`, not a symbol, because `q['symbol']` may be the failing
expression. The entry's position in the loaded queue is `len(active)` plus the
`expired` and `expiry_parse_failed` captures already made in the same call.
The inspection resolves that position against its own fold of the queue at
that call, and the fold (§4.2) then covers the removal.

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
  `engine/tracker.py`, alongside the existing `_LAST_EVAL`. Capture sites are
  restricted to the statement forms in §3.2.
- **Observer module.** A new `engine/admission_observer.py` does all
  derivation, validation and serialisation after the run. Its `flush(store)`
  never raises and is idempotent.
- **Flush in each entrypoint's `finally`.**
  - `daily_report.py` `__main__`: a new `try/finally` (one registered
    replacement, as `monitor_trades.py` had).
  - `monitor_trades.py`: one added call inside the existing `finally`, after
    the monitor observer flush. **This changes the registered diff of an
    accepted recorder** (§9 Q2).
- **Invocation reset.** At the start of `generate_report()` and `monitor()`:
  `_ADMISSION_EVAL.clear()` then `.update(_new_admission_store(token))`,
  the same swap-in contract as the monitor recorder. `_new_admission_store`
  is a registered function in `tracker.py` returning a literal dict plus one
  clock read. Identity is in §3.4.

### 3.1 Why capture inside `tracker.py`, not at the call sites

- **Inside `open_trade`:** every call produces a record, whoever the caller
  is. A future third caller cannot bypass it.
- **At the call sites:** `daily_report.py` would need a new import of the
  store. The `promote_queue` context (`source`, `use_live`, the checks) is
  only visible inside `promote_queue`, which is in `tracker.py` anyway.

The cost is a registered diff on `tracker.py`, the file both paths share
(§6).

### 3.2 Permitted capture statements (enforced, not descriptive)

r1 said capture sites do "no imports, formatting, arithmetic or I/O". That was
not even true of the accepted monitor recorder, which registers
`len(open_trades)`, `dict(params)` and `+= 1`. r2 replaces it with a
whitelist that `verify_recorder_isolation.py` checks on every registered
statement's AST:

| Form | Example | Constraint |
|---|---|---|
| Store assignment | `_ADMISSION_EVAL[k] = v`, `obs[k] = v` | target rooted at the store or a record created by it; `v` is a name, constant, subscript or attribute read already bound at that point |
| Record creation | `obs = {...}` then `_ADMISSION_EVAL[k].append(obs)` | literal keys; values as above |
| Counter | `_ADMISSION_EVAL[k] += 1` | integer constant only |
| Builtin read | `len(x)`, `dict(x)`, `list(x)` | argument a bound name; copies only |
| **Clock read** | `obs['t_enter'] = _time.time()` | the only permitted call outside the builtins above; result never read by decision code |
| Import | `import time as _time` | module level, once |
| Reset | `_ADMISSION_EVAL.clear()`, `_ADMISSION_EVAL.update(_new_admission_store(token))` | first statements of the two entrypoint functions only |

Anything else in a registered statement is a verifier FAIL: method calls on
production objects, comparisons, branches, formatting, other calls.
`time.time()` takes no input from decision state, has no side effect, and
returns a float that is converted to an aware UTC timestamp **in the
observer**, never at the capture site.

### 3.3 Decision time is an interval

A single "timestamp at the decision" cannot be honestly defined: the price is
observed inside `get_live_price` / `load_stock`, which are not instrumented
(invariant 1). r3 records a **bracket** around each **named event**. Every
time-dependent claim names the event it is about, and uses only that event's
bracket, from that event's owning record.

| Event | Meaning | Owner record | `t_start` | `t_end` |
|---|---|---|---|---|
| `E_obs` R1 | the scan observes the signal price | report heartbeat | `daily_report.py`, immediately before `scan_universe` (`:84`) | immediately after it returns |
| `E_obs` R2/R3 | validation observes the live price | promotion candidate | immediately before `_validate_queued` (`:569`) | immediately after it returns |
| `E_adm` | the pending is created (appended and saved) | admission | first statement of `open_trade` | at `returned` (§4.2) |
| `E_fill` | the fill decision | fill entry | top of the loop body (`:81`) | before `continue` / end of body |
| pass | the pass itself | pass record | function entry | each return |

`E_obs` precedes `E_adm` for the same signal; the brackets are never merged.
A bracket with no `t_end` (the call raised) is open-ended and any claim that
needs it is `unknown`.

### 3.4 Cross-recorder identity (monitor path)

The two recorders do **not** share a `cycle_id`. The monitor store leaves
`cycle_id` as `None` on purpose (`monitor_trades.py:50`) and its serialiser
mints one at flush (`monitor_observer.py:582`). Changing that would alter a
reviewed contract, so r3 does not.

| Item | Specification |
|---|---|
| Admission identity | own `cycle_id`, minted by `admission_observer.flush`; own `invocation_token` |
| Pairing key | the monitor store's `cycle_started_at` (microsecond ISO UTC) |
| Producer | `_new_invocation_store` (`monitor_trades.py:51`), already registered; written once at reset |
| Owner | the monitor recorder. Already present in every monitor heartbeat (`monitor_observer.py:624`) |
| Propagation | the monitor `finally` calls `admission_observer.flush(_ADMISSION_EVAL, peer=_MONITOR_EVAL)`. The observer only **reads** `peer.get("cycle_started_at")` and `peer.get("invocation_token")`, inside its own `try`, and writes them as `peer_cycle_started_at` / `peer_invocation_token` in the admission heartbeat |
| Report path | `peer: null`; no cross-check is claimed |

Inspection pairs the admission heartbeat with **exactly one** monitor
heartbeat in the same commit whose `cycle_started_at` is equal:

| Result | Verdict |
|---|---|
| exactly one | cross-check runs |
| none (monitor recorder unavailable or its flush failed: `_MONITOR_EVAL` is a plain dict in `monitor_trades.py`, so the key exists, but no heartbeat was written) | `peer_unavailable`: INCONCLUSIVE, never PASS |
| `peer_cycle_started_at` null or missing | `peer_unidentified`: INCONCLUSIVE |
| more than one | FAIL |

### 3.5 Persistence: facts in memory, effects from the committed state (r4, r5)

**Why the recorder cannot know the disk state.** Every production save is
`open(path, 'w')` followed by `json.dump` (`tracker.py:44-47`, `:258-264`,
`:314-317`). The open truncates first. A raise can therefore leave the old
file (open failed), an unparseable prefix (dump failed, as reproduced), or the
complete new file (close failed after the dump). Only reading the file
distinguishes them. The recorder must not read files at capture sites, so
the inspection reads them.

**Save fact.** Each registered save site has two markers (attempted before
the call, completed after it). The observer derives one value:

| `save` | Markers |
|---|---|
| `not_attempted` | neither |
| `completed` | both |
| `raised` | attempted only |

None of these values implies anything about the file.

**Observed state.** The cron wrappers run `git add logs/` and commit after
the Python process exits, whatever its exit status (`run_ares.sh:16-31`,
`run_monitor.sh:7-35`, no `set -e`). The only other step in between,
`build_dashboard.py`, writes only `psi_state.json`. So the run's commit holds
the four state files as the run left them. For each file the inspection
reads, in that commit:

| Observed | Meaning |
|---|---|
| membership | the file parses; membership by key: pending `(symbol, date)` and queue `symbol` as present/absent; trade `(symbol, signal_date, entry_date)` as a **count** (r5), because `virtual_trades.json` is a list that keeps closed trades |
| `unreadable` | the file does not parse, or is missing where the parent had it |

A truncated `json.dump` of a list never parses, because its closing `]` is
the last byte written. So a parseable file is always a complete dump, either
the old one or the new one.

**Writers and save chains (r5).** r4 said every writer reloads its file, so
a mutation reaches the disk only through the save in the same call. The r4
review showed that is false for one writer. The complete inventory at
`e4dd97c` (`grep` for `save_*(`, confirmed by the verifier's AST pass, §6.2):

| File | Save site | Function | List saved comes from |
|---|---|---|---|
| pending | `:248` | `execute_pending_signals` | `load_pending()` `:65`, same call |
| pending | `:657` | `open_trade` | `load_pending()` `:630`, same call |
| trades | `:249` | `execute_pending_signals` | `load_trades()` `:75`, same call |
| trades | `:947` | `check_open_trades` | `load_trades()` `:814`, same call |
| trades | `:1142` | `check_shadow_trades` | `load_trades()` `:1090`, same call |
| trades | `monitor_trades.py:276` | `__main__` path | `load_trades()` `:123`, same call |
| queue | `:356` | `queue_signal` | `load_queue()` `:323`, same call |
| queue | `:379` | `expire_queue` | `load_queue()` `:363`, same call |
| queue | `:485` | `maintain_queue` | **`expire_queue()`'s return value `:434`: hand-off, no reload** |
| queue | `:609` | `promote_queue` | `load_queue()` inline, same statement |
| ranked | `:436`, `:488` | `maintain_queue` | constant / derived from `:434` |
| ranked | `:610` | `promote_queue` | `load_ranked()`/`load_queue()` `:542-544`, same call |

Only `:226` adds to the trades list; the other three trade writers change
status and exit fields only. The inspection checks that: a trade's key fields
are identical in every commit after the one that added it, else FAIL.

So the unit is a **save chain**, not a single save. A mutation's chain is its
owning save (in the same call) followed by every registered carrier: a later
save of a list derived, without a reload, from a value the owning call
returned. At `e4dd97c` there is exactly one carrier: `expire_queue` `:379`
→ `maintain_queue` `:485`. (`check_open_trades` also calls `expire_queue`,
at `:953`, but only prints the result, so it is not a carrier.)

**When a carrier is reachable.** `save_queue(active)` is outside the
per-entry `try` (`:367-377`). A raise anywhere in it, at `open`, during the
dump or on close after a complete dump, propagates out of `expire_queue`;
`maintain_queue` has no handler and `daily_report.py:63` none either, so
`:485` is never reached and the report ends at its entrypoint `finally`.
`repro/probe_queue_handoff.py` runs the unmodified bodies and shows this for
both failure points (H1). So the carrier runs only after the owner
`completed` or was `not_attempted` (no expiry; then `active` is the loaded
list unchanged). It re-saves the owner's result plus `maintain_queue`'s own
removals, which have `:485` as their owning save.

The verifier enforces both premises (§6.2): every save site's argument must
trace to a `load_*` in the same function or to a registered carrier edge; and
the owner of a carrier edge must have no `try` around its save. A new hand-off,
or a handler added around `:379`, fails review.

**Per-file check, one total function.** For each key a run touched:
- `baseline` = its membership before the owning call, read from the
  **parent commit** for every file (r5: no fixed baselines anywhere),
  folded with earlier recorded mutations in the run whose save chain
  `completed`. If an earlier save chain for the same file `raised` and the
  run went on, the disk after it could be either dump: the baseline is
  ambiguous and the key is **INCONCLUSIVE** `baseline_ambiguous`.
- `intent` = the membership the recorded in-memory mutation leaves.
- `save` = that mutation's save chain, reduced to one value: `completed` if
  any save in the chain completed; otherwise `raised` if one was attempted;
  otherwise `not_attempted`. A carrier attempted after an owner that raised
  is impossible by the premise above; if the recorder shows one, the check is
  **FAIL** `carrier_after_raise`. If the owner completed and the carrier
  raised, the expiry is already in the owner's complete dump. For the
  carrier's own removals the file then holds that dump (`= baseline`), the
  carrier's dump (`= intent`) or a prefix (`unreadable`); the table below
  covers all three.

A parseable file is the old or the new complete dump, so `observed` must
equal `intent` or `baseline`:

| observed | vs intent / baseline | owning `save` | Result |
|---|---|---|---|
| `unreadable` | — | any | **INCONCLUSIVE** `<file>_unreadable` |
| membership | = intent | `completed` or `raised` | consistent |
| membership | = intent | `not_attempted` | consistent only if intent = baseline (no change); otherwise **FAIL** |
| membership | ≠ intent, = baseline | `completed` | **FAIL** (contradiction) |
| membership | ≠ intent, = baseline | `raised` or `not_attempted` | consistent, incident `<file>_not_persisted` |
| membership | ≠ intent, ≠ baseline | any | **FAIL** (no dump could produce it) |

For trades, `=` and `≠` compare counts (r5). With `baseline = n` and
`trade_appended`, the intent is `n + 1`; without it, `n`. An existing trade is
therefore `observed = baseline = intent`: consistent, and not a new trade.

The rows are disjoint, and they cover every combination of their inputs; control C-ENUM (§7)
checks this mechanically. Two runs' heartbeats in one commit, as with a manual run
overlapping a cron run, make the baseline ambiguous: every key both runs
touched is **INCONCLUSIVE** `concurrent_runs`.

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
| `cycle_id` | minted by `admission_observer.flush` (§3.4) |
| `invocation_token` | admission store, set at reset |
| `decision_path` | `report` \| `monitor` (set by the flushing entrypoint) |
| `run_kind`, `run_kind_detect` | `sample.fill_context()`, called by the observer at flush (same process, same environment): `scheduled\|manual`, `env\|tty_inferred` |
| bracket fields (`t_*`) | clock reads at the capture site (§3.3); serialised by the observer as tz-aware UTC ISO strings |
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
| `t_enter` | first statement of `open_trade` (`E_adm` start, §3.3) |
| `branch` | `duplicate_open` \| `queued` \| `duplicate_pending` \| `pending`: the branch **taken**, set on entering it. Not a completion marker |
| `pending_appended` | `pending` only: set after `pending.append` (`:635-656`) |
| `pending_save_attempted`, `pending_save_completed` | before and after `save_pending` (`:657`) |
| `queue_insert` | `queued` only, set inside `queue_signal`: `inserted` (`:343`) \| `already_queued` (`:325-327`) |
| `queue_event_write` | `queued` + `inserted` only: `written` \| `write_failed`, from the event log below |
| `queue_save_attempted`, `queue_save_completed` | before and after `save_queue` (`:356`); absent on `already_queued` |
| `evicted[]` | symbols evicted by this insertion, appended inside the eviction loop (`:350-353`) |
| `returned`, `t_return` | the return value, set as the **last statement before each `return`** (`:623`, `:627`, `:633`, `:658`); `t_return` ends `E_adm`. Absent if the call raised |

The two `duplicate` returns (`:623`, `:633`) are separate outcomes because
they have different causes.

**Call state (observer), from separate facts:**

| State | Facts |
|---|---|
| `returned` | `returned` set. The only normal completion |
| `raised_in_pending_save` | `branch: pending`, `pending_save_attempted`, not `pending_save_completed`, no `returned` |
| `raised_in_queue_save` | `branch: queued`, `queue_save_attempted`, not completed, no `returned` |
| `raised_elsewhere` | no `returned`, and neither save failure above; the last fact set is recorded |

A `raised_*` record is a complete, observed failure, not a missing record.

**Queue outcome (`queued` branch and `demoted_to_queue`): two dimensions
(r4).** r3's single `queue_outcome` mixed a memory fact (eviction) with a
disk claim (save), and the two overlapped. r4 separates them.

`queue_memory` comes from memory facts only and never mentions the disk:

| `queue_memory` | Facts |
|---|---|
| `kept` | `inserted`, symbol **not** in `evicted[]` |
| `inserted_then_evicted` | `inserted`, symbol in `evicted[]` (§2.2.2) |
| `already_queued` | early return at `:325-327`; no mutation |

`queue_save` is the owning save `:356`: `not_attempted` \| `completed` \|
`raised` (§3.5). `already_queued` has no owning save and its intent is
"no change".

**Persisted admission (inspection, §3.5):**
- A `pending` admission is persisted when the run's committed
  `pending_signals.json` holds its key.
- A `queued` admission is persisted when the committed `signal_queue.json`
  holds its symbol.
- The intent for the queue is the end-of-run membership, folding in later
  recorded queue mutations in the same run (`evicted`, `expired`,
  `expiry_parse_failed` (r5, §2.2.3), `dropped`, `promoted`, each with its
  own save chain, §3.5). Each `_log_queue_event` call is
  captured with `(symbol, action)` whether or not its write succeeded (below),
  so the fold uses the attempts, not the event file.
- `inserted_then_evicted` with `queue_save: raised` is no longer two
  outcomes at once. It is one memory value checked against one observed
  value. The baseline is read from the parent commit fold (§3.5). `inserted`
  says the reloaded queue (`:323`) did not hold the symbol, so the fold must
  say absent too; otherwise **FAIL** `baseline_memory_mismatch`. The intent
  is absent:
  - absent: consistent;
  - present: FAIL (neither dump holds it);
  - `unreadable`: INCONCLUSIVE.

**Queue-event write outcome.** `_log_queue_event` swallows write errors
(`:287-291`), so control flow cannot show success. Two registered statements
inside it append `(symbol, action, ok)` to `_ADMISSION_EVAL['queue_events']`:
one **after** the `with` block closes (`ok: true`, so the close and flush
succeeded), one in the existing `except` (`ok: false`). The observer
matches each `queued` record to its event by order and `(symbol, action)`;
an unmatched record is `queue_event_write: unobserved` and the record is
incomplete. Every `_log_queue_event` call is covered, including those from
`maintain_queue`, so the heartbeat also carries `queue_event_write_failures`.

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
| `queue_save_attempted/completed`, `ranked_save_attempted/completed` | around `:609-610`; absent when nothing was removed |
| `t_enter`, `t_exit`, `return_reason` | pass bracket (§3.3) |

Each `candidates[]` entry:

| Field | Raw value |
|---|---|
| `symbol` | |
| `queued_at`, `price_at_signal` | `q['date_added']`, `q['price_at_signal']`, the **original** scan values (A3) |
| `held` | `:562` |
| `valid`, `reason`, `checks` | `_validate_queued` return value, with `checks.live` as the decision price |
| `t_validate_start`, `t_validate_end` | bracket around `_validate_queued` (§3.3) |
| `open_trade_expected` | set immediately before the call at `:595` (§4.5) |
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
| `t_enter`, `t_exit` | entry bracket (§3.3) |
| `trade_appended` | set immediately after `trades.append` (`:226`), on **any** later outcome |
| `executed_marked` | set immediately after each `executed.append(sig)` (`:85`, `:90`, `:99`, `:113`, `:231`, `:239`) |
| `outcome` | see below |

`outcome` values:

| Outcome | Branch |
|---|---|
| `demoted_to_queue` | `:82-86`; `queue_insert`, `queue_event_write` and the queue save as in §4.2 (`already_queued` is the silent case, §2.2) |
| `discarded_already_open` | `:88-91` (§2.2) |
| `dropped_aged` | `:93-100` |
| `retained_no_data` / `dropped_no_data` | `:105-118` |
| `retained_bar_not_today` | `:120-127` |
| `filled` | `:129-231` |
| `retained_exception` / `dropped_exception` | `:232-243` |

A **pass-level** record is also written, with `pending_seen` (`len(pending)`
at `:65`), `open_count_before`, and the two saves at `:248-249`:
`pending_save_attempted/completed`, `trades_save_attempted/completed`. A
pass with no pending writes a pass record with `pending_seen: 0` and no saves
(the early return at `:66-67`). It is not silent.

**Effect: two independent per-file checks (r4).** r3 derived one-dimensional
`pending_effect` / `trade_effect` enums from save facts. The reviewer showed
they were neither sound (`unchanged_on_disk`) nor exhaustive. r4 derives
nothing about the disk in the observer. Each fill entry has two memory
intents, each checked by the §3.5 function against its own file:

| Dimension | Baseline (parent commit, §3.5) | Intent (memory) | Save chain | Observed (commit) |
|---|---|---|---|---|
| pending | membership of `(symbol, signal_date)`; must be present, because the entry iterates the loaded list (`:65`), else **FAIL** `baseline_memory_mismatch` | `executed_marked` → absent, else present | pass `pending_save` (`:248`) | `present` \| `absent` \| `unreadable` |
| trade | **count** `n` of `(symbol, signal_date, entry_date = run date)` (r5; r4 fixed it at absent) | `n + 1` if `trade_appended`, else `n` | pass `trades_save` (`:249`) | count `m` \| `unreadable` |

The trade dimension also yields a **new link** fact: `m − n = 1` and
consistent. `m = n` is no new link, whatever `n` is. Any other difference
already fails the §3.5 check.

Each dimension yields consistent, an incident, FAIL or INCONCLUSIVE. The
entry's verdict is the worse of the two. Episode end and trade link are read
from **observed** only (§5), never from the intent.

The named combinations (both dimensions consistent). "Trade" is the observed
count `m` against the parent count `n` (r5):

| `executed_marked` | `trade_appended` | pending observed | trade | Name |
|---|---|---|---|---|
| yes | yes | absent | `m = n + 1` | `filled` (with `outcome: dropped_exception`: `booked_with_drop_event`, §2.2.2) |
| yes | yes | absent | `m = n` | `vanished_fill` (§2.2.1) |
| yes | yes | present | `m = n` | `fill_not_persisted` |
| yes | yes | present | `m = n + 1` | impossible. `:249` runs only after `:248` returns. A present pending means `:248` raised or never ran, so `trades_save` is `not_attempted`, and an increment is then a FAIL by the trade check |
| no | yes | present | `m = n + 1` | `booked_pending_retained` (§2.2.2) |
| no | yes | present | `m = n` | `trade_not_persisted` with the pending retained: the reviewer's r3 case |
| yes | no | absent | `m = n = 0` | ordinary removal (drop, discard, demotion) |
| yes | no | absent | `m = n ≥ 1` | removal over an **existing** trade: `discarded_already_open` after `booked_pending_retained` (the r4 review's case), no new link |
| yes | no | present | `m = n` | `removal_not_persisted` |
| no | no | present | `m = n` | ordinary retention (`n ≥ 1` only after `booked_pending_retained`) |

`m = n ≥ 1` with `trade_appended` false is never read as a link. A trade
key can be present in the parent with `n ≥ 1` only through an earlier entry
of the same episode or, for a different episode, through a closed trade with
the same key; §5 decides which.

Any other observed pair fails a dimension check by the §3.5 table: FAIL or
INCONCLUSIVE. C-ENUM enumerates all of them. `outcome` stays the branch
taken.

**A2 derivation (observer only, never in the loop).** Two claims, each about
one named event (§3.3), each using only that event's bracket `[s, e]` from its
owner record. `O` = 09:30 `America/New_York` on `bar_date`, converted to UTC
with `zoneinfo`.

| Claim | Event | Bracket owner |
|---|---|---|
| `a2_admission`: the booked open preceded the pending's creation | `E_adm` | the episode's admission record: `[t_enter, t_return]` |
| `a2_observation`: the booked open preceded the price the decision used | `E_obs` | R1: the report heartbeat's scan bracket. R2/R3: the promoted candidate's `[t_validate_start, t_validate_end]` |

| Value | Condition |
|---|---|
| `after_open` | same session and `s >= O` |
| `before_open` | `bar_date` later than the event's session, or `e < O` |
| `unknown` | `s < O <= e`, or `e` absent |

Recorded with `session_open_basis: "NYSE 09:30 America/New_York"`, the
event name, and `after_open_seconds` (from `s`). The audit's historical A2 is
about `E_adm` only. The DST change on 1 Nov is covered by a control (§7).

### 4.5 Heartbeat — one per process run

Fields: `decision_path`, `cycle_id`, `run_kind`, `started`, `completed`,
`report_completed` or `monitor_completed`, `loop_exception`,
`lineage_complete`, `repository_commit`, manifests (§6.2),
`write_failures`, `queue_event_write_failures`, one **call-site state**
per registered call site (§6.2 inventory), and on the report path the scan
bracket `t_scan_start`, `t_scan_end` (`E_obs` for R1, §3.3). On the monitor
path: `peer_cycle_started_at`, `peer_invocation_token` (§3.4).

#### Coverage: expected / reached / completed, captured on both sides

r1 compared record counts with call counts taken by the same mechanism, so one
missed capture could hide in both. r2 captures the three states at
**different statements**: the caller marks `expected`, the callee marks
`reached` and `completed`.

| Call site | `expected` (caller, before the call) | `reached` (callee, first statement) | `completed` (callee, every return) |
|---|---|---|---|
| `daily_report.py:54` fill pass | `fill_pass_expected = True` | pass record created | `return_reason` set |
| `daily_report.py:64` promotion (scan) | `promotion_expected = True` | pass record created | `return_reason` set |
| `daily_report.py:84-119` scan admissions | `signals_seen = len(signals)` after `:84`; `scan_open_trade_expected += 1` before each `:119` | admission record created | `returned` set |
| `tracker.py:595` promotion admission | candidate `open_trade_expected = True` | admission record created | `returned` set |
| `monitor_trades.py:280` promotion (monitor) | `promotion_expected = True` inside `if updated:` | pass record created | `return_reason` set |

Per call site the observer derives one state:

| State | Meaning | Verdict |
|---|---|---|
| `not_expected` | caller never reached the site; reason recorded (`no_state_update`, `aborted_before`, `no_signals`) | PASS if the reason is consistent with the heartbeat; INCONCLUSIVE if the run aborted |
| `expected_not_reached` | caller marked it, callee never did | **FAIL**: capture defect |
| `reached_not_completed` | callee entered and did not return; for admissions refined to the `raised_*` call states (§4.2) | INCONCLUSIVE, with `loop_exception` and the call state |
| `completed` | normal | evaluate the records |

Reconciliation also requires `signals_seen == scan_open_trade_expected ==`
scan admissions reached, and one admission record per candidate with
`open_trade_expected`.

Independent corroboration, outside this recorder:

- **Monitor:** the monitor recorder's heartbeat `trade_state_save_completed`
  must equal `promotion_expected` in the paired admission heartbeat. Pairing
  is by `cycle_started_at` (§3.4); without a pair the check is INCONCLUSIVE.
  Two recorders, two capture sites.
- **Committed state:** the inspection tool's state-diff reconciliation (§7)
  does not use the recorder's counts at all.

**Future callers.** A new caller of `open_trade`, `promote_queue`,
`execute_pending_signals` or `queue_signal` would have no reset, no flush and
no `expected` mark. It is rejected at review time, not at run time: the
verifier's call-site inventory (§6.2) fails on any call site that is not
registered. A process that ran one anyway and committed state is caught by the
state-diff reconciliation as an unrecorded pending or trade.

---

## 5. Linkage: pending episodes, matched fail-closed

r1 treated `(symbol, signal_date)` as an identity. It is not one: a pending
can end and another start with the same symbol and date (r2's own audit
rerun found exactly this for HAFN). r2 treats it as a **candidate key** and
links through **episodes**.

**Episode (r4: boundaries from observed state).** For one key: the interval
from the first commit whose `pending_signals.json` holds it, opened by an
admission whose pending check is consistent (§3.5), to the first commit that
no longer holds it, closed by a fill entry whose pending check is consistent.
Two facts stay separate, as in r3, but both are now **observed**, not
derived from save facts:

- **Pending end:** the pending dimension's observed value (§4.4) is
  `absent`.
- **Trade link:** the trade dimension's **new link** fact (§4.4): the
  observed count rose by exactly one over the parent commit. Membership
  alone is never a link (r5). It can occur on an entry that does **not** end
  the episode.

| Pending observed | Trade observed | Episode | Trade link |
|---|---|---|---|
| absent | `m = n + 1` | closes | **new** link (`filled`; `booked_with_drop_event`) |
| absent | `m = n` | closes | no new link. If `n ≥ 1` and the episode already holds a link, it closes **linked** (the r4 review's case); if `n ≥ 1` and it holds none, **FAIL** `unowned_existing_trade`. Otherwise ordinary removal, or `vanished_fill` if `trade_appended` |
| present | `m = n + 1` | stays open | **new** link (`booked_pending_retained`); a later pass's `discarded_already_open` closes it, by the row above |
| present | `m = n` | stays open | no new link (retention; `trade_not_persisted`, `fill_not_persisted`, `removal_not_persisted`) |
| `unreadable` (either) | | **INCONCLUSIVE**: the episode state is unknown from this commit until a later commit is readable again. `load_pending` / `load_trades` raise on an unreadable file (`:37-42`, `:251-256`), so the next run fails visibly rather than reading an empty list | none |

A `demoted_to_queue` entry closes the episode; a later promotion opens a
**new** one. Its queue side is checked separately (§4.2): with
`queue_memory: inserted_then_evicted` and the queue observed `absent`, the
signal is gone (§2.2.2).

**Derived id.** `(symbol, signal_date, admitting cycle_id, admission sequence
in that cycle)`, computed by the observer and the inspection tool. It is never
written to production state.

**Matching rules (inspection, every violation is FAIL):**

1. At most one open episode per symbol at any time. A persisted admission for
   a symbol with an open episode is a FAIL. `open_trade`'s duplicate check
   (`:631-633`) should make it impossible; the rule tests that claim rather
   than assume it.
2. Every fill entry matches **exactly one** open episode for its symbol, with
   equal `signal_date`. Zero or several is a FAIL. There is no "latest
   earlier" selection.
3. Every entry whose pending is observed `absent` closes exactly the episode
   it matched, and no other entry closes it.
4. Every trade added to `virtual_trades.json` between two commits (a count
   increment on its key) matches exactly one episode with a **new link** in
   that interval, on symbol, `signal_date` and `entry_date` = fill run date.
   The link may come from an entry that did not close the episode. A trade
   already in the parent commit is never matched again (r5).
5. An episode holds at most one trade link. A second **new link** for one
   episode is a FAIL. A later entry that observes the existing trade
   (`m = n ≥ 1`) is not a second link; it is checked against the link the
   episode already holds: same key, and the trade is still in the file.
6. Incidents, not FAILs: `vanished_fill`, `booked_pending_retained`,
   `booked_with_drop_event`, `trade_not_persisted`, `fill_not_persisted`,
   `removal_not_persisted` and every `<file>_not_persisted`. Each is a §3.5
   row where memory, the save chain and the observed file agree, and it
   shows a production defect. The inspection reports them by name; the run's
   recorder verdict is unaffected.
7. Episode boundaries agree with the committed pending file: an episode is
   open across a commit exactly when that commit's `pending_signals.json`
   holds the key. Under r4 this holds by construction; the rule remains as
   a check on the implementation.
8. A fill entry and a later recorded admission in the **same run** that
   touch the same pending key make the end-of-run observation ambiguous for
   the fill entry: **INCONCLUSIVE** `key_reused_in_run`. With the report
   order (fills at `daily_report.py:54`, admissions at `:119`) this needs a
   pending dated today that is removed and re-admitted in one run.

**Pre-activation pendings.** A pending present at the base commit with no
admission record opens a `preactivation` episode. Its fill is linked as
`unlinked_preactivation`: reported, never counted as linked coverage.

**Rejected alternative: carry an `admission_id` in the pending and the trade.**
That writes a new key into `pending_signals.json` and `virtual_trades.json`.
It would break the invariant that the recorder never mutates production state
(the monitor recorder's control 26). It would also change the trade schema
that `export_csv`, `build_dashboard.py` and `sample.py` read. With the
episode rules a collision on the candidate key is a detected FAIL, not a
silent mislink. Raise the id again only if such FAILs occur in practice (§9 Q1).

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
| `engine/tracker.py` | store, capture assignments in `open_trade`, `promote_queue`, `execute_pending_signals`, `queue_signal`, `_log_queue_event`; save markers at the ten save sites that can change pending, trade or queue membership or the ranked list (§3.5 table, all rows except the three trade status writers); the `expiry_parse_failed` capture (§2.2.3) (r5) | additions only |
| `daily_report.py` | reset in `generate_report`; `try/finally` flush in `__main__` | additions + 1 replacement |
| `monitor_trades.py` | reset; `promotion_expected` mark; one flush call in the existing `finally` | additions, plus **re-registration of the existing `__main__` replacement** |

Pre-activation fixtures:
`tests/fixtures/{tracker,daily_report}_preadmission.py`. The existing
`tests/fixtures/monitor_trades_preactivation.py` stays the base for
`monitor_trades.py`.

**Q2, corrected.** r1 said "none of its existing digests change". That is
false for the one entry that matters: the admission flush goes inside the
`finally` of the registered `__main__` replacement, so that subtree's digest
**changes**. r2 treats it as a re-registration of a reviewed entry:

- the old and new `__main__` subtrees are shown side by side in the
  implementation bundle, and the digest change is listed by name;
- the admission flush runs **after** the monitor flush and its health check,
  in its own `try/except`, so it cannot pre-empt, replace or mask either the
  monitor evidence or the in-flight exception;
- containment controls 50–62 are rerun unchanged, plus new controls for the
  ordering (§7);
- all other existing monitor digests must be byte-identical; any other change
  is a FAIL.

**Verifier extensions.**

1. **Statement whitelist** (§3.2): every registered statement's AST must match
   a permitted form.
2. **Call-site inventory:** across all production `.py` files, the set of
   calls to `open_trade`, `promote_queue`, `execute_pending_signals`,
   `queue_signal` and `expire_queue` must equal a registered inventory
   (today: the five sites in §2.3, `tracker.py:84` and `:626` for
   `queue_signal`, `:434` and `:953` for `expire_queue`). A new caller is
   a FAIL.
3. **Writer inventory and save data flow (r5):**
   - The set of `save_pending`, `save_trades`, `save_queue`, `save_ranked`
     calls, and of `open(..., 'w'|'a')` on the four state files outside those
     functions, must equal the §3.5 table. A new writer is a FAIL.
   - For every save call, each name in its argument must be bound in the same
     function from a `load_*` call, or from a registered carrier edge (today
     only `maintain_queue`'s `queue = expire_queue()`, `:434`). Anything
     else, for example a list taken from a parameter or a return value, is a
     FAIL. The three trade status writers pass by the first rule.
   - The owner of each carrier edge must have its save call outside every
     `try` body in that function (`:379` today; the probe's
     `save_call_in_try` predicate). A handler added around it is a FAIL,
     because the carrier could then re-save a mutation whose owner raised.
   - Only `tracker.py:226` may call `append`, `extend`, `insert`, `remove`,
     `pop` or `del` on a list bound from `load_trades()`. Others are a FAIL.
4. **Three before/after pairs**, one registration table each.

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
- `admission`: `pending`, `queued` (`inserted` and `already_queued`),
  `duplicate_open`, `duplicate_pending`; queue event written vs write
  failed (unwritable `queue_events.jsonl`).
- `promotion_pass`: each early return; `unranked_fallback`; and each
  candidate outcome, including an unexamined tail after `break`.
- `fill`: all nine outcomes in §4.4, including both silent discards (§2.2).

**Persistence (r4: three failure points per save, observed from the
committed file)**

For each of the ten marked save sites (§3.5, §6.2), a patched `json.dump` / `open`
fails at each of three points, and the scratch repository commits `logs/`
as the wrapper does:

| Failure point | File left | Expected |
|---|---|---|
| `open` raises | old file | `save: raised`; observed = baseline; `<file>_not_persisted` (or consistent if intent = baseline) |
| dump raises after a partial write (the reviewer's probe) | unparseable prefix | `save: raised`; `<file>_unreadable`, INCONCLUSIVE; never "unchanged" |
| close raises after a complete dump | new file | `save: raised`; observed = intent; consistent |

Plus:
- The `save_pending` → `save_trades` vanished fill (§2.2.1) and the
  promotion's both-pending-and-queued case.
- A failure after a completed save does not retract it.
- A file edited between the parent commit and the run, to neither dump:
  FAIL (`≠ intent, ≠ baseline`).
- Two heartbeats in one commit touching the same key: `concurrent_runs`,
  INCONCLUSIVE.

**Save chains and the expiry carrier (r5)**
- C-HANDOFF: `save_queue` at `:379` fails at each of the three points
  (`open`, partial dump, close after a complete dump). Expected: the
  exception leaves `expire_queue` and `maintain_queue`; `:485` and `:488`
  have no `attempted` marker; the report heartbeat shows the abort; the
  committed queue is old, `unreadable` or the expiry dump respectively, and
  each is one row of §3.5. (`repro/probe_queue_handoff.py` already shows the
  propagation on the unmodified bodies.)
- No expiry, then `:485` saves: the chain is `not_attempted` → `completed`;
  intent = baseline for expiry; `maintain_queue`'s own drops are checked
  against `:485`.
- Expiry completed, then `:485` fails at each point: the expiry is
  consistent in every case; `maintain_queue`'s drops are `= baseline`
  (`queue_not_persisted`), `= intent` or `unreadable`.
- Synthetic recorder input with `:485` attempted after `:379` raised:
  FAIL `carrier_after_raise`.
- Report run with a free slot: `expire_queue` runs at `:953` and again at
  `:434`. The second call reloads; both expiries fold in order.
- Malformed `date_added` in the queue (probe H2): one
  `expiry_parse_failed` capture, the committed queue lacks the entry, the
  fold explains the removal, and the inspection names it.

**Trade links over existing membership (r5, the r4 review's case)**
- Two report runs on one date. Run 1: post-append exception at the
  `EXECUTED` print, so the trade is booked and the pending retained; parent
  count `n = 0`, observed `m = 1`: new link, episode open. Run 2 (21:00):
  `discarded_already_open`, `trade_appended` false, parent `n = 1`,
  observed `m = 1`: consistent, no new link, episode closes **linked** to
  run 1's trade. Inspection: one episode, one link, `booked_pending_retained`,
  PASS. Neither a trade-dimension FAIL nor a second link.
- The same, but run 2's `save_pending` fails at `open`: pending present,
  `m = n = 1`, `removal_not_persisted`, episode still open with its link.
- A trade present in the parent that no episode linked, under a
  `discarded_already_open` entry: FAIL `unowned_existing_trade`.
- A trade whose key fields change between commits: FAIL.
- A second increment for one episode: FAIL.

**C-ENUM: exhaustiveness and disjointness, mechanically**
- The §3.5 function is called on every tuple of (intent, baseline, observed
  ∈ {present, absent, unreadable}, save ∈ {not_attempted, completed,
  raised}): 2 × 2 × 3 × 3 = 36 tuples; and for trade counts on (baseline
  `n` ∈ {0, 1}, `trade_appended` ∈ {no, yes}, observed `m` ∈ {0, 1, 2,
  unreadable}, save ∈ 3 values): 2 × 2 × 4 × 3 = 48 tuples (r5). Each must
  return exactly one result, and must equal a hand-written expected table
  checked into the controls.
- The fill composition (§4.4) is called on every (`executed_marked`,
  `trade_appended`, pending observed, trade baseline `n` ∈ {0, 1}, trade
  observed `m` ∈ {0, 1, 2, unreadable}, pass `pending_save`, pass
  `trades_save`): 2 × 2 × 3 × 2 × 4 × 3 × 3 = 864 tuples (r5; r4: 324). Each must yield
  exactly one verdict and, if consistent, exactly one name. Tuples the
  source cannot produce (`trades_save` attempted while `pending_save` is not
  `completed`) must still return a verdict, not raise.
- The queue check (§4.2) is called on every (`queue_memory`, observed,
  `queue_save`).
- An observer with one table row removed, or two rows overlapping, fails
  C-ENUM (mutation).

**Coverage**
- Each state in §4.5 produced by a real run: `not_expected` (no update; no
  signals; aborted before), `reached_not_completed` (raise inside the callee),
  `completed`.
- `expected_not_reached`: the callee capture disabled while the caller mark
  remains: FAIL.
- A capture disabled on **both** sides is still caught, by the state-diff
  reconciliation (inspection) and, on the monitor path, by the monitor
  heartbeat cross-check.
- `signals_seen` disagreeing with scan admissions reached: FAIL.

**Linkage (episodes, §5)**
- Exactly one match succeeds.
- Zero matches; two open episodes; an admission while an episode is open;
  the same key reused after `demoted_to_queue` and re-promotion (a new
  episode, matched correctly); and a pre-activation pending: each has its own
  control.

**Clock and A2 derivation (named events)**
- The clock is read only at registered statements (whitelist control).
- `E_adm` bracket wholly after the open (16:10 UTC monitor admission):
  `a2_admission: after_open`. Admission at 21:01 UTC with a fill next
  session: `before_open`.
- Bracket straddling 13:30:00 UTC: `unknown`. An `E_adm` with no `t_return`
  (raise): `unknown`.
- `a2_observation` uses only the `E_obs` bracket: a scan bracket ending
  before the open with an `E_adm` after it gives `a2_observation:
  before_open` and `a2_admission: after_open`. The brackets are never merged.
- Admission at 13:31 UTC on Mon 2 Nov (after DST ends, 08:31 ET, open
  14:30 UTC): `before_open`.

**Completion and partial outcomes (r3, effects restated in r4)**
- `save_pending` raises in `open_trade`: `branch: pending`, no `returned`,
  call state `raised_in_pending_save`; the coverage state is
  `reached_not_completed`, never `completed`.
- Queue insertion that evicts the new candidate (one-slot queue holding a
  higher-confluence entry, the reviewer's probe): `queue_memory:
  inserted_then_evicted`, committed queue lacks the symbol: consistent. The
  same with `save_queue` failing at each of the three points: absent →
  consistent; unparseable → INCONCLUSIVE; never two outcomes.
- Fill exception after `trades.append` (stdout raising at the `EXECUTED`
  print, the reviewer's probe): `outcome: retained_exception`, trade
  count `n + 1`, pending `present`: episode open with a new link; the next
  pass's `discarded_already_open` closes it (r5: over `m = n ≥ 1`, no second link); inspection reports
  `booked_pending_retained` and passes. The same at `MAX_FILL_ATTEMPTS`:
  `booked_with_drop_event`.
- **The reviewer's r3 combination:** the same post-append exception, then
  `save_trades` failing. Pending `present`, trade `m = n` (open failed) →
  `trade_not_persisted`, episode open, no link; or trade `unreadable`
  (partial dump) → INCONCLUSIVE.
- `save_trades` raising after `save_pending`: episode closed, no link,
  `vanished_fill` reported.
- A second new link (count increment) for one episode: FAIL.

**Cross-recorder pairing (§3.4)**
- Normal monitor run: exactly one monitor heartbeat with equal
  `cycle_started_at`; cross-check runs.
- Monitor observer unimportable: `peer_unavailable`, INCONCLUSIVE.
- `peer_cycle_started_at` null: `peer_unidentified`, INCONCLUSIVE.
- Two monitor heartbeats with the same value: FAIL.
- The admission observer leaves `_MONITOR_EVAL` byte-identical (read-only
  control).

**Monitor entrypoint re-registration**
- Admission flush raising, or unwritable: the monitor flush, its health line
  and the in-flight exception are all unaffected.
- Monitor flush failing: the admission flush still runs.
- Containment controls 50–62 rerun unchanged.

**Verifier**
- An unregistered caller of `open_trade` (or the other four) fails the
  call-site inventory.
- r5 writer inventory: a new `save_queue` call; a save whose argument comes
  from a parameter or a new carrier; a `try` added around `:379`; a
  `trades.append` outside `:226`: each fails the verifier.
- A registered statement containing a non-whitelisted call or a comparison
  fails.

**Anti-vacuity**
- A harness that cannot fail is detected.

**Inspection: `tools/inspect_admission_cycles.py`, mutation-tested like v7**
- **State-diff reconciliation**, independent of the recorder's own claims:
  - every pending **added** between two commits has an `admission` record
    with `branch: pending`;
  - every pending **removed** has a `fill` entry with `executed_marked`;
  - every trade **added** (count increment on its key) has a `fill` entry
    with `trade_appended` (any `outcome`, §2.2.2); trades already in the
    parent are not re-matched, and their key fields are unchanged (r5);
  - every queue symbol added or removed has a captured queue mutation;
  - the §3.5 per-file check, for every key the run touched in the pending,
    trades and queue files, using the commit's files as the observed state.
    `queue_ranked.json` only orders promotion; no membership claim is made
    for it, and its save facts are recorded only.
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
| Q1 | Natural key `(symbol, signal_date)` or a carried `admission_id`? | r1 review: conditional. **r2:** candidate key only, linked through episodes with exactly-one matching and explicit terminals (§5). The id is still rejected because it mutates production state. |
| Q2 | Is amending the accepted monitor recorder's registered diff acceptable? | r1 review: acceptable in principle, digests alone insufficient. **r2:** the `__main__` replacement is re-registered and its change shown; ordering and containment controls added (§6.2). |
| Q3 | Should the observer duplicate fields already in `queue_events`? | r1 review: accepted. Original fields that are missing (e.g. `strength` on the queue route) are recorded as explicitly absent, never filled in. |
| Q4 | Should A2 be derived in the observer or only in the inspection tool? | r1 review: accepted once decision time and linkage are reliable. **r2:** bracketed decision time (§3.3), three-valued result, independent recomputation in the inspection. |
| Q5 | Does a manually *admitted*, scheduled-*filled* trade (AMP) belong in `clean_v3`? | r1 review: correctly deferred. Recorded via `run_kind`; capture does not change clean-sample policy. |
| Q6 | The 13:30 scan reads a session bar about one minute old (`signal['date']` = today). Is that in scope? | r1 review: agreed, out of scope. Recorded through `signal_date` and the bracket; strategy question. |

---

## 10. Sequence

1. ✔ External review of this draft: r5 accepted for implementation review
   (2026-10-22). Pack reproduced independently; not an activation approval.
2. Implement on branch `recorder/admission-v1`: fixtures first, then
   capture, observer, controls, inspection tool and mutation test.
3. Review bundle (same structure as monitor recorder v7).
4. Activation, after acceptance and on your go-ahead.
