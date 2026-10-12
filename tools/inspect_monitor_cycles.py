#!/usr/bin/env python3
"""Fail-closed inspection of monitor cycles after recorder activation.

    python3 tools/inspect_monitor_cycles.py <R> <M1> [<M2> ...]

<R> is the bot commit immediately before the first monitor cycle (normally the
13:30 UTC report commit). Each following argument is that cycle's own monitor
commit. Cycles are delimited ONLY by these commits, which must form a direct
parent chain; invocation_token is process-local (every cron run is token 1) and
is never used to tell cycles apart.

Exit 0 PASS: every cycle is complete, reconciled acceptance evidence.
Exit 1 FAIL: a defect, or the inspection itself could not be carried out.
Exit 2 INCONCLUSIVE: truthful but not acceptance evidence (an aborted cycle,
       or observation reported unavailable through the health path).
Any error inside this tool is a FAIL, never a pass.
"""
import json
import subprocess
import sys

HEARTBEAT = "logs/monitor_heartbeat_v1.jsonl"
OBSERVATION = "logs/monitor_observation_v1.jsonl"
HEALTH = "logs/monitor_health_v1.jsonl"
TRADES = "logs/virtual_trades.json"
PARITY = ("logs/tracker_parity_v1.jsonl", "logs/parity_heartbeat_v1.jsonl")
MONITOR_SUBJECT = "Ares V3 monitor "


class Fail(Exception):
    pass


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def full_sha(ref):
    return git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def added_json_lines(a, b, path):
    out = []
    for line in git("diff", "--unified=0", a, b, "--", path).splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            body = line[1:].strip()
            if not body:
                continue
            try:
                out.append(json.loads(body))
            except ValueError as exc:
                raise Fail(f"{path}: invalid JSON line added in {b[:7]}: {exc}")
    return out


def removed_lines(a, b, path):
    return [l for l in git("diff", "--unified=0", a, b, "--", path).splitlines()
            if l.startswith("-") and not l.startswith("---")]


def open_symbols(commit):
    trades = json.loads(git("show", f"{commit}:{TRADES}"))
    return sorted(t["symbol"] for t in trades if t.get("status") == "open")


def inspect(refs):
    if len(refs) < 2:
        raise Fail("need <R> and at least one monitor commit")
    shas = [full_sha(r) for r in refs]
    if len(set(shas)) != len(shas):
        raise Fail("the same commit was given twice")
    results, seen_ids, seen_starts = [], set(), set()
    for n, (a, b) in enumerate(zip(shas, shas[1:]), 1):
        tag = f"cycle {n} ({b[:7]})"
        parents = git("rev-list", "--parents", "-n", "1", b).split()[1:]
        if parents != [a]:
            raise Fail(f"{tag}: parent is {[p[:7] for p in parents]}, not "
                       f"{a[:7]}; the commits do not delimit exactly one cycle")
        subject = git("log", "-1", "--format=%s", b).strip()
        if not subject.startswith(MONITOR_SUBJECT):
            raise Fail(f"{tag}: not a monitor commit: {subject!r}")
        changed = git("diff", "--name-only", a, b).split()
        touched = [p for p in PARITY if p in changed]
        if touched:
            raise Fail(f"{tag}: monitor cycle changed parity evidence {touched}")
        for p in (HEARTBEAT, OBSERVATION, HEALTH):
            if removed_lines(a, b, p):
                raise Fail(f"{tag}: lines were removed from {p} (append-only)")
        beats = added_json_lines(a, b, HEARTBEAT)
        health = added_json_lines(a, b, HEALTH)
        obs = added_json_lines(a, b, OBSERVATION)
        if beats and health:
            raise Fail(f"{tag}: heartbeat AND health line in one cycle "
                       "(partial flush)")
        if health and not beats:
            results.append((tag, 2, f"observation unavailable: "
                            f"{[h.get('reason') for h in health]}"))
            continue
        if len(beats) != 1:
            raise Fail(f"{tag}: expected exactly 1 heartbeat, found {len(beats)}"
                       + (" and no health line" if not beats else ""))
        hb = beats[0]
        cid, start = hb.get("cycle_id"), hb.get("cycle_started_at")
        if not cid or not start:
            raise Fail(f"{tag}: heartbeat lacks cycle_id/cycle_started_at")
        if cid in seen_ids or start in seen_starts:
            raise Fail(f"{tag}: cycle_id or cycle_started_at repeats an earlier "
                       "cycle (reset failure)")
        seen_ids.add(cid)
        seen_starts.add(start)
        wrong = sorted({o.get("cycle_id") for o in obs} - {cid}, key=str)
        if wrong:
            raise Fail(f"{tag}: observations carry other cycle_ids {wrong}")
        syms = [o.get("symbol") for o in obs]
        if len(set(syms)) != len(syms):
            raise Fail(f"{tag}: duplicate symbol records {sorted(syms)}")
        expected = open_symbols(a)
        if hb.get("open_positions_seen") != len(expected):
            raise Fail(f"{tag}: open_positions_seen "
                       f"{hb.get('open_positions_seen')} != {len(expected)} "
                       f"open at {a[:7]}")
        if not hb.get("monitor_completed"):
            if hb.get("coverage_complete"):
                raise Fail(f"{tag}: aborted cycle claims coverage_complete")
            results.append((tag, 2, f"aborted cycle (truthful, see §5.2): "
                            f"{hb.get('loop_exception')}"))
            continue
        if sorted(syms) != expected:
            raise Fail(f"{tag}: records {sorted(syms)} != open at {a[:7]} "
                       f"{expected}")
        p = hb.get("population") or {}
        if not (hb.get("coverage_complete") is True
                and p.get("reconciled") is True
                and p.get("not_reached") == 0
                and p.get("incomplete") == 0
                and p.get("incomplete_labelled") == 0
                and p.get("duplicate_symbols") == []
                and p.get("attempted_matches_records") is True
                and hb.get("attempted") == hb.get("written") == len(obs)
                and hb.get("write_failures") == 0
                and hb.get("manifests_complete") is True):
            raise Fail(f"{tag}: heartbeat not complete and consistent: "
                       f"coverage={hb.get('coverage_complete')} population={p} "
                       f"attempted={hb.get('attempted')} "
                       f"written={hb.get('written')} records={len(obs)}")
        bad = [o.get("symbol") for o in obs if o.get("record_complete") is not True]
        if bad:
            raise Fail(f"{tag}: incomplete records {bad}")
        results.append((tag, 0, f"{len(obs)} records {sorted(syms)}, "
                        f"cycle_id {cid}"))
    return results


def main(argv):
    try:
        results = inspect(argv)
    except Fail as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:                          # noqa: BLE001
        print(f"FAIL: inspection error {type(exc).__name__}: {exc}")
        return 1
    for tag, code, msg in results:
        print(f"{'PASS' if code == 0 else 'INCONCLUSIVE'}  {tag}: {msg}")
    worst = max(code for _, code, _ in results)
    print("RESULT:", {0: "PASS", 2: "INCONCLUSIVE"}[worst])
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
