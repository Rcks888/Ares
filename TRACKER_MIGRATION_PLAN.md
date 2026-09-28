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
| Phase 0 — differential equivalence | **COMPLETE — daily-price path only** |
| Phase 1 — precision contract | **COMPLETE offline** — `engine/tracker_compat.py`, dormant, 65 tests passing |
| Phase 0.5 — inline decision-input capture | **PROPOSED — gates Phase 4** |
| Phase 0.5b — indicator/exception hardening | **REGISTERED, separate change** |
| Phase 2 — legacy-position clearance | Pending: ABM and SDGR open. **Gates Phase 5 only** |
| Phase 3 — rollback and state isolation | **COMPLETE — verified on the VPS 2026-09-28** |
| Phase 4 — parity comparison | **PAUSED — deployed disabled at 3611afc, re-gated behind Phase 0.5.** ABM and SDGR are required coverage |
| Phase 5 — controlled wiring | Pending, unauthorized |

**The phases are control domains, not a numerical execution order.** Numbering is
retained for document stability. Actual order: **0 → 1 → 3 → 0.5 → 4 → 2 → 5**.

> **AMENDED 2026-09-28.** Phase 4 is PAUSED and re-gated behind Phase 0.5. A
> production-time measurement showed the gateway is up during scheduled cycles
> (`restart_gateway.sh` at 13:00/16:00/17:25, `run_ares.sh` at 13:30/21:00) and
> the last real cycle used `(live)` prices for all five positions, so the
> daily-Close premise Phase 4 relied on does not hold when decisions are made.
> See `TRACKER_MIGRATION_PLAN_PHASE_0_5.md`. `ARES_PARITY` remains unset.

    Phase 0  differential equivalence      COMPLETE (daily path only)
    Phase 1  tracker_v3_2dp adapter        COMPLETE, dormant
    Phase 3  rollback + state isolation    ESTABLISHED
    Phase 4  live shadow comparison        AUTHORIZE NOW
             |
             ABM and SDGR stay under inline Policy A authority
             their full remaining lifecycles captured in shadow
             both close naturally
             |
    Phase 2  legacy-position clearance     COMPLETE at that point
             |
             review shadow evidence
             |
    Phase 5  controlled tracker wiring     only if all gates pass

## Lineage at time of writing

| Artifact | Value |
|---|---|
| Ares production commit | `3fde68d` (VPS synced 2026-09-28) |
| Ares commit on VPS | `3fde68d` — **in sync** |
| Athena research commit | `1cf6633` |
| `engine/exit_policy.py` md5 | `d00e621da121dc19320c739bc6b81f84` |
| `tracker.py` imports the module | **No** |

Research outputs and live runtime lineage are tracked separately on purpose. Do not
pull merely to align numbers; pull when there is an operational reason.

**VPS synchronised 2026-09-28** for the first justified reason: running the Phase 3
verification. The pull was proven additive before being accepted — 10 commits,
9 files, 2887 insertions, **0 deletions**, and no file in the live runtime path
(`tracker.py`, `exit_policy.py`, `indicators.py`, `signals.py`,
`daily_report.py`, `run_ares.sh`, `build_dashboard.py`), with those files'
checksums confirmed byte-identical before and after. Every module it delivered
remains dormant; the Phase 4 call site does not exist.

### Runtime the contract depends on

`tracker_v3_2dp` reproduces observed **Python float** behaviour, so the runtime is
part of the contract rather than incidental.

| | Laptop | VPS (captured 2026-09-28) |
|---|---|---|
| Python | 3.12.3 CPython | 3.12.3 CPython |
| arch / `mant_dig` | x86_64 / 53 | x86_64 / 53 |
| pandas | 3.0.3 | **3.0.5** |
| numpy | 2.2.6 | 2.2.6 |
| pandas_ta | 0.4.71b0 | 0.4.71b0 |
| `tracker.py` source hash | `be7b6038…` | `be7b6038…` |
| `exit_policy.py` md5 | `d00e621d…` | `d00e621d…` |

Rounding fingerprint — **identical on both hosts**:
`2.675→2.67, 0.125→0.12, 100.015→100.02, 109.985→109.98, 0.135→0.14`

**pandas differs: 3.0.3 laptop vs 3.0.5 VPS.** Not fatal, and not ignored. The
contract depends on Python float behaviour, not on pandas, and every
contract-critical field matches: fingerprint, `mant_dig`, tracker source hash and
`exit_policy` md5. The contract holds identically. Recorded because a future pandas
change could alter *indicator* values, which is a strategy / Phase 0.5 concern
rather than a parity-contract one.

**Marker contract verified on the VPS** against the loaded module at
`/root/ares/Ares/engine/tracker.py`, hash `be7b6038…`, match true.

**Phase 5 pre-deployment check:** re-run `tools/vps_phase3_verify.sh` and confirm
the fingerprint still matches. Not because a mismatch is expected, but because the
contract intentionally depends on observed runtime float behaviour, so "it passed on
the laptop" is not evidence about the VPS.

Note `pandas_ta` exposes no `__version__` attribute; resolve it with
`importlib.metadata.version("pandas_ta")`. A naive probe reports it as missing.

## Phase 0 — differential equivalence: COMPLETE (DAILY-PRICE PATH ONLY)

> **SCOPE CORRECTION 2026-09-28.** Phase 0 demonstrated no decision-changing
> differences on the **daily-price path** within the tested population. It did
> **not** test the production IBKR live-price path: its harness forced
> `get_live_price` to `None`, and production takes the live branch during
> scheduled cycles. Earlier wording in this document implying general tracker
> equivalence was too broad. Phase 0.5 adds exact inline decision-input capture
> so Phase 4 can evaluate the live path without a second market observation.

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

## Phase 2 — legacy-position clearance

**Gates Phase 5 only. It does not gate Phase 4.** Corrected from an earlier draft of
this plan, which gated shadow comparison on clearance. That was over-conservative and
had the value backwards: shadow mode has no trading authority, so open legacy
positions are not a reason to delay it — they are the best reason to start it.

Wait for **ABM** and **SDGR** to close **naturally, under Policy A**. Both are
pre-clean and their lifecycles began under inline tracker behaviour; they must not end
under the canonical module.

### Administrative closure is prohibited

Do **not** force-close either position under a "slot cleaning" or "pre-V3" reason to
unblock the migration. Rejected for four reasons:

1. **It inverts the dependency.** Infrastructure scheduling would drive a live trading
   decision. ECO and NEOG were allowed to run to their policy exits even when the
   outcome was obvious; this is the same line and there is no deadline forcing it.
2. **It permanently pollutes the trade log.** An administrative exit is not a policy
   decision. It creates a third exit category every future analysis must special-case,
   and it is unrecoverable — what Policy A would have done cannot be reconstructed.
3. **It destroys the two most relevant live observations.** SDGR is one of only two
   live instances of the giveback dead band, the exact mechanism Block A spent its
   whole budget on, where Universe A's equivalent population went 12 of 12 losers.
   ABM is the cleanest live `trail == stop, never ratcheted` case.
4. **It worsens realised P&L for no return.** Both are underwater; forcing them
   realises losses early, and the freed slots interact with the unfunded-sizing
   defect already being tracked.

### Verification at clearance

`phase` is `None` on all five open records — it is stamped at close, not at entry.
Clearance therefore cannot key off a phase field. Verify by **symbol and original
`entry_date`**, plus closed-or-resolved destination, recorded exit reason, and no
remaining open record for the same lifecycle. ABM 2026-09-09 and SDGR 2026-09-18 are
pre-clean; TMO 2026-09-21, WBD 2026-09-22 and SECZ 2026-09-25 are `clean_v3`.

This keeps the lifecycle boundary clean: ABM and SDGR open *and* close under inline
tracker authority; post-swap positions open *and* close under canonical authority.

### If either stays open a long time

Ares has **no time stop**, so natural closure has no guaranteed horizon. ABM is
already at day 16. Practically both are close — ABM 1.8% above its stop, SDGR 3.0%
above its trail — but if either drifts:

**Infrastructure scheduling will not cause an administrative exit.** The absence of a
V3.1 time stop becomes a separate portfolio-policy question, evaluated on its own
evidence — historical holding periods, capital occupancy, opportunity cost, MFE/MAE,
outcomes after stagnant periods, slot constraints, costs, with its own
pre-registration. It must **not** be retroactively justified as migration housekeeping.

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

### Phase 3 gate — conditions for starting Phase 4

**ALL MET. Verified on the VPS 2026-09-28** by `tools/vps_phase3_verify.sh`,
snapshot exit 0, all eight checks true, 258 assertions passing (65 adapter,
98 classifier, 95 wiring), marker contract valid against the loaded
`/root/ares/Ares/engine/tracker.py`, and every contract-critical field matching
the committed baseline. Artifacts on the VPS: `~/snapshot_A_pre.txt`,
`~/snapshot_A_vps.json`.

Phase 4 is therefore unblocked on the environment side. It still requires the real
`module_eval` and the single additive call site, neither of which exists.

1. Rollback tag resolves correctly (`pre-tracker-swap` → `d6cbd55`)
2. Rollback command tested
3. Pre-shadow state snapshot exists
4. Canonical module md5 recorded
5. Adapter contract version recorded
6. Adapter tests pass — in the **VPS-equivalent** runtime
7. Shadow state is cloned
8. Shadow results cannot mutate production state
9. Inline tracker remains authoritative
10. **Shadow failure cannot prevent the inline tracker from running**
11. Research scripts remain outside scheduled runtime entry points

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
7. Confirm Phase 4 coverage for both symbols is complete, including their exit bars.
8. Review the shadow summary. Do not authorize Phase 5 unless every Phase 4
   acceptance condition passes. **Both positions closing is not sufficient** if the
   shadow record was missing or failed during important bars.

## Phase 4 — shadow comparison

**Sequencing dependency:** Phase 4 requires the `tracker_v3_2dp` adapter to already
exist and be unit-tested, because the shadow run compares the adapter's output, not
the bare module's. The adapter can be written as a new unreferenced module with zero
live effect, which also reduces the Phase 5 commit to wiring only.

**Gated by Phase 1 complete + Phase 3 controls established. NOT gated by Phase 2.**
The inline tracker remains authoritative throughout, so shadow operation cannot alter
entry or exit timing, stops or trailing stops, position state, scale-out behaviour,
commissions, trade logs, `clean_v3`, or ABM/SDGR outcomes. **Phase 4 is measurement,
not migration.**

    inline tracker   = authoritative, controls paper actions, persists state
    module + adapter = cloned-state observer, records counterfactual, no side effects

### Fail-open requirement

**Shadow execution must fail open with respect to the inline tracker.** The inline
decision is produced and preserved independently; the shadow path receives cloned
inputs; if the shadow crashes, the inline action still proceeds.

The shadow path must not: hold a shared mutable reference to a live position, write
production position records or trade outcomes, submit or cancel orders, send ordinary
trade alerts, change cache contents, prevent inline evaluation, modify
`psi_state.json`, or change queue behaviour. A shadow failure may write a dedicated
diagnostic record and optionally raise a non-trading warning.

Note the interaction with the Phase 0.5 finding: the inline tracker's blanket
`except Exception` means an inline evaluation failure is currently silent. Phase 4
must therefore record inline status explicitly rather than inferring success from the
absence of an error.

### Required shadow coverage — ABM and SDGR

Designated **required coverage**, not blockers. They are the only live positions
carrying pre-clean stored state and sitting on the boundaries the contract must
preserve.

**ABM** exercises `trailing_stop == stop_loss` exactly. Capture: at least one
successful evaluation at equality; subsequent evaluations showing whether equality
persists or separates; every state transition to natural exit; the final exit decision
and exit bar; exact inline-versus-shadow comparison at exit. Tests whether equality
survives adapter projection, effective-stop selection stays consistent, exit
attribution uses the same comparison operator, carry-forward rounding stays identical,
and whether a later one-cent divergence could flip either side.

**SDGR** exercises the giveback dead band — entry 29.35, active trail 28.23, roughly
3.8% below entry. Capture: current dead-band state; every peak update and trail
ratchet; every effective-stop calculation; movement toward or away from entry; the bar
where the exit condition first becomes true; the final inline-versus-shadow decision;
whether the exit is attributed identically.

Per-evaluation success requires `inline_evaluation_succeeded` and
`shadow_evaluation_succeeded` both true, `would_change_action` false, decision fields
equivalent, and tracker-compatible state equivalent. **Any exception or missing shadow
result must be visible and classified, never treated as agreement.**

### Output contract

Record per comparison event: `timestamp`, `symbol`, `entry_date`,
`production_commit`, `exit_policy_md5`, `compatibility_contract`,
`inline_evaluation_status`, `shadow_evaluation_status`, `inline_decision`,
`shadow_decision`, `inline_exit_reason`, `shadow_exit_reason`,
`inline_effective_stop`, `shadow_effective_stop`, `inline_scaled_out`,
`shadow_scaled_out`, `inline_persistent_state`, `shadow_compatible_state`,
`difference_class`, `would_change_action`, `exception_type`, `exception_message`.

Lineage must be sufficient to reproduce every mismatch.

Result classes — at least six, because **a missing evaluation is not a match**:

| Class | Meaning |
|---|---|
| `MATCH` | Both evaluated, equivalent decisions |
| `NON_DECISION_STATE_DIFFERENCE` | Stored values differ, fully explained, cannot change the current action |
| `DECISION_CHANGING_MISMATCH` | Exit, scale-out, state transition, reason or action differs |
| `INLINE_EVALUATION_FAILURE` | |
| `SHADOW_EVALUATION_FAILURE` | |
| `BOTH_EVALUATIONS_FAILED` | |

### Phase 4 acceptance

Success is **not** merely "zero mismatches" — observation requirements count too.

1. No decision-changing mismatches
2. No unexplained persistent-state differences
3. No shadow mutation of production state
4. ABM equality-boundary coverage complete
5. SDGR dead-band lifecycle coverage complete
6. Both natural exit bars compared
7. Every evaluation failure separately classified
8. Adapter tests passing in the VPS-equivalent runtime
9. Lineage complete for every comparison record
10. Final summary generated before Phase 5 review

**No arbitrary calendar minimum** if ABM and SDGR supply the required lifecycle
transitions. Conversely, do not authorize Phase 5 merely because both positions
closed, if the shadow record was missing or failed during important bars.

## Phase 5 — controlled deployment

The commit contains **only**: the canonical module import, the call-site replacement,
a minimal state adapter if required, and a version or logging identifier.

It must **not** contain: rounding-policy changes, blanket-exception changes,
strategy-parameter changes, exit-rule changes, commission changes, new take-profit
behaviour, or unrelated refactoring.

After any synchronization, confirm no scheduled job imports or executes the research
harnesses. `run_exit_replay.py`, `trace_mismatch.py` and `run_tracker_equivalence.py`
are research-only and must stay outside the live runtime surface.
