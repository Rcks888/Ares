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

Controls 16-18 reproduce the three review findings on bundle v4:

    16  evidence inserted BEFORE existing lines passed "no removed lines"
    17  a cycle identity reused from history before <R> passed
    18  a three-key record claiming record_complete passed

Nothing is written outside a temporary directory.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
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


MANIFEST_FILES = sorted(set(mo.DECISION_MANIFEST) | set(mo.CLASSIFICATION_MANIFEST)
                        | set(mo.OBSERVATION_MANIFEST))


def new_repo(open_syms=OPEN):
    """Scratch repo with the REAL manifest files committed, so the recorder's
    repository_commit and manifest digests are genuine lineage, as on the VPS."""
    repo = Path(scratch())
    git(repo, "init", "-q")
    for rel in MANIFEST_FILES:
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dst)
    git(repo, "add", "-A")
    (repo / "logs").mkdir()
    write_json(repo / "logs" / "virtual_trades.json", trades(open_syms))
    (repo / "logs" / "tracker_parity_v1.jsonl").write_text('{"p": 1}\n')
    (repo / "logs" / "parity_heartbeat_v1.jsonl").write_text('{"h": 1}\n')
    return repo, commit(repo, "Ares V3 report 2026-10-19_13:32")


def run_cycle(repo, symbols=OPEN, n=1, mutate=None, **store_over):
    """One real recorder flush into the scratch repo, then the bot's commit.

    invocation_token stays 1 and cycle_id stays None, as in every real cron
    process: the serialiser mints the cycle_id.
    """
    mo.LOGS_DIR = repo / "logs"
    mo.ROOT = repo
    obs = [complete_obs("hold", symbol=s) for s in symbols]
    kw = dict(invocation_token=1, cycle_id=None,
              cycle_started_at=datetime.now(timezone.utc).isoformat(),
              observations=obs, attempted=len(obs),
              open_positions_seen=len(OPEN),
              trade_state_save_attempted=True,
              trade_state_save_completed=True)
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
    expect(repo, [r, m1, m2], 1, "cycle_id differs from the cycle's heartbeat")


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
    expect(repo, [r, m1, m2], 1, "not append-only")


@control("an incomplete record or heartbeat accepted as complete coverage")
def control_14_incomplete_rejected():
    repo, r = new_repo()
    obs = [complete_obs("hold", symbol="AMP"), {"symbol": "TMO",
                                                "observed_live": 10.0,
                                                "decision": "hold"}]
    m1 = run_cycle(repo, n=1, observations=obs, attempted=2)
    expect(repo, [r, m1], 1, "record_complete is not true")


@control("an inspection run outside a git repository")
def control_15_not_a_repo_is_fail():
    d = scratch()
    expect(d, ["HEAD~1", "HEAD"], 1, "FAIL:")


def edit_appended(logs, name, keep, fn):
    p = logs / name
    rows = read_lines(p)
    write_lines(p, rows[:keep] + [fn(dict(x)) for x in rows[keep:]])


@control("[v4 finding 1] new evidence inserted before existing records, which "
         "a 'no removed lines' check accepts")
def control_16_prepended_evidence_rejected():
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1)

    def prepend(logs):
        for name, keep in ((mo.OBSERVATION_NAME, len(OPEN)),
                           (mo.HEARTBEAT_NAME, 1)):
            p = logs / name
            rows = read_lines(p)
            write_lines(p, rows[keep:] + rows[:keep])
    m2 = run_cycle(repo, n=2, mutate=prepend)
    out = subprocess.run(["git", "diff", m1, m2], cwd=repo, capture_output=True,
                         text=True).stdout
    if any(l.startswith("-") and not l.startswith("---")
           for l in out.splitlines()):
        raise AssertionError("fixture must remove no line, or it does not "
                             "reproduce the finding")
    expect(repo, [r, m1, m2], 1, "not append-only")


@control("[v4 finding 2] a cycle identity reused from history before <R>")
def control_17_identity_from_history_rejected():
    repo, _ = new_repo()
    run_cycle(repo, n=1, cycle_id="hist0001")
    r2 = commit(repo, "Ares V3 report 2026-10-20_13:32")
    m = run_cycle(repo, n=1, cycle_id="hist0001")
    expect(repo, [r2, m], 1, "including history before the first reference")
    repo, _ = new_repo()
    run_cycle(repo, n=1)
    old_start = read_lines(repo / "logs" / mo.HEARTBEAT_NAME)[-1][
        "cycle_started_at"]
    r2 = commit(repo, "Ares V3 report 2026-10-20_13:32")
    m = run_cycle(repo, n=1, cycle_started_at=old_start)
    expect(repo, [r2, m], 1, "including history before the first reference")


@control("[v4 finding 3] a record carrying only symbol, cycle_id and "
         "record_complete: true, with consistent heartbeat counters")
def control_18_minimal_record_rejected():
    repo, r = new_repo()

    def strip(logs):
        cid = read_lines(logs / mo.HEARTBEAT_NAME)[-1]["cycle_id"]
        edit_appended(logs, mo.OBSERVATION_NAME, 0,
                      lambda x: {"symbol": x["symbol"], "cycle_id": cid,
                                 "record_complete": True})
    m1 = run_cycle(repo, n=1, mutate=strip)
    expect(repo, [r, m1], 1, "fails the frozen contract")


@control("a fully keyed record that violates its outcome contract or its "
         "derived values while claiming record_complete")
def control_19_forged_complete_record_rejected():
    cases = [
        ("skip_reason", "x", "must be null for hold"),
        ("effective_stop_rounded_2dp", 9.99, "is not round("),
        ("live_available", False, "must be true for hold"),
        ("entry_price", "10.0", "not a number"),
        ("decision", "incomplete", "has no complete outcome"),
        ("exit_date_basis", "bar_date", "exit_date_basis"),
    ]
    for key, value, needle in cases:
        repo, r = new_repo()

        def forge(logs, key=key, value=value):
            def f(x):
                x[key] = value
                return x
            edit_appended(logs, mo.OBSERVATION_NAME, 0, f)
        m1 = run_cycle(repo, n=1, mutate=forge)
        expect(repo, [r, m1], 1, needle)


@control("heartbeat population counts that disagree with the records "
         "(counters trusted instead of recomputed)")
def control_20_population_recomputed():
    repo, r = new_repo()

    def forge(logs):
        def f(h):
            h["population"]["evaluated"] -= 1
            h["population"]["skipped"] += 1
            return h
        edit_appended(logs, mo.HEARTBEAT_NAME, 0, f)
    m1 = run_cycle(repo, n=1, mutate=forge)
    expect(repo, [r, m1], 1, "recomputed from records")


@control("the tool's contract copy drifting from the recorder's frozen "
         "constants, so both could agree on a wrong schema")
def control_21_contract_copy_pinned():
    import importlib.util
    spec = importlib.util.spec_from_file_location("inspect_tool", TOOL)
    t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t)
    pairs = [
        ("RECORD_FIELDS", t.RECORD_FIELDS, mo.REGISTERED_OBSERVATION_FIELDS),
        ("ALWAYS_NON_NULL", t.ALWAYS_NON_NULL, mo._ALWAYS_NON_NULL),
        ("PRICED", t.PRICED, mo._PRICED_FIELDS),
        ("CLOSE", t.CLOSE, mo._CLOSE_FIELDS),
        ("CONTRACT", t.CONTRACT, {k: v for k, v in mo.OUTCOME_CONTRACT.items()
                                  if v is not None}),
        ("EVALUATED", t.EVALUATED, mo.EVALUATED_DECISIONS),
        ("EXIT_REASONS", t.EXIT_REASONS, mo.ALLOWED_EXIT_REASONS),
        ("decisions", set(t.CONTRACT) | {"incomplete"}, mo.DECISIONS),
        ("RECORD_SCHEMA_VERSION", t.RECORD_SCHEMA_VERSION,
         mo.RECORD_SCHEMA_VERSION),
        ("HEARTBEAT_SCHEMA_VERSION", t.HEARTBEAT_SCHEMA_VERSION,
         mo.HEARTBEAT_SCHEMA_VERSION),
        ("DECISION_PATH", t.DECISION_PATH, mo.DECISION_PATH),
    ]
    repo, _ = new_repo()
    run_cycle(repo, n=1, observations=[complete_obs("skipped", symbol="AMP"),
                                       complete_obs("hold", symbol="TMO")])
    hb = read_lines(repo / "logs" / mo.HEARTBEAT_NAME)[-1]
    recs = read_lines(repo / "logs" / mo.OBSERVATION_NAME)
    pairs += [
        ("HEARTBEAT_FIELDS", t.HEARTBEAT_FIELDS, set(hb)),
        ("POPULATION_FIELDS", t.POPULATION_FIELDS, set(hb["population"])),
        ("SKIP_REASON", {t.SKIP_REASON}, {recs[0]["skip_reason"]}),
        ("EXIT_DATE_BASIS", {t.EXIT_DATE_BASIS}, {recs[1]["exit_date_basis"]}),
    ]
    drift = [name for name, mine, theirs in pairs if mine != theirs]
    if drift:
        raise AssertionError(f"contract copy differs from the recorder: {drift}")
    for rec in recs:
        bad = t.record_violations(rec, hb)
        if bad:
            raise AssertionError(f"a real record fails the copy: {bad}")


@control("an aborted cycle: a truthful incomplete record is INCONCLUSIVE, a "
         "forged 'complete' record is FAIL")
def control_22_aborted_cycle_records():
    obs = [complete_obs("hold", symbol="AMP"),
           {"symbol": "TMO", "observed_live": 10.0}]
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1, observations=obs, attempted=2,
                   monitor_completed=False, loop_exception="KeyError: x")
    expect(repo, [r, m1], 2, "aborted cycle")
    repo, r = new_repo()

    def forge(logs):
        cid = read_lines(logs / mo.HEARTBEAT_NAME)[-1]["cycle_id"]
        edit_appended(logs, mo.OBSERVATION_NAME, 0,
                      lambda x: {"symbol": x["symbol"], "cycle_id": cid,
                                 "record_complete": True})
    m1 = run_cycle(repo, n=1, observations=obs, attempted=2,
                   monitor_completed=False, loop_exception="KeyError: x",
                   mutate=forge)
    expect(repo, [r, m1], 1, "fails the frozen contract")
    repo, r = new_repo()

    def forge_keyed(logs):
        def f(x):
            if x["record_complete"] is True:
                x["skip_reason"] = "x"            # fully keyed, still claims complete
            return x
        edit_appended(logs, mo.OBSERVATION_NAME, 0, f)
    m1 = run_cycle(repo, n=1, observations=obs, attempted=2,
                   monitor_completed=False, loop_exception="KeyError: x",
                   mutate=forge_keyed)
    expect(repo, [r, m1], 1, "must be null for hold")


@control("a heartbeat with a missing or unregistered key")
def control_23_heartbeat_schema_enforced():
    for fn, needle in ((lambda h: {k: v for k, v in h.items()
                                   if k != "lineage_complete"}, "missing"),
                       (lambda h: {**h, "verdict": "MATCH"}, "unregistered")):
        repo, r = new_repo()
        m1 = run_cycle(repo, n=1, mutate=lambda logs, fn=fn: edit_appended(
            logs, mo.HEARTBEAT_NAME, 0, fn))
        expect(repo, [r, m1], 1, needle)


@control("a heartbeat admitting a write failure or an unverifiable manifest "
         "while its records are valid")
def control_24_heartbeat_completeness_enforced():
    for key, value in (("write_failures", 1), ("manifests_complete", False),
                       ("coverage_complete", False)):
        repo, r = new_repo()

        def f(h, key=key, value=value):
            h[key] = value
            return h
        m1 = run_cycle(repo, n=1, mutate=lambda logs, f=f: edit_appended(
            logs, mo.HEARTBEAT_NAME, 0, f))
        expect(repo, [r, m1], 1, "not complete and consistent")


ZERO = dict(symbols=[], open_positions_seen=0,
            early_return_reason="no_open_positions",
            trade_state_save_attempted=False, trade_state_save_completed=False)


def heartbeat_forged(fn, open_syms=OPEN, **cycle):
    repo, r = new_repo(open_syms)
    m1 = run_cycle(repo, n=1, mutate=lambda logs: edit_appended(
        logs, mo.HEARTBEAT_NAME, 0, fn), **cycle)
    return repo, r, m1


@control("the harness cannot fail a valid zero-position cycle")
def control_25_zero_position_cycle_passes():
    repo, r = new_repo([])
    m1 = run_cycle(repo, n=1, **ZERO)
    expect(repo, [r, m1], 0, "RESULT: PASS")


@control("[v5 finding 1] a zero-position cycle with null lineage and "
         "unparseable or missing timestamps")
def control_26_reviewer_lineage_probe_rejected():
    def f(h):
        h.update(repository_commit=None, lineage_complete=False,
                 cycle_started_at="not-a-timestamp", cycle_completed_at=None)
        return h
    repo, r, m1 = heartbeat_forged(f, [], **ZERO)
    expect(repo, [r, m1], 1, "heartbeat fails the frozen schema")


@control("each lineage and time defect on its own, on an otherwise valid "
         "two-position cycle")
def control_27_lineage_and_time_each_rejected():
    def shift(key, delta):
        def f(h):
            t = datetime.fromisoformat(h[key]) + delta
            h[key] = t.isoformat()
            return h
        return f
    cases = [
        (lambda h: {**h, "repository_commit": None}, "repository_commit: not a commit id"),
        (lambda h: {**h, "lineage_complete": False}, "lineage_complete is not true"),
        (lambda h: {**h, "cycle_started_at": "not-a-timestamp"}, "cycle_started_at: not a timezone-aware"),
        (lambda h: {**h, "cycle_completed_at": None}, "cycle_completed_at: not a timezone-aware"),
        (lambda h: {**h, "cycle_started_at": h["cycle_started_at"][:19]}, "cycle_started_at: not a timezone-aware"),
        (shift("cycle_completed_at", -timedelta(hours=1)), "earlier than cycle_started_at"),
        (shift("cycle_started_at", -timedelta(days=1)), "earlier than the previous commit"),
        (shift("cycle_completed_at", timedelta(days=1)), "later than this cycle's commit"),
        (lambda h: {**h, "invocation_token": True}, "invocation_token"),
        (lambda h: {**h, "attempted": 2.0}, "attempted: not a non-negative integer"),
        (lambda h: {**h, "decision_manifest": {**h["decision_manifest"], "engine/tracker.py": None}}, "not md5"),
    ]
    for fn, needle in cases:
        repo, r, m1 = heartbeat_forged(fn)
        expect(repo, [r, m1], 1, needle)
    # a well-formed commit id that is not the commit the cycle ran on
    repo, r = new_repo()
    m1 = run_cycle(repo, n=1)
    def other_commit(logs):
        edit_appended(logs, mo.HEARTBEAT_NAME, 1,
                      lambda h: {**h, "repository_commit": r[:7]})
        edit_appended(logs, mo.OBSERVATION_NAME, len(OPEN),
                      lambda x: {**x, "repository_commit": r[:7]})
    m2 = run_cycle(repo, n=2, mutate=other_commit)
    expect(repo, [r, m1, m2], 1, "is not the commit the cycle ran on")
    # a record stamped outside its cycle
    repo, r = new_repo()

    def stale(logs):
        def f(x):
            x["timestamp"] = "2026-01-01T00:00:00+00:00"
            return x
        edit_appended(logs, mo.OBSERVATION_NAME, 0, f)
    m1 = run_cycle(repo, n=1, mutate=stale)
    expect(repo, [r, m1], 1, "timestamp outside the cycle")


@control("code that ran differing from the committed code (an uncommitted "
         "edit on the VPS), while the manifest claims completeness")
def control_28_manifest_must_match_committed_files():
    repo, r = new_repo()
    with open(repo / "engine" / "tracker.py", "a") as fh:
        fh.write("\n# local edit\n")
    m1 = run_cycle(repo, n=1)
    expect(repo, [r, m1], 1, "the code that ran is not the committed code")


@control("[v5 finding 2] non-finite numbers in evidence: written by the real "
         "recorder, injected as JSON tokens, or passed to the validator directly")
def control_29_non_finite_rejected():
    import importlib.util
    spec = importlib.util.spec_from_file_location("inspect_tool", TOOL)
    t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t)
    # the reviewer's probe: a record the validator accepts, then entry_price=inf
    repo, r = new_repo()
    run_cycle(repo, n=1)
    hb = read_lines(repo / "logs" / mo.HEARTBEAT_NAME)[-1]
    rec = read_lines(repo / "logs" / mo.OBSERVATION_NAME)[0]
    if t.record_violations(rec, hb):
        raise AssertionError("baseline record must be accepted")
    for bad in (float("inf"), float("-inf"), float("nan")):
        got = t.record_violations({**rec, "entry_price": bad}, hb)
        if not any("entry_price: not a number" in x for x in got):
            raise AssertionError(f"entry_price={bad} accepted: {got}")
    # the real recorder serialises inf as the token Infinity
    for value in (float("inf"), float("nan")):
        repo, r = new_repo()
        obs = [complete_obs("hold", symbol="AMP", entry_price=value),
               complete_obs("hold", symbol="TMO")]
        m1 = run_cycle(repo, n=1, observations=obs, attempted=2)
        if "Infinity" not in (repo / "logs" / mo.OBSERVATION_NAME).read_text() \
                and "NaN" not in (repo / "logs" / mo.OBSERVATION_NAME).read_text():
            raise AssertionError("fixture must put a non-finite token on disk")
        expect(repo, [r, m1], 1, "non-finite number")
    # a non-finite token anywhere in a heartbeat
    for token in ("Infinity", "-Infinity", "NaN"):
        repo, r = new_repo()

        def inject(logs, token=token):
            p = logs / mo.HEARTBEAT_NAME
            text = p.read_text()
            p.write_text(text.replace('"write_failures": 0',
                                      f'"write_failures": {token}', 1))
        m1 = run_cycle(repo, n=1, mutate=inject)
        expect(repo, [r, m1], 1, "non-finite number")


def main():
    saved, saved_root = mo.LOGS_DIR, mo.ROOT
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
        mo.LOGS_DIR, mo.ROOT = saved, saved_root
        for d in SCRATCH:
            shutil.rmtree(d, ignore_errors=True)
    print(f"\n  {len(RESULTS) - failed}/{len(RESULTS)} inspection controls passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
