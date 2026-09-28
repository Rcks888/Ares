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
| Phase 1 — precision contract | **COMPLETE offline** — `engine/tracker_compat.py`, dormant, 65 tests passing |
| Phase 0.5 — indicator/exception hardening | **REGISTERED, separate change** |
| Phase 2 — position clearance | Pending: ABM and SDGR still open |
| Phase 3 — rollback point | Pending |
| Phase 4 — shadow comparison | Pending |
| Phase 5 — controlled deployment | Pending, unauthorized |

## Lineage at time of writing

| Artifact | Value |
|---|---|
| Ares production commit | `777225c` |
| Ares commit on VPS | `0396d10` — behind by four; see below |
| Athena research commit | `1cf6633` |
| `engine/exit_policy.py` md5 | `d00e621da121dc19320c739bc6b81f84` |
| `tracker.py` imports the module | **No** |

Research outputs and live runtime lineage are tracked separately on purpose. Do not
pull merely to align numbers; pull when there is an operational reason.

**VPS divergence, stated precisely.** The VPS is four commits behind. The difference
includes documentation **and the dormant, unreferenced `tracker_v3_2dp` adapter plus
its tests** — that is source code, not documentation, so the earlier
"documentation-only" wording no longer holds. No missing commit is imported or
reachable by the current live runtime, so there is still no operational reason to
pull.

### Runtime the contract depends on

`tracker_v3_2dp` reproduces observed **Python float** behaviour, so the runtime is
part of the contract rather than incidental.

| | Laptop (captured) | VPS |
|---|---|---|
| Python | 3.12.3 CPython | **to capture before Phase 5** |
| OS / arch | Linux 6.18.33.2 WSL2 / x86_64 | to capture |
| pandas | 3.0.3 | to capture |
| numpy | 2.2.6 | to capture |
| pandas_ta | 0.4.71b0 | to capture |
| `float_info.mant_dig` | 53 | to capture |

Rounding fingerprint, laptop:
`[100.015, 100.005, 2.675, 0.125, 0.135, 109.985]` →
`[100.02, 100.0, 2.67, 0.12, 0.14, 109.98]`

**Phase 5 pre-deployment check:** run all 65 adapter assertions in the
VPS-equivalent environment and confirm the fingerprint matches. Not because a
mismatch is expected, but because the contract intentionally depends on observed
runtime float behaviour, so "it passed on the laptop" is not evidence about the VPS.

Note `pandas_ta` exposes no `__version__` attribute; resolve it with
`importlib.metadata.version("pandas_ta")`. A naive probe reports it as missing.

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
| `scale_out_shares`, `scale_out_pnl`, `scale_out_pnl_pct` | `round(x, 2)` |
| exit fill | `round(raw * (1 - slippage), 2)` — a **single** round |
| `exit_slippage` | `round(raw - raw * (1 - slippage), 4)` — 4dp, not 2 |

Event ordering, inputs, commissions and outputs are preserved unchanged.

**Correction to an earlier statement in this document.** The fill was previously
written here as `round(round(effective_stop, 2) * (1 - slippage), 2)`. Reading
`tracker._close_trade` directly shows that is wrong as a *fill rule*: the fill is a
single round, `round(raw_exit * (1 - slippage), 2)`, and within one bar `raw_exit` is
the **unrounded** `effective_stop`.

The double round merely *reproduced* the FDX 2022-06-21 and NFLX 2022-07-22
differences by coincidence of arithmetic. The real mechanism is that `trailing_stop`
is persisted at 2dp on bar N and read back rounded on bar N+1, so the tracker's fill
derives from a rounded carry-forward while the canonical module carried full
precision. **State persistence compounds; the fill does not.** The adapter's tests
assert the true mechanism, building the carry-forward explicitly rather than
double-rounding. Anyone who "fixes" the fill into a double round has misread this.

> **`tracker_v3_2dp` reproduces tracker state persistence. It must not be
> simplified into an explicit double-round fill formula.**

Verified sequence:

    bar N    trailing_stop calculated
             -> tracker persists trailing_stop at 2dp
    bar N+1  tracker reads the persisted 2dp value
             -> _close_trade applies slippage to that value
             -> fill rounded ONCE

### Implementation: `engine/tracker_compat.py` (dormant)

Two responsibilities only — `to_canonical_state` and `apply_tracker_v3_2dp`, plus
`tracker_v3_2dp_fill` and `tracker_v3_2dp_slippage`. It does not retrieve market
data, add indicators, decide commissions, write logs, save positions, send alerts,
execute closes, or mutate its input. It is not a second tracker.

Dormancy is **enforced by test**, not merely asserted: `tests/test_tracker_compat.py`
greps the repository and fails if anything outside the adapter and its own tests
references it, and separately asserts `tracker.py` imports neither `tracker_compat`
nor `exit_policy`, and that `engine/__init__.py` auto-imports nothing.

**Phase 5 must CONVERT this test, not delete it.** It transitions from:

    no runtime reference is allowed

to an allowed-import boundary test:

    only tracker.py may reference tracker_compat
    no other runtime entry point may import it

Deleting it would let the adapter later become reachable from a second call site
through an unnoticed import — precisely the failure the "not silently bypassed by
another call site" condition exists to prevent.

65 assertions cover: tracker's exact defaults; half-cent boundaries below, at and
above; which fields round and which must not; the fill and 4dp slippage; both
observed rounding classes; stop-equals-trail equality preserved exactly; scale-out
quantities; signed and zero P&L; non-mutation with `in_place` opt-in; determinism;
idempotence; JSON round-trip and native float types; `None` passthrough.

One test literal was initially wrong and the adapter was right: `100.015` rounds
**up**, because its float is `100.015000000000000568`, above the midpoint. Of the
boundary values used, only `0.125` is exactly representable, so banker's rounding is
rarely what decides the result — the float representation is. Expectations are
measured, not reasoned.

**Placement: APPROVED at the adapter.** The rounding lives in the call-site adapter,
not inside `exit_policy.py`. The canonical module stays precision-neutral.

Layering:

    position state + market bar + policy
        -> canonical decision            (exit_policy.py, full precision)
        -> tracker_v3_2dp adapter        (compatibility)
        -> legacy live state representation

Putting the rounding in the module would make **every** consumer inherit live storage
convention, including Athena: it would couple research calculation to a storage
choice, make full-precision analysis harder, blur strategy logic against runtime
compatibility, and invite a future reader to assume 2dp is financially necessary.

The acknowledged cost is that the module alone no longer specifies live behaviour.
The live contract is `exit_policy.py` + `tracker_v3_2dp` adapter + fill and
commission handling. Acceptable **only** while the adapter is explicitly versioned,
small, deterministic, independently tested, included in the shadow comparison,
included in this document, and not silently bypassed by another call site. **Treat it
as part of the production contract, not as glue.**

Required header, so nobody later "cleans up" the rounding and moves live behaviour:

    Compatibility contract: tracker_v3_2dp
    Preserves the state precision and round-fill-round behaviour of the
    current inline tracker during canonical module migration.
    This adapter is a production-parity requirement, not a strategy rule.
    Full-precision state is deferred to a separate controlled change.

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

## Phase 0.5 — indicator and exception hardening: REGISTERED

A **separate** production change, reviewed on its own, because it alters failure
handling. **Not in the migration commit.**

Scope: add an explicit minimum-history check; distinguish evaluation failure from
valid no-action; log symbol, row count and exception class; alert when an open
position receives no exit evaluation; track consecutive failed cycles per position;
preserve the current exit decision whenever data is sufficient; change no signal or
indicator formula.

Operational record format:

    {
      "symbol": "XYZ",
      "exit_evaluation_attempted": true,
      "exit_evaluation_succeeded": false,
      "decision": null,
      "failure_type": "insufficient_history",
      "available_bars": 29,
      "required_bars": 34,
      "position_left_unchanged": true
    }

Escalation, to be finalised later and **not** inserted into the migration: first
failure logs a data-quality warning; second consecutive failure escalates to Telegram;
further consecutive failures raise a high-priority unmanaged-position alert.

## Phase 2 — position clearance

Wait for **ABM** and **SDGR** to close. Both are pre-clean and their lifecycles began
under inline tracker behaviour; they must not end under the canonical module.

**Tracking caveat, measured:** `phase` is `None` on all five open records — it is
stamped at close, not at entry. Phase 2 clearance therefore cannot key off a phase
field. Use `entry_date` against the boundary: ABM 2026-09-09 and SDGR 2026-09-18 are
pre-clean; TMO 2026-09-21, WBD 2026-09-22 and SECZ 2026-09-25 are `clean_v3`.

## Phase 3 — rollback point

**Tag:** `pre-tracker-swap` on the last inline-tracker commit.

**One-command rollback**, restoring only the exit implementation:

    git checkout pre-tracker-swap -- engine/tracker.py

This touches **no file under `logs/`**, so open-position records, `psi_state.json`
and trade history are unaffected by a rollback. Confirmed by construction: the swap
commit is limited to `engine/` and the rollback path names a single file.

### Expected operational state at the rollback point

Source `logs/virtual_trades.json` at Ares `5063f48` (2026-09-25). 5 open, 5 closed,
realised net **−$12.41**.

| Symbol | Entry date | Entry | Shares | Stop | Trail | Peak | Scaled |
|---|---|---|---|---|---|---|---|
| ABM | 2026-09-09 | 50.65 | 2.94 | 48.67 | 48.67 | 50.91 | No |
| SDGR | 2026-09-18 | 29.35 | 5.08 | 25.06 | 28.23 | 31.37 | No |
| TMO | 2026-09-21 | 654.54 | 0.23 | 634.25 | 634.25 | 678.39 | No |
| WBD | 2026-09-22 | 30.87 | 4.83 | 29.31 | 29.31 | 30.87 | No |
| SECZ | 2026-09-25 | 14.68 | 10.15 | 12.07 | 14.14 | 15.71 | No |

ABM, TMO and WBD have `trailing_stop == stop_loss` — the trail never ratcheted, so
they will label `stop_loss` rather than `trailing_stop` if stopped. SDGR and SECZ
have an active trail sitting **below** entry (the giveback dead band). All five are
unscaled, so the scale-out branch is live for each.

This table is the post-rollback expectation. Any divergence after a rollback is a
defect, not drift. **Re-snapshot immediately before the swap** — these values move
daily.

### Clearance sequence, to run when ABM and SDGR close

1. Confirm ABM and SDGR are no longer open.
2. Verify their resolution by **symbol and `entry_date`**, not by the open-record
   `phase` field, which is unstamped until closure. Do **not** treat disappearance
   from the open list as closure — confirm each moved to the expected closed or
   otherwise resolved state.
3. Re-snapshot: open positions, closed count, realised P&L, `psi_state.json`,
   relevant log checksums, current production commit, canonical module md5,
   `tracker_compat` contract version, scheduler state.
4. Re-verify `pre-tracker-swap` resolves to `d6cbd55`.
5. Re-verify the rollback command.
6. Run all adapter and dormancy tests in the **VPS-equivalent** environment.
7. Authorize Phase 4 only then. Keep the inline tracker authoritative throughout.
8. Do not authorize Phase 5 unless the shadow comparison is clean.

## Phase 4 — shadow comparison

**Sequencing dependency:** Phase 4 requires the `tracker_v3_2dp` adapter to already
exist and be unit-tested, because the shadow run compares the adapter's output, not
the bare module's. The adapter can be written as a new unreferenced module with zero
live effect, which also reduces the Phase 5 commit to wiring only.

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
