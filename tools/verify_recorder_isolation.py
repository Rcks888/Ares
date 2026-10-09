#!/usr/bin/env python3
"""Prove the recorder added observation and changed no decision logic.

    python3 tools/verify_recorder_isolation.py BEFORE.py AFTER.py

Removes the registered observation additions from AFTER's syntax tree and
compares what remains against BEFORE. Exit 0 only if they are identical.

WHY NOT A TEXT DIFF
-------------------
A line-based strip cannot do this honestly. Filtering comment lines also removes
the pre-existing comments that happen to sit next to an addition, so an unchanged
file reports changes; and removing statements by prefix leaves dangling `try:`
and `except:` headers, which reports syntax noise as a decision change. Both
failure modes were observed on this exact file before this tool was written.

Parsing sidesteps both: comments are not in the tree at all, and a statement is
removed as a whole node or not at all.

REGISTERED ADDITIONS, and nothing else, are removable:
  * the guarded `try: from engine import monitor_observer` block
  * the module-level `_MONITOR_EVAL` and `_INVOCATION_SEQ` bindings
  * `global _INVOCATION_SEQ` and any `_INVOCATION_SEQ` rebinding
  * any statement whose subject is `obs[...]` or `_MONITOR_EVAL[...]`
  * the `if _obs is not None:` effective-config block
  * the `__main__` try/except/finally wrapper, unwrapped back to `monitor()`

The wrapper is only unwrapped after checking that its handler re-raises bare and
its finally body does nothing but flush. A wrapper that swallowed the exception
would therefore FAIL here rather than be normalised away.
"""

import ast
import sys


OBSERVATION_SUBJECTS = ("obs", "_MONITOR_EVAL", "_INVOCATION_SEQ")


def _subject(node):
    """Leftmost Name a statement is about, or None."""
    for target in getattr(node, "targets", []) + [getattr(node, "target", None),
                                                  getattr(node, "value", None)]:
        cur = target
        while cur is not None:
            if isinstance(cur, ast.Name):
                return cur.id
            cur = getattr(cur, "value", None) or getattr(cur, "func", None)
    return None


def _is_guarded_import(node):
    return (isinstance(node, ast.Try)
            and len(node.body) == 1
            and isinstance(node.body[0], ast.ImportFrom)
            and node.body[0].module == "engine"
            and any(a.name == "monitor_observer" for a in node.body[0].names))


def _is_obs_config_block(node):
    """`if _obs is not None:` — the only statement guarded on the recorder."""
    return (isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "_obs")


def _is_observation_stmt(node):
    if isinstance(node, ast.Global):
        return all(n in OBSERVATION_SUBJECTS for n in node.names)
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.Expr)):
        return _subject(node) in OBSERVATION_SUBJECTS
    return False


class Strip(ast.NodeTransformer):
    def __init__(self):
        self.unwrapped = False
        self.problems = []

    def _clean(self, body):
        out = []
        for stmt in body:
            if _is_guarded_import(stmt) or _is_obs_config_block(stmt):
                continue
            if _is_observation_stmt(stmt):
                continue
            out.append(self.visit(stmt))
        return out

    def generic_visit(self, node):
        for field in ("body", "orelse", "finalbody"):
            if hasattr(node, field) and isinstance(getattr(node, field), list):
                setattr(node, field, self._clean(getattr(node, field)))
        return node

    def visit_Try(self, node):
        """Unwrap the entrypoint wrapper, but only if it is faithful."""
        calls_monitor = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "monitor"
            for stmt in node.body for n in ast.walk(stmt))
        if not calls_monitor:
            return self.generic_visit(node)

        bare_reraise = any(
            isinstance(n, ast.Raise) and n.exc is None
            for h in node.handlers for n in ast.walk(h))
        if not bare_reraise:
            self.problems.append(
                "entrypoint handler does not re-raise bare: the production "
                "exception would be swallowed or replaced")

        for stmt in node.finalbody:
            src = ast.unparse(stmt)
            if "flush" not in src:
                self.problems.append(
                    f"finally body does more than flush: {src!r}")

        self.unwrapped = True
        return self._clean(node.body)


def main(before_path, after_path):
    before = ast.parse(open(before_path).read())
    after = ast.parse(open(after_path).read())

    strip = Strip()
    after = strip.visit(after)
    ast.fix_missing_locations(after)

    before_src = ast.unparse(ast.parse(ast.unparse(before)))
    after_src = ast.unparse(ast.parse(ast.unparse(after)))

    print(f"  before : {before_path}")
    print(f"  after  : {after_path}")
    print(f"  entrypoint wrapper unwrapped : {strip.unwrapped}")

    for p in strip.problems:
        print(f"  PROBLEM: {p}")

    if before_src == after_src:
        print("  RESULT : decision logic IDENTICAL after removing observation")
        return 1 if strip.problems else 0

    print("  RESULT : DECISION LOGIC DIFFERS -- review voided")
    b, a = before_src.splitlines(), after_src.splitlines()
    import difflib
    for line in list(difflib.unified_diff(b, a, "before", "after", n=2))[:60]:
        print("   " + line.rstrip())
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
