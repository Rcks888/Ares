#!/usr/bin/env python3
"""Prove the recorder added ONLY registered observation and changed no decision.

    python3 tools/verify_recorder_isolation.py BEFORE.py AFTER.py
    python3 tools/verify_recorder_isolation.py --register BEFORE.py AFTER.py
    python3 tools/verify_recorder_isolation.py --registry MODULE BEFORE.py AFTER.py

--registry selects the registration table (a module in tools/). The default,
recorder_registration, is the monitor_trades.py pair. Admission capture v1
adds one table per further pair (design §6.2):

    admission_registration_tracker        engine/tracker.py
    admission_registration_daily_report   daily_report.py

A table may set REQUIRE_ENTRYPOINT = False for a file with no `__main__`
wrapper (engine/tracker.py). Every other rule is identical for every table.

Exit 0 only if every difference between BEFORE and AFTER is an exactly
registered observation addition, and the one registered replacement.

WHY THE PREVIOUS DESIGN WAS UNSOUND
-----------------------------------
It removed statements by broad rules -- "the leftmost name is obs", "this is the
`if _obs is not None:` block", "the finally body mentions flush" -- which created
an exemption zone. Three defective candidates were independently reproduced as
false passes:

  A.  obs = trade["stop_loss"] = {...}
      Stripped because the FIRST target was obs. The second target mutated
      production state.

  B.  if _obs is not None:
          trade["stop_loss"] = 0
          ...existing capture...
      The whole block was removed without inspecting its contents.

  C.  finally:
          if _obs is not None:
              return_value = "flush"
              raise RuntimeError(return_value)
      Accepted because the substring "flush" appeared -- inside a string
      literal, in a body that replaced the production exception.

All three returned IDENTICAL, exit 0. A normalizer with discretion is a hole.

THE DESIGN NOW
--------------
No discretion. Two files are aligned recursively, scope by scope, and every
difference must be an EXACT registered subtree, identified by the sha256 of its
normalised `ast.unparse`:

  * additions   must each appear in REGISTERED_ADDITIONS
  * deletions   must each appear in REGISTERED_REPLACEMENTS
  * the entry-point wrapper must appear exactly once

Because the alignment proves everything else is equal, there is no separate
"compare the remainder" step that could be fooled: the alignment IS the
comparison.

Registration is by WHOLE subtree, so an extra statement inside a registered
block changes the digest and the block is no longer registered. That is what
closes A, B and C, rather than three targeted patches.

`ast.unparse` regenerates source from the tree, so comments and whitespace are
absent and formatting-only changes compare equal. They remain visible in the
human-readable diff, which is where they belong.

Changing the recorder legitimately changes these digests. Re-register with
--register, and the new digests must be reviewed as part of that change. That
friction is the control.
"""

import ast
import hashlib
import sys


def _norm(node):
    """Normalised source for one subtree. Formatting- and comment-free."""
    return ast.unparse(node)


def _digest(node):
    return hashlib.sha256(_norm(node).encode()).hexdigest()


def _decorated(node, text):
    """Prefix decorators, which are part of a definition's header: adding
    @property to a function changes what the name binds to."""
    decos = "".join(f"@{_norm(d)} " for d in node.decorator_list)
    return decos + text


def _signature(node):
    """Shallow identity used for alignment, excluding nested bodies.

    Compound statements must align on their header so their bodies can be
    compared recursively. Leaves align on their whole normalised source.
    Decorators, return annotations and class bases are header, so they are
    included; they are omitted from the text only when absent, which keeps
    every existing registered scope string unchanged.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        ret = f" -> {_norm(node.returns)}" if node.returns is not None else ""
        return _decorated(node, f"{kw} {node.name}({_norm(node.args)}){ret}")
    if isinstance(node, ast.ClassDef):
        bases = [_norm(b) for b in node.bases] + [_norm(k) for k in node.keywords]
        tail = f"({', '.join(bases)})" if bases else ""
        return _decorated(node, f"class {node.name}{tail}")
    if isinstance(node, ast.If):
        return f"if {_norm(node.test)}"
    if isinstance(node, ast.While):
        return f"while {_norm(node.test)}"
    if isinstance(node, ast.For):
        return f"for {_norm(node.target)} in {_norm(node.iter)}"
    if isinstance(node, ast.With):
        return f"with {','.join(_norm(i) for i in node.items)}"
    if isinstance(node, ast.Try):
        return "try"
    return _norm(node)


_COMPOUND = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If,
             ast.While, ast.For, ast.With, ast.Try)

_BODY_FIELDS = ("body", "orelse", "finalbody", "handlers")


def _header_dump(node):
    """Everything about a compound statement except its nested statement
    lists, as an ast.dump. Fail-closed backstop for _signature: any header
    field it does not spell out (type parameters, type comments, a future
    field) still makes the headers unequal."""
    import copy
    shell = copy.copy(node)
    for field in _BODY_FIELDS:
        if hasattr(shell, field):
            setattr(shell, field, [])
    return ast.dump(shell, include_attributes=False)


def _handler_header(h):
    return (_norm(h.type) if h.type is not None else None, h.name)


# --------------------------------------------------------------------------
# The registration table lives in its own committed module, so that a change to
# what is permitted shows up as a reviewable diff of digests rather than as an
# invisible consequence of running this tool.
#
# --register PRINTS a candidate table to stdout. It deliberately does not write
# the file: a verifier that re-registers whatever it finds proves nothing.
# --------------------------------------------------------------------------
try:
    from recorder_registration import (REGISTERED_ADDITIONS,
                                       REGISTERED_REPLACEMENTS)
except ImportError:                                   # pragma: no cover
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    try:
        from recorder_registration import (REGISTERED_ADDITIONS,
                                           REGISTERED_REPLACEMENTS)
    except ImportError:
        REGISTERED_ADDITIONS = None
        REGISTERED_REPLACEMENTS = None
REQUIRE_ENTRYPOINT = True


def _load_registry(name):
    """Swap in another registration table. A table that cannot be imported
    leaves nothing registered, so nothing can be accepted (exit 2)."""
    global REGISTERED_ADDITIONS, REGISTERED_REPLACEMENTS, REQUIRE_ENTRYPOINT
    import importlib
    here = __file__.rsplit("/", 1)[0]
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        mod = importlib.import_module(name)
        REGISTERED_ADDITIONS = mod.REGISTERED_ADDITIONS
        REGISTERED_REPLACEMENTS = mod.REGISTERED_REPLACEMENTS
        REQUIRE_ENTRYPOINT = getattr(mod, "REQUIRE_ENTRYPOINT", True)
    except (ImportError, AttributeError):
        REGISTERED_ADDITIONS = None
        REGISTERED_REPLACEMENTS = None

# Names that must never appear in a removed subtree, whatever its digest says.
# Defence in depth: if a registered digest is ever wrong, this still fires.
FORBIDDEN_IN_OBSERVATION = frozenset({
    "save_trades", "_close_trade", "promote_queue", "open_trade",
    "save_pending", "execute_pending_signals", "get_live_price", "load_stock",
    "add_indicators", "disconnect_ib", "check_open_trades",
})

ENTRYPOINT_SCOPE = "<module>/if __name__ == '__main__'"


def _forbidden_calls(node):
    bad = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
            if name in FORBIDDEN_IN_OBSERVATION:
                bad.add(name)
        # A chained or augmented target touching a non-observation subscript.
        if isinstance(n, ast.Assign) and len(n.targets) > 1:
            bad.add(f"multiple assignment targets: {_norm(n)[:60]}")
    return sorted(bad)


class Aligner:
    def __init__(self):
        self.additions = []     # (scope, digest, source)
        self.deletions = []     # (scope, digest, source)

    def compare(self, scope, pre_body, post_body):
        pre_sigs = [_signature(s) for s in pre_body]
        post_sigs = [_signature(s) for s in post_body]

        import difflib
        matcher = difflib.SequenceMatcher(a=pre_sigs, b=post_sigs, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for pre_s, post_s in zip(pre_body[i1:i2], post_body[j1:j2]):
                    self._descend(scope, pre_s, post_s)
            elif tag == "insert":
                for s in post_body[j1:j2]:
                    self.additions.append((scope, _digest(s), _norm(s)))
            elif tag == "delete":
                for s in pre_body[i1:i2]:
                    self.deletions.append((scope, _digest(s), _norm(s)))
            elif tag == "replace":
                for s in pre_body[i1:i2]:
                    self.deletions.append((scope, _digest(s), _norm(s)))
                for s in post_body[j1:j2]:
                    self.additions.append((scope, _digest(s), _norm(s)))

    def _descend(self, scope, pre_s, post_s):
        """Recurse into a matched compound statement.

        Signatures matched, so the headers are equal; the bodies may not be.
        """
        if not isinstance(pre_s, _COMPOUND):
            return
        child = f"{scope}/{_signature(pre_s)}"
        if (type(pre_s) is not type(post_s)
                or _header_dump(pre_s) != _header_dump(post_s)):
            self.deletions.append((scope, _digest(pre_s), _norm(pre_s)))
            self.additions.append((scope, _digest(post_s), _norm(post_s)))
            return
        for field in ("body", "orelse", "finalbody"):
            a = getattr(pre_s, field, None) or []
            b = getattr(post_s, field, None) or []
            if a or b:
                self.compare(child, a, b)
        pre_h = getattr(pre_s, "handlers", []) or []
        post_h = getattr(post_s, "handlers", []) or []
        if len(pre_h) != len(post_h):
            # Handler count changed. Report as a whole-statement difference
            # rather than trying to pair them up.
            self.deletions.append((child, _digest(pre_s), _norm(pre_s)))
            self.additions.append((child, _digest(post_s), _norm(post_s)))
            return
        for ph, qh in zip(pre_h, post_h):
            if _handler_header(ph) != _handler_header(qh):
                self.deletions.append((child, _digest(ph), _norm(ph)))
                self.additions.append((child, _digest(qh), _norm(qh)))
                continue
            self.compare(f"{child}/except", ph.body, qh.body)


def analyse(before_path, after_path):
    pre = ast.parse(open(before_path).read())
    post = ast.parse(open(after_path).read())
    aligner = Aligner()
    aligner.compare("<module>", pre.body, post.body)
    return aligner


def _emit_table(name, entries):
    """Print a registration table with an EXPECTED OCCURRENCE COUNT per key.

    The count is registered, not merely the shape. Without it, a duplicated
    registered statement is accepted: review demonstrated that doubling
    `_MONITOR_EVAL["attempted"] += 1` -- which double-counts the population and
    breaks reconciliation -- passed a registry that recorded only shapes.
    """
    from collections import Counter
    counts = Counter((s, d) for s, d, _ in entries)
    first = {}
    for scope, dig, src in entries:
        first.setdefault((scope, dig), src)
    print(f"{name} = {{")
    for (scope, dig), n in counts.items():
        label = first[(scope, dig)].splitlines()[0][:62].replace('"', "'")
        print(f'    ("{scope}",\n     "{dig}"):\n        ("{label}", {n}),')
    print("}")


def register(before_path, after_path):
    a = analyse(before_path, after_path)
    _emit_table("REGISTERED_ADDITIONS", a.additions)
    print()
    _emit_table("REGISTERED_REPLACEMENTS", a.deletions)
    return 0


def main(before_path, after_path):
    if REGISTERED_ADDITIONS is None:
        print("  RESULT : NOT PROVEN -- tools/recorder_registration.py is "
              "missing; nothing is registered, so nothing can be accepted")
        return 2

    a = analyse(before_path, after_path)
    problems, accepted = [], []

    def _check(entries, registry, kind):
        from collections import Counter
        seen = Counter((s, d) for s, d, _ in entries)
        for scope, dig, src in entries:
            key = (scope, dig)
            if key not in registry:
                problems.append(
                    f"UNREGISTERED {kind} in {scope}\n"
                    f"    digest {dig}\n"
                    f"    {src.splitlines()[0][:100]}"
                    + ("\n    decision logic may have been deleted or altered"
                       if kind == "REMOVAL" else ""))
                continue
            bad = _forbidden_calls(ast.parse(src).body[0])
            if bad:
                problems.append(
                    f"REGISTERED {kind} TOUCHES PRODUCTION in {scope}: {bad}")
                continue
        # Multiplicity is registered, not just shape. A duplicated registered
        # statement is an unreviewed behaviour change, not a free pass.
        for key, want in registry.items():
            label, expected = want if isinstance(want, tuple) else (want, 1)
            got = seen.get(key, 0)
            if got != expected:
                problems.append(
                    f"OCCURRENCE COUNT MISMATCH in {key[0]}\n"
                    f"    {label}\n"
                    f"    registered {expected}x, found {got}x")
            elif got:
                tag = label if kind == "ADDITION" else "replaced: " + label
                accepted.extend([(key[0], tag)] * got)
        for key in seen:
            if key in registry:
                continue

    _check(a.additions, REGISTERED_ADDITIONS, "ADDITION")
    _check(a.deletions, REGISTERED_REPLACEMENTS, "REMOVAL")

    wrappers = [s for s, _, _ in a.additions if s == ENTRYPOINT_SCOPE]
    if REQUIRE_ENTRYPOINT and len(wrappers) != 1:
        problems.append(
            f"expected exactly one entry-point addition in {ENTRYPOINT_SCOPE}, "
            f"found {len(wrappers)}")

    print(f"  before     : {before_path}")
    print(f"  after      : {after_path}")
    print(f"  additions  : {len(a.additions)} (registered keys "
          f"{len(REGISTERED_ADDITIONS)})  "
          f"deletions: {len(a.deletions)} (registered keys "
          f"{len(REGISTERED_REPLACEMENTS)})")
    for scope, label in accepted:
        print(f"    accepted : {label}   [{scope}]")
    for p in problems:
        print(f"  PROBLEM: {p}")

    if problems:
        print("  RESULT : NOT PROVEN -- unregistered change present")
        return 1
    print(f"  RESULT : only registered observation additions found; "
          f"all other logic aligned identically "
          f"({len(accepted)} accepted = {len(a.additions)} additions + "
          f"{len(a.deletions)} registered replacement)")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "--registry":
        _load_registry(argv[1])
        argv = argv[2:]
    if len(argv) == 3 and argv[0] == "--register":
        sys.exit(register(argv[1], argv[2]))
    if len(argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(argv[0], argv[1]))



if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
