"""tracker_diff_is_registered_phase05_only -- gate plus mutation controls.

Replaces `tracker_unchanged_since_tag`, which is now objectively false: the
tracker source IS changed. The claim this gate supports is narrower and true:

    Tracker decision behavior remains structurally identical to
    pre-tracker-swap. Tracker source differs only through the registered
    Phase 0.5 inline decision-input telemetry.

Each negative control mutates the candidate source and asserts the gate fails
FOR THE RIGHT REASON, not merely that some assertion tripped.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import tracker_diff as td  # noqa: E402

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


REF = td.reference_source()
CAND = td.candidate_source()


def mutate(old, new, src=None, count=1):
    """Text mutation with a verified anchor.

    Anchors use \n: candidate_source() reads via Path.read_text(), which applies
    universal newlines, so the CRLF line endings on disk are already normalised.
    Writing \r\n anchors here silently matches nothing -- the assert exists so a
    stale anchor fails loudly instead of testing an unmutated source.
    """
    src = CAND if src is None else src
    assert old in src, f"mutation anchor not found: {old!r}"
    assert src.replace(old, new, count) != src, "mutation was a no-op"
    return src.replace(old, new, count)


# --------------------------------------------------------------------------
# Decision-RESULT telemetry registration (2A'), proven on SYNTHETIC candidates.
#
# The registration lands before engine/tracker.py contains the block, so these
# controls build the candidate themselves. That is deliberate: the proof that the
# widened normalizer cannot be used as a hiding place must not depend on the
# production file already having been edited.
# --------------------------------------------------------------------------
EFF_ANCHOR = "            effective_stop = max(trade['stop_loss'], trailing_stop)\n"
RESULT_BLOCK = (
    '            _LAST_EVAL["results"][trade[\'symbol\']] = {\n'
    '                "cycle_token": _LAST_EVAL["cycle_token"],\n'
    '                "inline_effective_stop": effective_stop,\n'
    '            }\n')
# Production carries an explanatory comment ahead of the statement. Comments do
# not reach the AST, so they cannot affect registration -- but they DO matter to
# the strip/re-add identity below, which is a byte comparison.
RESULT_COMMENT = (
    "            # Copy of the value the decision below actually uses. trailing_stop\n"
    "            # may be an UNROUNDED ratchet while only round(x, 2) is persisted,\n"
    "            # so a ratchet to 48.674 stores 48.67 and leaves pre == post: no\n"
    "            # function of stored state can recover what was used. Copied here,\n"
    "            # never read back by any decision path.\n")
PROD_BLOCK = RESULT_COMMENT + RESULT_BLOCK
CLEAR_ANCHOR = '    _LAST_EVAL["packets"].clear()\n'
CLEAR_BLOCK = '    _LAST_EVAL["results"].clear()\n'


def strip_results(src=None):
    """The candidate with 2B's result telemetry removed again.

    Post-2B the real tracker already CONTAINS the block, so the controls mutate
    from a stripped base. Building the base by removal rather than by string
    assembly keeps the canonical block textually identical to production.
    """
    src = CAND if src is None else src
    out = src.replace(PROD_BLOCK, "").replace(CLEAR_BLOCK, "")
    assert '_LAST_EVAL["results"][' not in out, "result block did not strip"
    return out


def with_results(src=None, block=None, clear=True):
    """The candidate as 2B produces it, optionally with a poisoned block."""
    base = strip_results(src)
    out = mutate(EFF_ANCHOR, EFF_ANCHOR + (PROD_BLOCK if block is None
                                           else block), src=base)
    if clear:
        out = mutate(CLEAR_ANCHOR, CLEAR_ANCHOR + CLEAR_BLOCK, src=out)
    return out


def test_reassembled_candidate_is_the_real_tracker():
    """The base the controls mutate must be production, byte-for-byte."""
    check("strip + re-add reproduces the real tracker",
          with_results() == CAND)
    check("stripped base lacks the result block",
          '_LAST_EVAL["results"][' not in strip_results())
    check("production block is the registered canonical form",
          RESULT_BLOCK in CAND)


def test_registered_result_block_passes():
    v = gate(with_results())
    check("canonical result block registers", v["verdict"] == "PASS",
          v["failures"])
    check("result telemetry detected", v["result_telemetry_present"] is True)
    check("normalized AST still identical to rollback",
          v["normalized_ast_identical"] is True, v.get("ast_divergence"))
    check("result node counted once",
          v["telemetry"]["result_write_count"] == 1)
    check("sibling placement proven",
          v["telemetry"]["result_is_sibling_after_effective_stop"] is True)


def test_results_clear_after_publish_is_refused():
    """The dangerous intermediate state: new token beside OLD result telemetry.

    Dedicated control for the results store, mirroring the packet one. Inverting
    only the results clear leaves the packets ordering correct, so this proves
    the results store gets its own stale-evidence protection rather than
    inheriting the packet check's verdict.
    """
    v = gate(mutate(
        '    _LAST_EVAL["results"].clear()\n'
        '    _LAST_EVAL["cycle_token"] = _EVAL_CYCLE_SEQ',
        '    _LAST_EVAL["cycle_token"] = _EVAL_CYCLE_SEQ\n'
        '    _LAST_EVAL["results"].clear()'))
    check("results cleared AFTER publish -> gate FAILS", v["verdict"] == "FAIL")
    check("named as a results ordering violation",
          any("results clear() must precede" in f for f in v["failures"]),
          v["failures"])
    check("packets ordering still reported correct",
          v["telemetry"]["clear_before_publish"] is True)
    check("AST equality unaffected (ordering is the only fault)",
          v["normalized_ast_identical"] is True)


def test_A_adjacent_decision_mutation_is_caught():
    """Adjacent production logic must NOT be swallowed by the wider normalizer."""
    poisoned = RESULT_BLOCK + '            trade["trailing_stop"] = effective_stop\n'
    v = gate(with_results(block=poisoned))
    check("A: adjacent decision write -> normalized AST DIFFERS",
          v["normalized_ast_identical"] is False)
    check("A: gate fails", v["verdict"] == "FAIL")
    check("A: result block itself still registers",
          v["telemetry"]["result_write_count"] == 1)


def test_B_decision_mutation_inside_the_block_is_refused():
    """A decision write inside the dict cannot ride along."""
    bad = (
        '            _LAST_EVAL["results"][trade[\'symbol\']] = {\n'
        '                "cycle_token": _LAST_EVAL["cycle_token"],\n'
        '                "inline_effective_stop": effective_stop,\n'
        '                "sneak": trade.setdefault("stop_loss", effective_stop),\n'
        '            }\n')
    v = gate(with_results(block=bad))
    check("B: extra key -> not registered",
          v["telemetry"]["result_write_count"] == 0)
    check("B: unregistered statement diverges the AST",
          v["normalized_ast_identical"] is False)
    check("B: gate fails", v["verdict"] == "FAIL")


def test_C_persistence_inside_the_block_is_refused():
    bad = RESULT_BLOCK.replace(
        '                "inline_effective_stop": effective_stop,\n',
        '                "inline_effective_stop": save_trades(effective_stop),\n')
    v = gate(with_results(block=bad))
    check("C: call in the value -> not registered",
          v["telemetry"]["result_write_count"] == 0)
    check("C: gate fails", v["verdict"] == "FAIL")


def test_D_data_access_inside_the_block_is_refused():
    bad = RESULT_BLOCK.replace(
        '                "inline_effective_stop": effective_stop,\n',
        '                "inline_effective_stop": load_stock(trade[\'symbol\']),\n')
    v = gate(with_results(block=bad))
    check("D: data acquisition in the value -> not registered",
          v["telemetry"]["result_write_count"] == 0)
    check("D: gate fails", v["verdict"] == "FAIL")


def test_E_conditional_affecting_decision_state_is_refused():
    bad = (RESULT_BLOCK
           + '            if effective_stop > trade["stop_loss"]:\n'
             '                trade["exit_reason"] = "trailing_stop"\n')
    v = gate(with_results(block=bad))
    check("E: added decision branch -> AST DIFFERS",
          v["normalized_ast_identical"] is False)
    check("E: gate fails", v["verdict"] == "FAIL")


def test_F_extra_result_field_is_refused():
    bad = RESULT_BLOCK.replace(
        '            }\n',
        '                "unexpected_field": 1,\n            }\n')
    v = gate(with_results(block=bad))
    check("F: extra result field -> not registered",
          v["telemetry"]["result_write_count"] == 0)
    check("F: gate fails", v["verdict"] == "FAIL")


def test_G_missing_effective_stop_result_is_refused():
    bad = ('            _LAST_EVAL["results"][trade[\'symbol\']] = {\n'
           '                "cycle_token": _LAST_EVAL["cycle_token"],\n'
           '            }\n')
    v = gate(with_results(block=bad))
    check("G: incomplete result fields -> not registered",
          v["telemetry"]["result_write_count"] == 0)
    check("G: gate fails", v["verdict"] == "FAIL")


def test_H_duplicate_result_capture_is_refused():
    v = gate(with_results(block=RESULT_BLOCK + RESULT_BLOCK))
    check("H: duplicate result_write counted",
          v["telemetry"]["result_write_count"] == 2)
    check("H: gate fails on duplication", v["verdict"] == "FAIL")
    check("H: names the result_write count",
          any("result_write" in f for f in v["failures"]), v["failures"])


def test_I_capture_before_computation_is_refused():
    """Moved ABOVE the assignment: the local would be stale or unbound."""
    moved = mutate(EFF_ANCHOR, PROD_BLOCK + EFF_ANCHOR, src=strip_results())
    moved = mutate(CLEAR_ANCHOR, CLEAR_ANCHOR + CLEAR_BLOCK, src=moved)
    v = gate(moved)
    check("I: placement above the computation -> not a sibling-after",
          v["telemetry"]["result_is_sibling_after_effective_stop"] is False)
    check("I: gate fails", v["verdict"] == "FAIL")
    check("I: names placement",
          any("immediately following" in f for f in v["failures"]),
          v["failures"])
    check("I: gate does not report it as a count problem",
          v["telemetry"]["result_write_count"] == 1,
          v["telemetry"]["result_write_count"])


def test_J_reformatting_only_still_registers():
    """Formatting must not require re-registration; the diff still shows it."""
    reflowed = (
        '            _LAST_EVAL["results"][trade[\'symbol\']] = {"cycle_token":'
        ' _LAST_EVAL["cycle_token"], "inline_effective_stop": effective_stop}\n')
    v = gate(with_results(block=reflowed))
    check("J: single-line form still registers", v["verdict"] == "PASS",
          v["failures"])
    check("J: normalized AST identical", v["normalized_ast_identical"] is True)
    check("J: raw md5 differs from the multi-line form",
          td.md5(with_results(block=reflowed)) != td.md5(with_results()))
    check("J: change remains visible in the unified diff",
          "inline_effective_stop" in v["diff"])


def test_result_written_but_never_cleared_is_refused():
    v = gate(with_results(clear=False))
    check("written-not-cleared -> gate fails", v["verdict"] == "FAIL")
    check("names cross-cycle survival",
          any("never cleared" in f for f in v["failures"]), v["failures"])


def test_result_telemetry_is_required_and_present():
    """Post-2B the block is mandatory, so its removal must be caught.

    Reclassified from the 2A' form, which asserted the flag was False -- an
    assertion pinned to a phase rather than to an invariant. The invariant is
    that the flag and the tracker agree.
    """
    check("RESULT_TELEMETRY_REQUIRED is True post-2B",
          td.RESULT_TELEMETRY_REQUIRED is True)
    v = gate(CAND)
    check("real tracker PASSes with the result block", v["verdict"] == "PASS",
          v["failures"])
    check("result telemetry present in production",
          v["result_telemetry_present"] is True)


def test_removing_the_result_capture_is_caught():
    """The capture cannot be silently dropped while v2 claims to carry it."""
    v = gate(strip_results())
    check("stripped capture -> gate FAILS", v["verdict"] == "FAIL")
    check("stripped capture -> normalized AST still identical",
          v["normalized_ast_identical"] is True)
    check("names the missing result_write",
          any("result_write: expected 1, got 0" in f for f in v["failures"]),
          v["failures"])


def test_store_initializer_key_drift_is_caught():
    """The registered initializer is not a free-form dict."""
    v = gate(mutate('    "results": {},         # symbol -> decision-RESULT',
                    '    "results": {},\n    "sneak": {},         # x'))
    check("extra store key -> gate FAILS", v["verdict"] == "FAIL")
    check("names store drift",
          any("store initializer drift" in f for f in v["failures"]),
          v["failures"])
    check("store keys are exactly the registered three",
          gate(CAND)["store_keys"] == td.REGISTERED_STORE_KEYS)


def gate(cand):
    return td.evaluate(ref_src=REF, cand_src=cand)


def reason_present(v, needle):
    return any(needle.lower() in f.lower() for f in v["failures"])


# ---- the gate itself ------------------------------------------------------
def test_gate_passes_on_the_real_tracker():
    v = gate(CAND)
    check("verdict PASS on the working tree", v["verdict"] == "PASS",
          v["failures"])
    check("normalized non-telemetry AST identical to rollback reference",
          v["normalized_ast_identical"] is True)
    check("tracker source DOES differ from the tag (gate is not vacuous)",
          v["source_differs"] is True)
    # Phase-invariant: compare against the registration table rather than a
    # literal count. The hardcoded 7 broke the moment 2B registered the result
    # nodes, which is the signature of an assertion pinned to a phase instead of
    # to an invariant. The invariant is "every registered kind is present".
    check("every registered telemetry kind is present in the tracker",
          sorted(v["registered_telemetry_nodes"])
          == sorted(n for n, _ in td.REGISTERED),
          v["registered_telemetry_nodes"])
    check("reference contains no telemetry",
          v["reference_telemetry_nodes"] == [], v["reference_telemetry_nodes"])
    check("packet fields are exactly the registered six",
          v["packet_fields"] == td.REGISTERED_PACKET_FIELDS, v["packet_fields"])
    check("no telemetry readback", v["telemetry_readback_lines"] == [],
          v["telemetry_readback_lines"])
    check("tracker has zero parity-stack references",
          v["parity_references"] == [], v["parity_references"])
    check("a human-readable unified diff is produced",
          v["diff"].startswith("---") and "_LAST_EVAL" in v["diff"])


def test_rollback_tag_is_peeled_to_its_commit():
    commit, kind, ok = td.resolve_rollback()
    check("pre-tracker-swap is an ANNOTATED tag", kind == "tag", kind)
    check("peeled commit is the registered rollback point", ok, commit)
    check("the tag object sha is NOT the commit sha",
          td.git("rev-parse", td.ROLLBACK_TAG).strip() != commit)


# ---- 1-2. decision comparison operators ----------------------------------
def test_trailing_stop_attribution_operator_change_is_caught():
    # The live attribution is line 915: equality deliberately labels stop_loss.
    # Flipping > to >= silently reassigns every equal-stop exit to trailing_stop
    # -- the Block A B/D defect shape.
    v = gate(mutate(
        "reason = 'trailing_stop' if trailing_stop > trade['stop_loss'] "
        "else 'stop_loss'",
        "reason = 'trailing_stop' if trailing_stop >= trade['stop_loss'] "
        "else 'stop_loss'"))
    check("> to >= in stop attribution: FAIL", v["verdict"] == "FAIL")
    check("> to >= reported as an AST divergence, not a telemetry error",
          reason_present(v, "normalized non-telemetry AST"), v["failures"])
    d = v.get("ast_divergence", {})
    check("> to >= divergence names a Compare/cmpop node",
          "Gt" in str(d) or "GtE" in str(d) or d.get("node_type"), d)


def test_take_profit_comparison_change_is_caught():
    # Live gate is the STORED absolute take_profit, not a recomputed percentage.
    v = gate(mutate("if take_profit and current_price >= take_profit:",
                    "if take_profit and current_price > take_profit:"))
    check("take-profit comparison change: FAIL", v["verdict"] == "FAIL")
    check("take-profit change reported as AST divergence",
          reason_present(v, "normalized non-telemetry AST"), v["failures"])


# ---- 3. branch reordering -------------------------------------------------
def test_swapping_two_exit_branches_is_caught():
    import ast
    tree = ast.parse(CAND)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "check_open_trades")
    # find an if/elif chain inside the loop and swap the first two arms
    target = None
    for n in ast.walk(fn):
        if (isinstance(n, ast.If) and n.orelse
                and isinstance(n.orelse[0], ast.If)):
            target = n
            break
    if target is None:
        check("swap branches: an if/elif chain exists to swap", False)
        return
    inner = target.orelse[0]
    target.test, inner.test = inner.test, target.test
    target.body, inner.body = inner.body, target.body
    ast.fix_missing_locations(tree)
    v = gate(ast.unparse(tree))
    check("swapped exit branches: FAIL", v["verdict"] == "FAIL")
    check("swapped branches reported as AST divergence",
          reason_present(v, "normalized non-telemetry AST"), v["failures"])


# ---- 4. financial calculation --------------------------------------------
def test_commission_fallback_change_is_caught():
    # The literal is 'commission_per_trade', so a quoted 'commission' anchor
    # matches nothing -- the earlier version of this control silently skipped.
    anchor = "params.get('commission_per_trade', 1.00)"
    check("commission: the fallback anchor exists in tracker", anchor in CAND)
    if anchor not in CAND:
        return
    v = gate(mutate(anchor, "params.get('commission_per_trade', 9.99)"))
    check("commission fallback change: FAIL", v["verdict"] == "FAIL")
    check("commission change reported as AST divergence",
          reason_present(v, "normalized non-telemetry AST"), v["failures"])


# ---- 5. capture moved after the entry-day skip ---------------------------
def test_capture_moved_after_entry_day_skip_is_caught():
    import ast
    tree = ast.parse(CAND)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "check_open_trades")

    def relocate(body):
        for i, st in enumerate(body):
            if td.is_packet_write(st):
                # move it just past the entry-day skip
                for j in range(i + 1, len(body)):
                    s2 = body[j]
                    if (isinstance(s2, ast.If)
                            and any(isinstance(b, ast.Continue) for b in s2.body)):
                        node = body.pop(i)
                        body.insert(j, node)
                        return True
            for f in ("body", "orelse", "finalbody"):
                sub = getattr(st, f, None)
                if isinstance(sub, list) and relocate(sub):
                    return True
        return False

    moved = relocate(fn.body)
    ast.fix_missing_locations(tree)
    v = gate(ast.unparse(tree))
    check("capture relocation was applied", moved)
    check("capture after entry-day skip: FAIL", v["verdict"] == "FAIL")
    check("reported as a capture-position violation, not a generic mismatch",
          reason_present(v, "must precede the entry-day skip"), v["failures"])


# ---- 6. token published before clearing ---------------------------------
def test_token_published_before_clear_is_caught():
    # Anchor widened by 2B: the results clear now sits between these two lines,
    # so the old two-line anchor no longer existed. The break was the control
    # working as intended -- it refused to run against source it did not match,
    # rather than silently mutating nothing and passing.
    v = gate(mutate(
        '    _LAST_EVAL["packets"].clear()\n'
        '    _LAST_EVAL["results"].clear()\n'
        '    _LAST_EVAL["cycle_token"] = _EVAL_CYCLE_SEQ',
        '    _LAST_EVAL["cycle_token"] = _EVAL_CYCLE_SEQ\n'
        '    _LAST_EVAL["packets"].clear()\n'
        '    _LAST_EVAL["results"].clear()'))
    check("publish-before-clear: FAIL", v["verdict"] == "FAIL")
    check("reported as an ordering violation",
          reason_present(v, "clear() must precede"), v["failures"])
    check("BOTH stores' ordering is reported, not just packets",
          reason_present(v, "results clear() must precede"), v["failures"])
    check("the AST equality check still passes (ordering is the only fault)",
          v["normalized_ast_identical"] is True)


# ---- 7. unregistered packet field ---------------------------------------
def test_unregistered_packet_field_is_caught():
    v = gate(mutate('"bar_date": today,',
                    '"bar_date": today,\n                "atr": 1.0,'))
    check("extra packet field: FAIL", v["verdict"] == "FAIL")
    check("reported as packet schema drift", reason_present(v, "schema drift"),
          v["failures"])
    check("the offending key is named", "atr" in str(v["failures"]),
          v["failures"])


def test_missing_packet_field_is_caught():
    v = gate(mutate('                "price_source": price_source,\n', ""))
    check("missing packet field: FAIL", v["verdict"] == "FAIL")
    check("reported as packet schema drift", reason_present(v, "schema drift"),
          v["failures"])


# ---- 8. telemetry read back by decision logic --------------------------
def test_telemetry_readback_in_a_decision_branch_is_caught():
    v = gate(mutate(
        "            if today == trade['entry_date']:",
        "            if _LAST_EVAL['cycle_token'] and today == trade['entry_date']:"))
    check("telemetry read in a decision branch: FAIL", v["verdict"] == "FAIL")
    check("reported as a telemetry readback with line numbers",
          reason_present(v, "telemetry read outside registered nodes"),
          v["failures"])
    check("the readback line is reported",
          bool(v.get("telemetry_readback_lines")),
          v.get("telemetry_readback_lines"))


def test_telemetry_persistence_is_caught():
    v = gate(mutate("    save_trades(trades)",
                    "    save_trades(trades)\n    save_trades(_LAST_EVAL)"))
    check("telemetry persisted: FAIL", v["verdict"] == "FAIL")
    check("reported as persistence or readback",
          reason_present(v, "persisted") or reason_present(v, "readback")
          or reason_present(v, "telemetry read"), v["failures"])


# ---- 9. new network call ------------------------------------------------
def test_added_network_call_is_caught():
    v = gate(mutate("    trades = load_trades()",
                    "    import urllib.request\n"
                    "    urllib.request.urlopen('http://x')\n"
                    "    trades = load_trades()"))
    check("added network call: FAIL", v["verdict"] == "FAIL")
    check("reported as AST divergence",
          reason_present(v, "normalized non-telemetry AST"), v["failures"])


# ---- 10. new exception handler -----------------------------------------
def test_added_exception_handler_is_caught():
    v = gate(mutate(
        "    for trade in trades:",
        "    try:\n        pass\n    except Exception:\n        pass\n"
        "    for trade in trades:"))
    check("added exception handler: FAIL", v["verdict"] == "FAIL")
    check("reported as AST divergence",
          reason_present(v, "normalized non-telemetry AST"), v["failures"])


def test_removed_telemetry_node_is_caught():
    """The normalizer must not be satisfied by ABSENT telemetry."""
    v = gate(mutate("    _EVAL_CYCLE_SEQ += 1\n", ""))
    check("removed seq increment: FAIL", v["verdict"] == "FAIL")
    check("reported as a missing registered node",
          reason_present(v, "seq_increment"), v["failures"])


def test_duplicated_capture_site_is_caught():
    v = gate(mutate('            if today == trade[\'entry_date\']:',
                    '            _LAST_EVAL["packets"][trade[\'symbol\']] = {\n'
                    '                "cycle_token": _LAST_EVAL["cycle_token"],\n'
                    '                "price": current_price,\n'
                    '                "price_source": price_source,\n'
                    '                "rsi": current_rsi,\n'
                    '                "bearish_div": False,\n'
                    '                "bar_date": today,\n'
                    '            }\n'
                    '            if today == trade[\'entry_date\']:'))
    check("second capture site: FAIL", v["verdict"] == "FAIL")
    check("reported as more than one packet_write",
          reason_present(v, "packet_write"), v["failures"])


# ---- positive control: comments only ----------------------------------
def test_comment_only_change_does_not_affect_the_verdict():
    """Comments are NOT part of an AST, so they cannot change behaviour.

    Decision: comment-only edits PASS the structural gate and remain visible in
    the unified diff for human review.
    """
    mutated = mutate("# Phase 0.5 migration telemetry -- NOT authoritative",
                     "# Phase 0.5 migration telemetry (reworded) -- NOT authoritative")
    v = gate(mutated)
    check("comment-only change: still PASS", v["verdict"] == "PASS",
          v["failures"])
    check("comment-only change: AST still identical",
          v["normalized_ast_identical"] is True)
    check("comment-only change: candidate md5 DOES change",
          v["candidate_md5"] != td.md5(CAND))
    check("comment-only change: visible in the unified diff for review",
          "(reworded)" in v["diff"])


def test_docstring_change_is_visible_because_it_is_a_real_node():
    """Contrast: a docstring IS an AST node, so it is not silently allowed."""
    anchor = '    """Calculate number of days held from entry date to today."""'
    if anchor not in CAND:
        check("docstring anchor exists", False)
        return
    v = gate(mutate(anchor, '    """Reworded docstring."""'))
    check("docstring change is detected as an AST difference",
          v["verdict"] == "FAIL", v["failures"])


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
