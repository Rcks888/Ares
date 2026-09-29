"""The registered Phase 0.5 tracker-diff contract.

ONE QUESTION
------------
Does engine/tracker.py differ from pre-tracker-swap ONLY through the registered
Phase 0.5 inline decision-input telemetry?

The tracker source IS intentionally changed, so the old gate name
`tracker_unchanged_since_tag` is now objectively false and is replaced.

METHOD
------
Remove exactly the seven registered telemetry nodes from the candidate AST, then
require the ENTIRE remaining AST to be identical to the rollback-tag reference.
Removal rules match the registered nodes precisely -- they do NOT broadly drop
underscore-prefixed assignments or dictionary writes, because such a normalizer
would also discard a real decision change and call the result identical.

The removed nodes are then validated separately against the registered design,
so nothing can hide inside the portion the normalizer discards.

Comments and formatting do not appear in an AST and therefore cannot affect the
verdict. They remain visible in the unified diff for human review.
"""

import ast
import difflib
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKER = ROOT / "engine" / "tracker.py"

ROLLBACK_TAG = "pre-tracker-swap"
# The COMMIT the tag points at. pre-tracker-swap is an ANNOTATED tag, so
# `git rev-parse pre-tracker-swap` yields the tag OBJECT sha (0e2193c) and a
# naive comparison against the commit sha fails spuriously. Always peel with
# ^{commit}.
ROLLBACK_COMMIT = "d6cbd55d710bb839ad655ecf4194250f15244ac9"

STORE = "_LAST_EVAL"
SEQ = "_EVAL_CYCLE_SEQ"
CAPTURE_FN = "check_open_trades"
REGISTERED_PACKET_FIELDS = ("cycle_token", "price", "price_source", "rsi",
                            "bearish_div", "bar_date")
# Decision-RESULT telemetry (schema v2 work). The inline effective stop is
# computed from an UNROUNDED local that is never persisted, so no function of
# stored pre/post state can recover it: a ratchet to 48.674 rounds back to the
# stored 48.67, leaving pre == post while the value the decision actually used
# differed. The exact local is therefore copied at its computation point rather
# than reconstructed.
REGISTERED_RESULT_FIELDS = ("cycle_token", "inline_effective_stop")
# The authoritative local the result block copies.
EFFECTIVE_STOP_LOCAL = "effective_stop"
# Flipped to True by 2B, once engine/tracker.py actually contains the block.
# Until then the nodes are PERMITTED and fully structurally validated when
# present, but not required -- so this registration commit can land, and be
# proven against synthetic candidates, without the gate failing on a tracker
# that does not yet have the code.
RESULT_TELEMETRY_REQUIRED = False
# Inputs that must all resolve BEFORE the packet is captured.
DECISION_INPUTS = ("current_price", "price_source", "current_rsi", "today",
                   "latest")


def git(*args):
    r = subprocess.run(["git", "-C", str(ROOT)] + list(args),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def resolve_rollback():
    """Peel the annotated tag to its commit. Returns (commit, kind, ok)."""
    kind = git("cat-file", "-t", ROLLBACK_TAG).strip()
    commit = git("rev-parse", f"{ROLLBACK_TAG}^{{commit}}").strip()
    return commit, kind, commit == ROLLBACK_COMMIT


def reference_source():
    return git("show", f"{ROLLBACK_TAG}^{{commit}}:engine/tracker.py")


def candidate_source():
    return TRACKER.read_text(errors="replace")


def md5(s):
    return hashlib.md5(s.encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------------------
# Registered-node identification. Each predicate matches ONE registered shape.
# --------------------------------------------------------------------------
def _is_store_sub(node, key=None):
    """_LAST_EVAL[...] , optionally with a specific literal key."""
    if not isinstance(node, ast.Subscript):
        return False
    if not (isinstance(node.value, ast.Name) and node.value.id == STORE):
        return False
    if key is None:
        return True
    sl = node.slice
    return isinstance(sl, ast.Constant) and sl.value == key


def is_module_store_decl(n):
    return (isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == STORE
            and isinstance(n.value, ast.Dict))


def is_module_seq_decl(n):
    return (isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == SEQ
            and isinstance(n.value, ast.Constant) and n.value.value == 0)


def is_global_decl(n):
    return isinstance(n, ast.Global) and list(n.names) == [SEQ]


def is_seq_increment(n):
    return (isinstance(n, ast.AugAssign)
            and isinstance(n.target, ast.Name) and n.target.id == SEQ
            and isinstance(n.op, ast.Add)
            and isinstance(n.value, ast.Constant) and n.value.value == 1)


def is_packet_clear(n):
    """_LAST_EVAL["packets"].clear() with no arguments."""
    if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)):
        return False
    f = n.value.func
    return (isinstance(f, ast.Attribute) and f.attr == "clear"
            and _is_store_sub(f.value, "packets")
            and not n.value.args and not n.value.keywords)


def is_results_clear(n):
    """_LAST_EVAL["results"].clear() with no arguments."""
    if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)):
        return False
    f = n.value.func
    return (isinstance(f, ast.Attribute) and f.attr == "clear"
            and _is_store_sub(f.value, "results")
            and not n.value.args and not n.value.keywords)


def is_result_write(n):
    """_LAST_EVAL["results"][sym] = {"cycle_token": ..., "inline_...": ...}

    Deliberately a BARE Assign with no surrounding conditional. A separate
    results store needs no validity guard, and admitting a conditional into the
    registered region would create somewhere for a decision branch to hide
    inside the part the normalizer discards.

    Value expressions are restricted to the store's own token and the
    authoritative local. Any other expression -- a call, a trade read, an
    arithmetic rederivation -- fails registration, so this block cannot quietly
    become a second computation of the value it exists to copy.
    """
    if not (isinstance(n, ast.Assign) and len(n.targets) == 1):
        return False
    t = n.targets[0]
    if not (isinstance(t, ast.Subscript) and _is_store_sub(t.value, "results")):
        return False
    if not isinstance(n.value, ast.Dict):
        return False
    keys = [k.value for k in n.value.keys if isinstance(k, ast.Constant)]
    if len(keys) != len(n.value.keys):
        return False
    if tuple(keys) != REGISTERED_RESULT_FIELDS:
        return False
    for key, val in zip(keys, n.value.values):
        if key == "cycle_token":
            if not _is_store_sub(val, "cycle_token"):
                return False
        elif key == "inline_effective_stop":
            if not (isinstance(val, ast.Name)
                    and val.id == EFFECTIVE_STOP_LOCAL):
                return False
    return True


def is_token_publish(n):
    """_LAST_EVAL["cycle_token"] = _EVAL_CYCLE_SEQ"""
    return (isinstance(n, ast.Assign) and len(n.targets) == 1
            and _is_store_sub(n.targets[0], "cycle_token")
            and isinstance(n.value, ast.Name) and n.value.id == SEQ)


def is_packet_write(n):
    """_LAST_EVAL["packets"][<sym>] = {six registered fields}"""
    if not (isinstance(n, ast.Assign) and len(n.targets) == 1):
        return False
    t = n.targets[0]
    return (isinstance(t, ast.Subscript) and _is_store_sub(t.value, "packets")
            and isinstance(n.value, ast.Dict))


REGISTERED = (
    ("module_store_decl", is_module_store_decl),
    ("module_seq_decl", is_module_seq_decl),
    ("global_decl", is_global_decl),
    ("seq_increment", is_seq_increment),
    ("packet_clear", is_packet_clear),
    ("token_publish", is_token_publish),
    ("packet_write", is_packet_write),
    ("results_clear", is_results_clear),
    ("result_write", is_result_write),
)


def classify(node):
    for name, pred in REGISTERED:
        if pred(node):
            return name
    return None


class Normalizer(ast.NodeTransformer):
    """Removes ONLY the registered telemetry statements, recording each one."""

    def __init__(self):
        self.removed = []

    def visit(self, node):
        kind = classify(node)
        if kind is not None:
            self.removed.append((kind, node))
            return None
        return super().generic_visit(node)


def normalize(src):
    tree = ast.parse(src)
    nz = Normalizer()
    nz.visit(tree)
    ast.fix_missing_locations(tree)
    return tree, nz.removed


# --------------------------------------------------------------------------
# Telemetry structural validation (on the removed nodes)
# --------------------------------------------------------------------------
def telemetry_report(src):
    """Structure of the registered telemetry in `src`. Never raises on shape."""
    tree = ast.parse(src)
    _, removed = normalize(src)
    counts = {}
    for kind, _ in removed:
        counts[kind] = counts.get(kind, 0) + 1

    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == CAPTURE_FN), None)

    rep = {"counts": counts, "packet_fields": None, "clear_before_publish": None,
           "capture_before_entry_day_skip": None,
           "capture_after_inputs_resolve": None,
           "capture_before_first_decision_branch": None,
           "store_reads_outside_capture": [], "persisted": [],
           "telemetry_influences_logic": [], "parity_references": []}

    # exact packet field set
    pw = [n for k, n in removed if k == "packet_write"]
    if len(pw) == 1:
        keys = [k.value for k in pw[0].value.keys
                if isinstance(k, ast.Constant)]
        rep["packet_fields"] = tuple(keys)

    if fn is None:
        return rep

    def line_of(pred):
        return next((n.lineno for n in ast.walk(fn) if pred(n)), None)

    clear_ln = line_of(is_packet_clear)
    pub_ln = line_of(is_token_publish)
    write_ln = line_of(is_packet_write)
    if clear_ln and pub_ln:
        rep["clear_before_publish"] = clear_ln < pub_ln

    # entry-day suppression: `if today == trade['entry_date']: continue`
    skip_ln = None
    for n in ast.walk(fn):
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare):
            lf = n.test.left
            if (isinstance(lf, ast.Name) and lf.id == "today"
                    and any(isinstance(b, ast.Continue) for b in n.body)):
                skip_ln = n.lineno
                break
    if write_ln and skip_ln:
        rep["capture_before_entry_day_skip"] = write_ln < skip_ln

    # all five decision inputs assigned before the capture
    if write_ln:
        resolved = {}
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id in DECISION_INPUTS:
                        # min(), not setdefault: ast.walk is breadth-first, so
                        # the first node VISITED is not the first node by LINE.
                        resolved[t.id] = min(resolved.get(t.id, n.lineno),
                                             n.lineno)
            # Filter For-targets by DECISION_INPUTS as well. Without this the
            # loop variable `trade` entered `resolved` but could never appear in
            # the expected list, so the check reported FAIL while the invariant
            # actually held -- a false alarm, but the same defect shape would
            # hide a real ordering violation if the sets were compared loosely.
            if (isinstance(n, ast.For) and isinstance(n.target, ast.Name)
                    and n.target.id in DECISION_INPUTS):
                resolved.setdefault(n.target.id, n.lineno)
        # Require every registered input to be present AND first-assigned above
        # the capture. Anything weaker (e.g. comparing only the names that
        # happen to be present) would pass when an input went missing entirely.
        rep["_inputs_resolved_before_capture"] = sorted(
            k for k, v in resolved.items() if v < write_ln)
        rep["_inputs_missing"] = sorted(set(DECISION_INPUTS) - set(resolved))
        rep["capture_after_inputs_resolve"] = all(
            d in resolved and resolved[d] < write_ln for d in DECISION_INPUTS)

        # first decision branch = first If inside the per-trade loop that is not
        # the entry-day skip and not a telemetry node
        branch_lns = sorted(n.lineno for n in ast.walk(fn)
                            if isinstance(n, ast.If))
        after = [l for l in branch_lns if l > write_ln]
        rep["capture_before_first_decision_branch"] = bool(after)

    # --- decision-result telemetry placement -----------------------------
    # "After the effective_stop assignment" is too weak: anywhere later in the
    # function satisfies it, including after the exit decision has already been
    # taken, where the local may no longer be the value that decision used.
    # Required instead: the result write is the IMMEDIATELY FOLLOWING SIBLING of
    # the assignment, in the same statement list. That also rejects moving the
    # capture above the computation, which would copy a stale or unbound local.
    rep["result_is_sibling_after_effective_stop"] = None
    rep["result_write_count"] = rep["counts"].get("result_write", 0)
    rep["results_clear_count"] = rep["counts"].get("results_clear", 0)
    if rep["result_write_count"]:
        found = False
        for holder in ast.walk(fn):
            body = getattr(holder, "body", None)
            if not isinstance(body, list):
                continue
            for i, st in enumerate(body[:-1]):
                if (isinstance(st, ast.Assign) and len(st.targets) == 1
                        and isinstance(st.targets[0], ast.Name)
                        and st.targets[0].id == EFFECTIVE_STOP_LOCAL
                        and is_result_write(body[i + 1])):
                    found = True
        rep["result_is_sibling_after_effective_stop"] = found

    # clear() must precede the token publication for results too, on the same
    # reasoning as packets: publishing first leaves a new token beside
    # prior-invocation results, which a token-validating reader would accept.
    rep["results_clear_before_publish"] = None
    if rep["results_clear_count"] and pub_ln:
        rc_ln = line_of(is_results_clear)
        if rc_ln:
            rep["results_clear_before_publish"] = rc_ln < pub_ln

    # _LAST_EVAL must never be READ by decision logic, never persisted
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id == STORE and isinstance(n.ctx,
                                                                   ast.Load):
            rep["store_reads_outside_capture"].append(n.lineno)
        # persistence: STORE passed to save_trades / json.dump / open().write
        if isinstance(n, ast.Call):
            fname = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if fname in ("save_trades", "dump", "dumps", "write"):
                for a in ast.walk(n):
                    if isinstance(a, ast.Name) and a.id in (STORE, SEQ):
                        rep["persisted"].append((fname, n.lineno))
    return rep


def store_read_lines_outside_registered(src):
    """Loads of _LAST_EVAL that are NOT part of the registered nodes.

    The registered token publication legitimately reads _LAST_EVAL["cycle_token"]
    inside the packet it writes, so a raw count of Load contexts is not the
    invariant. What must be zero is any read reachable by decision logic.
    """
    tree = ast.parse(src)
    _, removed = normalize(src)
    registered_lines = set()
    for _, n in removed:
        for sub in ast.walk(n):
            if hasattr(sub, "lineno"):
                registered_lines.add(sub.lineno)
    bad = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Name) and n.id in (STORE, SEQ)
                and isinstance(n.ctx, ast.Load)
                and n.lineno not in registered_lines):
            bad.append(n.lineno)
    return bad


def unified_diff(ref, cand):
    return "".join(difflib.unified_diff(
        ref.splitlines(keepends=True), cand.splitlines(keepends=True),
        fromfile=f"{ROLLBACK_TAG}:engine/tracker.py",
        tofile="worktree:engine/tracker.py"))


def evaluate(ref_src=None, cand_src=None):
    """The gate. Returns a structured verdict dict."""
    ref_src = reference_source() if ref_src is None else ref_src
    cand_src = candidate_source() if cand_src is None else cand_src

    v = {"rollback_tag": ROLLBACK_TAG, "failures": []}
    commit, kind, ok = resolve_rollback()
    v["rollback_commit"] = commit
    v["rollback_tag_kind"] = kind
    v["rollback_commit_matches_registered"] = ok
    if not ok:
        v["failures"].append(f"rollback tag resolves to {commit}, "
                             f"expected {ROLLBACK_COMMIT}")

    v["reference_md5"] = md5(ref_src)
    v["candidate_md5"] = md5(cand_src)
    v["source_differs"] = ref_src != cand_src

    try:
        ref_tree, ref_removed = normalize(ref_src)
        cand_tree, cand_removed = normalize(cand_src)
    except SyntaxError as e:
        v["failures"].append(f"syntax error: {e}")
        v["verdict"] = "FAIL"
        return v

    v["reference_telemetry_nodes"] = sorted(k for k, _ in ref_removed)
    v["registered_telemetry_nodes"] = sorted(k for k, _ in cand_removed)

    # the reference must contain NO telemetry
    if ref_removed:
        v["failures"].append(
            f"reference already contains telemetry: {v['reference_telemetry_nodes']}")

    # 1. normalized structural equality -- the core statement
    ref_dump = ast.dump(ref_tree)
    cand_dump = ast.dump(cand_tree)
    v["normalized_ast_identical"] = ref_dump == cand_dump
    if not v["normalized_ast_identical"]:
        v["failures"].append("normalized non-telemetry AST DIFFERS from "
                             "rollback reference")
        v["ast_divergence"] = _first_divergence(ref_tree, cand_tree)

    # 2. telemetry structure exactly as registered
    rep = telemetry_report(cand_src)
    v["telemetry"] = rep
    expected_counts = {"module_store_decl": 1, "module_seq_decl": 1,
                       "global_decl": 1, "seq_increment": 1, "packet_clear": 1,
                       "token_publish": 1, "packet_write": 1}
    for k, want in expected_counts.items():
        got = rep["counts"].get(k, 0)
        if got != want:
            v["failures"].append(f"registered node {k}: expected {want}, got {got}")

    # --- registered decision-result telemetry ----------------------------
    n_rw = rep.get("result_write_count", 0)
    n_rc = rep.get("results_clear_count", 0)
    v["result_telemetry_required"] = RESULT_TELEMETRY_REQUIRED
    v["result_telemetry_present"] = bool(n_rw)
    want = 1 if RESULT_TELEMETRY_REQUIRED else None
    if want is not None:
        if n_rw != want:
            v["failures"].append(
                f"registered node result_write: expected {want}, got {n_rw}")
        if n_rc != want:
            v["failures"].append(
                f"registered node results_clear: expected {want}, got {n_rc}")
    else:
        if n_rw > 1:
            v["failures"].append(f"duplicate result_write nodes: {n_rw}")
        if n_rc > 1:
            v["failures"].append(f"duplicate results_clear nodes: {n_rc}")
    if n_rw and not n_rc:
        v["failures"].append(
            "result telemetry written but never cleared: prior-invocation "
            "results would survive into the next cycle")
    if n_rw and rep.get("result_is_sibling_after_effective_stop") is not True:
        v["failures"].append(
            "result capture must be the statement immediately following the "
            f"{EFFECTIVE_STOP_LOCAL} assignment")
    if n_rc and rep.get("results_clear_before_publish") is not True:
        v["failures"].append(
            "results clear() must precede cycle_token publication")

    v["packet_fields"] = rep["packet_fields"]
    if rep["packet_fields"] != REGISTERED_PACKET_FIELDS:
        extra = set(rep["packet_fields"] or ()) - set(REGISTERED_PACKET_FIELDS)
        missing = set(REGISTERED_PACKET_FIELDS) - set(rep["packet_fields"] or ())
        v["failures"].append(
            f"packet schema drift: extra={sorted(extra)} missing={sorted(missing)}")

    if rep["clear_before_publish"] is not True:
        v["failures"].append("clear() must precede cycle_token publication")
    if rep["capture_before_entry_day_skip"] is not True:
        v["failures"].append("packet capture must precede the entry-day skip")
    if rep["capture_after_inputs_resolve"] is not True:
        v["failures"].append("packet capture must follow all five decision inputs")
    if rep["capture_before_first_decision_branch"] is not True:
        v["failures"].append("packet capture must precede the first decision branch")

    # 3. telemetry must not be read back or persisted
    reads = store_read_lines_outside_registered(cand_src)
    v["telemetry_readback_lines"] = reads
    if reads:
        v["failures"].append(f"telemetry read outside registered nodes at "
                             f"lines {reads}")
    if rep["persisted"]:
        v["failures"].append(f"telemetry persisted: {rep['persisted']}")

    # 4. tracker must not depend on the parity stack
    try:
        import blast_radius as br
        found = br.scan_file(TRACKER, source=cand_src)
        v["parity_references"] = [f"{f['node']} {f['symbol']}" for f in found]
        if found:
            v["failures"].append(f"tracker references parity stack: "
                                 f"{v['parity_references']}")
    except Exception as e:                        # pragma: no cover
        v["failures"].append(f"blast-radius scan unavailable: {e}")

    v["diff"] = unified_diff(ref_src, cand_src)
    v["verdict"] = "PASS" if not v["failures"] else "FAIL"
    return v


def _first_divergence(a, b):
    """Locate the first structurally differing node for actionable reporting."""
    for x, y in zip(ast.walk(a), ast.walk(b)):
        dx, dy = ast.dump(x), ast.dump(y)
        if dx != dy:
            return {"node_type": type(x).__name__,
                    "reference_line": getattr(x, "lineno", None),
                    "candidate_line": getattr(y, "lineno", None),
                    "reference": dx[:220], "candidate": dy[:220]}
    la, lb = len(list(ast.walk(a))), len(list(ast.walk(b)))
    return {"node_count_reference": la, "node_count_candidate": lb}


def summary(v):
    def mark(x):
        return "PASS" if x else "FAIL"
    lines = [
        f"rollback tag                              : {v['rollback_tag']} "
        f"({v['rollback_tag_kind']} object)",
        f"rollback commit                           : {v['rollback_commit'][:7]}"
        f"  {mark(v['rollback_commit_matches_registered'])}",
        f"reference tracker md5                     : {v['reference_md5']}",
        f"candidate tracker md5                     : {v['candidate_md5']}",
        f"tracker source differs from rollback tag  : "
        f"{'YES' if v['source_differs'] else 'NO'}",
        f"registered telemetry nodes found          : "
        f"{len(v.get('registered_telemetry_nodes', []))}/7  "
        f"{mark(len(v.get('registered_telemetry_nodes', [])) == 7)}",
        f"packet schema                             : "
        f"{mark(v.get('packet_fields') == REGISTERED_PACKET_FIELDS)}",
        f"clear-before-publish ordering             : "
        f"{mark(v['telemetry'].get('clear_before_publish') is True)}"
        if "telemetry" in v else "",
        f"capture before entry-day skip             : "
        f"{mark(v['telemetry'].get('capture_before_entry_day_skip') is True)}"
        if "telemetry" in v else "",
        f"capture after decision inputs resolve     : "
        f"{mark(v['telemetry'].get('capture_after_inputs_resolve') is True)}"
        if "telemetry" in v else "",
        f"tracker telemetry readback                : "
        f"{'NONE' if not v.get('telemetry_readback_lines') else v['telemetry_readback_lines']}",
        f"tracker parity references                 : "
        f"{'NONE' if not v.get('parity_references') else v['parity_references']}",
        f"normalized non-telemetry AST              : "
        f"{'IDENTICAL' if v.get('normalized_ast_identical') else 'DIFFERS'}",
        f"forbidden changes detected                : {len(v['failures'])}",
        f"verdict                                   : {v['verdict']}",
    ]
    out = "\n".join(l for l in lines if l)
    if v["failures"]:
        out += "\n\nfailures:\n" + "\n".join(f"  - {f}" for f in v["failures"])
    return out


if __name__ == "__main__":
    v = evaluate()
    print(summary(v))
    print("\n--- unified diff (human review) ---")
    print(v["diff"])
