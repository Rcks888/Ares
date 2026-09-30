#!/usr/bin/env bash
# Phase 3 VPS verification: pre-pull snapshot, additive-pull proof, Snapshot A.
#
# Read-only with respect to the live runtime. It pulls, then PROVES the pull
# touched nothing in the trading path. It does not enable parity observation:
# every module it brings in stays dormant until the Phase 4 call site exists.
#
# Run on the VPS:   bash tools/vps_phase3_verify.sh
# Safe to re-run. Stops at the first problem instead of continuing.

cd "$(dirname "$0")/.." || exit 1

PRE_PULL_COMMIT=""
ADDITIVE_STATUS="NOT REACHED"
while [ $# -gt 0 ]; do
  case "$1" in
    --pre-pull-commit) PRE_PULL_COMMIT="$2"; shift 2 ;;
    --pre-pull-commit=*) PRE_PULL_COMMIT="${1#*=}"; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
done
# Snapshot label is DERIVED, not hardcoded. Snapshot A was taken before the
# Phase 4 bridge existed; any run once engine/parity_hook.py is present is a
# Snapshot B. A verifier must not assert a stage it cannot observe -- the same
# defect as the stale suite count this script used to embed.
if [ -f "$(dirname "$0")/../engine/parity_hook.py" ]; then SNAP="B"; else SNAP="A"; fi
OUT_PRE="$HOME/snapshot_${SNAP}_pre.txt"
OUT_A="$HOME/snapshot_${SNAP}_vps.json"

# Files that must be byte-identical before and after the pull.
RUNTIME_FILES="engine/tracker.py engine/exit_policy.py engine/indicators.py
               engine/signals.py daily_report.py run_ares.sh build_dashboard.py"

echo "=== 1. PRE-PULL STATE ==="
PRE=$(git rev-parse HEAD) || exit 1
{
  echo "=== PRE-PULL $(date -u +%FT%TZ) ==="
  echo "commit: $PRE"
  md5sum $RUNTIME_FILES logs/virtual_trades.json logs/psi_state.json 2>/dev/null
  echo "dirty:"; git status --porcelain
} | tee "$OUT_PRE"
PRE_SUMS=$(md5sum $RUNTIME_FILES 2>/dev/null)

echo
echo "=== 2. PULL ==="
if ! git pull --rebase --autostash; then
  echo
  echo "PULL FAILED. If the conflict is logs/psi_state.json (shown as UU),"
  echo "the VPS copy is authoritative -- resolve with:"
  echo "    git checkout HEAD -- logs/psi_state.json && git stash drop"
  echo "then re-run this script. Stopping."
  exit 1
fi
POST=$(git rev-parse HEAD)
echo "moved: $PRE -> $POST"

echo
echo "=== 3. PROVE THE PULL WAS ADDITIVE ==="
if [ "$PRE" = "$POST" ]; then
  # Do not claim this passed. If the pull was performed manually beforehand,
  # PRE == POST and the section compares a state to itself, so "identical" is
  # guaranteed regardless of what the deployment actually changed.
  if [ -z "$PRE_PULL_COMMIT" ]; then
    # Only advise when there is genuinely nothing to evaluate. Printing this
    # before then evaluating against a supplied commit read as contradictory.
    ADDITIVE_STATUS="NOT EVALUATED (repository already at target commit)"
    echo "  NOT EVALUATED: repository was already at target commit $POST"
    echo "  This section can only prove additivity when it performs the pull"
    echo "  itself. Re-run before pulling, or pass the pre-pull commit:"
    echo "      bash tools/vps_phase3_verify.sh --pre-pull-commit <sha>"
  else
    echo "  --- evaluating against supplied pre-pull commit $PRE_PULL_COMMIT ---"
    CHANGED=$(git diff --name-only "$PRE_PULL_COMMIT" "$POST" -- $RUNTIME_FILES)
    if [ -n "$CHANGED" ]; then
      echo "$CHANGED" | sed 's/^/  CHANGED: /'
      echo "STOP. The deployment modified the live runtime path."
      exit 1
    fi
    echo "  none - runtime path untouched since $PRE_PULL_COMMIT"
    LOGS_CHANGED=$(git diff --name-only "$PRE_PULL_COMMIT" "$POST" -- logs/)
    echo "  logs/ changed by deployment: ${LOGS_CHANGED:-none}"
    ADDITIVE_STATUS="EVALUATED against supplied $PRE_PULL_COMMIT"
  fi
else
  ADDITIVE_STATUS="EVALUATED ($PRE -> $POST)"
  echo "--- deletions/modifications in the runtime path (want: none) ---"
  CHANGED=$(git diff --name-only "$PRE" "$POST" -- $RUNTIME_FILES)
  if [ -n "$CHANGED" ]; then
    echo "$CHANGED" | sed 's/^/  CHANGED: /'
    echo
    echo "STOP. The pull modified the live runtime path. Do not proceed."
    echo "Roll back with:  git reset --hard $PRE"
    exit 1
  fi
  echo "  none - runtime path untouched"
  echo "--- diffstat ---"
  git diff --stat "$PRE" "$POST" | tail -12
fi

echo
echo "--- runtime file checksums unchanged (want: identical to pre-pull) ---"
if [ "$PRE_SUMS" = "$(md5sum $RUNTIME_FILES 2>/dev/null)" ]; then
  echo "  identical"
else
  echo "  MISMATCH:"; diff <(echo "$PRE_SUMS") <(md5sum $RUNTIME_FILES)
  echo "STOP. Runtime files differ after the pull."
  exit 1
fi

echo
echo "=== 4. SNAPSHOT $SNAP ==="
if [ ! -f tools/pre_parity_snapshot.py ]; then
  echo "STOP. tools/pre_parity_snapshot.py absent - pull did not land."
  exit 1
fi
python3 tools/pre_parity_snapshot.py > "$OUT_A" 2>"$OUT_A.err"
RC=$?
echo "snapshot exit=$RC  (0 = all Phase 3 checks pass)"
[ -s "$OUT_A.err" ] && { echo "--- stderr ---"; head -20 "$OUT_A.err"; }

python3 - "$OUT_A" "$PWD/tools/pre_parity_snapshot.py" <<'PY'
import json, pathlib, sys, traceback

# EXPECTED_COMPARISONS is the completion contract. The run fails unless exactly
# this many comparisons were executed AND succeeded. Counting them is what makes
# "the section did not run" distinguishable from "the section passed".
EXPECTED_COMPARISONS = 9

try:
    s = json.load(open(sys.argv[1]))
except Exception as exc:
    # Was sys.exit(0). An unparseable snapshot is a FAILED verification, not a
    # skippable inconvenience: nothing downstream was checked.
    print(f"SNAPSHOT UNREADABLE: {exc}")
    print("CROSS-HOST COMPARISON: NOT EXECUTED")
    print("FULL VPS VERIFICATION: FAIL")
    sys.exit(3)
print("\n--- phase3 gate ---")
for k, v in s.get("phase3_gate", {}).items():
    if k != "NOTE":
        print(f"  {k} = {v}")
print("--- suites ---")
for n, t in s.get("test_suites", {}).items():
    print(f"  {n:11} {t['assertions']:3} assertions  passed={t['passed']}")
print(f"  TOTAL {s.get('assertion_total')}")

# --- phase-aware parity evidence -----------------------------------------
# Printing fields is not the point; proving they are the expected structured
# evidence is. A missing parity_output block would otherwise render as
# "state = None" inside an otherwise green run, which reads as reassurance.
# Recognised states come from the snapshot tool's own contract, not a local list.
try:
    # Read the constants by PARSING, not importing. Importing the snapshot tool
    # would execute it -- pulling in engine/, running module-level work, and
    # coupling a display block to the tool's whole dependency tree. Parsing gives
    # the same single source with no side effects.
    import ast as _ast

    def _const(tree, name):
        for node in tree.body:
            if isinstance(node, _ast.Assign) and any(
                    getattr(t, "id", None) == name for t in node.targets):
                return tuple(e.value for e in node.value.elts)
        raise KeyError(name)

    _tree = _ast.parse(pathlib.Path(sys.argv[2]).read_text())
    STATES = _const(_tree, "PARITY_OUTPUT_STATES")
    NEEDS_AO = _const(_tree, "PARITY_STATES_REQUIRING_APPEND_ONLY")
except Exception as exc:
    STATES, NEEDS_AO = (), ()
    CONTRACT_LOAD_ERROR = str(exc)
else:
    CONTRACT_LOAD_ERROR = None

display_failures = []
if CONTRACT_LOAD_ERROR:
    # Must FAIL, not degrade. Falling back to empty tuples made every
    # state check `if STATES and ...` a no-op, so an unrecognised state would
    # have passed silently whenever the contract could not be imported.
    display_failures.append(
        f"cannot load the state contract: {CONTRACT_LOAD_ERROR}")
po = s.get("parity_output")
print("--- parity evidence (phase-aware) ---")
if not isinstance(po, dict):
    display_failures.append("parity_output block absent or not an object")
else:
    d = po.get("declaration")
    ao = po.get("append_only") or {}
    print(f"  state               = {po.get('state')}")
    print(f"  valid               = {po.get('valid')}")
    print(f"  collection_declared = {po.get('collection_declared')}")
    print(f"  records             = {po.get('records')}")
    if isinstance(d, dict):
        print(f"  declaration lines   = "
              f"{[e.get('line') for e in d.get('export_lines', [])]}")
        print(f"  entry_point_line    = {d.get('entry_point_line')}")
        print(f"  declared value      = {d.get('value')!r}")
    if ao:
        print(f"  append_only         = "
              f"prefix_preserved={ao.get('prefix_preserved')} "
              f"history={ao.get('history_append_only')} "
              f"committed={ao.get('committed_records')} "
              f"working={ao.get('working_tree_records')} "
              f"new={ao.get('new_records')}")
    for f in po.get("failures", []):
        print(f"  FAILURE: {f}")
    for n in po.get("notes", []):
        print(f"  note: {n}")

    # Structural contract.
    st = po.get("state")
    if STATES and st not in STATES:
        display_failures.append(f"state {st!r} is not a recognised state")
    if not isinstance(po.get("valid"), bool):
        display_failures.append("valid is not a bool")
    if not isinstance(po.get("collection_declared"), bool):
        display_failures.append("collection_declared is not a bool")
    recs = po.get("records")
    if not isinstance(recs, int) or isinstance(recs, bool) or recs < 0:
        display_failures.append("records is not a non-negative integer")
    if not isinstance(d, dict):
        display_failures.append("declaration block absent")
    elif d.get("entry_point_line") is None and st != "DECLARATION_MALFORMED":
        display_failures.append("entry_point_line absent outside a malformed "
                                "declaration")
    # State-aware: no file exists before the first cycle.
    if st in NEEDS_AO and not ao:
        display_failures.append(f"append_only block required for state {st}")

# --- collection health -------------------------------------------------------
# This block governs Phase 4 readiness and blocks Phase 5, and it was NOT printed
# on the first deployment that shipped it. DISPLAY CONTRACT passed anyway, because
# it only validated the parity_output state enumeration -- so the gate that
# authorises phase advancement was invisible in the one report read during a
# deployment. Presence is now enforced, not assumed.
ch = s.get("collection_health")
print("--- collection health ---")
if not isinstance(ch, dict):
    display_failures.append("collection_health block absent or not an object")
    print("  ABSENT")
else:
    for k in ("host_context", "host_role_source", "measures", "state",
              "last_due_cycle", "last_heartbeat", "covers_last_due_cycle",
              "parity_collection_recent", "phase4_operational_ready",
              "phase5_gates_satisfied", "phase5_authorization"):
        print(f"  {k:26s} = {ch.get(k)}")
    if ch.get("incomplete_cycles"):
        print(f"  incomplete_cycles          = {ch['incomplete_cycles']}")
    if ch.get("malformed"):
        print(f"  malformed                  = {ch['malformed']}")
    # Per-gate accounting. The aggregate flag alone hid that only ONE gate had
    # ever been wired in, so every registered gate is now shown individually.
    reg = ch.get("phase5_gate_registry") or []
    gates = ch.get("phase5_gates") or {}
    if reg:
        print("  phase5_gates:")
        for g in reg:
            x = gates.get(g)
            if not isinstance(x, dict):
                print(f"    {g:32s} MISSING")
                display_failures.append(f"registered gate not reported: {g}")
                continue
            mark = "pass" if x.get("passed") else (
                "FAIL" if x.get("evaluated") else "UNEVALUATED")
            print(f"    {g:32s} {mark:12s} {x.get('blocker') or ''}")
    else:
        display_failures.append("phase5_gate_registry absent")

    cov = s.get("required_shadow_coverage")
    tally = ch.get("coverage_tally")
    if isinstance(cov, dict) and cov:
        print("  required_coverage:")
        for sym in sorted(cov):
            d = cov[sym]
            print(f"    {sym:6s} {d.get('coverage_state'):14s} "
                  f"disposition={d.get('requirement_disposition'):9s} "
                  f"blocking={d.get('blocking')}")
            ev = d.get("evidence")
            if ev:
                print(f"           evidence {ev.get('cycle_id')} "
                      f"{str(ev.get('timestamp'))[:19]} "
                      f"{ev.get('inline_exit_reason')} "
                      f"stop {ev.get('inline_effective_stop')} "
                      f"{ev.get('difference_class')} "
                      f"basis={ev.get('inline_effective_stop_basis')}")
            if d.get("coverage_state") == "unobtainable" \
                    and d.get("requirement_disposition") == "active":
                display_failures.append(
                    f"{sym}: unobtainable coverage with an ACTIVE disposition "
                    f"must block and be resolved explicitly")
            if d.get("coverage_state") == "satisfied" and not d.get("evidence"):
                display_failures.append(
                    f"{sym}: satisfied without demonstrating evidence")
    else:
        display_failures.append("required_shadow_coverage absent or empty")
    if isinstance(tally, dict):
        # Denominator stays visible: "1 of 1 satisfied" after dropping a retired
        # obligation would be the misreading this accounting exists to prevent.
        print(f"  coverage_tally             = obligations "
              f"{tally.get('obligations')} | satisfied {tally.get('satisfied')} "
              f"| pending {tally.get('pending')} | retired_unobtainable "
              f"{tally.get('retired_unobtainable')} | unresolved "
              f"{tally.get('unresolved_unobtainable')}")
        print(f"  empirically_complete       = "
              f"{tally.get('empirically_complete')}")
        print(f"  administratively_resolved  = "
              f"{tally.get('administratively_resolved')}")
        if tally.get("empirically_complete") is None:
            display_failures.append("coverage_tally.empirically_complete absent")
    else:
        display_failures.append("coverage_tally absent")

    blockers = ch.get("phase5_blockers")
    if blockers:
        print("  phase5_blockers:")
        for b in blockers:
            print(f"    - {b}")
    elif blockers == []:
        print("  phase5_blockers: none")
        # An empty blocker list with gates outstanding is the exact false green
        # that shipped: it must never read as satisfied on its own.
        if not all(isinstance(gates.get(g), dict) and gates[g].get("passed")
                   for g in reg):
            display_failures.append(
                "phase5_blockers empty while registered gates are outstanding")

    # Structural contract. Each of these was displayable-but-unchecked before.
    if ch.get("host_context") not in ("live", "archive", "unknown"):
        display_failures.append(
            f"host_context {ch.get('host_context')!r} unrecognised")
    if ch.get("host_role_source") is None:
        display_failures.append("host_role_source absent")
    # Tri-state: True / False / None(unknown). None is legitimate and must be
    # printed, but the KEY must exist -- a missing key is not an unknown value.
    if "parity_collection_recent" not in ch:
        display_failures.append("parity_collection_recent absent")
    elif ch["parity_collection_recent"] not in (True, False, None):
        display_failures.append(
            f"parity_collection_recent {ch['parity_collection_recent']!r} "
            f"is not True/False/None")
    if not isinstance(ch.get("phase4_operational_ready"), bool):
        display_failures.append("phase4_operational_ready is not a bool")
    if not isinstance(ch.get("phase5_gates_satisfied"), bool):
        display_failures.append("phase5_gates_satisfied is not a bool")
    auth = ch.get("phase5_authorization")
    if not isinstance(auth, str) or not auth.startswith("PROHIBITED"):
        display_failures.append(
            f"phase5_authorization must be the standing prohibition, got "
            f"{auth!r}")
    if blockers is None:
        display_failures.append("phase5_blockers absent")
    # A satisfied-gates claim alongside listed blockers is self-contradictory.
    if ch.get("phase5_gates_satisfied") is True and blockers:
        display_failures.append(
            f"phase5_gates_satisfied True while blockers listed: {blockers}")
    # Freshness must never read fresh on a host that cannot measure it.
    if ch.get("host_context") == "unknown" and \
            ch.get("parity_collection_recent") is not None:
        display_failures.append(
            "host_context unknown but parity_collection_recent is not None")

if display_failures:
    print()
    for f in display_failures:
        print(f"  DISPLAY CONTRACT FAILURE: {f}")
    print("  DISPLAY CONTRACT: FAIL")
else:
    print("  DISPLAY CONTRACT: PASS")
m = s.get("marker_contract", {})
print("--- marker contract (LOADED module) ---")
print(f"  valid = {m.get('valid')}  match = {m.get('marker_contract_match')}")
print(f"  file  = {m.get('tracker_file')}")
print(f"  hash  = {m.get('tracker_source_hash')}")
r = s.get("runtime", {})
print("--- runtime ---")
print(f"  {r.get('python')} {r.get('implementation')} {r.get('machine')} "
      f"mant_dig={r.get('float_mant_dig')}")
print(f"  pandas={r.get('pandas')} numpy={r.get('numpy')} "
      f"pandas_ta={r.get('pandas_ta')}")
print(f"  fingerprint {r.get('rounding_fingerprint')}")
print(f"  exit_policy_md5 {s.get('lineage', {}).get('exit_policy_md5')}")

# Read the committed baseline rather than embedding literals. The first version
# hardcoded suite counts here and went stale in the same commit that added two
# assertions, reporting a DIFF that was purely my own bookkeeping. One source of
# truth, regenerated deliberately and reviewed as a diff.
import pathlib
bpath = pathlib.Path(sys.argv[2]).resolve().parent / "parity_baseline.json"
try:
    B = json.loads(bpath.read_text())
except Exception as exc:
    print(f"\nBASELINE MISSING OR UNREADABLE: {bpath} ({exc})")
    print("Cannot compare against the laptop. Stopping.")
    sys.exit(1)
# Read tolerantly and report a MISSING KEY rather than raising.
#
# This block previously did B["tracker_source_hash"] as a bare subscript outside
# the try/except above. When the baseline renamed that field to
# tracker_loaded_source_md5, the KeyError escaped AFTER all the green output had
# already printed, so the entire comparison below silently never ran. The script
# still exited 0 via the snapshot, which looked like a clean cross-host pass.
# A reporting failure must not be able to masquerade as a comparison result.
def need(*names):
    for n in names:
        if n in B:
            return B[n]
    return _MISSING


class _Missing:
    def __repr__(self):
        return "<ABSENT FROM BASELINE>"

    def __eq__(self, other):
        return False


_MISSING = _Missing()
EXPECT = {
    # getsource basis -- comparable to marker_contract.tracker_source_hash
    "tracker_loaded_source_md5": need("tracker_loaded_source_md5",
                                      "tracker_source_hash"),
    # raw on-disk bytes -- comparable to lineage.tracker_md5
    "tracker_raw_bytes_md5": need("tracker_raw_bytes_md5"),
    "exit_policy_md5": need("exit_policy_md5"),
    "fingerprint": need("rounding_fingerprint"),
    "suites": need("suite_assertions"),
    "assertion_total": need("assertion_total"),
}
absent = [k for k, v in EXPECT.items() if isinstance(v, _Missing)]
if absent:
    print(f"\nBASELINE FIELD(S) ABSENT: {absent}")
    print("The comparison cannot be performed. Treat this as a FAILED gate,")
    print("not as a pass -- an unreadable expectation proves nothing.")
print(f"\n--- COMPARISON WITH BASELINE ({bpath.name}, "
      f"generated {B.get('generated_at', B.get('generated_utc','?'))} from "
      f"{B.get('generated_from_commit', B.get('generated_on_commit','?'))}) ---")
bad = []
cmp_bad = []
executed = []
def cmp(label, got, want, fatal=True):
    executed.append(label)
    ok = got == want
    print(f"  {'OK  ' if ok else 'DIFF'} {label}: {got}" + ("" if ok else f"  want {want}"))
    if not ok and fatal:
        # cmp_bad drives the n/9 accounting; bad drives the exit code. Keeping
        # them separate stops a display-contract failure from being reported as
        # a failed cross-host comparison, which would misstate what was checked.
        cmp_bad.append(label)
        bad.append(label)
if absent:
    bad.append("baseline fields absent: " + ",".join(absent))
# Display-contract failures must affect the exit code, but they are not
# cross-host comparisons: adding them via cmp() would push executed past
# EXPECTED_COMPARISONS and report an inflated count.
for f in display_failures:
    bad.append(f"display contract: {f}")
# Two tracker hashes with DIFFERENT bases. Compared against their own basis only.
# tracker_raw_bytes_md5 is git-controlled content and must match exactly.
# tracker_loaded_source_md5 depends on inspect.getsource, so an interpreter
# difference could legitimately move it while the file is byte-identical.
cmp("tracker_raw_bytes_md5 (git content)",
    s.get("lineage", {}).get("tracker_md5"),
    EXPECT["tracker_raw_bytes_md5"])
cmp("tracker_loaded_source_md5 (getsource)", m.get("tracker_source_hash"),
    EXPECT["tracker_loaded_source_md5"])
cmp("exit_policy_md5", s.get("lineage", {}).get("exit_policy_md5"),
    EXPECT["exit_policy_md5"])
cmp("rounding_fingerprint", r.get("rounding_fingerprint"), EXPECT["fingerprint"])
cmp("suite assertion counts",
    {n: t["assertions"] for n, t in s.get("test_suites", {}).items()},
    EXPECT["suites"])
cmp("assertion_total", s.get("assertion_total"), EXPECT["assertion_total"])
td = s.get("tracker_diff", {})
cmp("tracker_diff verdict", td.get("verdict"), "PASS")
cmp("normalized_ast_identical", td.get("normalized_ast_identical"), True)
cmp("rollback commit", (td.get("rollback_commit") or "")[:7], "d6cbd55")
print("\n  (informational, a difference here is not automatically fatal)")
for k in ("python", "pandas", "numpy", "pandas_ta"):
    print(f"       {k} = {r.get(k)}")
print()
# Completion accounting. Abundant green output above must not decide the verdict.
n_exec = len(executed)
n_ok = n_exec - len(cmp_bad)
print(f"  expected comparisons   : {EXPECTED_COMPARISONS}")
print(f"  executed comparisons   : {n_exec}")
print(f"  successful comparisons : {n_ok}")
complete = (n_exec == EXPECTED_COMPARISONS and n_ok == EXPECTED_COMPARISONS
            and not bad and not display_failures)
print()
if bad:
    print("RESULT: DIFFERENCES FOUND -> " + ", ".join(bad))
    print("Do NOT authorize Phase 4 until each is explained.")
    print("A rounding_fingerprint difference is the serious one: it would mean")
    print("the tracker_v3_2dp contract does not hold identically on the VPS.")
if n_exec != EXPECTED_COMPARISONS:
    print(f"RESULT: INCOMPLETE -- {n_exec} of {EXPECTED_COMPARISONS} "
          f"comparisons ran. A skipped comparison is not a pass.")
print()
if complete:
    print(f"CROSS-HOST COMPARISON: {n_ok}/{EXPECTED_COMPARISONS} PASS")
    print("FULL VPS VERIFICATION: PASS")
    print("EXIT: 0")
    sys.exit(0)
print(f"CROSS-HOST COMPARISON: {n_ok}/{EXPECTED_COMPARISONS}"
      f"{' INCOMPLETE' if n_exec != EXPECTED_COMPARISONS else ' FAIL'}")
if display_failures:
    print(f"DISPLAY CONTRACT: FAIL ({len(display_failures)})")
print("FULL VPS VERIFICATION: FAIL")
print("EXIT: 1")
sys.exit(1)
PY
CMP_RC=$?

echo
echo "=== 4b. COLLECTION SCHEDULE CONTRACT vs LIVE CRONTAB ==="
# The registered schedule in pre_parity_snapshot.py is what freshness is computed
# against. Asserted only in the repo it would be UNFALSIFIABLE: staleness would be
# measured against a fiction and report PASS while cron actually ran at other
# times. This is the only place the two can be compared, because the crontab
# exists solely on the live host.
#
# Reported, NOT fatal: a schedule mismatch must not block deploying the fix for a
# schedule mismatch. It feeds collection health, not deployment integrity.
CRON_RAW="$(crontab -l 2>/dev/null | grep -E "run_ares|daily_report" | grep -v '^[[:space:]]*#' || true)"
if [ -z "$CRON_RAW" ]; then
  echo "  UNRESOLVED: no ares cron entries found (schedule unverifiable here)"
  echo "  SCHEDULE CONTRACT: UNRESOLVED"
else
  echo "$CRON_RAW" | sed 's/^/  cron: /'
  python3 - "$CRON_RAW" <<'PY'
import re, sys
sys.path.insert(0, ".")
import tools.pre_parity_snapshot as pps

registered = sorted(pps.PARITY_CYCLE_SCHEDULE_UTC)
found = []
for line in sys.argv[1].splitlines():
    f = line.split()
    if len(f) < 5:
        continue
    mins, hrs, dom, mon, dow = f[:5]
    for h in hrs.split(","):
        for m in mins.split(","):
            try:
                found.append((int(h), int(m), dow))
            except ValueError:
                pass
print(f"  registered contract : {registered} weekdays="
      f"{list(pps.PARITY_CYCLE_WEEKDAYS)} grace={pps.PARITY_CYCLE_GRACE_MINUTES}m")
times = sorted({(h, m) for h, m, _ in found})
print(f"  live crontab times  : {times}")
dows = sorted({d for _, _, d in found})
print(f"  live crontab dow    : {dows}")
problems = []
if times != registered:
    problems.append(f"time mismatch: contract {registered} vs cron {times}")
# A weekday-only contract against a cron that also fires at the weekend would
# under-report: no cycle would be EXPECTED on a day one actually ran.
if any(d in ("*", "*/1") for d in dows):
    problems.append(
        f"contract is weekday-only but cron day-of-week is {dows} -- weekend "
        f"cycles would run UNEXPECTED and their absence never detected")
for p in problems:
    print(f"  MISMATCH: {p}")
print("  SCHEDULE CONTRACT: " + ("FAIL" if problems else "PASS"))
PY
fi

echo
echo "=== 5. FINAL STATUS ==="
# The comparison exit code was previously DISCARDED: the script ended with echo
# statements, so it always exited 0 even when the comparison reported
# differences. Both the snapshot gate and the comparison must now agree.
echo "  snapshot gate exit     : $RC"
echo "  cross-host comparison  : $CMP_RC"
echo "  additive-pull section  : $ADDITIVE_STATUS"
if [ "$RC" -ne 0 ] || [ "$CMP_RC" -ne 0 ]; then
  echo
  echo "FULL VPS VERIFICATION: FAIL"
  echo "Do NOT enable ARES_PARITY=1. Resolve the failures above first."
  FINAL=1
else
  echo
  echo "FULL VPS VERIFICATION: PASS"
  FINAL=0
fi

echo
echo "artifacts: $OUT_PRE"
echo "           $OUT_A"
echo
echo "This script enabled nothing."
if [ "$SNAP" = "B" ]; then
  echo "The Phase 4 bridge is present but OFF: parity runs only when"
  echo "ARES_PARITY=1 is set on an invocation. This run did not set it, and"
  echo "the parity_output state above reports whether collection is declared,"
  echo "armed, or active, and validates the evidence in each case."
  echo "Phase 5 remains prohibited: observation only."
else
  echo "Phase 4 needs a call site that does not exist yet;"
  echo "Phase 5 remains prohibited."
fi

exit "$FINAL"
