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
    check("all 7 registered telemetry nodes present",
          len(v["registered_telemetry_nodes"]) == 7,
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
    v = gate(mutate(
        '    _LAST_EVAL["packets"].clear()\n'
        '    _LAST_EVAL["cycle_token"] = _EVAL_CYCLE_SEQ',
        '    _LAST_EVAL["cycle_token"] = _EVAL_CYCLE_SEQ\n'
        '    _LAST_EVAL["packets"].clear()'))
    check("publish-before-clear: FAIL", v["verdict"] == "FAIL")
    check("reported as an ordering violation",
          reason_present(v, "clear() must precede"), v["failures"])
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
