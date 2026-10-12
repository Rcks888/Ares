# Activation checklist — monitor-path recorder v1

Branch `recorder/monitor-observer-v1`. **Not activated.** This document is the
deployment and rollback review requested at the 2026-10-09 checkpoint; it does
not itself authorise anything.

Scope of what activation buys: the recorder closes **future** observation loss on
the monitor path. It does **not** decide which exit policy should govern
production, and it does not repair history. The five lost monitor exits remain
unobtainable and blocking.

---

## 1. Propagation mechanism — VERIFIED, and not what I claimed

My earlier statement that a `main` commit runs "at the next cycle" was **wrong**.
Verified against both launchers:

| | `run_ares.sh` | `run_monitor.sh` |
|---|---|---|
| `python <entrypoint>` | line 16 | line 7 |
| `git add` / `commit` | lines 30–31 | after Telegram |
| `git push`, then `pull --rebase` **only if the push failed** | lines 40–41 | at the end |

Two consequences:

1. **The pull runs AFTER the python process.** So a cycle that pulls new code
   executed the OLD code. The earliest cycle that can execute new code is the
   one *after* the pull. There is a one-cycle lag.
2. **The pull is conditional on the push failing.** If a cycle has nothing to
   commit, `git push` succeeds trivially, the `if` is not taken, and the new
   code **never arrives**. Log files change every cycle so a commit is usual —
   but arrival is incidental, not guaranteed.

Therefore: **merging is not deploying.** Auto-propagation is an unverified
side effect of a backup routine and is explicitly **out of scope for this
approval**. Deployment must be an explicit, verified step.

Required chain, each link confirmed before the next:

```
reviewed commit reaches main
        ↓
VPS receives THAT EXACT commit        (verify by SHA, not by "git pull said ok")
        ↓
loaded source + config fingerprints verified
        ↓
first monitor cycle observed
```

---

## 2. Window

**Any weekday gap from the completion of the 21:00 UTC cycle to the next 13:30
UTC cycle (16.5 hours; 05:00 → 21:30 MYT) qualifies.** No scheduled Ares process
runs inside it. The 64.5-hour weekend window below was the one reserved for the
original Mon 12 Oct activation, which was put on HOLD. It is kept as history.

**64.5 hours**, from the completion of the Friday 21:00 UTC cycle to the start of
the Monday 13:30 UTC cycle.

An earlier draft said 16.5 hours. That was wrong, and both parties computed it:
it assumed a Saturday 13:30 UTC boundary. Every Ares crontab entry is `1-5`,
weekday-only, so after Friday 21:00 the next scheduled Ares process is Monday
13:30. The extra 48 hours are real and are the reason deployment need not be
rushed.

Note also that the first cycle to execute the recorder is **Monday 16:10 UTC,
not 13:30**. The 13:30 cycle runs `run_ares.sh` → `daily_report.py`, which never
invokes `monitor_trades.py`.

Start only after the 21:00 cycle has **actually completed** — confirmed by its
heartbeat and its log commit, not by the clock. Before starting, confirm no other
scheduled process can modify the same files during the work:

```
13:00, 16:00, 17:25 UTC   restart_gateway.sh   (dependency, not a decision path)
13:30, 21:00 UTC          run_ares.sh
16:10, 17:30 UTC          run_monitor.sh
```

The 21:00 → 13:30 gap is the only window containing no scheduled process at all.

---

## 3. Pre-deployment acceptance

Every item read-only except where stated.

### 3.1 Preserve current evidence and running-source fingerprints

```sh
cd /root/ares/Ares
date -u
git rev-parse HEAD
git status --porcelain                       # expect clean
md5sum monitor_trades.py engine/tracker.py engine/data_feed.py \
       engine/indicators.py config/strategy_params.json daily_report.py
wc -l logs/tracker_parity_v1.jsonl logs/parity_heartbeat_v1.jsonl \
      logs/queue_events.jsonl
md5sum logs/tracker_parity_v1.jsonl logs/parity_heartbeat_v1.jsonl
cp logs/virtual_trades.json /root/ares/preact_virtual_trades.json
```

Record all of it. The parity line count and digest are the baseline for
"existing parity evidence preserved unchanged".

### 3.2 Verify the reviewed decision logic is unchanged

The one check that matters most: strip **only** the registered observation
additions from `monitor_trades.py` and confirm what remains is byte-identical to
the reviewed pre-activation file.

```sh
cd /tmp/ares_review
python3 tools/verify_recorder_isolation.py \
    tests/fixtures/monitor_trades_preactivation.py monitor_trades.py
echo "EXIT=$?"
```

**Acceptance: `only registered observation additions found; all other logic
aligned identically`, no `PROBLEM:` lines, exit 0.**

Do not use a text diff, and do not use added/removed line counts, for this
check. A previous draft of this section accepted "exactly one removed line and
120 added lines", which is satisfied by any change that happens to balance.

#### How the verifier establishes this

It aligns the two files recursively, scope by scope, and requires **every**
difference to be an exactly registered subtree, identified by the sha256 of its
normalised `ast.unparse`:

- additions must appear in `tools/recorder_registration.py`
- the single removal — the bare `monitor()` call — must appear there as a
  registered replacement
- exactly one entry-point wrapper may be added

Because the alignment proves everything else is equal, there is no separate
"compare the remainder" pass that could be fooled. Registration is by **whole
subtree**, so an extra statement inside a registered block changes its digest
and the block simply stops being registered.

`ast.unparse` regenerates source from the tree, so comments and whitespace are
absent and a formatting-only change compares equal. Such changes remain visible
in the human-readable diff, which is where they belong.

#### Why the previous verifier was replaced

It removed statements by broad rules — "the leftmost name is `obs`", "this is
the `if _obs is not None:` block", "the `finally` body mentions flush" — which
created an exemption zone. Review independently reproduced **three defective
candidates that all returned `IDENTICAL`, exit 0**:

| Defect | How it evaded the old rules |
|---|---|
| `obs = trade["stop_loss"] = {...}` | first target was `obs`, so the statement was stripped and the second, production target never inspected |
| `trade["stop_loss"] = 0` inside the `if _obs is not None:` block | the whole block was removed without reading its contents |
| `finally: ... return_value = "flush"; raise RuntimeError(...)` | the substring `"flush"` appeared — inside a string literal — in a body that replaced the production exception |

All three are now rejected, and each is a standing negative control rather than
a one-off patch:

```sh
cd /tmp/ares_review && python3 tests/run_verifier_negative_controls.py
echo "EXIT=$?"
```

**Acceptance: 14/14, exit 0.**

| Negative control | Must be rejected |
|---|---|
| 00 | the approved file must still PASS, or every rejection below is vacuous |
| 01 | chained production target on an observation assignment |
| 02 | production mutation inside the registered configuration block |
| 03 | exception replaced behind the word "flush" |
| 04 | a persistence call inside an observation assignment |
| 05 | an acquisition call inside an observation assignment |
| 06 | a `return` in `finally` |
| 07 | an additional handler that swallows the original exception |
| 08 | a production statement adjacent to a registered observation line |
| 09 | deletion of decision logic |
| 10 | a second entry-point wrapper |
| 12 | a **duplicated** registered statement |
| 13 | a **removed** registered statement |
| 14 | a missing registration table — must fail closed, not pass vacuously |

Controls 12 and 13 exist because registration initially pinned only the *shape*
of each addition, not how many times it may occur. Doubling
`_MONITOR_EVAL["attempted"] += 1` — which double-counts the population and
breaks reconciliation — passed. The registry now records an expected occurrence
count per key and the verifier requires an exact match in both directions.

#### Reconciling the accepted count

The verifier reports **47 accepted** against **46 registered addition keys**.
That is not a discrepancy:

```
46  registered additions, each registered 1x, each found exactly 1x
 1  registered replacement: the bare `monitor()` call
--
47  accepted items
```

Additions and the replacement are both reported as accepted. There are currently
**no repeated occurrences** — every key is registered `1x` — so accepted
additions equal registered addition keys exactly. Had any statement been
registered with a count above 1, the accepted total would exceed the key count by
the excess, and the verifier prints both numbers on the `additions :` line so the
arithmetic is checkable without reading the table.

The registration table is committed evidence, not configuration. A digest or
count change must be reviewed alongside the source change that caused it;
regenerating it to make the verifier pass defeats the control.

### 3.3 Confirm recorder failure cannot alter decisions, persistence or exceptions

```sh
git worktree add /tmp/ares_review origin/recorder/monitor-observer-v1
cd /tmp/ares_review
python3 tests/run_monitor_observer_controls.py        ; echo "EXIT=$?"
python3 tests/run_verifier_negative_controls.py       ; echo "EXIT=$?"
python3 tests/run_entrypoint_containment_controls.py  ; echo "EXIT=$?"
python3 tests/run_inspection_controls.py              ; echo "EXIT=$?"
```

**Acceptance: 44/44, 14/14 and 13/13, each exit 0. These are the 71 controls on
the recorder itself.** In addition, **25/25** inspection controls (exit 0)
establish that the §5.0 post-activation tool cannot pass a defective or
uninspectable history.

| Concern | Controls |
|---|---|
| exception propagation, **real deployed entry point executed** | containment 51, 54 |
| exception propagation, AST of the real `__main__` | 28 |
| recorder unavailable must not stop trading, and must not be silent | containment 52 |
| recorder cannot write must be distinguishable from success | containment 53 |
| recorder failure cannot mask a production exception | containment 54 |
| close recorded as four separate facts | containment 55, observer 34–36 |
| failed save not reported as completed | containment 56, observer 42 |
| no decision/persistence mutation | 26 |
| no market-data acquisition | 25, verifier-negative 05 |
| parity evidence untouched | 27, containment 57 |
| incomplete observation cannot become coverage | **30**, 31, 40, 41, 22, 23 |
| full invocation reset | **43**, 44 |
| isolation under I/O failure | 01, 02 |

Controls 30 and 43 exist because review reproduced the defects they catch: an
incomplete observation reporting `coverage_complete: true`, and invocation
metadata surviving into a later cycle.

The containment suite executes `monitor_trades.py`'s **own bytes** through
`runpy` with every engine dependency stubbed, asserting the copy's sha256 equals
the deployed file first. It replaced a control that tested a hand-written
replica of `__main__` and passed an exception-swallowing mutation the real file
would have failed.

Also compare the live-tree digests against the manifest the worktree computes,
since control 13 only sees the worktree:

```sh
cd /tmp/ares_review && python3 -c "
from engine import monitor_observer as mo
import json; print(json.dumps(mo.manifests()['decision']['digests'], indent=1))"
```

Every digest except `monitor_trades.py` must equal §3.1.

### 3.4 Register output paths and schedules before their first write

| | |
|---|---|
| Evidence | `logs/monitor_observation_v1.jsonl` |
| Heartbeat | `logs/monitor_heartbeat_v1.jsonl` |
| Observation health | `logs/monitor_health_v1.jsonl` |
| Schedule | 16:10 and 17:30 UTC, Mon–Fri |
| Must not be read by | dashboards, exporters, `clean_v3` statistics, Telegram, training datasets |
| Must never be merged with | `logs/tracker_parity_v1.jsonl`, `logs/parity_heartbeat_v1.jsonl` |

Confirm neither file exists yet, so the first write is unambiguous:

```sh
ls -l logs/monitor_observation_v1.jsonl logs/monitor_heartbeat_v1.jsonl \
      logs/monitor_health_v1.jsonl 2>&1
```

The health file exists so that three otherwise identical silences can be told
apart. It is written by a path that needs no recorder, because the condition it
reports may be that the recorder is unimportable:

| Observed | Meaning |
|---|---|
| a heartbeat line | the recorder ran |
| a health line, no heartbeat | the recorder was unavailable, or could not write |
| neither | the monitor itself never ran |

**Known limitation, accepted:** an OOM kill sends `SIGKILL`, which Python cannot
catch, so `finally` never runs and nothing at all is written — indistinguishable
from "the monitor never ran". There were zero OOM kills in the seven days
checked. A cycle-open record would close this gap and is not part of v1.

### 3.5 Identify and pre-test the rollback

**Use a single squashed activation commit with exactly one parent.**

A `--no-ff` merge commit has two parents, so reverting it requires choosing a
mainline with `-m` — a judgement call that should not be made during an
incident. `git merge --squash` produces one ordinary commit containing the whole
change, whose inverse is unambiguous.

```sh
cd /root/ares/Ares
git log --oneline -1                      # record the pre-activation SHA: <PRE>
```

The inverse must be tested against the **actual activation commit**, which means
creating it locally first and only then pushing. An inverse tested against a
differently-shaped commit proves nothing about the one that gets deployed.

#### What the rollback must and must not restore

An earlier draft required `git diff --stat <PRE> HEAD` over the **whole
repository** to be empty. That is wrong, and it would have failed in practice.
The bot commits log files every cycle, so by the time a rollback is needed the
tree legitimately differs from `<PRE>` in `logs/`. A whole-repository criterion
would either fail spuriously or, worse, invite reverting operational state.

Rollback is scoped to **deployment-owned** paths. Newer operational state and
evidence must be **preserved**:

| Path | On rollback |
|---|---|
| `monitor_trades.py` | restored to `<PRE>` |
| `engine/monitor_observer.py` | removed |
| `tools/verify_recorder_isolation.py`, `tools/recorder_registration.py` | restored / removed |
| `tests/` recorder suites, fixtures, driver | restored / removed |
| `*.md` activation and design documents | restored |
| `.gitignore` (the three recorder re-includes) | restored. Evidence files already committed stay tracked, because ignore rules do not untrack files |
| `logs/` **— all of it** | **untouched. Never reverted.** |

`logs/` includes `virtual_trades.json` (authoritative position state),
`tracker_parity_v1.jsonl`, `queue_events.jsonl`, and any
`monitor_observation_v1.jsonl` / `monitor_heartbeat_v1.jsonl` /
`monitor_health_v1.jsonl` the recorder produced before the rollback. That
evidence is the reason the recorder was deployed; discarding it during a
rollback would destroy the record of whatever prompted the rollback.

The activation commit touches no file under `logs/`, so its inverse cannot
either. The check below confirms that rather than assuming it.

```sh
# after creating <ACTIVATION_SHA> locally (step 1 below), BEFORE pushing:
cd ~/Olympus/Ares
git clone -q --no-hardlinks . /tmp/ares_revert_test
cd /tmp/ares_revert_test
git log --oneline -1                      # must show <ACTIVATION_SHA>

# 1. the activation commit must own no operational state.
#    A git failure or an empty file list is a FAIL, never "ok".
if ! files=$(git show --name-only --format= <ACTIVATION_SHA>); then
    echo "FAIL: git show failed"
elif [ -z "$files" ]; then
    echo "FAIL: activation commit lists no files"
elif grep -q '^logs/' <<<"$files"; then
    echo "FAIL: activation touches logs/"
else
    echo "ok: activation owns no logs/ ($(wc -l <<<"$files") files)"
fi

# 2. the inverse must apply
git revert --no-edit <ACTIVATION_SHA> && echo "INVERSE APPLIES CLEANLY"

# 3. deployment-owned paths must be restored EXACTLY
git diff --stat <PRE> HEAD -- monitor_trades.py engine/ tools/ tests/ '*.md' .gitignore
#    must be EMPTY

# 4. operational state must NOT have been reverted
git diff --name-only <PRE> HEAD -- logs/
#    whatever appears here is preserved newer evidence, which is correct

python3 -c "import ast; ast.parse(open('monitor_trades.py').read())"
md5sum monitor_trades.py                  # must equal the §3.1 value
cd ~ && rm -rf /tmp/ares_revert_test
```

**Acceptance: step 1 reports `ok`, step 3 is empty, and step 2 succeeds.** Step
4 is informational — a non-empty result there is expected and correct once the
bot has committed logs.

On the VPS the same scoping applies, and the local restore is the fast path
because it does not wait on git propagation at all:

```sh
cd /root/ares/Ares
git checkout <PRE> -- monitor_trades.py
rm -f engine/monitor_observer.py
md5sum monitor_trades.py                  # must equal the §3.1 value
git status --porcelain logs/              # whatever is here STAYS
```

Only `monitor_trades.py` and `engine/monitor_observer.py` are executed by cron.
`tools/` and `tests/` are inert at runtime and need not be removed urgently.

`git revert` is correct for published history because it adds a reversing commit
rather than rewriting. Two caveats, both load-bearing:

- it requires a **clean working tree**, which the VPS may not have mid-cycle; and
- **a revert on `main` is not a runtime rollback.** It must reach the VPS through
  the same verified deployment step as the activation.

Fastest true rollback is local and does not wait on git at all:

```sh
cd /root/ares/Ares
git checkout <PRE> -- monitor_trades.py && rm -f engine/monitor_observer.py
md5sum monitor_trades.py          # must equal the §3.1 value
```

That restores the reviewed decision file exactly. The `main` revert then follows
for history.

---

## 4. Deployment procedure

Explicit, inside the window, after the 21:00 cycle has completed.

```sh
# 1. laptop: isolated activation commit on main
cd ~/Olympus/Ares
git checkout main && git fetch -q origin
git rev-list --left-right --count origin/main...HEAD      # expect 0  0
git merge --squash recorder/monitor-observer-v1
git commit -m "activate: monitor-path recorder v1"        # <ACTIVATION_SHA>
git log --oneline -1 --parents                            # exactly ONE parent
# --- now run the §3.5 inverse test against <ACTIVATION_SHA>, then: ---
git push origin main

# 2. VPS: receive THAT EXACT commit
cd /root/ares/Ares
git status --porcelain                                    # must be clean
git fetch -q origin
git merge --ff-only origin/main
git rev-parse HEAD                                        # must equal <ACTIVATION_SHA>

# 3. verify what is actually on disk
md5sum monitor_trades.py engine/monitor_observer.py
python3 -VV
python3 -c "import ast; ast.parse(open('monitor_trades.py').read()); print('parses')"
python3 tests/run_monitor_observer_controls.py             # 44/44 on the live tree
python3 tests/run_verifier_negative_controls.py            # 14/14
python3 tests/run_entrypoint_containment_controls.py        # 13/13
python3 tests/run_inspection_controls.py                    # 25/25 (§5.0 tool)
python3 tools/verify_recorder_isolation.py \
    tests/fixtures/monitor_trades_preactivation.py monitor_trades.py
```

If step 2's `--ff-only` fails, the VPS has local commits. **Stop.** Resolve that
separately; do not rebase during activation.

**Do not infer success from the launcher's exit status.** `run_monitor.sh` pipes
python through `tee`, so `$?` is `tee`'s status and a failed cycle can look
clean. Use the heartbeat and the traceback in `/root/ares/cron.log` instead.
Changing the launcher — adding `set -o pipefail`, or capturing
`${PIPESTATUS[0]}` — would be a change to a shared production script and is
**out of scope for this activation**; it needs its own isolated review.

---

## 5. First monitor cycle inspection

The first cycles are 16:10 and 17:30 UTC (00:10 and 01:30 MYT).

**Decided 2026-10-12: inspect the next morning (MYT), after both cycles.** That
replaces "inspect before 17:30", which would have meant staying up past 00:10
MYT.
- **Accepted cost:** a recorder defect present in the first cycle repeats in
  the second before anyone looks.
- **Why that is acceptable:** the recorder cannot change a monitor decision,
  output, exception or production state; the 71 controls and the isolation
  verifier test exactly that. A second cycle risks a second bad record, not a
  trading consequence.
- **Inspect both cycles.** Each must pass §5.1–§5.3 on its own. The second
  must show the per-invocation reset: a new `cycle_id` and `cycle_started_at`,
  and no carry-over from the first.

**Never use `invocation_token` to tell cycles apart.** It is a process-local
counter, and every scheduled run is a new process, so every cycle carries token
`1`. Cycles are identified by `cycle_id`, which is minted fresh by each flush,
or by the bot commits that delimit them (`<R>`, `<M1>`, `<M2>`, §5.3).

The files are JSON Lines (one record per line), so after two cycles a plain
`json.tool` fails with "Extra data". Use `--json-lines`:

```sh
cd /root/ares/Ares
python3 -m json.tool --json-lines logs/monitor_heartbeat_v1.jsonl
wc -l logs/monitor_observation_v1.jsonl logs/monitor_heartbeat_v1.jsonl
python3 -m json.tool --json-lines logs/monitor_observation_v1.jsonl
```

### 5.0 Run the inspection tool first

The per-cycle checks in §5.1–§5.8 are carried out by
`tools/inspect_monitor_cycles.py`, not by hand-run snippets. Two hand-run
snippets in bundle v3 gave false results: one grouped cycles by the
process-local `invocation_token`, the other printed "ok" when `git` failed.
The v4 tool still trusted three producer claims (see the v5 review bundle). The
tool is fail-closed, trusts no claim the producer makes about itself, and has
its own controls (`tests/run_inspection_controls.py`, 25, with each of its 18
checks mutation-tested).

```sh
cd /root/ares/Ares
git log --format='%h %p | %ci %s' -6 -- logs/
#   <R>  = the commit immediately before the first monitor cycle (normally
#          "Ares V3 report ..._13:3x")
#   <M1> = the 16:10 monitor commit, whose parent must be <R>
#   <M2> = the 17:30 monitor commit, whose parent must be <M1>
venv/bin/python tools/inspect_monitor_cycles.py <R> <M1> <M2>; echo "exit $?"
```

| Exit | Meaning | Action |
|---|---|---|
| 0, `RESULT: PASS` | every cycle is complete, reconciled acceptance evidence | continue with the manual reading in §5.3 and §5.5 |
| 2, `RESULT: INCONCLUSIVE` | truthful but not acceptance evidence: an aborted cycle (§5.2) or observation unavailable (§5.8) | read the reason; no acceptance yet; not a rollback by itself |
| 1, `FAIL: …` | a defect, **or the inspection could not be carried out** (bad reference, a git error, commits that do not delimit exactly one cycle) | never read as a pass. Fix the reference and rerun, or treat as a defect under §6 |

Per cycle, the tool checks:
- `<M>`'s parent is exactly the previous reference, and its subject is a monitor commit.
- The monitor commit changed neither parity file.
- **Append-only, by bytes.** For each recorder file, the content at the
  previous reference must be an exact byte prefix of this cycle's content,
  ending on a line boundary. Only the appended suffix is parsed. A "no removed
  lines" diff check is not used: it accepts evidence inserted before existing
  records.
- Exactly one heartbeat, or a health line only; never both.
- **Identity against all history.** `cycle_id` and `cycle_started_at` must be
  absent from every heartbeat and observation already present at `<R>`, as
  well as from earlier supplied cycles.
- **Heartbeat schema.** The exact registered key set and population key set,
  the schema versions, and `decision_path: monitor`.
- **Every record validated independently.** The tool's own copy of the frozen
  schema and outcome contract is checked: the exact key set, values that are
  never null, types, the non-null, null, falsy, true and false rules for each
  outcome, the allowed exit and skip reasons, and the derived values
  (`live_available`, both `*_rounded_2dp` fields, `peak_price_precision_basis`,
  `exit_date_basis`). The cycle's `cycle_id` and `repository_commit` must
  also match. `record_complete: true` is required but never sufficient.
  Inspection control 21 pins the tool's copy to the recorder's constants and to
  a real heartbeat, so drift on either side is a FAIL.
- `open_positions_seen` equals the number of positions open at the previous
  reference, with one record per open symbol, no duplicates and no extras.
- **Population recomputed.** The heartbeat's `population` must equal the
  counts recomputed from the validated records.
- The full conjunctive heartbeat state in §5.1.
- In an aborted cycle, a record honestly marked `record_complete: false` is
  allowed (INCONCLUSIVE). A record claiming completeness must still pass the
  full contract.

A cycle with **no** commit at all means the monitor wrote nothing (it never ran,
or was SIGKILLed). The tool cannot be given such a cycle; treat it as the
"neither" state in §5.8.

### 5.1 Heartbeat, normal completed cycle

```
attempted == written
write_failures == 0
manifests_complete == true          (all three manifests)
monitor_completed == true
population.reconciled == true
population.not_reached == 0
population.incomplete == 0          (counted by record_complete, not by label)
population.incomplete_labelled == 0
population.duplicate_symbols == []
population.attempted_matches_records == true
coverage_complete == true
evaluated + skipped + incomplete + not_reached == open_positions_seen
```

`incomplete` is counted by `record_complete`, **not** by the `decision` label.
Review reproduced a record with `record_complete: false` in a cycle reporting
`coverage_complete: true`, because a record labelled `hold` was counted as
evaluated without checking whether it satisfied the `hold` contract. A gap
between `incomplete` and `incomplete_labelled` means a producer claimed an
outcome it could not substantiate.

The population reconciliation is required precisely because `attempted ==
written` is satisfiable by a cycle that recorded everything it attempted and
silently never attempted the rest. Control 22 demonstrates that case: 2 of 2
written, 3 never reached, `coverage_complete: false`.

### 5.2 Heartbeat, aborted cycle

`monitor_completed: false` with a populated `loop_exception` is **truthful
evidence, not a recorder defect.** Expected in that case:

```
monitor_completed == false
loop_exception    populated
not_reached       > 0
coverage_complete == false
```

And the original production exception must still have propagated — visible as a
non-zero exit and the traceback in `/root/ares/cron.log`. If the cycle aborted
but no traceback appears, the recorder swallowed it: **roll back**.

### 5.3 Per-observation

Validate the file **line by line**, not by eyeballing the last record — a single
malformed line is the failure mode that matters:

```sh
python3 -c "
import json,sys
bad=0
for i,l in enumerate(open('logs/monitor_observation_v1.jsonl'),1):
    if not l.strip(): continue
    try: json.loads(l)
    except Exception as e: bad+=1; print('line',i,'invalid:',e)
print('invalid lines:',bad); sys.exit(1 if bad else 0)"
```

For each record confirm: the trigger price (`observed_live`), both stop
precisions (`effective_stop_unrounded` and `effective_stop_rounded_2dp`), the
state movement (`peak_price_after`, `peak_advanced`, `trail_advanced`), the
action (`decision`, `exit_reason`), and — for a close — all four close facts
(`close_requested`, `close_price_argument`, `close_function_completed`,
`booked_exit_price`).

Field names changed during review. `*_persisted` became `*_rounded_2dp`,
because rounding a captured value reconstructs what `round()` would store and
does **not** prove `save_trades` ran. Persistence is an invocation fact and
appears in the heartbeat as `trade_state_save_attempted` /
`trade_state_save_completed`. `ratcheted` split into `peak_advanced` and
`trail_advanced`, because a new peak need not move the trailing stop.

Reconcile expectations against the **actual** starting state of **each** cycle,
not against the deployment snapshot. Two reasons:

- The 13:30 UTC report cycle runs *before* the first monitor cycle and can
  legitimately open, close or ratchet positions.
- Cycle 1 can close a position before cycle 2 starts.

The bot's own commits delimit each cycle. Because the recorder's files are now
re-included in `.gitignore`, every monitor cycle that writes evidence produces
a commit. The §5.0 tool requires one record per symbol open at `<R>` for
cycle 1, and at `<M1>` for cycle 2, with no others. It groups records by
`cycle_id` within each cycle's own commit, never by `invocation_token`. Any
symbol that
returns no live price must appear as `decision: skipped`,
`skip_reason: live_price_unavailable_cause_unknown`,
`effective_stop_unrounded: null`, `record_complete: true` — a skip is complete
evidence, not a defect. Expect no `closed` record unless a stop was genuinely
hit.

### 5.4 No additional market-data acquisition

**This cannot be confirmed from stdout, and the earlier draft's `grep` of the
monitor log was not evidence.** The monitor does not log one line per price
request, so a count of matching output lines measures formatting, not requests.
The same mistake in an earlier version of control 25 matched `get_live_price`
inside an explanatory comment.

What is actually established:

- **Statically**, control 25 extracts identifiers from the recorder's syntax
  tree — `Name.id`, `Attribute.attr`, `Import`/`ImportFrom` — and confirms no
  acquisition call is present. Docstrings and comments are not in the tree, so
  they cannot produce a match.
- **Structurally**, verifier negative control 05 rejects an acquisition call
  placed inside an observation assignment.
- **At runtime**, each cycle's observation count must equal that cycle's
  open-position count from §5.3, so the recorder added no per-symbol work. The
  §5.0 tool enforces this per cycle.

**Known limitation, accepted:** instrumenting `data_feed` to count requests
directly would modify a shared production module and is out of scope. Until
that is done separately, "no additional acquisition" rests on the static and
structural evidence above, not on a runtime request count.

### 5.5 No recorder-induced production mutation

Compare each monitor cycle against the state committed just before it, never
against the deployment snapshot, which the 13:30 report cycle has already
legitimately changed:

Run this only after §5.0 has passed, because the tool verifies that the three
references exist and delimit one cycle each. An empty diff is meaningful only
then; with an unverified reference, an empty screen is not evidence.

```sh
git diff --exit-code <R>  <M1> -- logs/virtual_trades.json; echo "cycle 1 diff exit $?"   # 0 none, 1 changes, >1 error
git diff --exit-code <M1> <M2> -- logs/virtual_trades.json; echo "cycle 2 diff exit $?"
```

Differences are expected **only** where the monitor legitimately decided:
`peak_price`, `trailing_stop`, a close, and `holding_days` (which `save_trades`
refreshes on open positions whenever it saves). Any other change to a position,
or any change in a cycle where every decision was `hold` and
`trade_state_save_attempted` is false, is a mutation and means **roll back**.

### 5.6 Parity evidence preserved

The monitor path must never touch the parity files; only report cycles write
them. The §5.0 tool checks each monitor commit against its parent and reports a
FAIL if either parity file changed. A git error is also a FAIL, never "ok"
(inspection control 02). Offline, observer control 27 covers the same property.

### 5.7 Missing observations cannot become coverage

Enforced per cycle by the §5.0 tool. The earlier snippet here checked only the
**last** heartbeat (`[-1]`), so after two cycles it would never have examined
cycle 1. A cycle claiming `coverage_complete` with any incomplete component is a
FAIL (inspection control 14). An aborted cycle that claims coverage is also a
FAIL.

### 5.8 Observation health

```sh
ls -l logs/monitor_health_v1.jsonl 2>&1
grep -c OBSERVATION-HEALTH /root/ares/cron.log
```

**On a healthy cycle this file must not exist and the count must be 0.** If it
does exist, the recorder failed and the cycle is unobserved — read `reason` and
`detail`. That is a recorder fault, not a trading fault: containment controls 52
and 53 establish that the monitor still completes and still prints its normal
output in both cases.

A health line **without** a heartbeat in the same cycle is the expected shape
(the tool reports it as INCONCLUSIVE). A health line *and* a heartbeat added by
the **same monitor commit** would mean the flush partially succeeded; the tool
reports that as a FAIL.

The pairing is by commit, not by `invocation_token`. The token is `1` in every
cron process, and the health line carries no `cycle_id` (none has been minted
when the recorder fails to import). Token pairing would therefore pair every
health line with every heartbeat ever written.

---

## 6. Abort criteria

Roll back immediately on any of:

- a monitor decision differing from what the pre-activation code would have made
- any production-state change attributable to the recorder
- an exception that did not propagate, or a traceback absent from `cron.log` on
  a cycle whose heartbeat says `monitor_completed: false`
- any write to the parity evidence or heartbeat
- `coverage_complete: true` alongside a non-zero `not_reached`, `incomplete`,
  `incomplete_labelled`, `write_failures`, or a non-empty `duplicate_symbols`
- `record_complete: false` on any record in a cycle claiming coverage
- `trade_state_save_completed: true` on a cycle whose `loop_exception` shows the
  save itself raised
- an invocation whose `cycle_id` or `cycle_started_at` equals the previous
  invocation's (a reset failure). `params_resolved` and `open_positions_seen`
  legitimately repeat between consecutive cycles, so on their own they are not
  evidence of anything
- any of the three control suites failing on the live tree
- the isolation verifier reporting anything other than exit 0

Do **not** treat a `monitor_health_v1.jsonl` line as an abort criterion on its
own. It reports that observation failed, which is the recorder degrading exactly
as designed; investigate it, but trading is unaffected.

---

## 7. What activation does not settle

Admission capture, which needs its own registered diff because
`logs/queue_events.jsonl` is empty for a direct scan admission. The authoritative
exit policy. The disposition of the five lost monitor exits, still blocking. The
unregistered `MIN_CLOSED_SAMPLE` threshold. The pending-path drift guard, flagged
and unverified.

Agreed order after this:

```
conditional recorder activation
        ↓
first monitor-cycle inspection
        ↓
admission-source capture, separately reviewed
        ↓
evidence-informed decision on the authoritative exit policy
```

Phase 5, policy unification and AI modelling remain **prohibited**.
