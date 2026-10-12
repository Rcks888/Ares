#!/usr/bin/env python3
"""Fail-closed inspection of monitor cycles after recorder activation.

    python3 tools/inspect_monitor_cycles.py <R> <M1> [<M2> ...]

<R> is the bot commit immediately before the first monitor cycle (normally the
13:30 UTC report commit). Each following argument is that cycle's own monitor
commit. Cycles are delimited ONLY by these commits, which must form a direct
parent chain; invocation_token is process-local (every cron run is token 1) and
is never used to tell cycles apart.

Nothing the producer claims about itself is trusted:
  - append-only is proven by bytes: each parent file must be an exact prefix of
    the child, and only the appended suffix is parsed;
  - cycle identity is checked against ALL heartbeat and observation history
    present at <R>, not only against the cycles supplied;
  - every record is validated against this file's own copy of the frozen
    schema and outcome contract; record_complete is required but never
    sufficient. The copy is pinned to the recorder's constants by inspection
    control 21, so drift in either is a FAIL, not a silent agreement;
  - heartbeat population counts are recomputed from the validated records;
  - lineage is resolved, not just present: repository_commit must be the
    commit the cycle ran on (the previous reference), and every manifest digest
    must equal the md5 of that file at that commit;
  - time is parsed, not just present: timezone-aware ISO timestamps, with
    previous commit <= started <= every record <= completed <= this commit;
  - every number in the evidence is finite; NaN and +/-Infinity are rejected
    already at JSON parsing, and again by type checks.

Exit 0 PASS: every cycle is complete, reconciled acceptance evidence.
Exit 1 FAIL: a defect, or the inspection itself could not be carried out.
Exit 2 INCONCLUSIVE: truthful but not acceptance evidence (an aborted cycle,
       or observation reported unavailable through the health path).
Any error inside this tool is a FAIL, never a pass.
"""
import hashlib
import json
import math
import numbers
import re
import subprocess
import sys
from datetime import datetime, timedelta

HEARTBEAT = "logs/monitor_heartbeat_v1.jsonl"
OBSERVATION = "logs/monitor_observation_v1.jsonl"
HEALTH = "logs/monitor_health_v1.jsonl"
TRADES = "logs/virtual_trades.json"
PARITY = ("logs/tracker_parity_v1.jsonl", "logs/parity_heartbeat_v1.jsonl")
MONITOR_SUBJECT = "Ares V3 monitor "

# ---- frozen contract, independent copy (pinned by inspection control 21) ----
RECORD_SCHEMA_VERSION = 1
HEARTBEAT_SCHEMA_VERSION = 1
DECISION_PATH = "monitor"
RECORD_FIELDS = frozenset({
    "record_schema_version", "decision_path", "timestamp",
    "invocation_token", "cycle_id", "repository_commit",
    "symbol", "entry_date", "holding_basis",
    "observed_live", "live_available", "skip_reason",
    "entry_price", "stop_loss",
    "trailing_stop_before", "trailing_stop_after_unrounded",
    "trailing_stop_rounded_2dp", "peak_price_before", "peak_price_after",
    "peak_price_precision_basis", "peak_advanced", "trail_advanced",
    "take_profit", "effective_stop_unrounded", "effective_stop_rounded_2dp",
    "decision", "exit_reason",
    "close_requested", "close_price_argument", "close_function_completed",
    "booked_exit_price",
    "shares_at_decision", "unrealized_pct_at_decision",
    "exit_date_recorded", "exit_date_basis",
    "record_complete", "missing_fields", "contract_violations",
})
ALWAYS_NON_NULL = frozenset({
    "record_schema_version", "decision_path", "timestamp", "invocation_token",
    "cycle_id", "symbol", "entry_date", "holding_basis", "decision",
    "entry_price", "stop_loss", "trailing_stop_before", "peak_price_before",
    "shares_at_decision", "peak_price_precision_basis", "exit_date_basis",
})
PRICED = frozenset({
    "observed_live", "effective_stop_unrounded", "effective_stop_rounded_2dp",
    "trailing_stop_after_unrounded", "trailing_stop_rounded_2dp",
    "peak_price_after", "unrealized_pct_at_decision", "exit_date_recorded",
})
CLOSE = frozenset({"exit_reason", "close_price_argument", "booked_exit_price",
                   "close_function_completed"})
CONTRACT = {
    "skipped": {"non_null": frozenset({"skip_reason"}),
                "falsy": frozenset({"observed_live"}),
                "null": PRICED - {"observed_live"} | CLOSE,
                "false": frozenset({"live_available", "peak_advanced",
                                    "trail_advanced", "close_requested"})},
    "hold": {"non_null": PRICED,
             "null": frozenset({"skip_reason"}) | CLOSE,
             "true": frozenset({"live_available"}),
             "false": frozenset({"peak_advanced", "trail_advanced",
                                 "close_requested"})},
    "state_update": {"non_null": PRICED,
                     "null": frozenset({"skip_reason"}) | CLOSE,
                     "true": frozenset({"live_available", "peak_advanced"}),
                     "false": frozenset({"close_requested"})},
    "closed": {"non_null": PRICED | frozenset({
                   "exit_reason", "close_price_argument", "booked_exit_price"}),
               "null": frozenset({"skip_reason"}),
               "true": frozenset({"live_available", "close_requested",
                                  "close_function_completed"})},
}
EVALUATED = frozenset({"hold", "state_update", "closed"})
EXIT_REASONS = frozenset({"stop_loss", "trailing_stop", "take_profit"})
SKIP_REASON = "live_price_unavailable_cause_unknown"
EXIT_DATE_BASIS = "process_local_naive_civil_date"
PEAK_BASES = frozenset({"two_dp_or_equal", "unrounded_initialisation_value"})
NUMERIC = frozenset({
    "entry_price", "stop_loss", "trailing_stop_before", "peak_price_before",
    "shares_at_decision", "take_profit", "observed_live",
    "effective_stop_unrounded", "effective_stop_rounded_2dp",
    "trailing_stop_after_unrounded", "trailing_stop_rounded_2dp",
    "peak_price_after", "unrealized_pct_at_decision", "close_price_argument",
    "booked_exit_price"})
BOOLEAN = frozenset({"live_available", "peak_advanced", "trail_advanced",
                     "close_requested", "close_function_completed"})
HEARTBEAT_FIELDS = frozenset({
    "heartbeat_schema_version", "record_schema_version", "decision_path",
    "cycle_id", "invocation_token", "repository_commit", "lineage_complete",
    "cycle_started_at", "cycle_completed_at", "open_positions_seen",
    "early_return_reason", "attempted", "written", "write_failures",
    "population", "trade_state_save_attempted", "trade_state_save_completed",
    "coverage_complete", "loop_exception", "monitor_completed",
    "decision_manifest", "classification_manifest", "observation_manifest",
    "manifests_complete", "manifest_missing", "effective_config",
    "config_review_required", "recorder_version",
})
HEARTBEAT_COUNTS = ("open_positions_seen", "attempted", "written",
                    "write_failures")
HEARTBEAT_BOOLS = ("lineage_complete", "monitor_completed", "coverage_complete",
                   "manifests_complete", "trade_state_save_attempted",
                   "trade_state_save_completed")
MANIFESTS = ("decision_manifest", "classification_manifest",
             "observation_manifest")
SHORT_SHA = re.compile(r"[0-9a-f]{7,40}")
MD5 = re.compile(r"[0-9a-f]{32}")
# commit times have 1 s resolution; started/completed are microsecond-exact
CLOCK_SLACK = timedelta(seconds=2)
POPULATION_FIELDS = frozenset({
    "open_positions_seen", "attempted", "evaluated", "skipped", "incomplete",
    "incomplete_labelled", "not_reached", "attempted_matches_records",
    "duplicate_symbols", "reconciled",
})


class Fail(Exception):
    pass


def git_bytes(*args):
    r = subprocess.run(["git", *args], capture_output=True)
    if r.returncode != 0:
        raise Fail(f"git {' '.join(args)} failed: "
                   f"{r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def git(*args):
    return git_bytes(*args).decode()


def full_sha(ref):
    return git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def blob(commit, path):
    """File bytes at commit, or None if the path does not exist there."""
    if not git("ls-tree", "--name-only", commit, "--", path).strip():
        return None
    return git_bytes("cat-file", "blob", f"{commit}:{path}")


def _reject_constant(token):
    raise ValueError(f"non-finite number {token} is not valid evidence")


def parse_lines(data, label):
    out = []
    for i, line in enumerate(data.split(b"\n")[:-1], 1):
        try:
            obj = json.loads(line, parse_constant=_reject_constant)
        except ValueError as exc:
            raise Fail(f"{label} line {i}: invalid JSON: {exc}")
        if not isinstance(obj, dict):
            raise Fail(f"{label} line {i}: not a JSON object")
        out.append(obj)
    return out


def appended(a, b, path, tag):
    """Records appended to path between a and b, proven append-only by bytes."""
    old, new = blob(a, path), blob(b, path)
    old = old or b""
    if new is None:
        if old:
            raise Fail(f"{tag}: {path} was deleted")
        return []
    if old and not old.endswith(b"\n"):
        raise Fail(f"{tag}: {path} at the previous reference ends mid-line")
    if not new.startswith(old):
        raise Fail(f"{tag}: {path} is not append-only: the previous content "
                   "is not a byte prefix of this cycle's content")
    suffix = new[len(old):]
    if suffix and not suffix.endswith(b"\n"):
        raise Fail(f"{tag}: {path} appended content ends mid-line")
    if b"\n\n" in b"\n" + suffix:
        raise Fail(f"{tag}: {path} appended content contains a blank line")
    return parse_lines(suffix, f"{tag}: {path} appended")


def history(commit):
    """Every cycle identity already present at commit."""
    beats = parse_lines(blob(commit, HEARTBEAT) or b"", f"history {HEARTBEAT}")
    obs = parse_lines(blob(commit, OBSERVATION) or b"", f"history {OBSERVATION}")
    ids = {h.get("cycle_id") for h in beats} | {o.get("cycle_id") for o in obs}
    starts = {h.get("cycle_started_at") for h in beats}
    return ids - {None}, starts - {None}


def is_num(v):
    return (isinstance(v, numbers.Real) and not isinstance(v, bool)
            and math.isfinite(v))


def is_count(v, minimum=0):
    return isinstance(v, int) and not isinstance(v, bool) and v >= minimum


def parse_time(v):
    """Timezone-aware ISO-8601 datetime, or None."""
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return t if t.tzinfo is not None and t.utcoffset() is not None else None


def commit_time(sha):
    return datetime.fromisoformat(git("log", "-1", "--format=%cI", sha).strip())


def record_violations(rec, hb):
    v = []
    keys = set(rec)
    if keys != RECORD_FIELDS:
        v.append(f"key set: missing {sorted(RECORD_FIELDS - keys)}, "
                 f"unregistered {sorted(keys - RECORD_FIELDS)}")
        return v
    if rec["record_schema_version"] != RECORD_SCHEMA_VERSION:
        v.append("record_schema_version")
    if rec["decision_path"] != DECISION_PATH:
        v.append("decision_path")
    if rec["cycle_id"] != hb["cycle_id"]:
        v.append("cycle_id differs from the cycle's heartbeat")
    if rec["repository_commit"] != hb["repository_commit"]:
        v.append("repository_commit differs from the cycle's heartbeat")
    for k in sorted(ALWAYS_NON_NULL):
        if rec[k] is None:
            v.append(f"{k}: null")
    for k in sorted(NUMERIC):
        if rec[k] is not None and not is_num(rec[k]):
            v.append(f"{k}: not a number: {rec[k]!r}")
    for k in sorted(BOOLEAN):
        if rec[k] is not None and not isinstance(rec[k], bool):
            v.append(f"{k}: not a boolean: {rec[k]!r}")
    if not isinstance(rec["symbol"], str) or not rec["symbol"]:
        v.append("symbol: not a non-empty string")
    c = CONTRACT.get(rec["decision"])
    if c is None:
        v.append(f"decision: {rec['decision']!r} has no complete outcome")
        return v
    for k in sorted(c.get("non_null", ())):
        if rec[k] is None:
            v.append(f"{k}: required for {rec['decision']}, is null")
    for k in sorted(c.get("null", ())):
        if rec[k] is not None:
            v.append(f"{k}: must be null for {rec['decision']}")
    for k in sorted(c.get("falsy", ())):
        if rec[k]:
            v.append(f"{k}: must be falsy for {rec['decision']}")
    for k in sorted(c.get("true", ())):
        if rec[k] is not True:
            v.append(f"{k}: must be true for {rec['decision']}")
    for k in sorted(c.get("false", ())):
        if rec[k] is not False:
            v.append(f"{k}: must be false for {rec['decision']}")
    if rec["decision"] == "closed" and rec["exit_reason"] not in EXIT_REASONS:
        v.append(f"exit_reason: {rec['exit_reason']!r}")
    if rec["decision"] == "skipped" and rec["skip_reason"] != SKIP_REASON:
        v.append(f"skip_reason: {rec['skip_reason']!r}")
    # values the serialiser derives; a mismatch means they were not derived
    if rec["live_available"] is not bool(rec["observed_live"]):
        v.append("live_available disagrees with observed_live")
    for raw, rnd in (("effective_stop_unrounded", "effective_stop_rounded_2dp"),
                     ("trailing_stop_after_unrounded",
                      "trailing_stop_rounded_2dp")):
        want = None if not is_num(rec[raw]) else round(float(rec[raw]), 2)
        if rec[rnd] != want:
            v.append(f"{rnd}: {rec[rnd]!r} is not round({raw}, 2) = {want!r}")
    if is_num(rec["peak_price_before"]):
        p = float(rec["peak_price_before"])
        want = ("two_dp_or_equal" if p == round(p, 2)
                else "unrounded_initialisation_value")
        if rec["peak_price_precision_basis"] != want:
            v.append("peak_price_precision_basis disagrees with peak_price_before")
    elif rec["peak_price_precision_basis"] in PEAK_BASES:
        v.append("peak_price_precision_basis claims a numeric peak")
    if rec["exit_date_basis"] != EXIT_DATE_BASIS:
        v.append("exit_date_basis")
    if rec["record_complete"] is not True:
        v.append("record_complete is not true")
    # missing_fields legitimately lists keys an outcome never emits (the
    # recorder fills them with null); it is informational, not a criterion
    if not isinstance(rec["missing_fields"], list):
        v.append("missing_fields: not a list")
    if rec["contract_violations"] != []:
        v.append("producer reported contract violations")
    return v


def heartbeat_violations(hb):
    v = []
    keys = set(hb)
    if keys != HEARTBEAT_FIELDS:
        return [f"key set: missing {sorted(HEARTBEAT_FIELDS - keys)}, "
                f"unregistered {sorted(keys - HEARTBEAT_FIELDS)}"]
    if hb["heartbeat_schema_version"] != HEARTBEAT_SCHEMA_VERSION:
        v.append("heartbeat_schema_version")
    if hb["record_schema_version"] != RECORD_SCHEMA_VERSION:
        v.append("record_schema_version")
    if hb["decision_path"] != DECISION_PATH:
        v.append("decision_path")
    if not isinstance(hb["cycle_id"], str) or not hb["cycle_id"]:
        v.append("cycle_id")
    if not is_count(hb["invocation_token"], 1):
        v.append(f"invocation_token: {hb['invocation_token']!r}")
    for k in HEARTBEAT_COUNTS:
        if not is_count(hb[k]):
            v.append(f"{k}: not a non-negative integer: {hb[k]!r}")
    for k in HEARTBEAT_BOOLS:
        if not isinstance(hb[k], bool):
            v.append(f"{k}: not a boolean: {hb[k]!r}")
    if hb["lineage_complete"] is not True:
        v.append("lineage_complete is not true")
    if not (isinstance(hb["repository_commit"], str)
            and SHORT_SHA.fullmatch(hb["repository_commit"])):
        v.append(f"repository_commit: not a commit id: "
                 f"{hb['repository_commit']!r}")
    start = parse_time(hb["cycle_started_at"])
    done = parse_time(hb["cycle_completed_at"])
    if start is None:
        v.append(f"cycle_started_at: not a timezone-aware ISO timestamp: "
                 f"{hb['cycle_started_at']!r}")
    if done is None:
        v.append(f"cycle_completed_at: not a timezone-aware ISO timestamp: "
                 f"{hb['cycle_completed_at']!r}")
    if start is not None and done is not None and done < start:
        v.append("cycle_completed_at is earlier than cycle_started_at")
    for m in MANIFESTS:
        d = hb[m]
        if not isinstance(d, dict) or not d:
            v.append(f"{m}: not a non-empty mapping")
        elif not all(isinstance(x, str) and MD5.fullmatch(x) for x in d.values()):
            v.append(f"{m}: a digest is missing or not md5")
    if hb["manifest_missing"] != []:
        v.append(f"manifest_missing: {hb['manifest_missing']!r}")
    p = hb["population"]
    if not isinstance(p, dict) or set(p) != POPULATION_FIELDS:
        v.append("population key set")
    return v


def lineage_violations(hb, a):
    """The cycle ran on <a>: the bot commits only AFTER the monitor exits, so
    HEAD during the cycle is the previous reference."""
    v = []
    if not a.startswith(hb["repository_commit"]):
        v.append(f"repository_commit {hb['repository_commit']} is not the "
                 f"commit the cycle ran on ({a[:7]})")
    for m in MANIFESTS:
        for path, digest in sorted(hb[m].items()):
            data = blob(a, path)
            if data is None:
                v.append(f"{m}: {path} does not exist at {a[:7]}")
            elif hashlib.md5(data).hexdigest() != digest:
                v.append(f"{m}: {path} digest differs from the file at {a[:7]} "
                         "(the code that ran is not the committed code)")
    return v


def time_violations(hb, recs, a, b):
    v = []
    start = parse_time(hb["cycle_started_at"])
    done = parse_time(hb["cycle_completed_at"])
    if start < commit_time(a) - CLOCK_SLACK:
        v.append("cycle_started_at is earlier than the previous commit")
    if done > commit_time(b) + CLOCK_SLACK:
        v.append("cycle_completed_at is later than this cycle's commit")
    for i, rec in enumerate(recs, 1):
        t = parse_time(rec.get("timestamp"))
        if t is None:
            v.append(f"record {i}: timestamp not a timezone-aware ISO timestamp")
        elif not start <= t <= done:
            v.append(f"record {i}: timestamp outside the cycle "
                     f"[{hb['cycle_started_at']}, {hb['cycle_completed_at']}]")
    return v


def recomputed_population(hb, recs):
    seen, attempted = hb["open_positions_seen"], hb["attempted"]
    syms = [r["symbol"] for r in recs]
    return {
        "open_positions_seen": seen,
        "attempted": attempted,
        "evaluated": sum(r["decision"] in EVALUATED for r in recs),
        "skipped": sum(r["decision"] == "skipped" for r in recs),
        "incomplete": 0,
        "incomplete_labelled": 0,
        "not_reached": seen - attempted,
        "attempted_matches_records": attempted == len(recs),
        "duplicate_symbols": sorted({s for s in syms if syms.count(s) > 1}),
        "reconciled": True,
    }


def open_symbols(commit):
    trades = json.loads(git("show", f"{commit}:{TRADES}"))
    return sorted(t["symbol"] for t in trades if t.get("status") == "open")


def inspect(refs):
    if len(refs) < 2:
        raise Fail("need <R> and at least one monitor commit")
    shas = [full_sha(r) for r in refs]
    if len(set(shas)) != len(shas):
        raise Fail("the same commit was given twice")
    seen_ids, seen_starts = history(shas[0])
    results = []
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
        beats = appended(a, b, HEARTBEAT, tag)
        health = appended(a, b, HEALTH, tag)
        obs = appended(a, b, OBSERVATION, tag)
        if beats and health:
            raise Fail(f"{tag}: heartbeat AND health line in one cycle "
                       "(partial flush)")
        if health and not beats:
            if obs:
                raise Fail(f"{tag}: observations without a heartbeat")
            results.append((tag, 2, f"observation unavailable: "
                            f"{[h.get('reason') for h in health]}"))
            continue
        if len(beats) != 1:
            raise Fail(f"{tag}: expected exactly 1 heartbeat, found {len(beats)}"
                       + (" and no health line" if not beats else ""))
        hb = beats[0]
        bad = heartbeat_violations(hb)
        if bad:
            raise Fail(f"{tag}: heartbeat fails the frozen schema: {bad}")
        cid, start = hb["cycle_id"], hb["cycle_started_at"]
        if cid in seen_ids or start in seen_starts:
            raise Fail(f"{tag}: cycle_id or cycle_started_at repeats an earlier "
                       "cycle, including history before the first reference "
                       "(reset failure)")
        seen_ids.add(cid)
        seen_starts.add(start)
        aborted = hb["monitor_completed"] is not True
        for i, rec in enumerate(obs, 1):
            bad = record_violations(rec, hb)
            if bad and aborted and rec.get("record_complete") is False:
                # an aborted loop may truthfully leave an incomplete record;
                # its identity and key set must still be genuine
                bad = [x for x in bad if x.startswith(("key set", "cycle_id",
                                                       "repository_commit",
                                                       "record_schema_version",
                                                       "decision_path"))]
            if bad:
                raise Fail(f"{tag}: record {i} ({rec.get('symbol')!r}) fails "
                           f"the frozen contract: {bad}")
        bad = lineage_violations(hb, a) + time_violations(hb, obs, a, b)
        if bad:
            raise Fail(f"{tag}: lineage or time evidence invalid: {bad}")
        syms = [o["symbol"] for o in obs]
        if len(set(syms)) != len(syms):
            raise Fail(f"{tag}: duplicate symbol records {sorted(syms)}")
        expected = open_symbols(a)
        if hb["open_positions_seen"] != len(expected):
            raise Fail(f"{tag}: open_positions_seen "
                       f"{hb['open_positions_seen']} != {len(expected)} "
                       f"open at {a[:7]}")
        if aborted:
            if hb["coverage_complete"] is not False:
                raise Fail(f"{tag}: aborted cycle claims coverage_complete")
            results.append((tag, 2, f"aborted cycle (truthful, see §5.2): "
                            f"{hb['loop_exception']}"))
            continue
        if sorted(syms) != expected:
            raise Fail(f"{tag}: records {sorted(syms)} != open at {a[:7]} "
                       f"{expected}")
        want = recomputed_population(hb, obs)
        if hb["population"] != want:
            raise Fail(f"{tag}: heartbeat population {hb['population']} != "
                       f"recomputed from records {want}")
        if not (hb["coverage_complete"] is True
                and hb["attempted"] == hb["written"] == len(obs)
                and hb["write_failures"] == 0
                and hb["manifests_complete"] is True
                and hb["manifest_missing"] == []
                and hb["lineage_complete"] is True
                and hb["loop_exception"] is None):
            raise Fail(f"{tag}: heartbeat not complete and consistent: "
                       f"coverage={hb['coverage_complete']} "
                       f"attempted={hb['attempted']} written={hb['written']} "
                       f"records={len(obs)} "
                       f"write_failures={hb['write_failures']}")
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
