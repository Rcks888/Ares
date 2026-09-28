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
OUT_PRE="$HOME/snapshot_A_pre.txt"
OUT_A="$HOME/snapshot_A_vps.json"

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
  echo "already up to date; nothing pulled"
else
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
echo "=== 4. SNAPSHOT A ==="
if [ ! -f tools/pre_parity_snapshot.py ]; then
  echo "STOP. tools/pre_parity_snapshot.py absent - pull did not land."
  exit 1
fi
python3 tools/pre_parity_snapshot.py > "$OUT_A" 2>"$OUT_A.err"
RC=$?
echo "snapshot exit=$RC  (0 = all Phase 3 checks pass)"
[ -s "$OUT_A.err" ] && { echo "--- stderr ---"; head -20 "$OUT_A.err"; }

python3 - "$OUT_A" "$PWD/tools/pre_parity_snapshot.py" <<'PY'
import json, sys
try:
    s = json.load(open(sys.argv[1]))
except Exception as exc:
    print(f"could not parse snapshot: {exc}"); sys.exit(0)
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
EXPECT = {
    "tracker_source_hash": B["tracker_source_hash"],
    "exit_policy_md5": B["exit_policy_md5"],
    "fingerprint": B["rounding_fingerprint"],
    "suites": B["suite_assertions"],
}
print(f"\n--- COMPARISON WITH BASELINE ({bpath.name}, "
      f"generated {B.get('generated_utc','?')} on "
      f"{B.get('generated_on_commit','?')}) ---")
bad = []
def cmp(label, got, want, fatal=True):
    ok = got == want
    print(f"  {'OK  ' if ok else 'DIFF'} {label}: {got}" + ("" if ok else f"  want {want}"))
    if not ok and fatal:
        bad.append(label)
cmp("tracker_source_hash", m.get("tracker_source_hash"),
    EXPECT["tracker_source_hash"])
cmp("exit_policy_md5", s.get("lineage", {}).get("exit_policy_md5"),
    EXPECT["exit_policy_md5"])
cmp("rounding_fingerprint", r.get("rounding_fingerprint"), EXPECT["fingerprint"])
cmp("suite assertion counts",
    {n: t["assertions"] for n, t in s.get("test_suites", {}).items()},
    EXPECT["suites"])
print("\n  (informational, a difference here is not automatically fatal)")
for k in ("python", "pandas", "numpy", "pandas_ta"):
    print(f"       {k} = {r.get(k)}")
print()
if bad:
    print("RESULT: DIFFERENCES FOUND -> " + ", ".join(bad))
    print("Do NOT authorize Phase 4 until each is explained.")
    print("A rounding_fingerprint difference is the serious one: it would mean")
    print("the tracker_v3_2dp contract does not hold identically on the VPS.")
else:
    print("RESULT: VPS matches the laptop on every contract-critical field.")
PY

echo
echo "artifacts: $OUT_PRE"
echo "           $OUT_A"
echo
echo "This script enabled nothing. Phase 4 needs a call site that does not exist"
echo "yet; Phase 5 remains prohibited."
