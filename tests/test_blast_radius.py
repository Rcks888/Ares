"""The production/parity boundary, asserted structurally.

Replaces four duplicated grep allow-lists. Those had to be edited once per new
test file (four times during Phase 0.5) and could be tripped by a comment or a
docstring -- which happened, and cost a debugging cycle.

Every check here answers a structural question from syntax nodes, and every
failure reports file, line, node type, symbol and reason.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import blast_radius as br  # noqa: E402

FAILS = []
COUNT = 0


def check(label, cond, got=None):
    global COUNT
    COUNT += 1
    if cond:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {got if got is not None else ''}")
        FAILS.append(label)


def test_repo_has_no_unauthorized_production_reference():
    findings, bad = br.audit()
    check("repository-wide: no unauthorized production parity reference",
          not bad, "\n" + br.describe(bad))
    check("the audit actually found references (scanner is not inert)",
          len(findings) > 10, len(findings))


def test_the_authorized_bridge_exists_and_is_the_only_one():
    findings, _ = br.audit()
    prod = [f for f in findings if br.is_production(f["file"])]
    bridges = [f for f in prod
               if Path(f["file"]).stem not in br.PARITY_MODULES]
    check("exactly one production module crosses the boundary",
          {f["file"] for f in bridges} == {"daily_report.py"},
          sorted({f["file"] for f in bridges}))
    check("it crosses only into parity_hook",
          {f["symbol"].split(".")[0] for f in bridges} == {"parity_hook"},
          sorted({f["symbol"] for f in bridges}))


def test_tracker_never_references_the_parity_stack():
    found = br.scan_file(ROOT / "engine" / "tracker.py")
    check("engine/tracker.py has zero structural parity references",
          not found, br.describe(found))


def test_hook_tracker_surface_is_pinned():
    surface = br.tracker_surface_used_by_hook()
    check("parity_hook uses exactly the pinned tracker surface",
          surface == set(br.PINNED_TRACKER_SURFACE),
          sorted(surface ^ set(br.PINNED_TRACKER_SURFACE)))
    check("_LAST_EVAL is detected despite being read via getattr string",
          "_LAST_EVAL" in surface, sorted(surface))


# ---- negative controls: prove the guard can fail ---------------------------
UNAUTH = ROOT / "engine" / "_fixture_unauthorized.py"


def _scan_src(src, name="engine/reporting.py"):
    return br.scan_file(ROOT / name, source=src)


def test_unauthorized_direct_import_is_detected():
    for src, label in (
            ("from engine import parity_eval\n", "from engine import parity_eval"),
            ("import engine.parity_runner\n", "import engine.parity_runner"),
            ("from engine.parity_compare import build_record\n",
             "from engine.parity_compare import build_record"),
            ("import engine.tracker_compat as tc\ntc.round_state({})\n",
             "aliased import + attribute use"),
            ("import importlib\nimportlib.import_module('engine.parity_eval')\n",
             "dynamic import_module"),
            ("m = __import__('engine.parity_runner')\n", "__import__"),
    ):
        found = _scan_src(src)
        bad = []
        for fi in found:
            v, why = br.verdict(fi)
            fi["verdict"], fi["reason"] = v, why
            if v == "unauthorized":
                bad.append(fi)
        check(f"detected unauthorized: {label}", bool(bad),
              f"scanned={br.describe(found)}")


def test_authorized_bridge_reference_passes():
    found = _scan_src("from engine.parity_hook import "
                      "observed_check_open_trades\n", name="daily_report.py")
    verdicts = [br.verdict(f)[0] for f in found]
    check("daily_report -> parity_hook is authorized",
          verdicts and all(v == "authorized" for v in verdicts),
          list(zip(verdicts, [f["symbol"] for f in found])))


def test_parity_internal_reference_passes():
    found = _scan_src("from engine import parity_compare as sc\n"
                      "sc.build_record()\n",
                      name="engine/parity_runner.py")
    verdicts = [br.verdict(f)[0] for f in found]
    check("parity_runner -> parity_compare is authorized (parity-internal)",
          verdicts and all(v == "authorized" for v in verdicts),
          list(zip(verdicts, [f["symbol"] for f in found])))


def test_comments_and_docstrings_are_ignored():
    """The exact failure mode of the old grep guard."""
    cases = (
        ('# parity_eval returns skipped_entry_day\n', "comment"),
        ('"""This module documents tracker_compat rounding."""\n', "docstring"),
        ('MSG = "see engine/parity_runner.py for details"\n', "string literal"),
        ("# TODO: do not import parity_compare here\n", "comment naming a module"),
        ('def f():\n    """Uses parity_hook conceptually."""\n    return 1\n',
         "function docstring"),
    )
    for src, label in cases:
        found = _scan_src(src)
        check(f"ignored (no structural reference): {label}", not found,
              br.describe(found))


def test_scanner_reports_actionable_diagnostics():
    found = _scan_src("from engine import parity_eval\n")
    check("a finding carries file, line, node and symbol",
          found and all(k in found[0] for k in ("file", "line", "node",
                                                "symbol")), found)
    if found:
        check("the node type is structural, not a boolean",
              found[0]["node"] == "ImportFrom", found[0]["node"])
        check("the line number is real", found[0]["line"] == 1, found[0]["line"])
        v, why = br.verdict(found[0])
        check("the reason explains the authorized path",
              "parity_hook" in why, why)


def test_a_real_unauthorized_file_fails_the_repo_audit():
    """End to end: write an offending production module, audit, then remove it.

    Proves the repo-wide audit -- not just the fixture scanner -- would catch a
    genuine boundary violation.
    """
    try:
        UNAUTH.write_text("from engine import parity_eval\n\n"
                          "def leak():\n    return parity_eval.evaluate\n")
        findings, bad = br.audit()
        files = {f["file"] for f in bad}
        check("repo audit catches a real unauthorized production module",
              "engine/_fixture_unauthorized.py" in files, sorted(files))
    finally:
        UNAUTH.unlink(missing_ok=True)
    findings, bad = br.audit()
    check("audit is clean again once the offender is removed", not bad,
          br.describe(bad))


if __name__ == "__main__":
    for fn in sorted([v for k, v in list(globals().items())
                      if k.startswith("test_")], key=lambda f: f.__name__):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{COUNT} assertions")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("ALL PASS")
