# Decision log

Durable record of settled decisions and of factual corrections that were
established after an incorrect claim had been made.

**Why this file exists.** Several substantial decisions — the entry-ranking model
sequence, the rejection of agent and reinforcement-learning architectures, the
objections that produced the entry-only constraint — were reached in discussion and
never written down. They were later partially recovered from memory, and in the
interim incorrect statements were made about what had been decided and what
evidence existed. A decision that lives only in a conversation is not a decision
the project can rely on.

**Format.** `D-nnn` settled decisions, `C-nnn` corrections of record. Each entry
cites the authoritative file and line where the underlying evidence lives.

---

## D-001 — `n >= 100` is a strategy-validity threshold only

**Settled 2026-09-30.**

`n >= 100` closed clean trades remains the **preregistered** threshold for a
**strategy-validity claim**. It was registered `ROADMAP.md`, section *"PRE-REGISTERED
— how the `clean_v3` live sample may be read"*, written **2026-09-24, before the
first `clean_v3` trade closed**. Derived from Run A′'s 145 trades: mean per-trade
−0.27%, SD 10.82%, giving a 95% CI half-width of ±3.16% at n=45 against the +3.0%
per trade needed to match SPY.

**It does not automatically become the Phase 5 cutover threshold.**
`minimum_closed_sample` remains `UNEVALUATED` by design. The two answer different
questions — strategy performance versus migration confidence — and conflating them
would let a threshold registered for one purpose silently satisfy a gate written for
another.

If `n >= 100` is ever adopted as the cutover threshold, it must be **registered as
such deliberately**, with its own statement of population, cleanliness exclusions,
runtime epoch, and whether exits require parity coverage. It may not be inherited.

Binding sub-rules, carried from the preregistration:

- Report the confidence interval whenever the mean is reported. A mean without its
  interval is not a result.
- A positive mean whose CI includes zero is **not** evidence of edge and must not be
  recorded as one.

---

## D-002 — the historical harnesses exist and are maintained

**Settled 2026-09-30.** Supersedes any statement that they are missing, absent, or
in need of reconstruction.

| Harness | Location | Establishes |
|---|---|---|
| `run_tracker_equivalence.py` | Athena | 20 boundary fixtures, 250 replayable historical Run A′ paths, 4,730 bars, zero decision-changing mismatches, 8,616 stored-value differences all reproduced by re-deriving tracker's rounding discipline |
| `run_exit_replay.py` | Athena | Block A stage 1; Policy A reproduced 293/293, before and after the scale-out fix |

Both are referenced from `TRACKER_MIGRATION_PLAN.md:116` and `:591`, and
`ROADMAP.md:2090`. They are **cross-repo**: the harnesses live in Athena while the
policy under test lives in Ares. A search confined to the Ares repository will not
find them, and their absence from an Ares-only search is not evidence of absence.

**Disposition: maintain, do not reconstruct.**

---

## D-003 — pretrained and agent architectures

**Settled 2026-09-30.**

**Deferred to optional feature extraction only** — FinGPT, FinBERT, Chronos,
PatchTST, TimesFM. They may not control entries or exits unless checkpoint identity,
pretraining cutoff, corpus provenance and incremental value over the preregistered
feature set are independently established. A checkpoint whose training data extends
beyond the historical decision date creates **potential** temporal leakage unless
cutoff and provenance are known; the objection is conditional, not categorical.

**Rejected for the current architecture** — TradingAgents and reinforcement-learning
agents. Reasons: simulator sensitivity, unstable reward definitions, poor
auditability, irreproducible decision paths.

**Model sequence, in order:** deterministic ranking control → single-factor baseline
→ six-feature linear model → LightGBM or CatBoost challenger. The tree must clearly
beat the linear model under **every** preregistered condition, is tested **once**,
and a failed condition is neither relaxed nor rerun on the same data.

**Standing constraints:** the model ranks **new entries only**; a declining rank
never sells a holding; exit authority stays with the deterministic policy; Ares must
retain the ability to hold cash.

---

## C-001 — heartbeat lineage is deployed but not yet verified

**Recorded 2026-09-30T02:43Z.** An earlier summary listed this as completed. It is
not.

The fix (heartbeat lineage reading `production_commit` rather than a key no producer
emits) shipped in `50a42a7`, **after** the last cycle ran. The two most recent
heartbeats are pre-fix and correctly carry `production_commit: null`:

```
2026-09-29T13:31:35Z   production_commit: null
2026-09-29T21:00:55Z   production_commit: null
```

**These must never be backfilled.** The item is complete only when a cycle running
the fixed code emits a non-null commit with `lineage_complete: true`.

Marking a fix verified on the strength of absent evidence is the same failure mode as
the Phase 5 false green, and is rejected for the same reason.

---

## C-002 — the 43 are untested entries, not excluded paths

**Recorded 2026-09-30.**

Correct statement: historical decision coverage is **250 of 293**;
excluded-path classification is **43 of 43**, all classified
`UNTESTED_INSUFFICIENT_WARMUP` — **not passed, not failed.**

Two distinct causes:

1. **Snapshot-start truncation (majority).** A harness limitation. Athena's frozen
   snapshot begins 2021-09-22, so early entries lack sufficient pre-entry bars
   within the snapshot. Run A′ admitted them because `prepare()` computes indicators
   once on the full frame and slices, leaving early rows `NaN` rather than raising.
2. **Genuine post-IPO short history.** `IOT@2022-01-20` (24 pre-entry bars) and
   `BIRK@2024-01-18` (67).

Describing these as "43 excluded historical paths" overstates the exclusion and
understates the coverage. See `TRACKER_MIGRATION_PLAN.md:252`.

---

## C-003 — the live-price path is not proven by Phase 0

**Recorded 2026-09-30.**

Phase 0 established equivalence on the **daily-price path only**. Its harness forced
`get_live_price` to `None`, while production takes the live branch during scheduled
cycles. See the scope correction at `TRACKER_MIGRATION_PLAN.md:106`.

The live-price path is being **evaluated by Phase 4 observation** — that is why
Phase 0.5 exact-input capture exists. Any wording implying the live path was
separately tested and proven is incorrect.

---

## Standing rules to prevent recurrence

1. **Decisions are recorded here on the day they are settled**, not at the next
   documentation pass. A decision recoverable only from conversation does not exist.
2. **"Does not exist" requires a search of both repositories.** Ares and Athena are
   separate checkouts; evidence, harnesses and vendored copies cross between them.
3. **Cite `file:line`** when asserting what a document establishes. Paraphrase from
   memory is how the retracted `PF 2.41 over 1060 trades` figure propagated.
4. **A threshold registered for one claim does not transfer to another gate.**
   Transfer requires its own registration.
5. **Never mark a fix verified while its evidence is absent.** Absent evidence is
   `UNEVALUATED`, never a pass.
6. **Record rejected alternatives and the reasons.** Without them, a future reader
   sees only the surviving plan and re-litigates settled questions — or worse,
   reinstates a rejected one.
