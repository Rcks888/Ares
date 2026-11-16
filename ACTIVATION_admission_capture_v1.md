# Activation: admission capture v1

Branch `recorder/admission-v1`. **Not activated.** This document is the
deployment and rollback review for the recorder whose implementation was
accepted in four milestones (M1 29 Oct, M2 3 Nov, M3 12 Nov at `a00b185`, M4
16 Nov at `d8c9633`). It does not itself authorise anything. Activation needs
the reviewer's approval of this document **and** the owner's go-ahead on the
day.

It follows `ACTIVATION_monitor_recorder_v1.md` (activated `5fd13e9`, 13 Oct)
section for section, and design §8 (`archive/DESIGN_admission_capture_v1.md`).
Where this document departs from the precedent, it says so.

Scope of what activation buys: every future admission of a signal into
production (scan direct, queue promotion by the report, queue promotion by the
monitor), every fill, demotion, drop and queue maintenance step is recorded,
with A2 timing evidence and trade links, from the first activated report
onwards. It does **not** change any decision, does not repair history (trades
admitted before activation stay `unlinked_preactivation`), and does not decide
any policy question that the evidence may later inform.

### New in this round (for the reviewer)

| Item | Where | Status |
|---|---|---|
| A1 | `tests/run_admission_parity_rehearsal.py`, the recorder under production parity mode (§3.3) | new control file, 10/10 |
| A2 | `tools/run_activation_checks.sh`, the accepted bundle's `full_repo_run.sh` plus A1, committed so the VPS runs one command (§4) | new script |
| A3 | design §8 first bullet superseded: Finviz is live, so R1 can occur | wording |
| A4 | VPS fast rollback uses `git restore --worktree`, not the precedent's `git checkout <PRE> --` (§3.5) | departure from precedent |
| A5 | first report inspected the same evening, before the first monitor cycle (§5) | departure from precedent |
| A6 | `INCONCLUSIVE` is a hold, not an abort (§6) | ruling requested |

---

## 1. Propagation mechanism — re-verified 16 Nov

Re-read against `main` at `baaf2a3`, not copied from the precedent. The line
numbers are unchanged:

| | `run_ares.sh` | `run_monitor.sh` |
|---|---|---|
| `python <entrypoint>` | line 16 | line 7 |
| `git add logs/` / `commit` | lines 30–31 | lines 34–35 |
| `git push`, then `pull --rebase --autostash` **only if the push failed** | lines 40–41 | lines 38–39 |

So, as before: a cycle that pulls new code ran the old code, and arrival by
pull is incidental. **Merging is not deploying.** Deployment is the explicit,
verified step in §4.

```
reviewed squash commit reaches main
        ↓
VPS fast-forwards to THAT EXACT commit       (verified by SHA)
        ↓
on-disk fingerprints and the full check run verified on the live tree
        ↓
first report cycle inspected, then the first monitor cycles
```

### What changes at runtime

| Executed by cron | Changed by activation | Run by |
|---|---|---|
| `daily_report.py` | yes: registered additions, one registered replacement (`generate_report()` in `__main__`) | `run_ares.sh` 13:30, 21:00 |
| `monitor_trades.py` | yes: registered additions, one registered replacement (`monitor()` in `__main__`) | `run_monitor.sh` 16:10, 17:30 |
| `engine/tracker.py` | yes: registered additions only, 142 | both |
| `engine/admission_observer.py` | new | both |
| launchers, `config/`, `engine/data_feed.py`, `indicators.py`, `signals.py`, `screener.py`, `sample.py`, `monitor_observer.py`, the parity stack, `build_dashboard.py` | **no** | |

The squash also carries tools, tests, fixtures, this document, the design
document, `README.md` and `.gitignore`. None of them is executed by cron.

---

## 2. Window

**A weekday, 05:00–13:00 UTC (13:00–21:00 MYT)**, design §8. It sits inside
the 21:00 → 13:30 UTC gap, the only stretch with no scheduled process, and
ends before the 13:00 UTC `restart_gateway.sh`, which is a dependency rather
than a decision path but is still kept clear.

Start only after the previous night's 21:00 UTC report has **actually
completed**: its `Ares V3 report` commit is on `origin/main` (checked on the
laptop with `git log -1 --format='%h %s' origin/main`), not inferred from the
clock.

**The first cycle to execute the recorder is the 13:30 UTC report the same
day (21:30 MYT)**, unlike the monitor recorder, whose first cycle was 16:10.
Both entrypoints change, so the order on day 1 is: 13:30 report, 16:10 and
17:30 monitor, 21:00 report.

| UTC | MYT | Process |
|---|---|---|
| 13:00, 16:00, 17:25 | 21:00, 00:00, 01:25 | `restart_gateway.sh` (dependency) |
| 13:30, 21:00 | 21:30, 05:00 | `run_ares.sh` → `daily_report.py` |
| 16:10, 17:30 | 00:10, 01:30 | `run_monitor.sh` → `monitor_trades.py` |

---

## 3. Pre-deployment acceptance

Every item is read-only except where stated.

### 3.1 Preserve current evidence and running-source fingerprints (VPS)

Run inside the window, **before** §4 step 2:

```sh
cd /root/ares/Ares
source /root/ares/Ares/venv/bin/activate
date -u
git log -1 --format='%H %s'
git status --porcelain
python -VV
md5sum daily_report.py monitor_trades.py engine/tracker.py \
       engine/data_feed.py engine/indicators.py engine/signals.py \
       engine/screener.py config/strategy_params.json engine/sample.py \
       engine/monitor_observer.py
wc -l logs/tracker_parity_v1.jsonl logs/parity_heartbeat_v1.jsonl \
      logs/monitor_observation_v1.jsonl logs/monitor_heartbeat_v1.jsonl \
      logs/queue_events.jsonl
md5sum logs/tracker_parity_v1.jsonl logs/parity_heartbeat_v1.jsonl \
       logs/monitor_observation_v1.jsonl logs/monitor_heartbeat_v1.jsonl
ls -l logs/admission_v1.jsonl logs/admission_heartbeat_v1.jsonl \
      logs/admission_health_v1.jsonl 2>&1
mkdir -p /root/ares/preact_admission
cp logs/virtual_trades.json logs/pending_signals.json logs/signal_queue.json \
   logs/queue_ranked.json /root/ares/preact_admission/
```

Acceptance:

- `git status --porcelain` prints nothing.
- The commit is the previous night's `Ares V3 report` commit, the same one the
  laptop sees on `origin/main`.
- The three `ls` lines all say `No such file or directory`, so the first write
  is unambiguous.
- The md5 values equal `main` at `baaf2a3`:

| File | md5 |
|---|---|
| `daily_report.py` | `80a9f29ee2da5e852395e7eaa3d26404` |
| `monitor_trades.py` | `22a9da16029123742215aafa4ea6375f` |
| `engine/tracker.py` | `3c23d191b4bccea221678502bd82f30a` |
| `engine/data_feed.py` | `28216583761fdbc7ea9c0c5dae6b4721` |
| `engine/indicators.py` | `f2cf8af87476b03b5c27e220de5abcec` |
| `engine/signals.py` | `da2ba2596cf43f6405dfd4521824c33d` |
| `engine/screener.py` | `e2cf4a430b5f33bf493d1c1e32936b68` |
| `config/strategy_params.json` | `804c997a93348d426c54dca27367afd7` |
| `engine/sample.py` | `e74dcaee2af6f59691f5f202431ba709` |
| `engine/monitor_observer.py` | `059f3f828247b1757be8cd159d1cb473` |

The first three are also the md5 values of `tests/fixtures/tracker_preadmission.py`,
`daily_report_preadmission.py` and `monitor_trades_preadmission.py` (checked
16 Nov). That is the link that matters: every isolation verifier in §3.2
compares the deployed file against a fixture, so the fixture must be the code
that is running today. If any of the three differs, **stop**: the reviewed
baseline is not what production runs.

The `wc`/`md5sum` of the parity and monitor-recorder files are the baseline for
"existing evidence preserved": after activation those files may only grow by
appended lines.

### 3.2 Verify the reviewed decision logic is unchanged

Each entrypoint is checked against its pre-admission fixture by the AST
alignment verifier accepted for the monitor recorder, with a per-file
registration table:

| Command | Acceptance |
|---|---|
| `verify_recorder_isolation.py --registry admission_registration_tracker tests/fixtures/tracker_preadmission.py engine/tracker.py` | `only registered observation additions found; all other logic aligned identically (142 accepted = 142 additions + 0 registered replacement)` |
| `verify_recorder_isolation.py --registry admission_registration_daily_report tests/fixtures/daily_report_preadmission.py daily_report.py` | `… (20 accepted = 19 additions + 1 registered replacement)` |
| `verify_recorder_isolation.py tests/fixtures/monitor_trades_preactivation.py monitor_trades.py` | `… (55 accepted = 54 additions + 1 registered replacement)` |
| `verify_statement_whitelist.py` | `every admission addition matches a permitted form` |
| `verify_call_inventory.py` | `call-site inventory matches the registered table` |
| `verify_writer_inventory.py` | `writer inventory, save functions and save data flow match the registered tables` |

The monitor file is checked against the **monitor recorder's** pre-activation
fixture with the combined registry, so one run covers both recorders'
additions; the whitelist check compares it to `monitor_trades_preadmission.py`
(10 additions, 0 deletions).

As in the precedent, no text diff or line count is used for this check.

### 3.3 Confirm recorder failure cannot alter decisions, persistence or exceptions

All accepted through M1–M4, plus A1. Exact counts on `d8c9633` + this round:

| Suite | Count | Concern |
|---|---|---|
| `run_persistence_controls.py` | 61/61 | write isolation, partial writes, unwritable logs |
| `run_admission_capture_controls.py` | 55/55 | every outcome captured; capture failure contained |
| `run_report_entrypoint_controls.py` | 16/16 | `daily_report.py` own bytes: exception propagation, health path |
| `run_monitor_admission_entrypoint_controls.py` | 34/34 | `monitor_trades.py` own bytes: ordering against the monitor recorder, containment |
| `run_admission_isolation_smoke.py` (real and stubbed indicators) | ALL PASS ×2 | recorder on vs off: production state identical |
| `run_report_e2e_smoke.py` | ALL PASS | the real report entrypoint end to end |
| `run_call_inventory_controls.py`, `run_statement_whitelist_controls.py`, `run_writer_inventory_controls.py`, `run_verifier_negative_controls.py` | 35/35, 51/51, 41/41, 25/25 | the verifiers in §3.2 reject what they must |
| `run_a2_classification_controls.py` | 11/11 | A2 three-valued classification |
| `run_admission_inspection_controls.py` | 83/83 | the §5 tool cannot pass a defective history |
| `run_monitor_observer_controls.py`, `run_entrypoint_containment_controls.py`, `run_inspection_controls.py` | 44/44, 13/13, 31/31 | the active monitor recorder is unaffected |
| **A1** `run_admission_parity_rehearsal.py` | **10/10** | **new**, below |

#### A1: the recorder under production parity mode

`run_ares.sh` exports `ARES_PARITY=1`, so in production the report's
`check_open_trades()` runs inside the Phase 4 parity observer. Every accepted
admission suite runs with parity **off**: the drivers remove the variable.
That gap is now closed by a rehearsal, not argued away.

Two scratch git repositories, same seed, same stubs, `ARES_PARITY=1`, the real
`daily_report.py` run twice each as `__main__`:

- **P0**, the pre-admission code (`tests/fixtures/*_preadmission.py`,
  observer removed), i.e. what `main` runs today;
- **P1**, the branch code.

The seed is chosen so that parity evaluates real decisions: four open trades,
one of them (OPD) with its stop above the price; two pending (one fills, one
is demoted to the queue because the slots are full); a queue with one entry to
expire, one to fail validation and one to promote. Indicators are a
deterministic stub with the two columns `check_open_trades` reads (`rsi` 50,
no bearish divergence), so both the inline and the shadow evaluation run and
the rehearsal needs no `pandas_ta`. In rep1 OPD is closed by its stop, which
frees a slot, so `check_open_trades`' own `expire_queue()` and a scan
promotion (QAA) both run under parity; rep2 fills QAA.

| Check | Requires |
|---|---|
| r01 | both chains exit 0 every run; one parity heartbeat appended per run with `attempted == written` = records appended, no errors; every record `MATCH` and `SUCCEEDED_*`; the OPD `close:stop_loss` decision present |
| r02 | pending, trades, queue, ranked queue, queue events and the trade report identical between P0 and P1 after every run, time fields aside; report stdout identical (clock times masked) |
| r03 | parity records and heartbeat identical between P0 and P1, time, cycle-id, commit and tracker source-identity fields aside; the two state hashes one unchanged value per cycle and, in every run that closes no trade, equal to the committed `virtual_trades.json` |
| r04 | P0's tracker and report are the fixtures and it has no observer; P0 has no admission file; P1 appends records and a heartbeat every run, writes no health line; no admission file names a parity path and no parity file mentions admission |
| r05 | `inspect_admission_cycles.py` passes P1's chain: QOLD expiry, PRE fill, PRT demotion, QAA promotion and fill, both scan inserts; 0 open episodes; no FAIL or INCONCLUSIVE |
| n01–n05 | negative controls: a lost queue entry, a changed parity decision, a trades file whose hash no longer binds, an admission heartbeat naming the parity file, a missing parity heartbeat. Each must be reported by its named check |

Two exemptions, stated rather than hidden:

- The state hashes are excluded from the P0/P1 equality because they cover
  `virtual_trades.json`, which carries the wall-clock `fill_run_ts`. They are
  bound to the committed file instead.
- That binding is skipped for a run that closes a trade, because
  `check_shadow_trades()` (`engine/tracker.py:1356`, called later in the same
  report) legitimately rewrites the closed trade's shadow block after the hash
  point. Found while building A1; it is production behaviour in both P0 and P1.
  rep2, which closes nothing, binds in both chains, and n03 proves the binding
  bites.

Runtime about 2.5 s; stable over repeated runs.

### 3.4 Register output paths and schedules before their first write

| | |
|---|---|
| Records | `logs/admission_v1.jsonl` |
| Heartbeat | `logs/admission_heartbeat_v1.jsonl` |
| Health | `logs/admission_health_v1.jsonl` |
| Written by | `daily_report.py` (13:30, 21:00 UTC) and `monitor_trades.py` (16:10, 17:30 UTC), Mon–Fri |
| Committed by | the launchers' `git add logs/`: all three are re-included in `.gitignore` by the activation commit |
| Read by | `tools/inspect_admission_cycles.py` only. Checked 16 Nov: no dashboard, exporter, `clean_v3` statistic, Telegram path or other production code reads them |
| Must never be merged with | `logs/tracker_parity_v1.jsonl`, `logs/parity_heartbeat_v1.jsonl`, `logs/monitor_*_v1.jsonl`, `logs/queue_events.jsonl` |
| Size | about 5–15 KB per report cycle in the rehearsals, less per monitor cycle: well under 100 KB a day |

Heartbeat and health, as accepted in M3/M4 (Z5):

| Observed for a cycle | Meaning |
|---|---|
| a heartbeat line | the recorder ran |
| a health line, no heartbeat | the recorder was unavailable or could not write |
| neither | the entrypoint never ran, or was killed (SIGKILL/OOM: accepted limitation, as for the monitor recorder) |

A health line is also announced on stderr as
`ADMISSION-HEALTH: <reason>: <detail>`. Both launchers merge stderr into the
`tee`d output (`2>&1`), so it reaches `cron.log`. It does not reach
Telegram: the report's message is `build_dashboard.py` output, and the
monitor's is a grep for `CLOSED` and the position emoji lines.

### 3.5 Identify and pre-test the rollback

**A single squashed activation commit with exactly one parent**, subject
`activate: admission capture v1`. That exact subject is what every later
command finds the commit by, so no SHA has to be typed.

The inverse is tested against the actual activation commit, created locally
and **not pushed** until the test passes. Rehearsed 16 Nov in a clone of
`main` at `baaf2a3` with the branch at `d8c9633`: squash clean (`README.md`
auto-merged), one parent, 35 files, none under `logs/`; revert applied cleanly;
deployment-owned paths restored exactly, the three entrypoint files back to the
§3.1 md5 values, the observer removed. It is repeated on the day against the
real commit.

| Path | On rollback |
|---|---|
| `daily_report.py`, `monitor_trades.py`, `engine/tracker.py` | restored to the parent |
| `engine/admission_observer.py` | removed |
| `tools/`, `tests/` admission suites, fixtures, verifiers, registries | restored / removed |
| `*.md` (this document, design, `README.md`) | restored |
| `.gitignore` (the three admission re-includes) | restored. Admission files already committed stay tracked |
| `logs/`, **all of it** | **untouched, never reverted**, including any admission evidence written before the rollback |

The inverse test (laptop, after creating the commit, before pushing):

```sh
cd ~/Olympus/Ares
ACT=$(git log -1 --format=%h --grep='^activate: admission capture v1$')
rm -rf /tmp/ares_revert_test
git clone -q --no-hardlinks . /tmp/ares_revert_test
cd /tmp/ares_revert_test
git log -1 --format='%h %s'                     # must be $ACT, activate: ...
if ! files=$(git show --name-only --format= "$ACT"); then
    echo "FAIL: git show failed"
elif [ -z "$files" ]; then
    echo "FAIL: activation commit lists no files"
elif grep -q '^logs/' <<<"$files"; then
    echo "FAIL: activation touches logs/"
else
    echo "ok: activation owns no logs/ ($(wc -l <<<"$files") files)"
fi
git revert --no-edit "$ACT" && echo "INVERSE APPLIES CLEANLY"
git diff --stat "$ACT~1" HEAD -- daily_report.py monitor_trades.py engine/ \
    tools/ tests/ archive/ '*.md' .gitignore    # must be EMPTY
md5sum daily_report.py monitor_trades.py engine/tracker.py   # must equal §3.1
test ! -e engine/admission_observer.py && echo "observer removed"
cd ~ && rm -rf /tmp/ares_revert_test
```

**Acceptance:** `ok`, `INVERSE APPLIES CLEANLY`, an empty diff, the §3.1 md5
values, `observer removed`.

#### Fast rollback on the VPS (A4: departs from the precedent)

The precedent's fast path was `git checkout <PRE> -- monitor_trades.py`. That
command also writes the old file into the **index**, so the bot's next
`git add logs/ && git commit` would commit the code rollback silently, under an
`Ares V3 report` subject. Here the restore touches the working tree only:

```sh
cd /root/ares/Ares
ACT=$(git log -1 --format=%h --grep='^activate: admission capture v1$')
git restore --source="$ACT~1" --worktree -- daily_report.py monitor_trades.py engine/tracker.py
rm -f engine/admission_observer.py
md5sum daily_report.py monitor_trades.py engine/tracker.py   # must equal §3.1
git diff --cached --name-only                                # must print nothing
```

(`git restore` needs git ≥ 2.23. Confirmed 16 Nov: the VPS has git 2.43.0 and
the venv Python 3.12.3 (GCC 13.3.0), the same versions as the laptop where
every suite was run.)

The pre-admission entrypoints do not import the observer, so removing it is
safe. The monitor recorder remains active, as before activation. `tools/` and
`tests/` are inert at runtime and stay until the `main` revert.

Then, for history, on the laptop: `git pull -q --ff-only`,
`git revert --no-edit "$ACT"`, push. To bring the VPS back to a clean tree on
that revert, inside a window:

```sh
cd /root/ares/Ares
git restore --worktree -- daily_report.py monitor_trades.py engine/tracker.py engine/admission_observer.py
git fetch -q origin
git merge --ff-only origin/main
git log --oneline -5                             # the revert is in the history
md5sum daily_report.py monitor_trades.py engine/tracker.py   # must equal §3.1
test ! -e engine/admission_observer.py && echo "observer removed"
git status --porcelain                           # must print nothing
```

The first line briefly puts the activated files back on disk, which is why it
runs only inside a window, when no process executes them.

---

## 4. Deployment procedure

Inside the window, after the 21:00 UTC report has completed.

**Step 1, laptop (I prepare it; it is pushed only on your word):**

```sh
cd ~/Olympus/Ares
git checkout main && git fetch -q origin && git pull -q --ff-only
git rev-list --left-right --count origin/main...HEAD   # expect 0  0
git log -1 --format='%h %s'                            # last night's 21:00 report
git merge --squash recorder/admission-v1
git commit -q -m "activate: admission capture v1"
git log -1 --format='%h %p %s'                         # exactly ONE parent
# --- §3.5 inverse test against this commit, then: ---
git push -q origin main
```

**Step 2, VPS (you):**

```sh
cd /root/ares/Ares
source /root/ares/Ares/venv/bin/activate
git status --porcelain                         # must print nothing
git fetch -q origin
git merge --ff-only origin/main
git log -1 --format='%h %s'                    # the SHA I send you, activate: admission capture v1
md5sum daily_report.py monitor_trades.py engine/tracker.py engine/admission_observer.py
python -c "import ast; [ast.parse(open(f).read()) for f in ('daily_report.py', 'monitor_trades.py', 'engine/tracker.py', 'engine/admission_observer.py')]; print('parses')"
bash tools/run_activation_checks.sh /root/ares/Ares 2>&1 | tee /root/ares/activation_checks_admission.txt | tail -4
ls -l logs/admission_v1.jsonl logs/admission_heartbeat_v1.jsonl logs/admission_health_v1.jsonl 2>&1
git status --porcelain                         # must still print nothing
```

Acceptance for step 2:

- `--ff-only` succeeds. If it fails, the VPS has local commits: **stop**, do
  not rebase during activation.
- The commit is the one pushed in step 1.
- md5 values (unchanged since M4, `d8c9633`):

| File | md5 |
|---|---|
| `daily_report.py` | `dca3ae2073bc46bda3d3eb4455ee58be` |
| `monitor_trades.py` | `d3cd1cf45b68608fd440a3812b902404` |
| `engine/tracker.py` | `87a2114ae228d42244fa0fd13e00d996` |
| `engine/admission_observer.py` | `7af06141e7d2a967d1c47cc66d26153e` |

- `parses`.
- The check run ends `commands : 40, failed: 0` and `RESULT: ALL EXIT 0`, with
  `venv : /root/ares/Ares/venv` at the top, and `uncommitted paths after: 0`.
  The laptop run takes about 2.5 minutes; on the VPS allow 10. The full output
  stays in `/root/ares/activation_checks_admission.txt` for the logbook.
- The admission files still do not exist: the checks wrote only to temporary
  directories.

If anything in step 2 fails, run the §3.5 fast rollback before 13:30 UTC and
stop.

As in the precedent, **do not infer a cycle's success from the launcher's exit
status**: python is piped through `tee`. Changing the launchers is out of scope.

---

## 5. First-cycle inspections

Inspections run on the laptop after `git pull` on `~/Olympus/Ares`; they read
only git history.

**Inspection 1: the first report, the same evening (A5).** The 13:30 UTC
report is the first activated cycle; it commits at about 13:32 UTC (21:32
MYT). Inspecting it at about 21:45 MYT is before the first monitor cycle
(16:10 UTC, 00:10 MYT), so a defect seen there can be rolled back before the
monitor ever runs the new code. The precedent waited for the next morning
because its first cycle was at 00:10 MYT; this one is not.

**Inspection 2: the next morning (MYT)**, after the 16:10 and 17:30 monitor
cycles and the 21:00 report: all four cycles together, plus the usual monitor
inspection.

```sh
cd ~/Olympus/Ares && git pull -q --ff-only
ACT=$(git log -1 --format=%h --grep='^activate: admission capture v1$')
git log --reverse --format='%h %s' "$ACT"..HEAD
python3 tools/inspect_admission_cycles.py --verbose "$ACT" \
    $(git log --reverse --format=%h --grep='^Ares V3 ' "$ACT"..HEAD)
```

and, at inspection 2, the existing monitor routine on the first report and the
two monitor commits:

```sh
python3 tools/inspect_monitor_cycles.py \
    $(git log --reverse --format=%h --grep='^Ares V3 ' "$ACT"..HEAD | head -3)
```

### What the tool must show

- Exit 0, `RESULT: PASS`. An `incident` line does not fail a cycle (M4 Z1–Z5);
  each one is read and logged.
- **Expected on day 1:** `unlinked_preactivation` for every pending entry or
  open trade that existed before activation, and A2 `unknown` for their
  episodes. Those signals were admitted by code that recorded nothing.
- Every cycle's heartbeat: `report_completed` / `monitor_completed` true,
  `records_attempted == records_written`, `write_failures` 0,
  `records_incomplete` 0, `manifests_complete` true, `run_kind` `scheduled`,
  `repository_commit` equal to the commit the cycle ran on (the activation
  commit for cycle 1).
- Decision and observation manifest digests equal the step 2 table for the
  four changed files and the §3.1 table for the rest.

### Alongside the tool

- **Parity unaffected:** each activated report appends exactly one parity
  heartbeat with `attempted == written`, `write_failures` 0, no
  `capture_error` / `shadow_system_error`, and as many parity records as open
  positions. The parity and monitor-recorder files only grew: the first
  N lines of each, N being its §3.1 count, are byte-identical to the
  pre-activation file.
- **Commit contents:** each cycle commit (`git show --stat`) touches only
  `logs/`, and the only new paths are the admission records and heartbeat.
- **No health line** (`logs/admission_health_v1.jsonl` absent), and no
  `ADMISSION-HEALTH` or traceback in `/root/ares/cron.log` for those cycles
  (you, on the VPS: `grep -n 'ADMISSION-HEALTH\|Traceback' /root/ares/cron.log | tail`).
- **Telegram:** the 21:32 MYT dashboard and the monitor messages look as they
  did before activation.

Inspections continue at the normal routine afterwards: every weekday, both
tools, the admission tool over each day's commits from the last inspected
one.

---

## 6. Abort criteria

Roll back immediately (§3.5 fast path, then the `main` revert) on any of:

- the admission tool exits 1 (`FAIL`) on an activated cycle;
- a report or monitor decision differing from what the pre-activation code
  would have made, or a production state file changed by the recorder;
- a production exception that did not propagate, or a traceback absent from
  `cron.log` on a cycle whose heartbeat says `report_completed: false` or
  `monitor_completed: false`;
- any change to existing lines of the parity or monitor-recorder files, a
  missing parity heartbeat on a report cycle, or `attempted != written` there
  where it was equal before;
- a cycle commit touching anything outside `logs/`, or an admission file
  outside the three registered paths;
- `record_complete: true` alongside non-empty `missing_fields` or
  `contract_violations`;
- a heartbeat whose `cycle_id` equals the previous invocation's (a reset
  failure);
- any suite or verifier failing on the live tree in §4 step 2.

**Not abort criteria on their own:**

- **A health line**: observation failed and said so, which is the designed
  degradation. Trading is unaffected. Investigate the same day.
- **`INCONCLUSIVE` (exit 2), A6, ruling requested:** the tool could not
  decide, for example on an unreadable file at `<R>`. It is a **hold**: the
  cause is found the same day. It becomes an abort if the cause is the
  recorder. If it is not, it is logged as a finding.

---

## 7. What activation does not settle

- **O1** (accepted limitation, M2): a `BaseException` in the monitor
  recorder's flush skips the admission block for that monitor cycle. The
  heartbeat and health matrix makes it visible; it is not coverage.
- **SIGKILL/OOM** leaves no heartbeat and no health line, as for the monitor
  recorder.
- **Clean-sample policy** (Q5): `run_kind` is recorded; whether a manually
  admitted, scheduled-filled trade belongs in `clean_v3` is not decided.
- **The 13:30 bar** (Q6, known issues 11 and 13): recorded through
  `signal_date` and the A2 bracket; a strategy question, out of scope.
- **The authoritative exit policy**, the five lost monitor exits, the
  unregistered `MIN_CLOSED_SAMPLE`, the pending-path drift guard: unchanged.
- **Known issue 8** (the OHLCV cache accepts backwards history) and the
  13 Nov 13:31 scan that screened 55 instead of 103: unrelated to the
  recorder, not investigated here.

Ares stays **FROZEN**. Activation is not an unfreeze, a config change or a
strategy change. Phase 5, policy unification and AI modelling remain
prohibited.

Agreed order after this:

```
admission capture activation
        ↓
first report and monitor inspections
        ↓
routine inspections; evidence accumulates
        ↓
evidence-informed questions (A2 timing, admission routes), each separately reviewed
```
