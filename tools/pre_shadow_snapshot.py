"""Pre-shadow state snapshot — Phase 4 gate artifact.

    python3 tools/pre_shadow_snapshot.py            # print
    python3 tools/pre_shadow_snapshot.py --write     # also save timestamped JSON

Read-only. Touches no production state, sends nothing, and is not referenced by
any scheduled entry point. Re-run immediately before Phase 4 starts and again
before Phase 5 wiring: these values move daily, so a stale snapshot is worse
than none.

Captures the Phase 3 gate evidence plus the boundary state that makes ABM and
SDGR valuable shadow subjects.
"""
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import shadow_compare as sc  # noqa: E402
from engine.tracker_compat import CONTRACT_VERSION  # noqa: E402

SUITES = {
    "adapter": "tests/test_tracker_compat.py",
    "classifier": "tests/test_shadow_compare.py",
    "wiring": "tests/test_shadow_runner.py",
}

PRE_CLEAN = {"ABM": "2026-09-09", "SDGR": "2026-09-18"}
ROLLBACK_TAG = "pre-tracker-swap"
EXPECTED_TAG_COMMIT = "d6cbd55"


def sh(*args):
    try:
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           timeout=30)
        return r.stdout.strip() if r.returncode == 0 else f"ERROR: {r.stderr.strip()}"
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
            "shadow_compare_md5": md5(ROOT / "engine" / "shadow_compare.py"),
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
        tests[name] = {
            "path": path,
            "passed": out.rstrip().endswith("ALL PASS"),
            "assertions": out.count("  pass  "),
            "tail": out.strip().splitlines()[-1] if out.strip() else "NO OUTPUT",
        }
    snap["test_suites"] = tests
    snap["assertion_total"] = sum(t["assertions"] for t in tests.values())
    snap["assertion_note"] = (
        "The three suites are DISJOINT files; assertion_total is their sum, "
        "not a superset relationship. Do not read it as requiring any other "
        "count to pass independently.")
    snap["phase3_gate"] = {
        "rollback_tag_resolves": lin["rollback_tag_commit"].startswith(
            EXPECTED_TAG_COMMIT),
        "tracker_unchanged_since_tag": sh(
            "git", "diff", "--stat", ROLLBACK_TAG, "HEAD", "--",
            "engine/tracker.py") == "",
        "logs_unchanged_since_tag": sh(
            "git", "diff", "--stat", ROLLBACK_TAG, "HEAD", "--", "logs/") == "",
        "all_suites_pass": all(t["passed"] for t in tests.values()),
        "marker_contract_valid": bool(snap["marker_contract"].get("valid")),
        "working_tree_clean": lin["working_tree_clean"],
        "shadow_output_absent": not (ROOT / "logs"
                                     / "tracker_shadow_v1.jsonl").exists(),
        "NOTE": ("Tests here ran in THIS runtime. The gate requires them to "
                 "pass in the VPS-equivalent runtime; run this same script on "
                 "the VPS and diff the 'runtime' and 'marker_contract' blocks "
                 "before authorizing Phase 4."),
    }
    snap["phase3_gate"]["all_local_checks_pass"] = all(
        v for k, v in snap["phase3_gate"].items() if isinstance(v, bool))

    print(json.dumps(snap, indent=2))
    if "--write" in sys.argv:
        out = ROOT / "logs" / "snapshots"
        out.mkdir(parents=True, exist_ok=True)
        stamp = snap["snapshot_utc"].replace(":", "").replace("-", "")[:15]
        dest = out / f"pre_shadow_{stamp}.json"
        dest.write_text(json.dumps(snap, indent=2))
        print(f"\nwritten: {dest}", file=sys.stderr)
    return 0 if snap["phase3_gate"]["all_local_checks_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
