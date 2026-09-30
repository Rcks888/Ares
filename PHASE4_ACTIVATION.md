# Phase 4 activation — proposed change set and first-cycle checklist

Status: DRAFT FOR REVIEW. Nothing here is applied. `ARES_PARITY` is unset and
`logs/tracker_parity_v1.jsonl` is absent.

The activation is **not** a one-line change. Reading `run_ares.sh` against the
gates produced three findings, two of which are prerequisites.

---

## Finding 1 (blocking) — `parity_output_absent` becomes a permanent failure

`tools/pre_parity_snapshot.py:432`

```python
"parity_output_absent": not (ROOT / "logs" / "tracker_parity_v1.jsonl").exists(),
```

This feeds `all_local_checks_pass`. The gate encodes "Phase 4 has not started",
so **the first parity write makes the verifier exit non-zero on every subsequent
run, forever.** Activating without addressing this converts the deployment gate
into a permanent known-false failure requiring manual interpretation — the exact
condition we removed when replacing `logs_unchanged_since_tag`.

It must become phase-aware before activation, not after.

Discriminator: the **committed declaration** in `run_ares.sh`, not the verifier
process's own environment (which will not have the variable set).

- collection not declared -> file must be ABSENT (today's meaning, preserved)
- collection declared     -> file may exist, and must be valid: parseable JSONL,
  append-only against the recorded line count, every record schema-complete, and
  zero `DECISION_CHANGING_MISMATCH`

## Finding 2 — CORRECTED: it was inverted

My original claim was that `git add logs/` would commit the parity file to an
unapproved path and fail the log gate. **That was wrong**, and the review
correctly demanded it be tested rather than assumed.

```
$ git check-ignore -v logs/tracker_parity_v1.jsonl
.gitignore:42:logs/*    logs/tracker_parity_v1.jsonl
```

Controlled test with a temporary file of that exact name:

```
$ touch logs/tracker_parity_v1.jsonl && git add logs/
$ git status --short -- logs/tracker_parity_v1.jsonl
<nothing: NOT staged>
```

So there would have been **no gate failure at all**. The real consequence is the
opposite and worse: the evidence would never be committed, would live on a single
host with no backup, and would never reach the laptop for review — while an
`ALLOWED_RUNTIME_LOGS` entry implied it was being archived.

Allow-listing a path does not override an ignore rule. A scoped negation is
required (`!logs/tracker_parity_v1.jsonl`), not `git add -f logs/`, which would
stage other ignored runtime artifacts.

Both are now implemented, and `_parity_output_trackable()` plus a control that
performs the real `git add logs/` prove the file actually stages and that no
other ignored log becomes newly trackable.

## Finding 3 (not blocking, but a real trap) — `.env` placement would silently fail

`run_ares.sh` sources `/root/ares/.env` at line 11, which is **after**
`daily_report.py` runs at line 8. Adding `ARES_PARITY=1` to `.env` — the most
natural-looking placement — would never reach the trading process. Parity would
stay off while appearing configured, and the only symptom would be an empty
parity file that looks like "no positions to compare".

`enabled()` reads `os.environ` at call time (`engine/parity_hook.py:52`), inside
the function, so a plain `export` before line 8 is sufficient and correct.

---

## Proposed change 1 of 3 — `run_ares.sh`

```diff
 #!/bin/bash
 export ARES_SCHEDULED=1
 export PATH=/root/jdk-17.0.12/bin:$PATH
 export DISPLAY=:1

+# Phase 4 parity collection. OBSERVATION ONLY: the inline tracker remains the
+# sole decision authority and parity never writes production state. Must stay
+# above the daily_report.py line -- /root/ares/.env is sourced afterwards, so a
+# variable placed there would not reach the trading process.
+# To disable, comment out this single line.
+export ARES_PARITY=1
+
 cd /root/ares/Ares
 source /root/ares/Ares/venv/bin/activate
 python daily_report.py 2>&1 | tee /tmp/ares_output.txt
```

One line plus comment. No other strategy flag changes. Removal is a comment-out.

## Proposed change 2 of 3 — allow-list the evidence path

```diff
 ALLOWED_RUNTIME_LOGS = (
     "logs/psi_state.json",
     "logs/virtual_trades.json",
     "logs/trades_report.csv",
     "logs/signal_queue.json",
     "logs/queue_ranked.json",
     "logs/queue_events.jsonl",
     "logs/last_scan_summary.txt",
+    # Phase 4 migration evidence. Append-only, bot-committed for off-host
+    # backup. Validated by the phase-aware parity-output gate, not merely
+    # tolerated: see _parity_output_state().
+    "logs/tracker_parity_v1.jsonl",
 )
```

### Field-name correction (would have been silent)

The draft checked `r.get("verdict")`. Deriving the schema structurally from
`build_record` showed the frozen field is **`difference_class`** — 45 fields
total. Neither `verdict` nor `result_class` (the internal return name in
`_classify`) exists in a record, so `r.get(...)` would have returned `None` and
**every real `DECISION_CHANGING_MISMATCH` would have passed silently.**

Both constants now come from `engine.parity_compare`
(`sc.DECISION_CHANGING_MISMATCH`, `sc.RECORD_SCHEMA_VERSION`) and the field set
is AST-derived from `build_record`, so there is no local copy to drift.

## Proposed change 3 of 3 — phase-aware parity-output gate

Replaces the bare `.exists()` check. Sketch:

```python
def _collection_declared():
    """True when run_ares.sh declares collection, ignoring comments."""
    for raw in (ROOT / "run_ares.sh").read_text().splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if re.match(r"export\s+ARES_PARITY=1\b", line):
            return True
    return False


def _parity_output_state():
    p = ROOT / "logs" / "tracker_parity_v1.jsonl"
    declared = _collection_declared()
    out = {"collection_declared": declared, "exists": p.exists(),
           "records": 0, "failures": []}
    if not declared:
        # Phase 0.5 meaning, unchanged.
        out["valid"] = not p.exists()
        if p.exists():
            out["failures"].append("parity output present but collection "
                                   "is not declared in run_ares.sh")
        return out
    if not p.exists():
        # Declared but nothing written yet: legitimate before the first cycle.
        out["valid"] = True
        out["failures"].append("NOTE: declared, no records yet")
        return out
    recs = []
    for i, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            recs.append(json.loads(line))
        except Exception as exc:
            out["failures"].append(f"line {i} unparseable: {exc}")
    out["records"] = len(recs)
    for i, r in enumerate(recs, 1):
        missing = REQUIRED_RECORD_FIELDS - set(r)
        if missing:
            out["failures"].append(f"record {i} missing {sorted(missing)}")
        if r.get("verdict") == "DECISION_CHANGING_MISMATCH":
            out["failures"].append(f"record {i} DECISION_CHANGING_MISMATCH")
    out["valid"] = not [f for f in out["failures"]
                        if not f.startswith("NOTE:")]
    return out
```

Gate key renamed `parity_output_absent` -> `parity_output_state_valid`, with the
old name retained in prose only. Append-only is checked against a recorded count
so truncation or rewriting of evidence is detected.

Requires new failure-mode controls (declared/undeclared x present/absent,
unparseable line, missing field, mismatch record, truncation) and a rebaseline.

---

## First-cycle checklist

Manual, observed, off-cron. Next cron cycle is 13:00 UTC.

### Before activation

- [ ] `pgrep -af "daily_report.py|run_ares.sh|restart_gateway.sh"` -> empty
- [ ] `date -u` -> comfortably before 13:00 UTC
- [ ] `git status --short` -> empty
- [ ] `git rev-parse --short HEAD` -> the reviewed activation commit
- [ ] Snapshot B archived (`/root/snapshot_B_pre.txt`, `/root/snapshot_B_vps.json`)
- [ ] `ls logs/tracker_parity_v1.jsonl` -> absent
- [ ] `md5sum logs/virtual_trades.json logs/psi_state.json` -> record; expect
      `d812643821804aa3356804108a91255b` and `7a65c21bcb2056b426a887ff6100be2e`
      if no cycle has run since deployment
- [ ] Record the five open positions and their stored state (ABM, SDGR, SECZ,
      TMO, WBD): entry date, stop_loss, trailing_stop, peak
- [ ] IBKR gateway in the intended state for live-price coverage
- [ ] `grep -n "ARES_PARITY" run_ares.sh` -> exactly one active export, above the
      `daily_report.py` line
- [ ] `python3 tools/pre_parity_snapshot.py` -> `collection_declared: true`,
      `state: ARMED_NOT_STARTED`, `records: 0`, all checks pass
- [ ] `bash tools/vps_phase3_verify.sh` -> 9/9 PASS, exit 0

### Invocation

```sh
cd /root/ares/Ares
bash run_ares.sh 2>&1 | tee /tmp/ares_phase4_cycle1.txt
```

Run the real script so the activation path itself is exercised, rather than
setting the variable by hand — a hand-set variable would not prove the committed
placement works.

### During the cycle

- [ ] Inline tracker output appears as normal, in the usual order
- [ ] No position evaluated twice
- [ ] No parity traceback
- [ ] No genuine inline error suppressed
- [ ] `check_open_trades` not re-called as a fallback
- [ ] No unexpected network acquisition by parity

### Immediately after

- [ ] attempted > 0
- [ ] written == attempted
- [ ] missing records == 0
- [ ] `DECISION_CHANGING_MISMATCH` == 0
- [ ] `SHADOW_CONTRACT_INVALID` == 0
- [ ] `INVALID_SHADOW_RECORD` == 0
- [ ] `INLINE_INPUT_NOT_CAPTURED` == 0 unless a real inline failure occurred
- [ ] production-state mutation incidents == 0
- [ ] `state == ACTIVE_VALID` and `records > 0`
- [ ] `append_only.prefix_preserved == true`, `history_append_only` not false
- [ ] the first-cycle bot commit contains only approved runtime log paths plus
      the new parity evidence, and no code

Frozen `shadow_*` field names are used verbatim. Do not rename during activation.

### Required position coverage

**ABM** — required equality boundary. `trailing_stop == stop_loss` preserved;
equality attributes a stop at that level as `stop_loss`; any one-cent separation
detected.

**SDGR** — required giveback dead-band lifecycle. Correct stored values; captured
live price is the exact inline price; trail state and attribution agree.

**SECZ** — supplemental dead-band coverage only. Must not substitute for missing
or failed SDGR coverage.

### State integrity

Compare `virtual_trades.json` and `psi_state.json` after the cycle against the
expected normal tracker changes. Legitimate inline changes (peak ratchet, normal
exit) are allowed. The test is whether parity introduced any change beyond the
inline result.

### If clean

Archive the JSONL. Write a first-cycle inspection summary. Confirm complete
lineage on every record and the runtime price-source distribution. Continue
collection under cron, monitoring each cycle. Keep ABM and SDGR under inline
Policy A until natural closure and require their final exit bars in the evidence.
Do **not** proceed to Phase 5.

### If a problem is found

Keep the inline result. Comment out `export ARES_PARITY=1`. Preserve the parity
file and `/tmp/ares_phase4_cycle1.txt`. Classify the problem. Do not delete or
rewrite evidence. Do not replay the trading cycle — a parity-system failure is
not a reason to re-run inline. Phase 5 remains prohibited.

---

# Post-activation record — Sep 29–30, 2026

Everything above is the plan as written *before* activation. This section records
what actually happened and is appended rather than merged, so the plan and the
outcome stay separable.

## Activation timeline

| UTC | Event |
|-----|-------|
| 2026-09-29 03:30 | declaration commit `64f6b9c` authored |
| 2026-09-29 ~05:31 | manual cycle runs the **external** launcher — no `ARES_PARITY`, **SDGR exits unobserved** |
| 2026-09-29 05:35 | repo `run_ares.sh` lands on the VPS working tree |
| 2026-09-29 05:52:22 | **first parity record** — activation effective |
| 2026-09-29 09:31 | crontab repointed to `/root/ares/Ares/run_ares.sh` |
| 2026-09-29 13:30 | first *automated* cycle |
| 2026-09-29 21:00 | **ABM exits, captured, MATCH** |

`activation_effective_at` is recorded as `2026-09-29T05:52:22.528410+00:00` with
source `first_parity_record` and confidence `upper_bound`. It is an upper bound by
construction: collection was certainly active by then and may have become
effective slightly earlier. The commit timestamp is deliberately **not** used —
authoring a declaration is not deploying it, and the 8-hour gap above is exactly
why.

## The closing requirement above was only half met

The plan's final instruction was:

> Keep ABM and SDGR under inline Policy A until natural closure and require their
> final exit bars in the evidence.

Both closed naturally. Only one produced the required evidence.

| Symbol | Natural closure | Final exit bar in evidence | Resolution |
|--------|----------------|----------------------------|------------|
| ABM | 2026-09-29 `stop_loss` | **yes** — cycle `d800900e`, MATCH | `satisfied` |
| SDGR | 2026-09-28 `trailing_stop` | **no** — exited ~17 min pre-activation | `unobtainable`, retired |

SDGR's requirement is retired with documented cause. Retirement changes only the
*disposition*; `coverage_state` remains `unobtainable` permanently, so the missing
evidence is never restated as success. The accounting keeps the denominator
visible:

```
obligations 2 | satisfied 1 | pending 0 | retired_unobtainable 1 | unresolved 0
empirically_complete      False     <- evidence is genuinely missing
administratively_resolved True      <- nothing blocks
```

Those two flags must never collapse into one number.

## Change sets added after activation

| Item | Commits | Effect |
|------|---------|--------|
| 2A | `b4ceb41`, `f809cef` | version-aware evidence schema; v1's 45 fields frozen as a literal so the original records are judged against their own schema |
| 2A′ | `9f9ccb3`, `8513ed0` | decision-result telemetry registered in the tracker-diff proof as exact AST subtree contracts |
| 2B | `8a43064`, `393acea` | **capture the exact unrounded inline `effective_stop`** instead of reconstructing it from rounded state |
| 2C | `99c6b08` | baseline for schema v2 |
| 1 | `d335471`, `90b6b71` | collection heartbeat, schedule-aware freshness, host-role marker, activation provenance |
| display | `5f94c8f`, `08619fc` | collection health surfaced in the verifier report |
| gates | `50a42a7`, `cb9d92b` | Phase 5 gate registry and coverage dispositions |

`engine/tracker.py` changed exactly once in this sequence (Item 2B, +11 lines,
three registered statements) and its hash has been `3c23d191…` ever since.

## Operational contracts now enforced

- **Collection schedule** — weekday cycles at 13:30 and 21:00 UTC plus a
  90-minute grace, checked against the live crontab. A fixed 36h or 48h staleness
  rule was rejected: the Friday 21:00 → Monday 13:30 gap is 64.5h and would
  false-alarm every weekend.
- **Host role** — read from `/root/ares/.ares_live_host` containing
  `ARES_LIVE_HOST_V1`, not inferred. The previous inference read "live" on any
  checkout that had pulled the bot's log commits, including the laptop.
  The marker is **operational configuration and is not committed**; a new
  production host must have it provisioned manually.
- **Gate split** — `deployment_integrity_valid` remains the exit-code authority
  so a stale collector never blocks deploying its own repair, while
  `parity_collection_recent` blocks Phase 4 readiness and Phase 5.
- **Phase 5** — gated by a fixed registry where an absent or unevaluated gate is
  itself a blocker. `minimum_closed_sample` is deliberately unregistered and
  reports `UNEVALUATED`; choosing a threshold with the closed count already
  visible would be fitting the bar to the data. Operator authorization is a
  separate human decision and is absent.

Phase 5 remains **prohibited**.
