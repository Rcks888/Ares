# Tracker canonicalization — migration plan

Replacing `engine/tracker.py`'s inline exit chain with a call to the canonical
`engine/exit_policy.py`. **Not authorized.** This document is the plan and the gate
record, not a deployment.

Not a Block A experiment. Block A stage 1 is closed — Policies B and D failed the
registered bar and Policy A is retained, *not validated*. Nothing here depends on
that result or reopens it.

## Current status

| Item | State |
|---|---|
| Phase 0 — differential equivalence | **COMPLETE** over tested coverage |
| Phase 1 — precision contract | **DECIDED below, not implemented** |
| Phase 2 — position clearance | Pending: ABM and SDGR still open |
| Phase 3 — rollback point | Pending |
| Phase 4 — shadow comparison | Pending |
| Phase 5 — controlled deployment | Pending, unauthorized |

## Lineage at time of writing

| Artifact | Value |
|---|---|
| Ares production commit | `777225c` |
| Ares commit on VPS | `0396d10` — behind by one, documentation only |
| Athena research commit | `1cf6633` |
| `engine/exit_policy.py` md5 | `d00e621da121dc19320c739bc6b81f84` |
| `tracker.py` imports the module | **No** |

Research outputs and live runtime lineage are tracked separately on purpose. Do not
pull merely to align numbers; pull when there is an operational reason.

## Phase 0 — differential equivalence: COMPLETE

Harness `Athena/run_tracker_equivalence.py`. 20 boundary fixtures and 250 historical
Run A′ paths, 4,730 bars, cloned state, per-transition comparison, `tracker.py`
unmodified.

**Zero decision-changing mismatches** — no difference in `exit_reason`, `status`,
termination, `scaled_out` or `scale_out_date`. 8,616 stored-value differences, all
reproduced exactly by re-deriving tracker's own rounding discipline.

Established wording:

> No decision-changing differences were detected within the tested population.
> Full-precision and tracker-rounded state are not universally equivalent by
> construction.

## Phase 1 — precision contract: DECISION

**The first migration preserves live tracker rounding exactly.** Full precision is
deferred to a separate change with its own hypothesis, fixtures, shadow comparison
and deployment decision.

Rationale: the migration must not change both *where* exit decisions are computed and
*how* persistent state is rounded. If behaviour shifted afterwards, the cause would be
unattributable between canonicalization and precision.

Contract identifier, to be stamped on state and logs:

    state_precision_contract = "tracker_v3_2dp"

Specification — an explicit versioned contract, **not** a scattered set of `round()`
calls:

| Field | Stored as |
|---|---|
| `peak_price` | `round(x, 2)` |
| `trailing_stop` | `round(x, 2)` |
| `shares` | `round(x, 2)` |
| `scale_out_price` | `round(x, 2)` |
| `scale_out_shares` | `round(x, 2)` |
| exit fill | `round(round(effective_stop, 2) * (1 - slippage), 2)` |

Event ordering, inputs, commissions and outputs are preserved unchanged.

**Placement recommendation.** The rounding belongs in the **call-site adapter inside
`tracker.py`**, not inside `exit_policy.py`. The canonical module should stay
precision-neutral so Athena is unaffected and the legacy contract does not leak into
shared code. Tradeoff, stated rather than hidden: the adapter then carries behaviour
that must itself be reviewed, and the module alone will not fully specify live
behaviour. Accepted, because the alternative embeds a deprecated contract in the
module both consumers share.

## The 43 excluded historical paths

Classified `UNTESTED_INSUFFICIENT_WARMUP` — **not passed, not failed.** Historical
decision coverage is 250 of 293; excluded-path classification is 43 of 43.

`add_indicators` was measured directly: it **raises `AttributeError` on any frame
with fewer than 34 bars**, because `ta.macd` returns `None` and `macd.iloc[:, 0]`
then fails. Fails at 30 bars, succeeds at 34. It has no length guard and never
returns `None` itself.

Two distinct causes among the 43:

1. **Snapshot-start truncation (majority).** Athena's frozen snapshot begins
   2021-09-22, so entries from late 2021 to early 2022 lack 100 preceding bars
   *within the snapshot*. Run A′ admitted them because `prepare()` computes
   indicators once on the full frame and then slices, leaving early rows as `NaN`
   rather than raising. The harness instead steps bar by bar and re-derives per
   slice, which is stricter. **Harness reconstruction limitation.**
2. **Genuine post-IPO short history.** `IOT@2022-01-20` had 24 pre-entry bars and
   `BIRK@2024-01-18` had 67. These are real, not snapshot artifacts.

**Live-reachability verdict for entry: NOT reachable.** `signals.py` calls the same
`add_indicators` on the same frame, so a symbol with under 34 bars of cached history
raises at scan time and can never signal. Entry is self-gating, so a position cannot
open below the threshold.

**Live-reachability verdict for an already-open position: REACHABLE.** If a held
symbol's cached history is ever truncated below 34 bars — cache rebuild, partial
write, corrupted or evicted file — `check_open_trades` raises inside
`add_indicators`, the blanket `except Exception` swallows it, and that position
receives **no exit evaluation** for the cycle. See the deferred item below. This does
not require a new IPO; it requires a cache fault.

The uncovered state transitions are addressed by synthetic boundary fixtures rather
than by forcing an invalid historical reconstruction.

## DEFERRED ENGINEERING — tracker exception observability

Not part of the migration commit. Recording it there would again combine behavioural
canonicalization with error-handling changes.

`check_open_trades` wraps each trade in `except Exception` and only prints. So:

    data or indicator fault
        -> exception caught
        -> message printed, loop continues
        -> position had no actionable decision

is **observationally identical** to:

    evaluated correctly, no exit condition met

Known concrete trigger: cached history under 34 bars for a held symbol.

Replace or instrument the blanket path so faults are distinguishable from valid
no-action decisions. Minimum counters, which the Phase 4 shadow run should emit even
before the tracker itself is changed:

    exit_evaluation_attempted
    exit_evaluation_succeeded
    exit_evaluation_failed
    failure_type
    no_action_decision

**No decision** (evaluation succeeded, no exit condition met) and **evaluation
failure** (no decision could be produced) must never share the same silent output.

## Phase 2 — position clearance

Wait for **ABM** and **SDGR** to close. Both are pre-clean and their lifecycles began
under inline tracker behaviour; they must not end under the canonical module.

## Phase 3 — rollback point

Before any shadow run or deployment: tag the last inline-tracker commit, preserve the
existing `tracker.py`, define a one-command rollback, record the expected operational
state, and confirm rollback does not alter open-position records.

## Phase 4 — shadow comparison

Both implementations run from cloned state; **only the current tracker controls paper
actions.** The shadow module runs under `tracker_v3_2dp`, since full-precision state
is already known to differ. Record per evaluation: timestamp, symbol, tracker
decision, module decision, tracker state, module-compatible state, difference class,
`would_change_action`.

## Phase 5 — controlled deployment

The commit contains **only**: the canonical module import, the call-site replacement,
a minimal state adapter if required, and a version or logging identifier.

It must **not** contain: rounding-policy changes, blanket-exception changes,
strategy-parameter changes, exit-rule changes, commission changes, new take-profit
behaviour, or unrelated refactoring.

After any synchronization, confirm no scheduled job imports or executes the research
harnesses. `run_exit_replay.py`, `trace_mismatch.py` and `run_tracker_equivalence.py`
are research-only and must stay outside the live runtime surface.
