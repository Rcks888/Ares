#!/usr/bin/env python3
"""Fail-closed inspection of admission capture cycles (design §3.5, §4, §5,
§7 *Inspection*; step 3 M4).

    python3 tools/inspect_admission_cycles.py [--verbose] <R> <C1> [<C2> ...]

--verbose also lists every consistent check.

<R> is any commit before the first cycle. Each following argument is one bot
commit ("Ares V3 report ..." or "Ares V3 monitor ..."); together they must
form a direct parent chain, so every commit between <R> and the last one is
inspected and none is skipped.

Nothing the recorder claims about itself is sufficient:
  - the v7 checks: append-only by bytes for the three admission evidence
    files; cycle identity unique against all history at <R>; exact key sets
    from this file's own copy of the schema (pinned to the observer by the
    inspection controls); every number finite; lineage resolved
    (repository_commit is the commit the run started on, every manifest names
    its registered paths and every digest is the md5 of that file there);
    every timestamp timezone-aware and inside [parent commit, this commit];
  - the heartbeat's counts and call-site states are recomputed from the
    records;
  - state-diff reconciliation, independent of the records' save facts: every
    pending added or removed, every trade added and every queue symbol added
    or removed between two commits must have a captured mutation; trades
    already in the parent keep their key fields;
  - the §3.5 per-file check (tools/admission_persistence.py) for every key a
    run touched in the pending, trade and queue files, with the parent
    commit as baseline and this commit as the observed state;
  - pending episodes and trade links (§5, rules 1-8), carried across the
    whole chain;
  - A2 (§4.4), recomputed here for every new trade link;
  - cross-checks: a promoted candidate agrees with its admission record; a
    monitor run is paired with its monitor heartbeat, and a monitor run that
    saved trade state has a monitor promotion pass.

Ordering rules (record order, not identity):
  - an expiry inside maintain_queue is the record at maintenance seq + 1
    (M3 Y7);
  - maintain_queue's own removals are the symbols, present in its baseline,
    whose first kept / dropped / check_retry event at or after the end of its
    expiry's event window is `dropped`. maintain_queue emits exactly one of
    those per entry it validates, and only maintenance survivors reach a
    later promotion pass.

Verdicts per key: consistent < incident < INCONCLUSIVE < FAIL. An incident is
consistent with the code and reported by name (§5 rule 6); it does not change
the result.

Exit 0 PASS: every cycle reconciles (incidents are listed).
Exit 1 FAIL: a defect, or the inspection could not be carried out.
Exit 2 INCONCLUSIVE: truthful but not acceptance evidence.
Any error inside this tool is a FAIL, never a pass.
"""
import hashlib
import json
import math
import numbers
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import admission_persistence as ap  # noqa: E402

RECORDS = "logs/admission_v1.jsonl"
HEARTBEAT = "logs/admission_heartbeat_v1.jsonl"
HEALTH = "logs/admission_health_v1.jsonl"
MONITOR_HEARTBEAT = "logs/monitor_heartbeat_v1.jsonl"
PENDING = "logs/pending_signals.json"
TRADES = "logs/virtual_trades.json"
QUEUE = "logs/signal_queue.json"
SUBJECTS = (("Ares V3 report ", "report"), ("Ares V3 monitor ", "monitor"))

RECORD_SCHEMA_VERSION = 1
HEARTBEAT_SCHEMA_VERSION = 1

# ---- frozen schema: an independent copy, pinned by the controls ----------

ENVELOPE = frozenset({
    "record_schema_version", "decision_path", "cycle_id", "invocation_token",
    "run_kind", "run_kind_detect", "repository_commit"})
COMMON = frozenset({"kind", "seq", "parent_seq", "missing_fields",
                    "contract_violations", "record_complete"})
QUEUE_INSERT_BODY = frozenset({"symbol", "insert", "queue_memory",
                               "queue_event_write", "evicted", "queue_save",
                               "returned"})
FILL_OPTIONAL = frozenset({
    "bar_date", "today_utc", "bar_open", "entry_price", "slippage_pct",
    "shares", "stop_loss", "take_profit", "stdev_fallback", "fill_source",
    "entry_date", "pending_age_days", "queue"})
BODY = {
    "admission": (frozenset({
        "symbol", "signal_date", "decision_price", "signal",
        "signal_fields_absent", "from_queue", "route",
        "open_positions_before", "pending_before", "max_positions", "branch",
        "pending_appended", "pending_save", "returned", "call_state",
        "t_enter", "t_return"}), frozenset({"queue"})),
    "fill": (frozenset({
        "symbol", "signal_date", "from_queue", "signal",
        "signal_fields_absent", "outcome", "entry_state", "fill_attempts",
        "trade_appended", "executed_marked", "exception", "t_enter",
        "t_exit"}), FILL_OPTIONAL),
    "fill_pass": (frozenset({
        "pending_seen", "open_count_before", "max_positions", "pending_save",
        "trades_save", "return_reason", "t_enter", "t_exit"}), frozenset()),
    "promotion_pass": (frozenset({
        "source", "use_live", "free_slots", "ranked_basis", "ranked_count",
        "candidates_examined", "fill_window_ok", "fill_window_reason",
        "early_return_reason", "candidates", "queue_save", "ranked_save",
        "return_reason", "t_enter", "t_exit"}), frozenset()),
    "queue_insert": (QUEUE_INSERT_BODY, frozenset()),
    "queue_expiry": (frozenset({"removals", "event_window", "queue_save",
                                "returned"}), frozenset()),
    "queue_maintenance": (frozenset({"queue_save", "ranked_save",
                                     "ranked_save_site", "returned",
                                     "qe_index"}), frozenset()),
}
CANDIDATE = (frozenset({
    "symbol", "queued_at", "price_at_signal", "queue_entry",
    "queue_entry_fields_absent", "held", "valid", "reason", "checks",
    "t_validate_start", "t_validate_end", "open_trade_expected",
    "open_trade_result", "outcome"}),
    frozenset({"promotion_signal_date", "promotion_price"}))
HEARTBEAT_FIELDS = frozenset({
    "heartbeat_schema_version", "record_schema_version", "decision_path",
    "decision_path_valid", "cycle_id", "invocation_token", "run_kind",
    "run_kind_detect", "repository_commit", "lineage_complete", "started",
    "flushed_at", "report_completed", "monitor_completed", "loop_exception",
    "records_attempted", "records_written", "write_failures",
    "records_incomplete", "queue_events", "queue_event_write_failures",
    "call_sites", "t_scan_start", "t_scan_end", "promotion_expected",
    "peer_cycle_started_at", "peer_invocation_token", "peer_status",
    "decision_manifest", "classification_manifest", "observation_manifest",
    "manifests_complete", "manifest_missing"})
MANIFESTS = ("decision_manifest", "classification_manifest",
             "observation_manifest")
MANIFEST_PATHS = {
    "decision_manifest": frozenset({
        "monitor_trades.py", "daily_report.py", "engine/tracker.py",
        "engine/data_feed.py", "engine/indicators.py", "engine/signals.py",
        "engine/screener.py", "config/strategy_params.json"}),
    "classification_manifest": frozenset({"engine/sample.py"}),
    "observation_manifest": frozenset({"engine/admission_observer.py",
                                       "engine/monitor_observer.py"}),
}
SITES = {
    "report": frozenset({"fill_pass", "promotion_scan", "scan_admissions",
                         "promotion_admissions"}),
    "monitor": frozenset({"promotion_monitor", "promotion_admissions"}),
}
SOURCE = {"report": "scan", "monitor": "monitor"}
SITE_VERDICTS = frozenset({"PASS", "EVALUATE", "INCONCLUSIVE", "FAIL"})
FILL_OUTCOMES = frozenset({
    "demoted_to_queue", "discarded_already_open", "dropped_aged",
    "retained_no_data", "dropped_no_data", "retained_bar_not_today",
    "filled", "retained_exception", "dropped_exception"})
FILL_REMOVES = frozenset({
    "demoted_to_queue", "discarded_already_open", "dropped_aged",
    "dropped_no_data", "filled", "dropped_exception"})
CANDIDATE_OUTCOMES = frozenset({"held_dropped", "deferred_transient",
                                "dropped_invalid", "promoted",
                                "dropped_open_trade"})
PROMOTION_REMOVES = frozenset({"held_dropped", "dropped_invalid", "promoted",
                               "dropped_open_trade"})
PASS_RETURNS = frozenset({"no_free_slots", "queue_empty",
                          "fill_window_closed", "completed"})
ADMISSION_ROUTES = frozenset({"scan_direct", "queue_scan", "queue_monitor"})
MAINTENANCE_ACTIONS = frozenset({"kept", "dropped", "check_retry"})
TIME_FIELDS = {
    "admission": ("t_enter", "t_return"), "fill": ("t_enter", "t_exit"),
    "fill_pass": ("t_enter", "t_exit"),
    "promotion_pass": ("t_enter", "t_exit")}
SAVE_FIELDS = {
    "admission": ("pending_save",), "fill_pass": ("pending_save",
                                                  "trades_save"),
    "promotion_pass": ("queue_save", "ranked_save"),
    "queue_expiry": ("queue_save",),
    "queue_maintenance": ("queue_save", "ranked_save")}
SHORT_SHA = re.compile(r"[0-9a-f]{7,40}")
MD5 = re.compile(r"[0-9a-f]{32}")
CYCLE_ID = re.compile(r"[0-9a-f]{8}")
CLOCK_SLACK = timedelta(seconds=2)
SESSION_OPEN_BASIS = "NYSE 09:30 America/New_York"
A2_BAR_BEFORE_EVENT = "a2_bar_before_event_session"

CONSISTENT, INCIDENT = ap.CONSISTENT, ap.INCIDENT
INCONCLUSIVE, FAIL = ap.INCONCLUSIVE, ap.FAIL


class Fail(Exception):
    pass


# ---- git and parsing (as v7) ---------------------------------------------

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


def finite(obj):
    """Every number anywhere in obj is finite (1e999 parses to inf)."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return True
    if isinstance(obj, numbers.Real):
        return math.isfinite(obj)
    if isinstance(obj, dict):
        return all(finite(v) for v in obj.values())
    if isinstance(obj, list):
        return all(finite(v) for v in obj)
    return False


def parse_lines(data, label):
    out = []
    for i, line in enumerate(data.split(b"\n")[:-1], 1):
        try:
            obj = json.loads(line, parse_constant=_reject_constant)
        except ValueError as exc:
            raise Fail(f"{label} line {i}: invalid JSON: {exc}")
        if not isinstance(obj, dict):
            raise Fail(f"{label} line {i}: not a JSON object")
        if not finite(obj):
            raise Fail(f"{label} line {i}: non-finite number")
        out.append(obj)
    return out


def appended(a, b, path, tag):
    """Lines appended to path between a and b, proven append-only by bytes."""
    old, new = blob(a, path), blob(b, path)
    old = old or b""
    if new is None:
        if old:
            raise Fail(f"{tag}: {path} was deleted")
        return []
    if old and not old.endswith(b"\n"):
        raise Fail(f"{tag}: {path} at the previous commit ends mid-line")
    if not new.startswith(old):
        raise Fail(f"{tag}: {path} is not append-only: the previous content "
                   "is not a byte prefix of this commit's content")
    suffix = new[len(old):]
    if suffix and not suffix.endswith(b"\n"):
        raise Fail(f"{tag}: {path} appended content ends mid-line")
    if b"\n\n" in b"\n" + suffix:
        raise Fail(f"{tag}: {path} appended content contains a blank line")
    return parse_lines(suffix, f"{tag}: {path} appended")


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
    return datetime.fromisoformat(git("log", "-1", "--format=%cI",
                                      sha).strip())


def state(commit, path, parent_had_file):
    data = blob(commit, path)
    return ap.parse_state(None if data is None else data.decode(
        errors="replace"), parent_had_file)


def pend_keys(st):
    return {(p.get("symbol"), p.get("date")) for p in st}


def queue_syms(st):
    return {q.get("symbol") for q in st}


def tcount(st, symbol, signal_date, entry_date=None):
    """Trades on (symbol, signal_date[, entry_date]); entry_date None = any."""
    if st == ap.UNREADABLE:
        return ap.UNREADABLE
    return sum(1 for t in st if t.get("symbol") == symbol
               and t.get("signal_date") == signal_date
               and (entry_date is None or t.get("entry_date") == entry_date))


def trade_key(t):
    return (t.get("symbol"), t.get("signal_date"), t.get("entry_date"))


# ---- A2 (§4.4): an independent copy of the classifier, pinned by control ---

def session_open_utc(bar_date):
    from zoneinfo import ZoneInfo
    if isinstance(bar_date, str):
        bar_date = date.fromisoformat(bar_date)
    return datetime.combine(bar_date, time(9, 30),
                            ZoneInfo("America/New_York")).astimezone(
                                timezone.utc)


def classify_a2(start, end, bar_date):
    """(value, violations) for one named event's bracket [start, end]."""
    from zoneinfo import ZoneInfo
    if start is None or end is None or bar_date is None:
        return "unknown", []
    if isinstance(bar_date, str):
        bar_date = date.fromisoformat(bar_date)
    o = session_open_utc(bar_date)
    session = start.astimezone(ZoneInfo("America/New_York")).date()
    if bar_date < session:
        return "unknown", [A2_BAR_BEFORE_EVENT]
    if bar_date > session or end < o:
        return "before_open", []
    if start >= o:
        return "after_open", []
    return "unknown", []


# ---- frozen contract ------------------------------------------------------

def call_returned(rec):
    kind = rec.get("kind")
    if kind == "admission":
        return rec.get("call_state") == "returned"
    if kind == "fill":
        return rec.get("entry_state") == "completed"
    if kind == "fill_pass":
        return rec.get("return_reason") in ("no_pending", "completed")
    if kind == "promotion_pass":
        return rec.get("return_reason") in PASS_RETURNS
    return rec.get("returned") is True


def _keyset(obj, required, optional, label):
    keys = set(obj)
    v = []
    if not required <= keys or not keys <= required | optional:
        v.append(f"{label}key set: missing {sorted(required - keys)}, "
                 f"unexpected {sorted(keys - required - optional)}")
    return v


def _save_ok(v, allow_none=False):
    return v in ap.SAVE_VALUES or (allow_none and v is None)


def _removal_ok(x):
    if not isinstance(x, dict):
        return False
    if x.get("action") == "expired":
        return (set(x) == {"action", "symbol", "event_write"}
                and isinstance(x["symbol"], str)
                and x["event_write"] in ("written", "write_failed"))
    return (x.get("action") == "expiry_parse_failed"
            and set(x) == {"action", "active_len"}
            and is_count(x["active_len"]))


def record_violations(rec, hb, n_records):
    kind = rec.get("kind")
    if kind not in BODY:
        return [f"unknown kind {kind!r}"]
    req, opt = BODY[kind]
    v = _keyset(rec, req | ENVELOPE | COMMON, opt, "")
    if v:
        return v
    for k in ("decision_path", "cycle_id", "invocation_token", "run_kind",
              "run_kind_detect", "repository_commit"):
        if rec[k] != hb[k]:
            v.append(f"{k} differs from its heartbeat")
    if rec["record_schema_version"] != RECORD_SCHEMA_VERSION:
        v.append("record_schema_version")
    if not is_count(rec["seq"]) or rec["seq"] >= n_records:
        v.append(f"seq {rec['seq']!r}")
    p = rec["parent_seq"]
    if p is not None and not (is_count(p) and is_count(rec["seq"])
                              and p < rec["seq"]):
        v.append(f"parent_seq {p!r}")
    if rec["contract_violations"] != []:
        v.append(f"contract_violations {rec['contract_violations']}")
    if rec["missing_fields"] != []:
        v.append(f"missing_fields {rec['missing_fields']}")
    if not isinstance(rec["record_complete"], bool) or \
            rec["record_complete"] != call_returned(rec):
        v.append("record_complete disagrees with the call's own state")
    for f in SAVE_FIELDS.get(kind, ()):
        if not _save_ok(rec[f]):
            v.append(f"{f} {rec[f]!r}")
    for f in TIME_FIELDS.get(kind, ()):
        if rec[f] is not None and parse_time(rec[f]) is None:
            v.append(f"{f} not a timezone-aware ISO timestamp")
    if kind in ("admission", "fill"):
        if not isinstance(rec["symbol"], str) or \
                not isinstance(rec["signal_date"], str):
            v.append("symbol or signal_date not a string")
        if rec.get("queue") is not None:
            v += _keyset(rec["queue"], QUEUE_INSERT_BODY, frozenset(),
                         "queue child ")
    if kind == "admission":
        if rec["route"] not in ADMISSION_ROUTES:
            v.append(f"route {rec['route']!r}")
        if not isinstance(rec["pending_appended"], bool):
            v.append("pending_appended")
    if kind == "fill":
        if rec["outcome"] is not None and rec["outcome"] not in FILL_OUTCOMES:
            v.append(f"outcome {rec['outcome']!r}")
        if not isinstance(rec["executed_marked"], bool) or \
                not isinstance(rec["trade_appended"], bool):
            v.append("executed_marked or trade_appended")
        elif call_returned(rec) and rec["outcome"] in FILL_OUTCOMES and (
                (rec["outcome"] in FILL_REMOVES) != rec["executed_marked"]):
            v.append("executed_marked disagrees with outcome")
        if rec["trade_appended"] is True and not (
                isinstance(rec.get("entry_date"), str)
                and isinstance(rec.get("bar_date"), str)):
            v.append("trade appended without entry_date and bar_date")
    if kind == "queue_insert":
        if rec["insert"] not in ("inserted", "already_queued"):
            v.append(f"insert {rec['insert']!r}")
        if not _save_ok(rec["queue_save"],
                        allow_none=rec["insert"] == "already_queued"):
            v.append(f"queue_save {rec['queue_save']!r}")
        if not isinstance(rec["evicted"], list):
            v.append("evicted")
    if kind == "queue_expiry":
        w = rec["event_window"]
        if not (isinstance(w, list) and len(w) == 2 and is_count(w[0])
                and (w[1] is None or (is_count(w[1]) and w[1] >= w[0]))):
            v.append(f"event_window {w!r}")
        if not isinstance(rec["removals"], list) or not all(
                _removal_ok(x) for x in rec["removals"]):
            v.append("removals")
    if kind == "queue_maintenance" and not is_count(rec["qe_index"]):
        v.append("qe_index")
    if kind == "promotion_pass":
        if rec["source"] != SOURCE[hb["decision_path"]]:
            v.append(f"source {rec['source']!r} on a "
                     f"{hb['decision_path']} run")
        cands = rec["candidates"]
        if not isinstance(cands, list) or \
                rec["candidates_examined"] != len(cands):
            v.append("candidates")
        else:
            for i, c in enumerate(cands):
                if not isinstance(c, dict):
                    v.append(f"candidates[{i}] not an object")
                    continue
                v += _keyset(c, CANDIDATE[0], CANDIDATE[1],
                             f"candidates[{i}] ")
                if c.get("outcome") is not None and \
                        c.get("outcome") not in CANDIDATE_OUTCOMES:
                    v.append(f"candidates[{i}].outcome")
                for f in ("t_validate_start", "t_validate_end"):
                    if c.get(f) is not None and parse_time(c[f]) is None:
                        v.append(f"candidates[{i}].{f}")
    return v


def _event_ok(e):
    """[symbol, action, written]; an expiry parse failure carries
    len(active) and no write (tracker :495, observer _derive_expiry)."""
    if not (isinstance(e, list) and len(e) == 3 and isinstance(e[1], str)):
        return False
    if e[1] == "expiry_parse_failed":
        return is_count(e[0]) and e[2] is None
    return isinstance(e[0], str) and isinstance(e[2], bool)


def heartbeat_violations(hb):
    v = _keyset(hb, HEARTBEAT_FIELDS, frozenset(), "heartbeat ")
    if v:
        return v
    if hb["heartbeat_schema_version"] != HEARTBEAT_SCHEMA_VERSION or \
            hb["record_schema_version"] != RECORD_SCHEMA_VERSION:
        v.append("schema versions")
    path = hb["decision_path"]
    if path not in SITES or hb["decision_path_valid"] is not True:
        v.append(f"decision_path {path!r}")
        return v
    if not (isinstance(hb["cycle_id"], str)
            and CYCLE_ID.fullmatch(hb["cycle_id"])):
        v.append(f"cycle_id {hb['cycle_id']!r}")
    if not (isinstance(hb["repository_commit"], str)
            and SHORT_SHA.fullmatch(hb["repository_commit"])):
        v.append(f"repository_commit {hb['repository_commit']!r}")
    if hb["lineage_complete"] is not True:
        v.append("lineage_complete is not true")
    for m in MANIFESTS:
        d = hb[m]
        if not isinstance(d, dict) or set(d) != MANIFEST_PATHS[m]:
            v.append(f"{m} does not name exactly its registered paths")
        elif not all(isinstance(x, str) and MD5.fullmatch(x)
                     for x in d.values()):
            v.append(f"{m} has a missing or malformed digest")
    if hb["manifests_complete"] is not True or hb["manifest_missing"] != []:
        v.append("manifests incomplete")
    if parse_time(hb["started"]) is None or \
            parse_time(hb["flushed_at"]) is None:
        v.append("started or flushed_at not a timezone-aware ISO timestamp")
    elif parse_time(hb["flushed_at"]) < parse_time(hb["started"]):
        v.append("flushed_at is earlier than started")
    for f in ("report_completed", "monitor_completed", "promotion_expected"):
        if not isinstance(hb[f], bool):
            v.append(f"{f} not boolean")
    other = "monitor_completed" if path == "report" else "report_completed"
    if hb[other] is not False:
        v.append(f"{other} true on a {path} run")
    done = hb[f"{path}_completed"] is True
    if done != (hb["loop_exception"] is None):
        v.append("completion flag disagrees with loop_exception")
    for f in ("records_attempted", "records_written", "write_failures",
              "records_incomplete", "queue_event_write_failures"):
        if not is_count(hb[f]):
            v.append(f"{f} not a count")
    ev = hb["queue_events"]
    if not isinstance(ev, list) or not all(_event_ok(e) for e in ev):
        v.append("queue_events malformed")
    elif hb["queue_event_write_failures"] != sum(e[2] is False for e in ev):
        v.append("queue_event_write_failures disagrees with queue_events")
    sites = hb["call_sites"]
    if not isinstance(sites, dict) or not SITES[path] <= set(sites):
        v.append("call_sites do not name the registered sites")
    elif any(not isinstance(s, dict) or s.get("verdict") not in SITE_VERDICTS
             for s in sites.values()):
        v.append("call_sites carry an unknown verdict")
    for f in ("t_scan_start", "t_scan_end"):
        if hb[f] is not None and parse_time(hb[f]) is None:
            v.append(f"{f} not a timezone-aware ISO timestamp")
    if path == "report":
        if (hb["peer_status"], hb["peer_cycle_started_at"],
                hb["peer_invocation_token"]) != ("not_applicable", None,
                                                 None):
            v.append("peer fields on a report run")
    elif hb["peer_status"] != "identified" or \
            parse_time(hb["peer_cycle_started_at"]) is None:
        v.append(f"monitor run without an identified peer "
                 f"({hb['peer_status']!r})")
    return v


def recomputed_sites(hb, recs):
    """Counts the heartbeat's call-site states must carry, from the records."""
    v = []
    sites = hb["call_sites"]
    by_kind = Counter(r["kind"] for r in recs)
    pps = [r for r in recs if r["kind"] == "promotion_pass"]
    adm = [r for r in recs if r["kind"] == "admission"]
    q_adm = [r for r in adm if r["from_queue"] is True]
    if hb["decision_path"] == "report":
        if sites["fill_pass"].get("count") != by_kind["fill_pass"]:
            v.append("fill_pass count")
        if sites["promotion_scan"].get("count") != len(pps):
            v.append("promotion_scan count")
        s_adm = [r for r in adm if r["from_queue"] is False]
        sa = sites["scan_admissions"]
        if sa.get("reached") != len(s_adm) or sa.get("completed") != sum(
                1 for r in s_adm if r["returned"] is not None):
            v.append("scan_admissions reached/completed")
    else:
        if sites["promotion_monitor"].get("count") != len(pps):
            v.append("promotion_monitor count")
        if by_kind["fill_pass"] or by_kind["fill"]:
            v.append("fill records on a monitor run")
    pa = sites["promotion_admissions"]
    expected = sum(1 for p in pps for c in p["candidates"]
                   if c.get("open_trade_expected") is True)
    if (pa.get("expected"), pa.get("reached"), pa.get("linked")) != (
            expected, len(q_adm),
            sum(1 for r in q_adm if r["parent_seq"] is not None)):
        v.append("promotion_admissions expected/reached/linked")
    if hb["records_attempted"] != len(recs) or \
            hb["records_written"] != len(recs) or hb["write_failures"]:
        v.append(f"records attempted {hb['records_attempted']} written "
                 f"{hb['records_written']} != {len(recs)} records")
    if hb["records_incomplete"] != sum(1 for r in recs
                                       if not r["record_complete"]):
        v.append("records_incomplete")
    return v


def lineage_violations(hb, a):
    v = []
    if not a.startswith(hb["repository_commit"]):
        v.append(f"repository_commit {hb['repository_commit']} is not the "
                 f"commit the run started on ({a[:7]})")
    for m in MANIFESTS:
        for path, digest in sorted(hb[m].items()):
            data = blob(a, path)
            if data is None:
                v.append(f"{m}: {path} does not exist at {a[:7]}")
            elif hashlib.md5(data).hexdigest() != digest:
                v.append(f"{m}: {path} digest differs from the file at "
                         f"{a[:7]}")
    return v


def time_violations(hb, recs, a, b):
    v = []
    start, done = parse_time(hb["started"]), parse_time(hb["flushed_at"])
    if start < commit_time(a) - CLOCK_SLACK:
        v.append("started is earlier than the parent commit")
    if done > commit_time(b) + CLOCK_SLACK:
        v.append("flushed_at is later than this commit")
    stamps = [("t_scan_start", hb["t_scan_start"]),
              ("t_scan_end", hb["t_scan_end"])]
    for r in recs:
        for f in TIME_FIELDS.get(r["kind"], ()):
            stamps.append((f"record {r['seq']} {f}", r[f]))
        for i, c in enumerate(r.get("candidates") or []):
            for f in ("t_validate_start", "t_validate_end"):
                stamps.append((f"record {r['seq']} candidates[{i}].{f}",
                               c.get(f)))
    for label, s in stamps:
        t = parse_time(s)
        if s is not None and not start <= t <= done:
            v.append(f"{label} outside [started, flushed_at]")
    return v


# ---- mutations captured by one run ----------------------------------------
#
# A mutation: (file, key) -> intent, under one owning call's save chain.
# `order` places it in the run; mutations of one owner share their save.

class Mut:
    __slots__ = ("file", "key", "intent", "save", "owner", "order", "rec",
                 "role", "memory", "problem")

    def __init__(self, file, key, intent, save, owner, order, rec, role,
                 memory=None, problem=None):
        self.file, self.key, self.intent, self.save = file, key, intent, save
        self.owner, self.order, self.rec, self.role = owner, order, rec, role
        self.memory, self.problem = memory, problem


def _inner_expiry(recs, mq):
    nxt = [r for r in recs if r["seq"] == mq["seq"] + 1]
    return nxt[0] if nxt and nxt[0]["kind"] == "queue_expiry" else None


def mutations(recs, hb, base):
    """Every captured mutation of one run, and the expiry parse failures.
    `base` is the parent commit's state per file (for maintenance's
    baseline filter only)."""
    by_seq = {r["seq"]: r for r in recs}
    events = hb["queue_events"]
    muts, parse_failed, problems = [], 0, []
    carriers = {}
    for r in recs:
        if r["kind"] == "queue_maintenance":
            ex = _inner_expiry(recs, r)
            if ex is not None:
                carriers[ex["seq"]] = r
    for r in recs:
        k, s = r["kind"], r["seq"]
        if k == "admission":
            if r["branch"] == "pending" and r["pending_appended"]:
                muts.append(Mut("pending", (r["symbol"], r["signal_date"]),
                                "present", r["pending_save"], ("adm", s), s,
                                r, "admission"))
        elif k == "fill":
            fp = by_seq.get(r["parent_seq"])
            if fp is None or fp["kind"] != "fill_pass":
                problems.append(f"fill {s} has no fill pass")
                continue
            key = (r["symbol"], r["signal_date"])
            muts.append(Mut("pending", key, "absent" if r["executed_marked"]
                            else "present", fp["pending_save"],
                            ("fill_pass", fp["seq"]), s, r, "fill"))
            if r["trade_appended"]:
                muts.append(Mut("trades", (r["symbol"], r["signal_date"],
                                           r["entry_date"]), "added",
                                fp["trades_save"], ("fill_pass", fp["seq"]),
                                s, r, "fill"))
        elif k == "queue_insert":
            owner = ("qi", s)
            if r["insert"] == "already_queued":
                muts.append(Mut("queue", r["symbol"], "present", None, owner,
                                s, r, "insert", memory="already_queued"))
                continue
            muts.append(Mut("queue", r["symbol"], "absent"
                            if r["queue_memory"] == "inserted_then_evicted"
                            else "present", r["queue_save"], owner, s, r,
                            "insert", memory=r["queue_memory"]))
            for e in r["evicted"]:
                if e != r["symbol"]:
                    muts.append(Mut("queue", e, "absent", r["queue_save"],
                                    owner, s, r, "evicted"))
        elif k == "queue_expiry":
            mq = carriers.get(s)
            save, problem = ap.chain_save(
                r["queue_save"], (mq["queue_save"],) if mq else ())
            for x in r["removals"]:
                if x["action"] == "expiry_parse_failed":
                    parse_failed += 1
                    continue
                muts.append(Mut("queue", x.get("symbol"), "absent", save,
                                ("expiry", s), s, r, "expired",
                                problem=problem))
        elif k == "queue_maintenance":
            ex = _inner_expiry(recs, r)
            start = r["qe_index"]
            if ex is not None:
                start = ex["event_window"][1]
                if start is None:
                    continue
            order = (ex["seq"] if ex else s) + 0.5
            gone = {x.get("symbol") for x in (ex["removals"] if ex else [])}
            present = base["queue"] if base["queue"] == ap.UNREADABLE \
                else queue_syms(base["queue"]) | {
                    m.key for m in muts
                    if m.file == "queue" and m.intent == "present"}
            first = {}
            for e in events[start:]:
                if e[1] in MAINTENANCE_ACTIONS and e[0] not in first:
                    first[e[0]] = e[1]
            for sym, action in first.items():
                if sym in gone or (present != ap.UNREADABLE
                                   and sym not in present):
                    continue
                muts.append(Mut("queue", sym, "absent" if action == "dropped"
                                else "present", r["queue_save"],
                                ("maintenance", s), order, r, "maintenance"))
        elif k == "promotion_pass":
            for c in r["candidates"]:
                o = c.get("outcome")
                if o in PROMOTION_REMOVES or o == "deferred_transient":
                    muts.append(Mut("queue", c.get("symbol"), "absent"
                                    if o in PROMOTION_REMOVES else "present",
                                    r["queue_save"], ("promotion", s), s, r,
                                    "candidate"))
    muts.sort(key=lambda m: m.order)
    return muts, parse_failed, problems


AMBIGUOUS = "ambiguous"


def membership(file, st, key):
    if file == "pending":
        return ap.pending_membership(st, *key)
    if file == "queue":
        return ap.queue_membership(st, key)
    return tcount(st, *key)


def annotate(muts, base):
    """Baseline of every mutation (§3.5: the parent commit folded with
    earlier owners whose save chain completed; ambiguous after an earlier
    owner on the same file raised) and whether a later owner supersedes it.
    Returns {id(mut): (baseline, later)}: later is None when the commit
    can show the mutation's effect (it is the last on its key, or a later
    owner leaves the same intent); "noclaim" when it changed nothing and a
    later owner changed the key; "reused" when it changed the key and a
    later owner changed it again, so no commit can show whether it
    persisted (§5 rule 8, generalised to every file)."""
    out = {}
    for file in ("pending", "trades", "queue"):
        fm = [m for m in muts if m.file == file]
        owners = []
        for m in fm:
            if m.owner not in owners:
                owners.append(m.owner)
        folded, raised = {}, False
        for owner in owners:
            group = [m for m in fm if m.owner == owner]
            for m in group:
                if raised:
                    b = AMBIGUOUS
                else:
                    b = folded.get(m.key, membership(file, base[file], m.key))
                out[id(m)] = [b, None]
            saves = {m.save for m in group}
            for m in group:
                if m.save != "completed" or raised:
                    continue
                if file != "trades":
                    folded[m.key] = m.intent
                elif is_count(out[id(m)][0]):
                    folded[m.key] = out[id(m)][0] + 1
            if "raised" in saves:
                raised = True
        last = {}
        for m in fm:
            last[m.key] = m
        for m in fm:
            lm = last[m.key]
            if lm is m or lm.owner == m.owner or m.intent == lm.intent:
                out[id(m)][1] = None
            elif m.intent != out[id(m)][0]:
                out[id(m)][1] = "reused"
            else:
                out[id(m)][1] = "noclaim"
    return {k: tuple(v) for k, v in out.items()}


def check_mut(m, baseline, observed):
    """§3.5 verdict for a non-fill mutation that is the last on its key."""
    if baseline == AMBIGUOUS:
        return INCONCLUSIVE, f"{m.file}_baseline_ambiguous"
    if m.problem:
        return FAIL, m.problem
    if m.role == "insert":
        return ap.check_queue(m.memory, observed, m.save, baseline=baseline)
    return ap.check_membership(m.file, m.intent, baseline, observed, m.save)


def state_diff(base, obs, muts, parse_failed):
    """Independent of save facts: every membership change between the two
    commits has a captured mutation. Returns [(verdict, reason)]."""
    out = []
    for file, path in (("pending", PENDING), ("trades", TRADES),
                       ("queue", QUEUE)):
        if ap.UNREADABLE in (base[file], obs[file]):
            out.append((INCONCLUSIVE, f"{file}_unreadable"))
    intents = {}
    for m in muts:
        intents.setdefault((m.file, m.key), set()).add(m.intent)
    if ap.UNREADABLE not in (base["pending"], obs["pending"]):
        a, b = pend_keys(base["pending"]), pend_keys(obs["pending"])
        for k in sorted(b - a, key=str):
            if "present" not in intents.get(("pending", k), ()):
                out.append((FAIL, f"unrecorded_pending_added {k}"))
        for k in sorted(a - b, key=str):
            if "absent" not in intents.get(("pending", k), ()):
                out.append((FAIL, f"unrecorded_pending_removed {k}"))
    if ap.UNREADABLE not in (base["trades"], obs["trades"]):
        a, b = base["trades"], obs["trades"]
        if len(b) < len(a):
            out.append((FAIL, f"trades_removed {len(a)} -> {len(b)}"))
        for i in range(min(len(a), len(b))):
            if trade_key(a[i]) != trade_key(b[i]):
                out.append((FAIL, f"trade_key_changed at {i}: "
                            f"{trade_key(a[i])} -> {trade_key(b[i])}"))
        rec = Counter(m.key for m in muts if m.file == "trades")
        for k, n in sorted(Counter(trade_key(t) for t in b[len(a):]).items(),
                           key=str):
            if rec[k] < n:
                out.append((FAIL, f"unrecorded_trade {k}"))
    if ap.UNREADABLE not in (base["queue"], obs["queue"]):
        a, b = queue_syms(base["queue"]), queue_syms(obs["queue"])
        for s in sorted(b - a, key=str):
            if "present" not in intents.get(("queue", s), ()):
                out.append((FAIL, f"unrecorded_queue_added {s}"))
        silent = [s for s in sorted(a - b, key=str)
                  if "absent" not in intents.get(("queue", s), ())]
        if len(silent) > parse_failed:
            out.append((FAIL, f"unrecorded_queue_removed {silent}"))
        elif silent:
            out.append((INCIDENT, f"expiry_parse_failed_removal {silent}"))
        if parse_failed > len(silent):
            out.append((INCONCLUSIVE, "expiry_parse_failed_unattributed"))
    return out


# ---- episodes (§5) --------------------------------------------------------

class Episode:
    __slots__ = ("id", "key", "origin", "adm", "hb", "cand", "link")

    def __init__(self, key, origin, adm=None, hb=None, cand=None):
        self.key, self.origin = key, origin
        self.adm, self.hb, self.cand = adm, hb, cand
        self.id = (key + (adm["cycle_id"], adm["seq"]) if adm else
                   key + (origin,))
        self.link = None


def _candidate(recs, adm):
    """The promoted candidate behind a queue-route admission."""
    if adm.get("from_queue") is not True:
        return None
    pp = [r for r in recs if r.get("seq") == adm.get("parent_seq")
          and r.get("kind") == "promotion_pass"]
    if not pp:
        return None
    hit = [c for c in pp[0].get("candidates") or []
           if c.get("symbol") == adm.get("symbol")
           and c.get("open_trade_expected") is True]
    return hit[0] if len(hit) == 1 else None


class Episodes:
    def __init__(self, base, hist_recs, hist_beats):
        st = state(base, PENDING, False)
        if st == ap.UNREADABLE:
            raise Fail(f"pending_signals.json at {base[:7]} is unreadable; "
                       "no episode baseline")
        beats = {h.get("cycle_id"): h for h in hist_beats}
        by_cycle = {}
        for r in hist_recs:
            by_cycle.setdefault(r.get("cycle_id"), []).append(r)
        last = {}
        for r in hist_recs:
            if r.get("kind") == "admission" and r.get("branch") == "pending" \
                    and r.get("pending_appended") is True:
                last[(r.get("symbol"), r.get("signal_date"))] = r
        self.open, self.unknown = {}, set()
        for key in sorted(pend_keys(st), key=str):
            adm = last.get(key)
            if adm is None:
                self.open[key] = Episode(key, "preactivation")
            else:
                self.open[key] = Episode(
                    key, "history", adm, beats.get(adm.get("cycle_id")),
                    _candidate(by_cycle.get(adm.get("cycle_id"), []), adm))
        syms = Counter(k[0] for k in self.open)
        dup = sorted(s for s, n in syms.items() if n > 1)
        if dup:
            raise Fail(f"rule 1: several pending episodes per symbol at "
                       f"{base[:7]}: {dup}")

    def symbol_busy(self, symbol):
        return any(k[0] == symbol for k in list(self.open) +
                   list(self.unknown))

    def forget(self, key):
        self.open.pop(key, None)
        self.unknown.add(key)


def a2_note(ep, rec, hb):
    """A2 for a new link: (verdicts, note)."""
    bar = rec.get("bar_date")
    out = []
    if ep.adm is not None:
        s, e = parse_time(ep.adm.get("t_enter")), parse_time(
            ep.adm.get("t_return"))
        adm_v, bad = classify_a2(s, e, bar)
        out += bad
        secs = (None if s is None else
                int((s - session_open_utc(bar)).total_seconds()))
    else:
        adm_v, secs = "unknown", None
    if ep.adm is None:
        obs_v = "unknown"
    elif ep.adm.get("route") == "scan_direct":
        h = ep.hb or {}
        obs_v, bad = classify_a2(parse_time(h.get("t_scan_start")),
                                 parse_time(h.get("t_scan_end")), bar)
        out += bad
    else:
        c = ep.cand or {}
        obs_v, bad = classify_a2(parse_time(c.get("t_validate_start")),
                                 parse_time(c.get("t_validate_end")), bar)
        out += bad
    note = (f"A2 {rec['symbol']} bar {bar}: a2_admission {adm_v}"
            f" (after_open_seconds {secs}), a2_observation {obs_v}"
            f" [{SESSION_OPEN_BASIS}; episode {ep.origin}]")
    return [(FAIL, f"{x} {rec['symbol']}") for x in sorted(set(out))], note


# ---- one commit ------------------------------------------------------------

def _fill(eps, r, fp, pm, ann, base, obs, concurrent, V, notes, links,
          inconclusive_trades, hb):
    key = (r["symbol"], r["signal_date"])
    if ("pending", key) in concurrent:
        V.append((INCONCLUSIVE, f"concurrent_runs pending {key}"))
        eps.forget(key)
        return
    if key in eps.unknown:
        V.append((INCONCLUSIVE, f"episode_unknown {key}"))
        if ap.pending_membership(obs["pending"], *key) == "absent":
            eps.unknown.discard(key)
        if r["trade_appended"]:
            inconclusive_trades.add((key + (r["entry_date"],)))
        return
    ep = eps.open.get(key)
    if ep is None:
        V.append((FAIL, f"rule 2: fill_without_episode {key}"))
        return
    b, later = ann[id(pm)]
    if later == "reused":
        V.append((INCONCLUSIVE, f"rule 8: key_reused_in_run {key}"))
        eps.open.pop(key, None)
        if r["trade_appended"]:
            inconclusive_trades.add((key + (r["entry_date"],)))
        return
    if b == AMBIGUOUS:
        V.append((INCONCLUSIVE, f"pending_baseline_ambiguous {key}"))
        eps.forget(key)
        return
    if r["trade_appended"]:
        tk = key + (r["entry_date"],)
    else:
        tk = ep.link or key + (None,)
    n, m = tcount(base["trades"], *tk), tcount(obs["trades"], *tk)
    res = ap.check_fill(r["executed_marked"], r["trade_appended"],
                        ap.pending_membership(obs["pending"], *key), n, m,
                        fp["pending_save"], fp["trades_save"],
                        outcome=r["outcome"], pending_baseline=b)
    v, name = res["verdict"], res["name"]
    if v == FAIL:
        V.append((FAIL, f"fill {key}: {res['reasons']}"))
        return
    if v == INCONCLUSIVE:
        V.append((INCONCLUSIVE, f"fill {key}: {res['reasons']}"))
        eps.forget(key)
        if r["trade_appended"]:
            inconclusive_trades.add(tk)
        return
    if v == INCIDENT or name not in ("filled", "removed", "retained",
                                     "removed_over_existing_trade"):
        V.append((INCIDENT, f"{name} {key}"))
    else:
        V.append((CONSISTENT, f"fill {name} {key} ({r['outcome']})"))
    if res["new_link"]:
        if ep.link is not None:
            V.append((FAIL, f"rule 5: second_trade_link {key}"))
            return
        ep.link = tk
        links[tk] += 1
        if ep.origin == "preactivation":
            V.append((INCIDENT, f"unlinked_preactivation {key}"))
        bad, note = a2_note(ep, r, hb)
        V.extend(bad)
        notes.append(note)
    if ap.pending_membership(obs["pending"], *key) == "absent":
        if name == "removed_over_existing_trade":
            if ep.link is None and ep.origin != "preactivation":
                V.append((FAIL, f"unowned_existing_trade {key}"))
                return
            if ep.link is not None and tcount(obs["trades"], *ep.link) < 1:
                V.append((FAIL, f"rule 5: linked trade missing {ep.link}"))
                return
        eps.open.pop(key)
        notes.append(f"episode closed {key} ({name}"
                     f"{', linked' if ep.link else ''})")


def _admission(eps, r, am, ann, result, recs, hb, concurrent, V, notes):
    key = (r["symbol"], r["signal_date"])
    if ("pending", key) in concurrent:
        V.append((INCONCLUSIVE, f"concurrent_runs pending {key}"))
        eps.forget(key)
        return
    b, later = ann[id(am)]
    if later == "reused":
        V.append((INCONCLUSIVE, f"key_reused_in_run {key}"))
        eps.forget(key)
        return
    v, reason = result
    if v == CONSISTENT:
        if eps.symbol_busy(key[0]):
            V.append((FAIL, f"rule 1: admission_with_open_episode {key}"))
            return
        eps.open[key] = Episode(key, "admitted", r, hb, _candidate(recs, r))
        V.append((CONSISTENT, f"pending admitted {key}"))
        notes.append(f"episode opened {key} ({r['route']})")
    elif v == INCIDENT:
        V.append((INCIDENT, f"{reason} {key}"))
    elif v == INCONCLUSIVE:
        V.append((INCONCLUSIVE, f"{reason} {key}"))
        eps.forget(key)
    else:
        V.append((FAIL, f"{reason} {key}"))


def _cross_checks(recs, hb, monitor_beats, V):
    for pp in (r for r in recs if r["kind"] == "promotion_pass"):
        for c in pp["candidates"]:
            if c.get("open_trade_expected") is not True:
                continue
            hit = [a for a in recs if a["kind"] == "admission"
                   and a["parent_seq"] == pp["seq"]
                   and a["symbol"] == c.get("symbol")]
            if len(hit) != 1:
                V.append((FAIL, f"promoted candidate {c.get('symbol')} has "
                          f"{len(hit)} admission records"))
                continue
            a = hit[0]
            if (a["signal_date"], a["decision_price"]) != (
                    c.get("promotion_signal_date"),
                    c.get("promotion_price")) or (
                    a["returned"] is not None
                    and c.get("open_trade_result") is not None
                    and a["returned"] != c.get("open_trade_result")):
                V.append((FAIL, f"promoted candidate {c.get('symbol')} "
                          "disagrees with its admission record"))
    if hb["decision_path"] != "monitor":
        return
    pair = [m for m in monitor_beats
            if m.get("cycle_started_at") == hb["peer_cycle_started_at"]
            and m.get("invocation_token") == hb["peer_invocation_token"]]
    if len(pair) != 1:
        V.append((INCONCLUSIVE, f"peer_unpaired ({len(pair)} monitor "
                  "heartbeats match)"))
        return
    saved = pair[0].get("trade_state_save_completed")
    if saved is not hb["promotion_expected"]:
        V.append((FAIL, f"monitor trade_state_save_completed {saved!r} != "
                  f"promotion_expected {hb['promotion_expected']!r}"))
    if saved is True and not any(r["kind"] == "promotion_pass"
                                 for r in recs):
        V.append((FAIL, "monitor saved trade state without a promotion "
                  "pass record"))


def _gap(eps, base, obs, V, notes):
    """No admission evidence for this commit (health path): what changed is
    unknown, never silently accepted."""
    if ap.UNREADABLE in (base["pending"], obs["pending"]):
        for k in list(eps.open):
            eps.forget(k)
        return
    a, b = pend_keys(base["pending"]), pend_keys(obs["pending"])
    for k in sorted(b - a, key=str):
        eps.unknown.add(k)
        V.append((INCONCLUSIVE, f"pending added without evidence {k}"))
    for k in sorted(a - b, key=str):
        if k in eps.open:
            eps.open.pop(k)
            notes.append(f"episode closed unobserved {k}")
        eps.unknown.discard(k)
    if ap.UNREADABLE not in (base["trades"], obs["trades"]) and \
            len(obs["trades"]) > len(base["trades"]):
        V.append((INCONCLUSIVE, "trades added without evidence"))


def reconcile(eps, groups, base, obs, monitor_beats, V, notes):
    links, inconclusive_trades = Counter(), set()
    data, touched = [], Counter()
    for hb, recs in groups:
        muts, pf, probs = mutations(recs, hb, base)
        V.extend((FAIL, p) for p in probs)
        data.append((hb, recs, muts, annotate(muts, base), pf))
        for fk in {(m.file, m.key) for m in muts}:
            touched[fk] += 1
    concurrent = {fk for fk, n in touched.items() if n > 1}
    all_muts, parse_failed = [], 0
    for hb, recs, muts, ann, pf in data:
        all_muts += muts
        parse_failed += pf
        results = {}
        for m in muts:
            if m.role == "fill" or m.file == "trades":
                continue
            fk = (m.file, m.key)
            if fk in concurrent:
                if m.role != "admission":
                    V.append((INCONCLUSIVE,
                              f"concurrent_runs {m.file} {m.key}"))
                continue
            b, later = ann[id(m)]
            if later == "reused":
                if m.role != "admission":
                    V.append((INCONCLUSIVE,
                              f"key_reused_in_run {m.file} {m.key}"))
                continue
            if later == "noclaim" and m.role != "admission":
                continue
            res = check_mut(m, b, membership(m.file, obs[m.file], m.key))
            results[id(m)] = res
            if m.role != "admission":
                V.append((res[0], f"{res[1] or m.file + ' consistent'} "
                          f"{m.key} ({m.role})"))
        by_seq = {r["seq"]: r for r in recs}
        pend = {m.rec["seq"]: m for m in muts if m.file == "pending"}
        for r in recs:
            m = pend.get(r["seq"])
            if m is None:
                continue
            if r["kind"] == "fill":
                _fill(eps, r, by_seq[r["parent_seq"]], m, ann, base, obs,
                      concurrent, V, notes, links, inconclusive_trades, hb)
            else:
                _admission(eps, r, m, ann, results.get(id(m)), recs, hb,
                           concurrent, V, notes)
        _cross_checks(recs, hb, monitor_beats, V)
    V.extend(state_diff(base, obs, all_muts, parse_failed))
    if ap.UNREADABLE not in (base["trades"], obs["trades"]):
        added = Counter(trade_key(t)
                        for t in obs["trades"][len(base["trades"]):])
        for k in sorted(set(added) | set(links), key=str):
            if k in inconclusive_trades:
                continue
            if added[k] != links[k]:
                V.append((FAIL, f"rule 4: trade {k} added {added[k]}, "
                          f"new links {links[k]}"))
    rule7(eps, obs, V)


def rule7(eps, obs, V):
    if obs["pending"] == ap.UNREADABLE:
        for k in list(eps.open):
            eps.forget(k)
        return
    held = pend_keys(obs["pending"])
    for k in sorted(set(eps.open) - held, key=str):
        V.append((FAIL, f"rule 7: episode open without a pending {k}"))
        eps.open.pop(k)
    for k in sorted(held - set(eps.open) - eps.unknown, key=str):
        V.append((FAIL, f"rule 7: pending without an episode {k}"))
    eps.unknown &= held


# ---- the chain -------------------------------------------------------------

def _subject_path(b, tag):
    subject = git("log", "-1", "--format=%s", b).strip()
    for prefix, path in SUBJECTS:
        if subject.startswith(prefix):
            return path
    raise Fail(f"{tag}: not a bot report or monitor commit: {subject!r}")


def _states(a, b):
    base, obs = {}, {}
    for file, path in (("pending", PENDING), ("trades", TRADES),
                       ("queue", QUEUE)):
        base[file] = state(a, path, False)
        obs[file] = state(b, path, blob(a, path) is not None)
    return base, obs


def _groups(beats, recs, tag):
    ids = [h["cycle_id"] for h in beats]
    if len(set(ids)) != len(ids):
        raise Fail(f"{tag}: two heartbeats share a cycle_id")
    groups = []
    for hb in beats:
        mine = [r for r in recs if r.get("cycle_id") == hb["cycle_id"]]
        seqs = sorted(r.get("seq") for r in mine
                      if is_count(r.get("seq")))
        if seqs != list(range(len(mine))):
            raise Fail(f"{tag}: run {hb['cycle_id']}: record seq values are "
                       f"not 0..{len(mine) - 1}")
        groups.append((hb, sorted(mine, key=lambda r: r["seq"])))
    stray = [r for r in recs if r.get("cycle_id") not in ids]
    if stray:
        raise Fail(f"{tag}: {len(stray)} records without a heartbeat in "
                   "this commit")
    return groups


def _validate(groups, a, b, tag):
    for hb, recs in groups:
        bad = heartbeat_violations(hb)
        if bad:
            raise Fail(f"{tag}: heartbeat {hb.get('cycle_id')!r} fails the "
                       f"frozen schema: {bad}")
        for r in recs:
            bad = record_violations(r, hb, len(recs))
            if bad:
                raise Fail(f"{tag}: record {r.get('seq')} ({r.get('kind')}, "
                           f"{r.get('symbol')!r}) fails the frozen "
                           f"contract: {bad}")
        bad = (recomputed_sites(hb, recs) + lineage_violations(hb, a)
               + time_violations(hb, recs, a, b))
        if bad:
            raise Fail(f"{tag}: run {hb['cycle_id']}: heartbeat, lineage or "
                       f"time evidence invalid: {bad}")


def _run_verdicts(hb, recs, V):
    path = hb["decision_path"]
    for name, s in sorted(hb["call_sites"].items()):
        if s["verdict"] in (FAIL, INCONCLUSIVE):
            V.append((s["verdict"], f"call site {name}: {s.get('state')}"))
    if hb[f"{path}_completed"] is not True:
        V.append((INCONCLUSIVE, f"aborted {path} run: "
                  f"{hb['loop_exception']}"))
    elif any(not r["record_complete"] for r in recs):
        V.append((INCONCLUSIVE, "a recorded call raised in a completed run"))
    if hb["queue_event_write_failures"]:
        V.append((INCIDENT, f"queue_event_write_failed "
                  f"{hb['queue_event_write_failures']}"))


def inspect(refs):
    if len(refs) < 2:
        raise Fail("need <R> and at least one bot commit")
    shas = [full_sha(r) for r in refs]
    if len(set(shas)) != len(shas):
        raise Fail("the same commit was given twice")
    r0 = shas[0]
    hist_beats = parse_lines(blob(r0, HEARTBEAT) or b"", f"history {HEARTBEAT}")
    hist_recs = parse_lines(blob(r0, RECORDS) or b"", f"history {RECORDS}")
    seen_ids = {h.get("cycle_id") for h in hist_beats} | {
        r.get("cycle_id") for r in hist_recs}
    seen_starts = {h.get("started") for h in hist_beats}
    eps = Episodes(r0, hist_recs, hist_beats)
    results = []
    for n, (a, b) in enumerate(zip(shas, shas[1:]), 1):
        tag = f"cycle {n} ({b[:7]})"
        parents = git("rev-list", "--parents", "-n", "1", b).split()[1:]
        if parents != [a]:
            raise Fail(f"{tag}: parent is {[p[:7] for p in parents]}, not "
                       f"{a[:7]}; the commits do not form a direct chain")
        path = _subject_path(b, tag)
        beats = appended(a, b, HEARTBEAT, tag)
        recs = appended(a, b, RECORDS, tag)
        health = appended(a, b, HEALTH, tag)
        monitor_beats = appended(a, b, MONITOR_HEARTBEAT, tag)
        base, obs = _states(a, b)
        V, notes = [], []
        if health and beats:
            raise Fail(f"{tag}: heartbeat AND admission health line in one "
                       "commit (partial flush)")
        if not beats:
            if recs:
                raise Fail(f"{tag}: admission records without a heartbeat")
            if not health:
                raise Fail(f"{tag}: no admission heartbeat and no health "
                           "line")
            V.append((INCONCLUSIVE, "admission evidence unavailable: "
                      f"{[h.get('reason') for h in health]}"))
            _gap(eps, base, obs, V, notes)
            results.append((tag, path, V, notes, 0))
            continue
        groups = _groups(beats, recs, tag)
        if path not in {hb["decision_path"] for hb, _ in groups}:
            raise Fail(f"{tag}: no {path} heartbeat in a {path} commit")
        for hb, _ in groups:
            if hb["cycle_id"] in seen_ids or hb["started"] in seen_starts:
                raise Fail(f"{tag}: cycle_id or started repeats an earlier "
                           "run, including history before <R>")
            seen_ids.add(hb["cycle_id"])
            seen_starts.add(hb["started"])
        _validate(groups, a, b, tag)
        for hb, grecs in groups:
            _run_verdicts(hb, grecs, V)
        if len(groups) > 1:
            notes.append(f"{len(groups)} runs in one commit")
        reconcile(eps, groups, base, obs, monitor_beats, V, notes)
        results.append((tag, path, V, notes, len(recs)))
    return results, eps


CODE = {CONSISTENT: 0, INCIDENT: 0, INCONCLUSIVE: 2, FAIL: 1}
WORD = {0: "PASS", 1: "FAIL", 2: "INCONCLUSIVE"}


def cycle_code(V):
    return CODE[ap.worst(*[v for v, _ in V])] if V else 0


def main(argv):
    verbose = "--verbose" in argv
    argv = [a for a in argv if a != "--verbose"]
    try:
        results, eps = inspect(argv)
    except Fail as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:                          # noqa: BLE001
        print(f"FAIL: inspection error {type(exc).__name__}: {exc}")
        return 1
    worst = 0
    for tag, path, V, notes, n in results:
        code = cycle_code(V)
        worst = max(worst, code, key=lambda c: (0, 2, 1).index(c))
        print(f"{WORD[code]:<13} {tag} {path}: {n} records")
        for v, reason in V:
            if v != CONSISTENT or verbose:
                print(f"  {v:<11} {reason}")
        for note in notes:
            print(f"  {'note':<11} {note}")
    print(f"open episodes: {len(eps.open)} "
          f"({sum(1 for e in eps.open.values() if e.link)} linked), "
          f"unknown: {len(eps.unknown)}")
    print("RESULT:", WORD[worst])
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
