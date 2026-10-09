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

**16.5 hours**, from the completion of a 21:00 UTC cycle to the start of the next
13:30 UTC cycle. Not 16.

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
cd /root/ares/Ares
git fetch -q origin
git show origin/main:monitor_trades.py > /tmp/monitor_before.py
git show origin/recorder/monitor-observer-v1:monitor_trades.py > /tmp/monitor_after.py
diff /tmp/monitor_before.py /tmp/monitor_after.py | grep '^<'
diff /tmp/monitor_before.py /tmp/monitor_after.py | grep -c '^>'
```

**Acceptance: exactly one removed line, exactly `    monitor()`, and 120 added
lines.**

The single removal is the bare `monitor()` call under `__main__`, replaced by the
`try/except/finally` that wraps the identical call. It is a removal in `diff`
terms only; the call itself survives inside the `try`. Control 28 proves the
replacement still calls `monitor()`, still re-raises bare, and still flushes in
the `finally`.

An earlier draft of this section asserted "no lines removed", which this check
immediately falsified. The corrected criterion names the one permitted removal
rather than tolerating removals in general.

Every added line must be a comment, an `obs[...]` or `_MONITOR_EVAL[...]`
assignment, the guarded import, or the entrypoint wrapper. **Any other removed
or altered line voids the review.**

To confirm no decision logic moved, use the purpose-built verifier rather than a
text diff:

```sh
cd /tmp/ares_review
python3 tools/verify_recorder_isolation.py /tmp/monitor_before.py /tmp/monitor_after.py
echo "EXIT=$?"
```

**Acceptance: `decision logic IDENTICAL after removing observation`, no
`PROBLEM:` lines, exit 0.**

It removes the registered observation additions from the syntax tree and compares
what remains. It unwraps the entrypoint wrapper **only after** confirming the
handler re-raises bare and the `finally` body does nothing but flush, so a
wrapper that swallowed the production exception fails rather than being
normalised away.

A line-based strip was tried first and abandoned: filtering comment lines also
removes pre-existing comments adjacent to additions, and removing statements by
prefix leaves dangling `try:` and `except:` headers. Both reported an unchanged
file as changed. Parsing avoids each — comments are absent from the tree, and a
statement is removed whole or not at all.

Demonstrated to detect the two repairs this design forbids:

| Injected change | Verdict |
|---|---|
| `trailing_stop_pct` default 0.08 → 0.10 | `DECISION LOGIC DIFFERS`, diff shown |
| entry-day skip added to the loop | `DECISION LOGIC DIFFERS`, diff shown |
| bare `raise` → `pass` in the handler | `PROBLEM: ... would be swallowed`, exit 1 |

### 3.3 Confirm recorder failure cannot alter decisions, persistence or exceptions

```sh
git worktree add /tmp/ares_review origin/recorder/monitor-observer-v1
cd /tmp/ares_review && python3 tests/run_monitor_observer_controls.py
```

**Acceptance: 29/29, exit 0.** The relevant controls:

| Concern | Control |
|---|---|
| exception propagation, deployed file | 28 (AST of the real `__main__`) |
| exception propagation, pattern | 24 |
| no decision/persistence mutation | 26 |
| no market-data acquisition | 25 |
| parity evidence untouched | 27 |
| failure cannot become coverage | 22, 23 |
| isolation under I/O failure | 01, 02 |

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
| Schedule | 16:10 and 17:30 UTC, Mon–Fri |
| Must not be read by | dashboards, exporters, `clean_v3` statistics, Telegram, training datasets |
| Must never be merged with | `logs/tracker_parity_v1.jsonl`, `logs/parity_heartbeat_v1.jsonl` |

Confirm neither file exists yet, so the first write is unambiguous:

```sh
ls -l logs/monitor_observation_v1.jsonl logs/monitor_heartbeat_v1.jsonl 2>&1
```

### 3.5 Identify and pre-test the rollback

**Use an isolated, clearly identified activation commit, not a merge commit.**
Reverting a merge requires choosing a mainline parent with `-m`, which is exactly
the kind of judgement call that should not be made during an incident.

```sh
cd /root/ares/Ares
git log --oneline -1                      # record the pre-activation SHA: <PRE>
```

Test the inverse **before** deploying, in the throwaway worktree:

```sh
cd /tmp/ares_review
git revert --no-edit --no-commit <ACTIVATION_SHA> && echo "INVERSE APPLIES CLEANLY"
git diff --stat
git revert --abort 2>/dev/null; git reset --hard
```

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
git merge --no-ff --no-commit recorder/monitor-observer-v1
git commit -m "activate: monitor-path recorder v1"        # <ACTIVATION_SHA>
git push origin main

# 2. VPS: receive THAT EXACT commit
cd /root/ares/Ares
git status --porcelain                                    # must be clean
git fetch -q origin
git merge --ff-only origin/main
git rev-parse HEAD                                        # must equal <ACTIVATION_SHA>

# 3. verify what is actually on disk
md5sum monitor_trades.py engine/monitor_observer.py
python3 -c "import ast; ast.parse(open('monitor_trades.py').read()); print('parses')"
python3 tests/run_monitor_observer_controls.py             # 29/29 on the live tree
```

If step 2's `--ff-only` fails, the VPS has local commits. **Stop.** Resolve that
separately; do not rebase during activation.

---

## 5. First monitor cycle inspection

The first cycle is 16:10 UTC. Inspect **before** 17:30.

```sh
cd /root/ares/Ares
cat logs/monitor_heartbeat_v1.jsonl | python3 -m json.tool
wc -l logs/monitor_observation_v1.jsonl
cat logs/monitor_observation_v1.jsonl | python3 -m json.tool
```

### 5.1 Heartbeat, normal completed cycle

```
attempted == written
manifests_complete == true
monitor_completed == true
population.reconciled == true
population.not_reached == 0
population.incomplete == 0
coverage_complete == true
evaluated + skipped + incomplete + not_reached == open_positions_seen
```

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

For each record, confirm captured: trigger price (`observed_live`), the
`effective_stop_unrounded` **and** `effective_stop_persisted_as`, the action
(`decision`, `exit_reason`), and the persistence outcome
(`trailing_stop_persisted`, `peak_price_after`, `ratcheted`).

Expect, given the current book:

- 5 records, one per open position
- **WBD** `decision: skipped`, `skip_reason: live_price_unavailable_cause_unknown`,
  `effective_stop_unrounded: null`, `record_complete: true`
- the rest `hold` or `state_update`
- no `closed` unless a stop was genuinely hit

### 5.4 No additional market-data acquisition

```sh
grep -c "get_live_price\|reqHistoricalData" /tmp/ares_monitor.txt
```

Price requests must equal the open-position count, unchanged from before
activation. Control 25 proves the recorder contains no acquisition call; this
confirms it at runtime.

### 5.5 No recorder-induced production mutation

```sh
diff <(python3 -m json.tool /root/ares/preact_virtual_trades.json) \
     <(python3 -m json.tool logs/virtual_trades.json)
```

Differences are expected **only** where the monitor legitimately decided —
`peak_price`, `trailing_stop`, or a close. Any change to a position the recorder
merely observed, or any change on a cycle where every decision was `hold`, is a
mutation and means **roll back**.

### 5.6 Parity evidence preserved

```sh
wc -l logs/tracker_parity_v1.jsonl logs/parity_heartbeat_v1.jsonl
md5sum logs/tracker_parity_v1.jsonl logs/parity_heartbeat_v1.jsonl
```

The monitor path must not have touched either. Digests must equal §3.1 until the
next **report** cycle runs. Control 27 covers this.

### 5.7 Missing observations cannot become coverage

```sh
python3 -c "
import json
b=[json.loads(l) for l in open('logs/monitor_heartbeat_v1.jsonl')][-1]
p=b['population']
print('claims coverage:', b['coverage_complete'])
print('residual       :', p['not_reached'], 'incomplete:', p['incomplete'])
assert not b['coverage_complete'] or (p['not_reached']==0 and p['incomplete']==0 \
    and b['write_failures']==0 and b['monitor_completed'])
print('coverage claim is conjunctive and consistent')"
```

---

## 6. Abort criteria

Roll back immediately on any of:

- a monitor decision differing from what the pre-activation code would have made
- any production-state change attributable to the recorder
- an exception that did not propagate
- an additional price request per symbol
- any write to the parity evidence or heartbeat
- `coverage_complete: true` alongside a non-zero `not_reached` or `incomplete`
- the control suite failing on the live tree

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
