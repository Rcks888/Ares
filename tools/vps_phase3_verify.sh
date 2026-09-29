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
  ADDITIVE_STATUS="NOT EVALUATED (repository already at target commit)"
  echo "  NOT EVALUATED: repository was already at target commit $POST"
  echo "  This section can only prove additivity when it performs the pull"
  echo "  itself. Re-run before pulling, or pass the pre-pull commit:"
  echo "      bash tools/vps_phase3_verify.sh --pre-pull-commit <sha>"
  if [ -n "$PRE_PULL_COMMIT" ]; then
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
import json, sys, traceback

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
executed = []
def cmp(label, got, want, fatal=True):
    executed.append(label)
    ok = got == want
    print(f"  {'OK  ' if ok else 'DIFF'} {label}: {got}" + ("" if ok else f"  want {want}"))
    if not ok and fatal:
        bad.append(label)
if absent:
    bad.append("baseline fields absent: " + ",".join(absent))
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
n_ok = n_exec - len(bad)
print(f"  expected comparisons   : {EXPECTED_COMPARISONS}")
print(f"  executed comparisons   : {n_exec}")
print(f"  successful comparisons : {n_ok}")
complete = (n_exec == EXPECTED_COMPARISONS and n_ok == EXPECTED_COMPARISONS
            and not bad)
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
print("FULL VPS VERIFICATION: FAIL")
print("EXIT: 1")
sys.exit(1)
PY
CMP_RC=$?

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
  echo "parity_output_absent above confirms no records exist yet."
  echo "Phase 5 remains prohibited: observation only."
else
  echo "Phase 4 needs a call site that does not exist yet;"
  echo "Phase 5 remains prohibited."
fi

exit "$FINAL"
