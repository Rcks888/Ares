#!/usr/bin/env python3
"""Controls for tools/inspect_monitor_cycles.py, the post-activation inspection.

    python3 tests/run_inspection_controls.py

Each control builds a scratch git repository with the bot's real commit
pattern (a report commit, then one commit per monitor cycle) and the REAL
recorder's flush output, then runs the inspection tool on it. Control 0 proves
a healthy history passes; every other control must be rejected with the stated
exit code. Controls 1 and 2 reproduce the two review findings on bundle v3:

    1  grouping by invocation_token, which is 1 in every cron process
    2  a failed git command reported as "parity untouched"

Nothing is written outside a temporary directory.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TOOL = ROOT / "tools" / "inspect_monitor_cycles.py"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from engine import monitor_observer as mo                    # noqa: E402
from run_monitor_observer_controls import complete_obs, store  # noqa: E402

OPEN = ["AMP", "TMO"]
RESULTS = []
SCRATCH = []


def scratch():
    d = tempfile.mkdtemp(prefix="ares_inspect_")
    SCRATCH.append(d)
    return d


def control(why):
    def wrap(fn):
        RESULTS.append((fn.__name__, why, fn))
        return fn
    return wrap


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def trades(open_syms):
    return [{"symbol": s, "status": "open"} for s in open_syms] + \
           [{"symbol": "OLD", "status": "closed"}]


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n")


def commit(repo, subject):
    git(repo, "add", "-A", "logs")
    git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def new_repo():
    repo = Path(scratch())
    git(repo, "init", "-q")
    (repo / "logs").mkdir()
    write_json(repo / "logs" / "virtual_trades.json", trades(OPEN))
    (repo / "logs" / "tracker_parity_v1.jsonl").write_text('{"p": 1}\n')
    (repo / "logs" / "parity_heartbeat_v1.jsonl").write_text('{"h": 1}\n')
    return repo, commit(repo, "Ares V3 report 2026-10-19_13:32")


def run_cycle(repo, symbols=OPEN, n=1, mutate=None, **store_over):
    """One real recorder flush into the scratch repo, then the bot's commit.

    invocation_token stays 1 and cycle_id stays None, as in every real cron
    process: the serialiser mints the cycle_id.
    """
    mo.LOGS_DIR = repo / "logs"
    obs = [complete_obs("hold", symbol=s) for s in symbols]
    kw = dict(invocation_token=1, cycle_id=None,
              cycle_started_at=f"2026-10-19T{15 + n}:10:0{n}+00:00",
              observations=obs, attempted=len(obs),
              open_positions_seen=len(OPEN))
    kw.update(store_over)
    mo.flush(store(**kw))
    if mutate:
        mutate(repo / "logs")
    return commit(repo, f"Ares V3 monitor 2026-10-19_{15 + n}:1{n}")


def read_lines(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def write_lines(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def inspect(repo, *refs):
    r = subprocess.run([sys.executable, str(TOOL), *refs], cwd=repo,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def expect(repo, refs, code, needle):
    got, out = inspect(repo, *refs)
    if got != code or needle not in out:
        raise AssertionError(f"want exit {code} with {needle!r}; "
                             f"got exit {got}:\n{out}")


def two_cycles():
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1)
    m2 = run_cycle(repo, n=2)
    return repo, r, m1, m2


@control("the harness cannot pass anything: a healthy two-cycle history passes")
def control_00_healthy_history_passes():
    repo, r, m1, m2 = two_cycles()
    expect(repo, [r, m1, m2], 0, "RESULT: PASS")


@control("[finding 1] cycles told apart by invocation_token, which is 1 in "
         "every process: cycle 2 records under cycle 1's identity")
def control_01_cross_cycle_records_rejected():
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1)
    first_id = read_lines(repo / "logs" / mo.HEARTBEAT_NAME)[0]["cycle_id"]

    def restamp(logs):
        p = logs / mo.OBSERVATION_NAME
        rows = read_lines(p)
        for rec in rows[len(OPEN):]:
            rec["cycle_id"] = first_id
        write_lines(p, rows)
    m2 = run_cycle(repo, n=2, mutate=restamp)
    tokens = {x["invocation_token"] for x in read_lines(
        repo / "logs" / mo.OBSERVATION_NAME)}
    if tokens != {1}:
        raise AssertionError(f"fixture must reproduce token 1 everywhere: {tokens}")
    expect(repo, [r, m1, m2], 1, "observations carry other cycle_ids")


@control("[finding 2] an invalid commit reference reported as "
         "'parity untouched'")
def control_02_git_failure_is_fail():
    repo, r, m1, m2 = two_cycles()
    expect(repo, [r, m1, "deadbeef"], 1, "FAIL: git rev-parse")
    expect(repo, [r, m1, "<M2>"], 1, "FAIL: git rev-parse")


@control("a monitor cycle that changed parity evidence")
def control_03_parity_touched_rejected():
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1)

    def touch(logs):
        with open(logs / "parity_heartbeat_v1.jsonl", "a") as fh:
            fh.write('{"h": 2}\n')
    m2 = run_cycle(repo, n=2, mutate=touch)
    expect(repo, [r, m1, m2], 1, "changed parity evidence")


@control("commits that do not delimit one cycle (a report or a manual commit "
         "in between)")
def control_04_non_adjacent_commits_rejected():
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1)
    commit(repo, "manual: unrelated")
    m2 = run_cycle(repo, n=2)
    expect(repo, [r, m1, m2], 1, "do not delimit exactly one cycle")


@control("a non-monitor commit given as a cycle")
def control_05_report_commit_as_cycle_rejected():
    repo, r = new_repo()
    rep = commit(repo, "Ares V3 report 2026-10-19_21:01")
    expect(repo, [r, rep], 1, "not a monitor commit")


@control("a reset failure: cycle 2 reuses cycle 1's cycle_id")
def control_06_reused_cycle_id_rejected():
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1, cycle_id="same0001")
    m2 = run_cycle(repo, n=2, cycle_id="same0001")
    expect(repo, [r, m1, m2], 1, "reset failure")


@control("records that do not match the positions open before the cycle")
def control_07_population_mismatch_rejected():
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1, symbols=["AMP"], open_positions_seen=1)
    expect(repo, [r, m1], 1, "open_positions_seen 1 != 2")
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1, symbols=["AMP", "XYZ"])
    expect(repo, [r, m1], 1, "records ['AMP', 'XYZ'] != open")


@control("cycle 2 judged against the deployment state instead of cycle 1's "
         "result (a position closed in cycle 1)")
def control_08_anchor_is_previous_cycle():
    repo, r = new_repo()
    write_json(repo / "logs" / "virtual_trades.json", trades(["TMO"]))
    m1 = run_cycle(repo, n=1)                       # AMP closed in cycle 1
    m2 = run_cycle(repo, n=2, symbols=["TMO"], open_positions_seen=1)
    expect(repo, [r, m1, m2], 0, "RESULT: PASS")


@control("a cycle with no evidence at all (no heartbeat, no health line)")
def control_09_missing_heartbeat_rejected():
    repo, r = new_repo()
    write_json(repo / "logs" / "virtual_trades.json", trades(OPEN))
    m1 = commit(repo, "Ares V3 monitor 2026-10-19_16:11")
    expect(repo, [r, m1], 1, "expected exactly 1 heartbeat, found 0")


@control("a health line and a heartbeat in the same cycle (partial flush)")
def control_10_health_and_heartbeat_rejected():
    repo, r = new_repo()
    (repo / "logs" / "monitor_health_v1.jsonl").write_text(
        '{"observation_failed": true, "reason": "flush_failed"}\n')
    m1 = run_cycle(repo, n=1)
    expect(repo, [r, m1], 1, "partial flush")


@control("observation unavailable reported as PASS")
def control_11_health_only_is_inconclusive():
    repo, r = new_repo()
    (repo / "logs" / "monitor_health_v1.jsonl").write_text(
        '{"observation_failed": true, "reason": "recorder_import_failed"}\n')
    m1 = commit(repo, "Ares V3 monitor 2026-10-19_16:11")
    expect(repo, [r, m1], 2, "RESULT: INCONCLUSIVE")


@control("an aborted cycle reported as PASS")
def control_12_aborted_cycle_is_inconclusive():
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1, symbols=["AMP"], attempted=1,
                   monitor_completed=False, loop_exception="ValueError: x")
    expect(repo, [r, m1], 2, "aborted cycle")


@control("evidence lines removed from an append-only file")
def control_13_removed_lines_rejected():
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1)

    def drop_first(logs):
        p = logs / mo.OBSERVATION_NAME
        write_lines(p, read_lines(p)[1:])
    m2 = run_cycle(repo, n=2, mutate=drop_first)
    expect(repo, [r, m1, m2], 1, "removed")


@control("an incomplete record or heartbeat accepted as complete coverage")
def control_14_incomplete_rejected():
    repo, r = new_repo()
    obs = [complete_obs("hold", symbol="AMP"), {"symbol": "TMO",
                                                "observed_live": 10.0,
                                                "decision": "hold"}]
    m1 = run_cycle(repo, n=1, observations=obs, attempted=2)
    expect(repo, [r, m1], 1, "not complete and consistent")


@control("an inspection run outside a git repository")
def control_15_not_a_repo_is_fail():
    d = scratch()
    expect(d, ["HEAD~1", "HEAD"], 1, "FAIL:")


def main():
    saved = mo.LOGS_DIR
    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    try:
        for name, why, fn in RESULTS:
            try:
                fn()
                print(f"  PASS  {name.ljust(width)}  {why}")
            except Exception as exc:                      # noqa: BLE001
                failed += 1
                print(f"  FAIL  {name.ljust(width)}  {why}")
                print(f"        -> {type(exc).__name__}: {exc}")
    finally:
        mo.LOGS_DIR = saved
        for d in SCRATCH:
            shutil.rmtree(d, ignore_errors=True)
    print(f"\n  {len(RESULTS) - failed}/{len(RESULTS)} inspection controls passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
