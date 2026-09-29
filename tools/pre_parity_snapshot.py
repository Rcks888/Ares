"""Pre-shadow state snapshot — Phase 4 gate artifact.

    python3 tools/pre_parity_snapshot.py            # print
    python3 tools/pre_parity_snapshot.py --write     # also save timestamped JSON

Read-only. Touches no production state, sends nothing, and is not referenced by
any scheduled entry point. Re-run immediately before Phase 4 starts and again
before Phase 5 wiring: these values move daily, so a stale snapshot is worse
than none.

Captures the Phase 3 gate evidence plus the boundary state that makes ABM and
SDGR valuable shadow subjects.
"""
import ast
import hashlib
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import parity_compare as sc  # noqa: E402
from engine.tracker_compat import CONTRACT_VERSION  # noqa: E402

SUITES = {
    "adapter": "tests/test_tracker_compat.py",
    "classifier": "tests/test_parity_compare.py",
    "wiring": "tests/test_parity_runner.py",
    "evaluator": "tests/test_parity_eval.py",
    "bridge": "tests/test_parity_hook.py",
    # Phase 0.5. Registered here so the baseline cannot silently under-report:
    # an unregistered suite contributes zero assertions and its failures never
    # reach the gate, which is the same false-pass shape as an unregistered test.
    "phase05_capture": "tests/test_phase05_capture.py",
    "phase05_live_path": "tests/test_phase05_live_path.py",
    "phase05_integrity": "tests/test_phase05_integrity.py",
    "phase05_network": "tests/test_phase05_network.py",
    "blast_radius": "tests/test_blast_radius.py",
    "output_path": "tests/test_output_path.py",
    "tracker_diff": "tests/test_tracker_diff.py",
    "vps_verify": "tests/test_vps_verify_failure_modes.py",
    "parity_output": "tests/test_parity_output_gate.py",
}

PRE_CLEAN = {"ABM": "2026-09-09", "SDGR": "2026-09-18"}
ROLLBACK_TAG = "pre-tracker-swap"


IMPLEMENTATION_GLOBS = ("engine/", "tests/", "tools/pre_parity_snapshot.py",
                        "daily_report.py")

# Operational state the live host legitimately rewrites every cycle. On a live
# host, logs_unchanged_since_tag is a GUARANTEED failure, and a permanently
# failing gate teaches reviewers to ignore the suite. It is therefore scoped to
# development checkouts, and the live host gets a gate that actually discriminates.
ALLOWED_RUNTIME_LOGS = (
    "logs/psi_state.json",
    "logs/virtual_trades.json",
    "logs/trades_report.csv",
    "logs/signal_queue.json",
    "logs/queue_ranked.json",
    "logs/queue_events.jsonl",
    "logs/last_scan_summary.txt",
    # Phase 4 migration evidence. Append-only, bot-committed for off-host backup.
    # Validated by _parity_output_state(), not merely tolerated: allow-listing a
    # path only stops the log gate from flagging it, and on its own that would
    # replace one check with no check.
    "logs/tracker_parity_v1.jsonl",
)
RUNTIME_COMMIT_AUTHORS = ("ares-bot@users.noreply.github.com",)

PARITY_OUTPUT = "logs/tracker_parity_v1.jsonl"
ACTIVATION_ENTRY_POINT = "daily_report.py"


def _frozen_record_fields():
    """The record schema, DERIVED from build_record itself.

    Not a local list. A hand-copied field set drifts the moment build_record
    changes, and the gate would keep validating a schema that no longer exists
    while still reporting success. Raises if the structure cannot be found --
    an underivable schema is a failure, not an empty requirement.
    """
    src = (ROOT / "engine" / "parity_compare.py").read_text()
    fn = [n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.FunctionDef) and n.name == "build_record"]
    if not fn:
        raise RuntimeError("build_record not found in engine/parity_compare.py")
    dicts = [n for n in ast.walk(fn[0])
             if isinstance(n, ast.Dict) and len(n.keys) > 10]
    if not dicts:
        raise RuntimeError("build_record's record dict not found")
    d = max(dicts, key=lambda n: len(n.keys))
    keys = {k.value for k in d.keys if isinstance(k, ast.Constant)}
    if len(keys) != len(d.keys):
        raise RuntimeError("build_record has computed keys; schema not derivable")
    return keys


def _collection_declared():
    """Is Phase 4 collection declared, AND declared where it takes effect?

    Placement is the whole point. run_ares.sh sources /root/ares/.env AFTER
    daily_report.py runs, so a declaration below the Python invocation -- or in
    .env -- never reaches the trading process. Parity would stay off while
    appearing configured, and the only symptom would be an empty evidence file
    indistinguishable from "no positions to compare".

    A loose search for the export anywhere in the file cannot tell those apart,
    so this walks the script in line order and tracks the value as the shell
    would, up to the entry point.
    """
    out = {"declared": False, "valid": True, "failures": [],
           "export_lines": [], "entry_point_line": None, "value": None}
    path = ROOT / "run_ares.sh"
    if not path.exists():
        out["valid"] = False
        out["failures"].append("run_ares.sh absent")
        return out

    lines = path.read_text().splitlines()
    entry = None
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        if re.search(rf"python3?\s+{re.escape(ACTIVATION_ENTRY_POINT)}\b",
                     stripped):
            entry = i
            break
    out["entry_point_line"] = entry
    if entry is None:
        out["valid"] = False
        out["failures"].append(
            f"{ACTIVATION_ENTRY_POINT} invocation not found; placement of the "
            "declaration cannot be verified")
        return out

    # Assignments and unsets in line order, tracking the effective value.
    value = None
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        m = re.match(r"(?:export\s+)?ARES_PARITY=(\S*)", stripped)
        if m:
            after = i > entry
            out["export_lines"].append(
                {"line": i, "value": m.group(1).strip('"\''),
                 "after_entry_point": after})
            if not after:
                value = m.group(1).strip('"\'')
            continue
        if re.match(r"unset\s+ARES_PARITY\b", stripped):
            out["export_lines"].append(
                {"line": i, "value": "<unset>", "after_entry_point": i > entry})
            if i <= entry:
                value = None

    out["value"] = value
    out["declared"] = value == "1"
    pre = [e for e in out["export_lines"] if not e["after_entry_point"]]
    post = [e for e in out["export_lines"] if e["after_entry_point"]]

    assigns = [e for e in pre if e["value"] != "<unset>"]
    if len(assigns) > 1:
        out["valid"] = False
        out["failures"].append(
            f"{len(assigns)} pre-invocation ARES_PARITY assignments at lines "
            f"{[e['line'] for e in assigns]}; exactly one is required")
    if post and any(e["value"] == "1" for e in post):
        out["valid"] = False
        out["failures"].append(
            f"ARES_PARITY=1 at line {[e['line'] for e in post if e['value']=='1']} "
            f"is AFTER the {ACTIVATION_ENTRY_POINT} invocation at line {entry}; "
            "it cannot reach the trading process")
    if assigns and value is None:
        out["valid"] = False
        out["failures"].append(
            "ARES_PARITY is unset before the invocation after being assigned; "
            "the declaration has no effect")
    return out


def _git_show(rev, path):
    """Bytes of path at rev, or None when absent there."""
    try:
        return subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT,
                              capture_output=True, check=True).stdout
    except subprocess.CalledProcessError:
        return None


def _append_only_history():
    """Append-only, anchored to Git rather than to a mutable sidecar count.

    A recorded line count stored next to the evidence can be rewritten together
    with the evidence. Byte-prefix containment across committed history cannot:
    it detects truncation, rewriting of old records, reordering, replacement and
    mid-file insertion, none of which a line count necessarily changes.
    """
    out = {"prefix_preserved": None, "history_append_only": None,
           "committed_records": 0, "working_tree_records": 0,
           "new_records": 0, "violations": []}
    wt = ROOT / PARITY_OUTPUT
    head = _git_show("HEAD", PARITY_OUTPUT)
    if wt.exists():
        cur = wt.read_bytes()
        out["working_tree_records"] = len(
            [l for l in cur.splitlines() if l.strip()])
        base = head or b""
        out["committed_records"] = len(
            [l for l in base.splitlines() if l.strip()])
        out["prefix_preserved"] = cur.startswith(base)
        out["new_records"] = (out["working_tree_records"]
                              - out["committed_records"])
        if not out["prefix_preserved"]:
            out["violations"].append(
                "working tree is not a byte-extension of the committed "
                "evidence: history was truncated, reordered or rewritten")

    revs = subprocess.run(
        ["git", "log", "--format=%H", "--", PARITY_OUTPUT],
        cwd=ROOT, capture_output=True, text=True).stdout.split()
    revs.reverse()
    ok = True
    prev = b""
    for rev in revs:
        cur = _git_show(rev, PARITY_OUTPUT)
        if cur is None:
            ok = False
            out["violations"].append(f"{rev[:7]} deleted the evidence file")
            continue
        if not cur.startswith(prev):
            ok = False
            out["violations"].append(
                f"{rev[:7]} is not a byte-extension of its parent")
        prev = cur
    out["history_append_only"] = ok if revs else None
    return out


def _parity_output_state():
    """Phase-aware evidence gate.

    parity_output_absent encoded "Phase 4 has not started", so the first write
    would have made every later run fail permanently -- a known-false gate needing
    manual interpretation, which is what replacing logs_unchanged_since_tag was
    meant to end. The states below are explicit so ARMED_NOT_STARTED is not
    reported as a footnote inside a failure list.
    """
    decl = _collection_declared()
    p = ROOT / PARITY_OUTPUT
    out = {"collection_declared": decl["declared"],
           "declaration": decl, "exists": p.exists(), "records": 0,
           "state": None, "valid": False, "failures": [], "notes": []}

    if not decl["valid"]:
        out["state"] = "DECLARATION_MALFORMED"
        out["failures"] = list(decl["failures"])
        return out

    if not decl["declared"]:
        if p.exists():
            out["state"] = "UNDECLARED_OUTPUT_PRESENT"
            out["failures"].append(
                "parity output present but collection is not declared where it "
                "takes effect in run_ares.sh")
            return out
        out["state"] = "NOT_DECLARED_ABSENT"
        out["valid"] = True
        out["notes"].append("Phase 0.5: collection disabled, no evidence file")
        return out

    if not p.exists():
        out["state"] = "ARMED_NOT_STARTED"
        out["valid"] = True
        out["notes"].append("collection declared; no cycle recorded")
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
    if out["failures"]:
        out["state"] = "UNPARSEABLE_JSONL"
        return out

    try:
        required = _frozen_record_fields()
    except Exception as exc:
        out["state"] = "SCHEMA_UNDERIVABLE"
        out["failures"].append(f"record schema not derivable: {exc}")
        return out

    lineage = ("production_commit", "exit_policy_md5", "compatibility_contract",
               "tracker_source_hash", "cycle_id", "timestamp")
    drift, mismatches, incomplete = [], [], []
    seen = set()
    for n, r in enumerate(recs, 1):
        missing = required - set(r)
        if missing:
            drift.append(f"record {n} missing {sorted(missing)}")
        extra = set(r) - required
        if extra:
            drift.append(f"record {n} has unregistered {sorted(extra)}")
        if r.get("record_schema_version") != sc.RECORD_SCHEMA_VERSION:
            drift.append(f"record {n} schema_version "
                         f"{r.get('record_schema_version')!r}")
        # Constant comes from the classifier. The field is difference_class --
        # NOT verdict and NOT result_class, both of which would have made
        # r.get(...) return None and silently pass every real mismatch.
        if r.get("difference_class") == sc.DECISION_CHANGING_MISMATCH:
            mismatches.append(f"record {n} {r.get('symbol')}")
        if [k for k in lineage if r.get(k) in (None, "")]:
            incomplete.append(
                f"record {n} lineage incomplete: "
                f"{[k for k in lineage if r.get(k) in (None, '')]}")
        ident = (r.get("cycle_id"), r.get("symbol"))
        if ident in seen:
            drift.append(f"record {n} duplicate cycle/symbol identity {ident}")
        seen.add(ident)

    hist = _append_only_history()
    out["append_only"] = hist

    # Failures are CUMULATIVE and the state is the most severe one present.
    # Selecting a single branch would have let unrelated schema drift mask a
    # DECISION_CHANGING_MISMATCH: the drift branch would report only its own
    # failures and the mismatch would never appear anywhere in the output.
    out["failures"].extend(mismatches + drift + incomplete + hist["violations"])
    if mismatches:
        out["state"] = "DECISION_CHANGING_MISMATCH_PRESENT"
    elif drift:
        out["state"] = "SCHEMA_DRIFT"
    elif incomplete:
        out["state"] = "LINEAGE_INCOMPLETE"
    elif hist["prefix_preserved"] is False:
        out["state"] = "EVIDENCE_TRUNCATED"
    elif hist["history_append_only"] is False:
        out["state"] = "EVIDENCE_REWRITTEN"
    elif not recs:
        out["state"] = "DECLARED_BUT_EMPTY"
        out["failures"].append("evidence file exists but contains no records")
    else:
        out["state"] = "ACTIVE_VALID"
        out["valid"] = True
    return out


def _parity_output_trackable():
    """Would an ordinary `git add logs/` stage the evidence?

    .gitignore has `logs/*`, and allow-listing a path in ALLOWED_RUNTIME_LOGS
    does not override it. Without a scoped negation the bot's `git add logs/`
    silently skips the file, so the evidence would exist on one host with no
    backup while the allow-list entry suggested it was being archived.
    """
    out = {"ignored": None, "trackable": None, "newly_trackable_others": []}
    r = subprocess.run(["git", "check-ignore", "-q", PARITY_OUTPUT],
                       cwd=ROOT, capture_output=True)
    out["ignored"] = r.returncode == 0
    out["trackable"] = not out["ignored"]
    return out


def _live_log_changes_are_bot_only_and_allowlisted():
    """Runtime-aware replacement for logs_unchanged_since_tag on a live host.

    Proves four separable things, so a real fault cannot hide behind the
    legitimate churn of scheduled trading:
      1. every changed log path is on the approved operational allow-list
      2. commits that touch logs/ are authored by the runtime bot
      3. runtime-authored commits do NOT touch engine/, tests/ or tools/
      4. Phase 0.5 (non-bot) commits do NOT touch logs/
    """
    base = f"{ROLLBACK_TAG}^{{commit}}"
    out = {"is_live_host": None, "changed_logs": [], "unapproved_log_paths": [],
           "runtime_commits_touching_code": [], "review_commits_touching_logs": [],
           "worktree_clean": None, "valid": False}

    changed = [l for l in sh("git", "diff", "--name-only", base,
                             "HEAD").splitlines() if l.strip()]
    out["changed_logs"] = sorted(f for f in changed if f.startswith("logs/"))
    out["is_live_host"] = bool(out["changed_logs"])
    out["unapproved_log_paths"] = sorted(
        f for f in out["changed_logs"] if f not in ALLOWED_RUNTIME_LOGS)

    # Per-commit authorship vs touched paths.
    log = sh("git", "log", "--format=%H%x1f%ae", f"{base}..HEAD").splitlines()
    for line in log:
        if "\x1f" not in line:
            continue
        sha, email = line.split("\x1f", 1)
        files = [f for f in sh("git", "show", "--name-only", "--format=",
                               sha).splitlines() if f.strip()]
        is_bot = email.strip() in RUNTIME_COMMIT_AUTHORS
        code = [f for f in files
                if any(f.startswith(g) for g in IMPLEMENTATION_GLOBS)]
        logs = [f for f in files if f.startswith("logs/")]
        if is_bot and code:
            out["runtime_commits_touching_code"].append(
                {"commit": sha[:7], "files": code})
        if not is_bot and logs:
            out["review_commits_touching_logs"].append(
                {"commit": sha[:7], "files": logs})

    out["worktree_clean"] = sh("git", "status", "--porcelain") == ""
    out["valid"] = (not out["unapproved_log_paths"]
                    and not out["runtime_commits_touching_code"]
                    and not out["review_commits_touching_logs"]
                    and out["worktree_clean"])
    out["claim"] = ("Phase 0.5 work did not modify operational logs. Live "
                    "trading logs have advanced legitimately through scheduled "
                    "Ares Bot cycles and are validated separately as "
                    "allow-listed runtime state.")
    return out
# Changes permitted between generated_from_commit and HEAD without invalidating
# the baseline. Everything else means the baseline no longer describes HEAD.
BASELINE_DRIFT_ALLOWED = ("tools/parity_baseline.json",)


def _baseline_still_describes_head():
    """Ancestor-based validity, not equality.

    Requiring HEAD == generated_from_commit is unsatisfiable under the two-commit
    workflow: the baseline necessarily lands in the commit AFTER the one it
    describes. Instead require that the recorded commit is an ancestor of HEAD
    and that nothing but the baseline itself changed since.
    """
    dest = ROOT / "tools" / "parity_baseline.json"
    if not dest.exists():
        return {"valid": False, "reason": "baseline absent"}
    try:
        base = json.loads(dest.read_text())
    except Exception as e:
        return {"valid": False, "reason": f"unreadable baseline: {e}"}
    commit = base.get("generated_from_commit") or base.get(
        "generated_on_commit")          # tolerate the pre-rename field on read
    if not commit:
        return {"valid": False, "reason": "no generated_from_commit recorded"}
    anc = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", commit, "HEAD"],
        capture_output=True, text=True).returncode == 0
    changed = [l for l in sh("git", "diff", "--name-only", commit,
                             "HEAD").splitlines() if l.strip()]
    relevant = [f for f in changed
                if any(f.startswith(g) for g in IMPLEMENTATION_GLOBS)
                and f not in BASELINE_DRIFT_ALLOWED]
    return {"valid": bool(anc) and not relevant,
            "generated_from_commit": commit,
            "is_ancestor_of_head": anc,
            "files_changed_since": changed,
            "relevant_changes_since": relevant,
            "reason": ("ok" if anc and not relevant
                       else "not an ancestor of HEAD" if not anc
                       else f"implementation changed since baseline: {relevant}")}


def _tracker_diff_evidence():
    """Structured evidence from the registered Phase 0.5 tracker-diff gate."""
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import tracker_diff as td
        v = td.evaluate()
    except Exception as e:                                # pragma: no cover
        return {"verdict": "ERROR", "error": f"{type(e).__name__}: {e}"}
    # The unified diff is for human review and is intentionally not embedded.
    return {k: v[k] for k in (
        "rollback_tag", "rollback_commit", "rollback_tag_kind",
        "rollback_commit_matches_registered", "reference_md5", "candidate_md5",
        "source_differs", "registered_telemetry_nodes", "packet_fields",
        "normalized_ast_identical", "telemetry_readback_lines",
        "parity_references", "verdict", "failures") if k in v}


def _tracker_diff_gate():
    return _tracker_diff_evidence().get("verdict") == "PASS"
EXPECTED_TAG_COMMIT = "d6cbd55"


def sh(*args):
    """Run a command and ALWAYS return its output, pass or fail.

    The first version returned only stderr on a non-zero exit. A failing test
    suite exits 1, so its entire stdout -- including which assertion failed --
    was discarded and reported as "0 assertions", indistinguishable from the
    file never having run. The diagnosis was destroyed by the diagnostic tool.
    stdout is now always preserved, with stderr appended.
    """
    try:
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           timeout=300)
        out = (r.stdout or "").strip()
        if r.returncode != 0 and (r.stderr or "").strip():
            out = (out + "\n" if out else "") + \
                f"[exit {r.returncode}] {r.stderr.strip()}"
        return out
    except Exception as exc:                        # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {exc}"


def md5(path):
    p = Path(path)
    if not p.exists():
        return "MISSING"
    return hashlib.md5(p.read_bytes()).hexdigest()


def pkg_version(name):
    """importlib.metadata, because some packages lack __version__.

    pandas_ta is exactly that case: a naive probe reports it missing.
    """
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:                               # noqa: BLE001
        return "UNKNOWN"


CONCURRENCY_MODULES = {"threading", "multiprocessing", "asyncio",
                       "concurrent", "concurrent.futures", "_thread"}
CONCURRENCY_CALLS = {"Thread", "Process", "ThreadPoolExecutor",
                     "ProcessPoolExecutor", "Pool", "start_new_thread"}


def _concurrency_findings():
    """Files introducing concurrency, which would void stdout-capture safety.

    Parses the AST rather than grepping text. A string scan is wrong here and
    was measurably wrong: the first version flagged parity_runner.py because a
    COMMENT in it says "no threading, multiprocessing or asyncio anywhere".
    Prose about concurrency is not concurrency. Only real imports and call
    targets count.

    First-party code only; venv dependencies legitimately use threads.
    """
    import ast as _ast
    hits = []
    files = sorted(set(list((ROOT / "engine").glob("*.py"))
                       + list(ROOT.glob("*.py"))))
    for p in files:
        try:
            tree = _ast.parse(p.read_text(errors="replace"))
        except Exception:                           # noqa: BLE001
            continue
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in CONCURRENCY_MODULES:
                        hits.append(f"{p.name}: import {a.name}")
            elif isinstance(node, _ast.ImportFrom):
                if node.module and node.module.split(".")[0] in CONCURRENCY_MODULES:
                    hits.append(f"{p.name}: from {node.module} import ...")
            elif isinstance(node, _ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in CONCURRENCY_CALLS:
                    hits.append(f"{p.name}:{node.lineno}: {name}()")
    return hits


def _has_concurrency():
    return bool(_concurrency_findings())


def main():
    trades_path = ROOT / "logs" / "virtual_trades.json"
    trades = json.loads(trades_path.read_text()) if trades_path.exists() else []
    open_t = [t for t in trades if t.get("status") == "open"]
    closed = [t for t in trades if t.get("status") == "closed"]

    def net(t):
        return round((t.get("pnl") or 0) - (t.get("total_commission") or 0), 2)

    snap = {
        "snapshot_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Phase 4 pre-shadow gate artifact. Re-run before Phase 5.",
        "lineage": {
            "ares_commit": sh("git", "rev-parse", "--short", "HEAD"),
            "ares_branch": sh("git", "rev-parse", "--abbrev-ref", "HEAD"),
            "working_tree_clean": sh("git", "status", "--porcelain") == "",
            "uncommitted": sh("git", "status", "--porcelain").splitlines(),
            "rollback_tag": ROLLBACK_TAG,
            "rollback_tag_commit": sh("git", "rev-parse", "--short",
                                      f"{ROLLBACK_TAG}^{{commit}}"),
            "rollback_tag_expected": EXPECTED_TAG_COMMIT,
            "exit_policy_md5": md5(ROOT / "engine" / "exit_policy.py"),
            "tracker_md5": md5(ROOT / "engine" / "tracker.py"),
            "compatibility_contract": CONTRACT_VERSION,
            "record_schema_version": sc.RECORD_SCHEMA_VERSION,
            "tracker_compat_md5": md5(ROOT / "engine" / "tracker_compat.py"),
            "parity_compare_md5": md5(ROOT / "engine" / "parity_compare.py"),
        },
        "checksums": {
            "virtual_trades.json": md5(trades_path),
            "psi_state.json": md5(ROOT / "logs" / "psi_state.json"),
            "signal_queue.json": md5(ROOT / "logs" / "signal_queue.json"),
        },
        "portfolio": {
            "open_count": len(open_t),
            "closed_count": len(closed),
            "realised_net": round(sum(net(t) for t in closed), 2),
            "open_positions": [
                {
                    "symbol": t.get("symbol"),
                    "entry_date": t.get("entry_date"),
                    "entry_price": t.get("entry_price"),
                    "shares": t.get("shares"),
                    "original_shares": t.get("original_shares", t.get("shares")),
                    "stop_loss": t.get("stop_loss"),
                    "trailing_stop": t.get("trailing_stop", t.get("stop_loss")),
                    "peak_price": t.get("peak_price", t.get("entry_price")),
                    "take_profit": t.get("take_profit"),
                    "scaled_out": t.get("scaled_out", False),
                    "phase_field": t.get("phase"),
                    "cohort_by_entry_date": ("pre_clean"
                                             if t.get("symbol") in PRE_CLEAN
                                             else "clean_v3"),
                    "trail_equals_stop": (
                        t.get("trailing_stop", t.get("stop_loss"))
                        == t.get("stop_loss")),
                    # Dead band requires the trail to have RATCHETED above the
                    # initial stop and still sit below entry. A bare
                    # trail < entry is true for every unratcheted position too,
                    # since the initial stop is below entry by construction --
                    # reporting that as dead band would show five positions in
                    # the band when only two are.
                    "trail_ratcheted": (
                        t.get("trailing_stop", t.get("stop_loss"))
                        > t.get("stop_loss")),
                    "in_giveback_dead_band": (
                        t.get("trailing_stop", t.get("stop_loss"))
                        > t.get("stop_loss")
                        and t.get("trailing_stop", t.get("stop_loss"))
                        < t.get("entry_price")),
                    "will_label_stop_loss_if_stopped": (
                        t.get("trailing_stop", t.get("stop_loss"))
                        <= t.get("stop_loss")),
                }
                for t in open_t
            ],
        },
        "required_shadow_coverage": {
            sym: {
                "expected_entry_date": date,
                "still_open": any(t.get("symbol") == sym for t in open_t),
                "entry_date_matches": any(
                    t.get("symbol") == sym and t.get("entry_date") == date
                    for t in open_t),
                "gates": "Phase 5 only. Does not gate Phase 4.",
            }
            for sym, date in PRE_CLEAN.items()
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "float_mant_dig": sys.float_info.mant_dig,
            "rounding_fingerprint": {
                s: round(float(s), 2)
                for s in ("2.675", "0.125", "100.015", "109.985", "0.135")
            },
            "pandas": pkg_version("pandas"),
            "numpy": pkg_version("numpy"),
            "pandas_ta": pkg_version("pandas_ta"),
        },
        "phase3_gate": {},
    }

    # Marker contract against the ACTUALLY LOADED tracker module, not the
    # working-tree file: Python may have loaded another installed or cached
    # path, and stdout-based failure detection would then be unverified.
    try:
        from engine import tracker as _tracker
        snap["marker_contract"] = sc.verify_marker_contract(_tracker)
    except Exception as exc:                        # noqa: BLE001
        snap["marker_contract"] = {"valid": False,
                                   "error": f"{type(exc).__name__}: {exc}"}

    lin = snap["lineage"]
    tests = {}
    for name, path in SUITES.items():
        out = sh(sys.executable, path)
        lines = out.splitlines()
        tests[name] = {
            "path": path,
            "passed": out.rstrip().endswith("ALL PASS"),
            "assertions": out.count("  pass  "),
            "failures": [l.strip() for l in lines if l.lstrip().startswith("FAIL")],
            "tail": lines[-1].strip() if lines else "NO OUTPUT",
        }
    snap["test_suites"] = tests
    snap["assertion_total"] = sum(t["assertions"] for t in tests.values())
    snap["assertion_note"] = (
        "The suites are DISJOINT files; assertion_total is their sum, "
        "not a superset relationship. Do not read it as requiring any other "
        "count to pass independently.")
    snap["tracker_diff"] = _tracker_diff_evidence()
    snap["baseline_validity"] = _baseline_still_describes_head()
    snap["live_logs"] = _live_log_changes_are_bot_only_and_allowlisted()
    snap["parity_output"] = _parity_output_state()
    snap["parity_output_tracking"] = _parity_output_trackable()
    snap["phase3_gate"] = {
        "baseline_describes_head": snap["baseline_validity"]["valid"],
        "rollback_tag_resolves": lin["rollback_tag_commit"].startswith(
            EXPECTED_TAG_COMMIT),
        # Renamed from tracker_unchanged_since_tag, which became objectively
        # false once Phase 0.5 telemetry landed: the tracker source IS changed.
        # The supportable claim is narrower -- tracker decision behavior remains
        # structurally identical to pre-tracker-swap, and the source differs only
        # through the registered Phase 0.5 inline decision-input telemetry.
        # A text diff cannot establish that, so this delegates to the AST gate.
        "tracker_diff_is_registered_phase05_only": _tracker_diff_gate(),
        # Was logs_unchanged_since_tag, an absence test. That is wrong in BOTH
        # environments now: once review commits are rebased onto bot commits, the
        # dev checkout carries the same log history as the live host, so "did any
        # log change" no longer distinguishes them and would fail everywhere.
        # The invariant that actually matters is authorship -- no reviewed commit
        # may touch operational logs -- and it holds on either host for the same
        # reason, which is what makes it worth gating on.
        "logs_unmodified_by_review_commits":
            not snap["live_logs"]["review_commits_touching_logs"],
        "live_log_changes_are_bot_only_and_allowlisted":
            snap["live_logs"]["valid"],
        "all_suites_pass": all(t["passed"] for t in tests.values()),
        "marker_contract_valid": bool(snap["marker_contract"].get("valid")),
        "working_tree_clean": lin["working_tree_clean"],
        # Was parity_output_absent, a bare .exists() that encoded "Phase 4 has
        # not started" and would therefore have failed permanently from the first
        # write onward. Now phase-aware: see _parity_output_state().
        "parity_output_state_valid": snap["parity_output"]["valid"],
        "parity_output_trackable": snap["parity_output_tracking"]["trackable"],
        # stdout capture is only safe while evaluation is sequential.
        "single_threaded_verified": not _has_concurrency(),
        "concurrency_findings": _concurrency_findings(),
        "NOTE": ("Tests here ran in THIS runtime. The gate requires them to "
                 "pass in the VPS-equivalent runtime; run this same script on "
                 "the VPS and diff the 'runtime' and 'marker_contract' blocks "
                 "before authorizing Phase 4."),
    }
    snap["phase3_gate"]["all_local_checks_pass"] = all(
        v for k, v in snap["phase3_gate"].items()
        if isinstance(v, bool) and k != "all_local_checks_pass")

    print(json.dumps(snap, indent=2))

    if "--update-baseline" in sys.argv:
        # Deliberate, reviewable regeneration. The baseline is the ONE source of
        # truth for cross-host comparison; vps_phase3_verify.sh reads it instead
        # of embedding literals, which is what went stale before.
        td_ev = snap["tracker_diff"]
        if td_ev.get("verdict") != "PASS":
            print("\nREFUSING to update the baseline: the tracker-diff gate is "
                  f"{td_ev.get('verdict')}.\n  " +
                  "\n  ".join(td_ev.get("failures") or ["(no detail)"]),
                  file=sys.stderr)
            return 1
        tp = ROOT / "engine" / "tracker.py"
        base = {
            "baseline_schema_version": 1,
            "generated_at": snap["snapshot_utc"],
            # The commit whose implementation and test population this baseline
            # DESCRIBES -- not the commit that CONTAINS this file. A baseline
            # cannot name the commit containing itself without self-reference:
            # recording it would change the file, producing another commit.
            # Provenance is therefore: generated_from_commit = implementation
            # commit; the baseline artifact lives in the following commit, which
            # git history already records.
            "generated_from_commit": lin["ares_commit"],
            # Three hashes of ONE file, named by SERIALIZATION rather than by
            # role, because a mismatch between them is not evidence of tampering.
            # Each answers a different question and must never be cross-compared.
            "tracker_raw_bytes_md5": hashlib.md5(tp.read_bytes()).hexdigest(),
            "tracker_normalized_text_md5": hashlib.md5(
                tp.read_text().encode()).hexdigest(),
            "tracker_loaded_source_md5": snap["marker_contract"].get(
                "tracker_source_hash"),
            "tracker_hash_bases": {
                "tracker_raw_bytes_md5": (
                    "md5(Path.read_bytes()) -- CRLF preserved as stored on disk; "
                    "file-integrity evidence"),
                "tracker_normalized_text_md5": (
                    "md5(Path.read_text().encode()) -- CRLF normalised to LF; "
                    "the AST/diff-gate input, equals tracker_diff "
                    "candidate_tracker_md5"),
                "tracker_loaded_source_md5": (
                    "md5(inspect.getsource(tracker).encode()) -- LF plus a "
                    "trailing newline getsource appends; marker-contract and "
                    "runtime-loaded-source evidence"),
                "note": ("engine/tracker.py has no trailing newline, so "
                         "tracker_loaded_source_md5 differs from "
                         "tracker_normalized_text_md5 by exactly one byte. Do "
                         "not normalise one into another to make them agree."),
            },
            # THREE different md5 values legitimately describe the same file.
            # Recorded explicitly because a reviewer comparing them would
            # otherwise reasonably suspect a mismatch:
            #   raw bytes                  c0e9e9bc...  CRLF preserved on disk
            #   read_text()                465fe06f...  CRLF normalised to LF
            #   inspect.getsource()        12bd27d3...  LF + trailing newline
            # engine/tracker.py has no trailing newline, and getsource appends
            # one, so the marker-contract hash and the tracker-diff candidate
            # hash differ by exactly one byte. Neither is wrong; they answer
            # different questions and must not be cross-compared.

            "exit_policy_md5": lin["exit_policy_md5"],
            "compatibility_contract": lin["compatibility_contract"],
            "record_schema_version": lin["record_schema_version"],
            "rounding_fingerprint": snap["runtime"]["rounding_fingerprint"],
            "suite_assertions": {n: t["assertions"] for n, t in tests.items()},
            "assertion_total": snap["assertion_total"],
            # Reconciled, not absorbed. The bridge suite DECREASED 86 -> 66
            # because Phase 4 reconstruction was withdrawn: 10 premise/bar tests
            # were deleted and 5 packet tests added (17 -> 12 functions). Every
            # deleted test targeted a name now listed under
            # withdrawn_phase4_dependencies.must_be_absent, so the decrease is
            # the expected consequence of the withdrawal. The zero-network
            # coverage those tests provided did not vanish -- it moved to
            # phase05_network (89 assertions) with negative controls.
            "suite_count_changes_since_previous_baseline": {
                "wiring": {"was": 95, "now": 132,
                           "reason": "registered 8 omitted refusal tests plus "
                                     "the registration-completeness guard"},
                "evaluator": {"was": 61, "now": 63,
                              "reason": "packet-schema refusal coverage"},
                "bridge": {"was": 86, "now": 66,
                           "reason": "Phase 4 premise/bar reconstruction tests "
                                     "deleted with the architecture they tested; "
                                     "replaced by packet-capture tests"},
            },
            # The reviewed Phase 0.5 contract, recorded as EXPECTATIONS rather
            # than as whatever the working tree happened to produce. The
            # tracker-diff gate replaces tracker_unchanged_since_tag, which
            # became objectively false once telemetry landed.
            "tracker_diff_is_registered_phase05_only": {
                "rollback_tag": ROLLBACK_TAG,
                "rollback_commit": td_ev.get("rollback_commit"),
                "rollback_tag_kind": td_ev.get("rollback_tag_kind"),
                "reference_tracker_md5": td_ev.get("reference_md5"),
                "candidate_tracker_md5": td_ev.get("candidate_md5"),
                "source_differs": td_ev.get("source_differs"),
                "registered_telemetry_node_count": len(
                    td_ev.get("registered_telemetry_nodes") or []),
                "registered_telemetry_nodes": td_ev.get(
                    "registered_telemetry_nodes"),
                "packet_fields": list(td_ev.get("packet_fields") or ()),
                "normalized_non_telemetry_ast": (
                    "identical" if td_ev.get("normalized_ast_identical")
                    else "DIFFERS"),
                "telemetry_readback": (
                    "none" if not td_ev.get("telemetry_readback_lines")
                    else td_ev.get("telemetry_readback_lines")),
                "tracker_parity_references": (
                    "none" if not td_ev.get("parity_references")
                    else td_ev.get("parity_references")),
                "expected_verdict": "PASS",
            },
            # Phase 4 reconstruction was DELETED, not disabled. These names must
            # not reappear as execution dependencies. PRICE_SOURCE_NONDETERMINISTIC
            # survives as an UNASSIGNED name only, so historical records stay
            # readable -- that is deliberately distinguished from an active
            # dependency below.
            "withdrawn_phase4_dependencies": {
                "must_be_absent": [
                    "_bar_fn", "_bar_recheck", "_cache_file", "BarStale",
                    "BarUnavailable", "_premise", "PREMISE_DAILY_ONLY",
                    "_ib_connection", "make_bar", "NonDeterministicPrice",
                    "daily_only_price_premise", "cache_mtime_sentinel",
                ],
                "readable_but_unassigned": ["PRICE_SOURCE_NONDETERMINISTIC"],
                "note": ("An unassigned enum name kept for record readability is "
                         "NOT an execution dependency. The distinction matters: "
                         "absence of the name would break historical reads, while "
                         "assignment of it would resurrect a withdrawn premise."),
            },
            "note": ("Regenerate deliberately with "
                     "'python3 tools/pre_parity_snapshot.py --update-baseline' "
                     "when assertions are added or tracker.py legitimately "
                     "changes. A diff here must be reviewed, never auto-synced."),
        }
        dest = ROOT / "tools" / "parity_baseline.json"
        dest.write_text(json.dumps(base, indent=2) + "\n")
        print(f"\nbaseline written: {dest}", file=sys.stderr)

    if "--write" in sys.argv:
        out = ROOT / "logs" / "snapshots"
        out.mkdir(parents=True, exist_ok=True)
        stamp = snap["snapshot_utc"].replace(":", "").replace("-", "")[:15]
        dest = out / f"pre_parity_{stamp}.json"
        dest.write_text(json.dumps(snap, indent=2))
        print(f"\nwritten: {dest}", file=sys.stderr)
    return 0 if snap["phase3_gate"]["all_local_checks_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
