"""Phase 5 readiness gates and required-coverage dispositions.

WHY THIS FILE EXISTS
--------------------
phase5_gates_satisfied read True with an empty blocker list. It was derived from
`not blockers`, and the only blocker ever wired in was collection freshness -- so
the moment freshness legitimately turned True, the most safety-critical gate in
the system went green. A known-TRUE gate, the mirror of the known-false trap.

Two defects made that possible and both are pinned here:

1. The satisfied flag was derived from an empty blocker list rather than from
   proof that every registered gate was evaluated AND passed.
2. required_shadow_coverage tested only still_open. While ABM and SDGR were both
   open that was adequate; when both closed it became actively misleading,
   because a closed symbol with captured exit evidence and a closed symbol whose
   evidence was lost forever are both simply "not open".
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import pre_parity_snapshot as pps  # noqa: E402

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


def exit_record(sym="ABM", **over):
    """A record that genuinely demonstrates the required event."""
    r = {"symbol": sym, "parity_action": "exit", "record_schema_version": 2,
         "difference_class": "MATCH", "would_change_action": False,
         "inline_exit_reason": "stop_loss", "shadow_exit_reason": "stop_loss",
         "inline_effective_stop": 48.67, "shadow_effective_stop": 48.67,
         "inline_effective_stop_basis": "captured_inline_local",
         "entry_date": pps.REQUIRED_COVERAGE.get(sym, {}).get("entry_date"),
         "cycle_id": "d800900e", "timestamp": "2026-09-29T21:00:55+00:00",
         "production_commit": "33d8106", "abm_equality_boundary": {}}
    r.update(over)
    return r


def cov(records, open_symbols=()):
    return pps.required_coverage(records, set(open_symbols))


# ---- 1. the invariant that surfaced when both symbols closed ----------------
def test_closed_symbol_with_zero_records_is_never_satisfied():
    c = cov([], open_symbols=())
    for sym in pps.REQUIRED_COVERAGE:
        check(f"{sym}: closed + zero records != satisfied",
              c[sym]["coverage_state"] != "satisfied", c[sym]["coverage_state"])
        check(f"{sym}: resolves to unobtainable, not pending",
              c[sym]["coverage_state"] == "unobtainable", c[sym])
    check("a closed symbol is NEVER left pending",
          all(v["coverage_state"] != "pending" for v in c.values()))


def test_open_symbol_with_no_record_is_pending():
    c = cov([], open_symbols=("ABM", "SDGR"))
    for sym in pps.REQUIRED_COVERAGE:
        check(f"{sym}: open + no record is pending",
              c[sym]["coverage_state"] == "pending", c[sym]["coverage_state"])
        check(f"{sym}: pending blocks", c[sym]["blocking"] is True)


def test_closed_symbol_with_valid_exit_match_is_satisfied():
    c = cov([exit_record("ABM")], open_symbols=())
    a = c["ABM"]
    check("ABM satisfied by a captured exit MATCH",
          a["coverage_state"] == "satisfied", a["coverage_state"])
    check("satisfied does not block", a["blocking"] is False)
    check("the demonstrating evidence is carried, not just a boolean",
          a["evidence"]["cycle_id"] == "d800900e"
          and a["evidence"]["inline_effective_stop"] == 48.67, a["evidence"])


# ---- 2. presence is not coverage -------------------------------------------
def test_a_record_while_still_open_cannot_satisfy_the_exit_requirement():
    held = exit_record("ABM", parity_action="hold")
    c = cov([held], open_symbols=("ABM",))
    check("a hold record does not discharge an exit obligation",
          c["ABM"]["coverage_state"] == "pending", c["ABM"])


def test_non_match_and_action_changing_records_cannot_satisfy():
    for over, why in ((dict(difference_class="DIFFERENT"), "not a MATCH"),
                      (dict(would_change_action=True), "would change action"),
                      (dict(shadow_exit_reason="trailing_stop"),
                       "reasons disagree"),
                      (dict(shadow_effective_stop=48.68), "stops disagree"),
                      (dict(inline_effective_stop=None), "no stop captured"),
                      (dict(record_schema_version=99), "unregistered schema")):
        c = cov([exit_record("ABM", **over)], open_symbols=())
        check(f"a record that {why} cannot satisfy",
              c["ABM"]["coverage_state"] != "satisfied",
              c["ABM"]["coverage_state"])


def test_a_later_reentry_cannot_discharge_the_registered_position():
    c = cov([exit_record("ABM", entry_date="2026-10-15")], open_symbols=())
    check("a different entry_date is a different position",
          c["ABM"]["coverage_state"] != "satisfied", c["ABM"]["coverage_state"])
    check("and the rejection states why",
          "different position" in (c["ABM"]["evidence"] or {}).get("rejected", ""),
          c["ABM"]["evidence"])


def test_supplemental_symbol_cannot_satisfy_sdgr():
    """SECZ dead-band observations are supplemental. They must not leak across."""
    c = cov([exit_record("SECZ")], open_symbols=())
    check("a SECZ exit does not satisfy SDGR",
          c["SDGR"]["coverage_state"] == "unobtainable", c["SDGR"])
    check("SECZ is not even a registered obligation",
          "SECZ" not in c and "SECZ" not in pps.REQUIRED_COVERAGE)


# ---- 3. unobtainable vs retired are different facts ------------------------
def test_unobtainable_but_not_retired_still_blocks():
    saved = pps.COVERAGE_RETIREMENTS
    try:
        pps.COVERAGE_RETIREMENTS = {}
        c = cov([], open_symbols=())
        check("unobtainable with an active disposition BLOCKS",
              c["SDGR"]["blocking"] is True, c["SDGR"])
        check("disposition defaults to active, never to retired",
              c["SDGR"]["requirement_disposition"] == "active", c["SDGR"])
    finally:
        pps.COVERAGE_RETIREMENTS = saved


def test_retirement_stops_blocking_without_changing_the_state():
    c = cov([], open_symbols=())
    s = c["SDGR"]
    check("SDGR remains unobtainable AFTER retirement",
          s["coverage_state"] == "unobtainable", s["coverage_state"])
    check("retirement is never rewritten as satisfied",
          s["coverage_state"] != "satisfied")
    check("retirement stops it blocking", s["blocking"] is False)
    check("the retirement record is attached", s["retirement"] is not None)
    check("it acknowledges missing evidence explicitly",
          "does not classify the requirement as satisfied"
          in s["retirement"]["acknowledgement"])
    check("no substitute symbol was accepted",
          s["retirement"]["substitute_accepted"] is None)
    check("future observability is recorded as false",
          s["retirement"]["future_observability"] is False)


def test_retirement_cannot_excuse_a_still_collectable_gap():
    """A retirement applied to a PENDING obligation would excuse a gap that can
    still be collected. Only unobtainable may be retired."""
    saved = pps.COVERAGE_RETIREMENTS
    try:
        pps.COVERAGE_RETIREMENTS = {
            "ABM": {"disposition": "retired", "reason": "premature"}}
        c = cov([], open_symbols=("ABM",))
        check("a retired but PENDING obligation still blocks",
              c["ABM"]["blocking"] is True, c["ABM"])
    finally:
        pps.COVERAGE_RETIREMENTS = saved


def test_unrecognised_disposition_never_weakens():
    saved = pps.COVERAGE_RETIREMENTS
    try:
        pps.COVERAGE_RETIREMENTS = {"SDGR": {"disposition": "waived"}}
        c = cov([], open_symbols=())
        check("an unregistered disposition falls back to active",
              c["SDGR"]["requirement_disposition"] == "active", c["SDGR"])
        check("and therefore still blocks", c["SDGR"]["blocking"] is True)
    finally:
        pps.COVERAGE_RETIREMENTS = saved


# ---- 4. the denominator stays visible --------------------------------------
def test_tally_keeps_retired_obligations_in_the_denominator():
    t = pps.coverage_tally(cov([exit_record("ABM")], open_symbols=()))
    check("2 obligations, not 1", t["obligations"] == 2, t)
    check("1 satisfied", t["satisfied"] == 1, t)
    check("1 retired unobtainable", t["retired_unobtainable"] == 1, t)
    check("0 unresolved", t["unresolved_unobtainable"] == 0, t)
    check("empirical completeness is FALSE -- evidence is genuinely missing",
          t["empirically_complete"] is False, t)
    check("administrative resolution is TRUE -- nothing blocks",
          t["administratively_resolved"] is True, t)
    check("the two are not conflated",
          t["empirically_complete"] != t["administratively_resolved"])


# ---- 5. the gate registry --------------------------------------------------
_SNAP = None


def snap():
    """Assembled gate block WITHOUT re-running the suites.

    A plain snapshot call here would not terminate: the snapshot runs every
    registered suite, and this suite is registered, so it would invoke itself.
    --no-suites breaks that cycle and is prevented from reporting a green gate.
    """
    global _SNAP
    if _SNAP is None:
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "tools" /
                                                "pre_parity_snapshot.py"),
                            "--no-suites"],
                           capture_output=True, text=True, cwd=str(ROOT))
        _SNAP = json.loads(r.stdout)
    return _SNAP


def test_no_suites_mode_cannot_manufacture_a_green_gate():
    s = snap()
    check("the mode is recorded in the output",
          s["suites_skipped"] is True, s.get("suites_skipped"))
    check("all_suites_pass is FORCED False when nothing ran",
          s["phase3_gate"]["all_suites_pass"] is False,
          s["phase3_gate"]["all_suites_pass"])
    check("no suite results are fabricated", s["test_suites"] == {},
          s["test_suites"])
    check("so deployment integrity cannot pass in this mode",
          s["phase3_gate"]["deployment_integrity_valid"] is False)
    src = (ROOT / "tools" / "pre_parity_snapshot.py").read_text()
    check("emptiness alone cannot satisfy all_suites_pass",
          "bool(tests) and not skip_suites" in src)


def test_every_registered_gate_is_evaluated_and_reported():
    c = snap()["collection_health"]
    check("the registry is published", c["phase5_gate_registry"]
          == list(pps.PHASE5_GATE_REGISTRY), c.get("phase5_gate_registry"))
    for g in pps.PHASE5_GATE_REGISTRY:
        check(f"gate {g} is present in the computed set", g in c["phase5_gates"])
        for k in ("evaluated", "passed", "evidence", "blocker"):
            check(f"gate {g} reports {k}", k in c["phase5_gates"][g])


def test_an_absent_gate_is_itself_a_blocker():
    check("the registry is a fixed tuple, not derived from what was computed",
          isinstance(pps.PHASE5_GATE_REGISTRY, tuple))
    src = (ROOT / "tools" / "pre_parity_snapshot.py").read_text()
    check("missing registered gates become named blockers",
          "registered gate not evaluated" in src)
    check("satisfied requires all registered gates evaluated AND passed",
          'gates[g]["evaluated"] and gates[g]["passed"]' in src)
    check("satisfied is NOT derived from `not blockers` alone",
          'snap["collection_health"]["phase5_gates_satisfied"] = not blockers'
          not in src)


def test_minimum_sample_threshold_is_unregistered_and_blocks():
    check("no threshold is preregistered", pps.MIN_CLOSED_SAMPLE is None)
    check("the population is explicitly undeclared",
          "undeclared" in pps.MIN_CLOSED_SAMPLE_POPULATION)
    c = snap()["collection_health"]
    g = c["phase5_gates"]["minimum_closed_sample"]
    check("an unregistered threshold reports evaluated=False",
          g["evaluated"] is False, g)
    check("and passed=False", g["passed"] is False, g)
    check("and names the reason", g["blocker"] == "threshold not registered", g)
    check("an unevaluated gate appears in the blockers",
          any("minimum_closed_sample" in b for b in c["phase5_blockers"]),
          c["phase5_blockers"])


def test_operator_authorization_is_independent_of_computed_gates():
    check("authorization is a constant, never computed",
          pps.OPERATOR_AUTHORIZATION is False)
    c = snap()["collection_health"]
    check("it appears as a blocker while absent",
          any("operator authorization" in b for b in c["phase5_blockers"]),
          c["phase5_blockers"])
    check("the standing prohibition is unchanged",
          "PROHIBITED" in c["phase5_authorization"], c["phase5_authorization"])


def test_phase2_clearance_is_separate_from_empirical_coverage():
    c = snap()["collection_health"]
    g = c["phase5_gates"]["phase2_clearance"]
    check("Phase 2 clearance passes: no pre-clean position is still open",
          g["passed"] is True, g)
    check("it is explicit that this is NOT an evidence claim",
          "NOT whether their evidence was collected" in g["evidence"]["question"],
          g["evidence"])
    check("meanwhile empirical coverage is still incomplete",
          c["coverage_tally"]["empirically_complete"] is False,
          c["coverage_tally"])
    check("so clearance True coexists with incomplete evidence",
          g["passed"] is True
          and c["coverage_tally"]["empirically_complete"] is False)


def test_the_live_governing_result():
    """The exact state the operator must see today."""
    s = snap()
    c, cv = s["collection_health"], s["required_shadow_coverage"]
    check("ABM satisfied", cv["ABM"]["coverage_state"] == "satisfied")
    check("ABM evidence is the 21:00 equality-boundary exit",
          cv["ABM"]["evidence"]["inline_exit_reason"] == "stop_loss"
          and cv["ABM"]["evidence"]["inline_effective_stop"] == 48.67,
          cv["ABM"]["evidence"])
    check("ABM stop came from the captured inline local",
          cv["ABM"]["evidence"]["inline_effective_stop_basis"]
          == "captured_inline_local")
    check("SDGR unobtainable", cv["SDGR"]["coverage_state"] == "unobtainable")
    check("SDGR retired", cv["SDGR"]["requirement_disposition"] == "retired")
    check("SDGR is NOT satisfied", cv["SDGR"]["coverage_state"] != "satisfied")
    check("phase5_gates_satisfied is False",
          c["phase5_gates_satisfied"] is False, c["phase5_gates_satisfied"])
    check("blockers are named, not empty", len(c["phase5_blockers"]) > 0,
          c["phase5_blockers"])
    check("phase5 remains PROHIBITED", "PROHIBITED" in c["phase5_authorization"])


def test_empty_coverage_set_cannot_pass_vacuously():
    """all() over an empty dict is True. The gate must require the registered
    set, not merely the absence of failures -- which is how the original null
    coverage passed."""
    src = (ROOT / "tools" / "pre_parity_snapshot.py").read_text()
    check("the coverage set is compared against the registry",
          "set(cov) == set(REQUIRED_COVERAGE)" in src)
    check("and emptiness is rejected", "and bool(cov)" in src)
    check("coverage is read from the top level, not phase3_gate",
          'snap.get("required_shadow_coverage")' in src)


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
