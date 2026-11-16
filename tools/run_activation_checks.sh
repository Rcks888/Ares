#!/bin/bash
# Admission capture v1 activation checks (ACTIVATION_admission_capture_v1.md
# §3 and §4). Every verifier and suite, then RESULT: ALL EXIT 0 or the number
# that failed. Read-only on the tree it runs in: all scratch work goes to
# temporary directories. Exits non-zero if anything failed.
#
#   bash tools/run_activation_checks.sh /root/ares/Ares
#
# On the VPS, activate the venv first so python3 is the interpreter cron uses.
set -u
REPO="${1:-.}"
cd "$REPO" || exit 2
echo "repo     : $(pwd)"
echo "branch   : $(git rev-parse --abbrev-ref HEAD)"
echo "commit   : $(git rev-parse HEAD)"
echo "uncommitted paths: $(git status --porcelain | wc -l)"
echo "python   : $(command -v python3) $(python3 --version 2>&1)"
echo "venv     : ${VIRTUAL_ENV:-none}"
echo "started  : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
FAILED=0
N=0
run() {
  N=$((N + 1))
  echo
  echo "===== [$N] $*"
  out=$("$@" 2>&1)
  code=$?
  echo "$out" | tail -4
  echo "----- [$N] exit $code"
  [ "$code" -eq 0 ] || FAILED=$((FAILED + 1))
}
run python3 tools/verify_call_inventory.py
run python3 tests/run_call_inventory_controls.py
run python3 tools/verify_statement_whitelist.py
run python3 tests/run_statement_whitelist_controls.py
run python3 tools/verify_writer_inventory.py
run python3 tests/run_writer_inventory_controls.py
run python3 tests/run_persistence_controls.py
run python3 tools/verify_recorder_isolation.py --registry admission_registration_tracker tests/fixtures/tracker_preadmission.py engine/tracker.py
run python3 tools/verify_recorder_isolation.py --registry admission_registration_daily_report tests/fixtures/daily_report_preadmission.py daily_report.py
run python3 tools/verify_recorder_isolation.py tests/fixtures/monitor_trades_preactivation.py monitor_trades.py
run python3 tests/run_verifier_negative_controls.py
run python3 tests/run_a2_classification_controls.py
run python3 tests/run_report_entrypoint_controls.py
run python3 tests/run_monitor_admission_entrypoint_controls.py
run python3 tests/run_admission_capture_controls.py
run python3 tests/run_admission_isolation_smoke.py
run env ARES_SMOKE_STUB_INDICATORS=1 python3 tests/run_admission_isolation_smoke.py
run python3 tests/run_report_e2e_smoke.py
run python3 tests/run_admission_parity_rehearsal.py
run python3 tests/run_monitor_observer_controls.py
run python3 tests/run_entrypoint_containment_controls.py
run python3 tests/run_inspection_controls.py
run python3 tests/run_admission_inspection_controls.py
for t in tests/test_*.py; do
  if grep -q '__main__' "$t"; then
    run python3 "$t"
  else
    echo
    echo "===== skipped $t: no __main__ (pytest-style; not a plain script)"
  fi
done
run python3 tests/tracker_diff.py
echo
echo "uncommitted paths after: $(git status --porcelain | wc -l)"
echo "finished : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "commands : $N, failed: $FAILED"
[ "$FAILED" -eq 0 ] && echo "RESULT: ALL EXIT 0" || echo "RESULT: $FAILED FAILED"
[ "$FAILED" -eq 0 ]
