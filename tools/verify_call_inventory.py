#!/usr/bin/env python3
"""Call-site inventory for the admission routes (design §6.2, extension 2).

    python3 tools/verify_call_inventory.py [ROOT]
    python3 tools/verify_call_inventory.py --register [ROOT]

Across every production `.py` file under ROOT (the repository root by
default), the calls to the five admission functions must equal the registered
inventory in tools/admission_call_inventory.py, exactly:

    open_trade  promote_queue  execute_pending_signals  queue_signal
    expire_queue

A site is (file, enclosing scope, normalised call source). The call source
includes the arguments, so `promote_queue(source="monitor", use_live=True)`
with any other argument is a different site. R1 is identified by an explicit
`from_queue` value and R2/R3 by `source` (design §2.3), so an argument change
must fail like a new caller.

FAIL on:
  * a call site that is not registered (a new caller, or a moved one)
  * a registered site that is missing
  * a registered site found a different number of times
  * any other reference to one of the five names: an alias (`f = open_trade`),
    a call through a variable, `getattr` with the name as a string, or an
    import that renames it (`import ... as`)

Production files: ROOT/*.py and ROOT/engine/*.py. tests/, tools/, archive/
and fixtures are not production. A new production file is scanned
automatically, so a caller in it fails.

Exit 0 only if the inventory matches; 1 on any mismatch; 2 if the inventory
table cannot be loaded.
"""

import ast
import glob
import importlib
import os
import sys

TARGETS = ("open_trade", "promote_queue", "execute_pending_signals",
           "queue_signal", "expire_queue")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(HERE)


def production_files(root):
    files = sorted(glob.glob(os.path.join(root, "*.py"))
                   + glob.glob(os.path.join(root, "engine", "*.py")))
    return [os.path.relpath(f, root) for f in files]


def _callee(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(self, relpath):
        self.relpath = relpath
        self.scope = ["<module>"]
        self.sites = {}
        self.references = []
        self._call_funcs = set()

    def _nest(self, label, node):
        self.scope.append(label)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self._nest(f"def {node.name}()", node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._nest(f"class {node.name}", node)

    def visit_If(self, node):
        test = ast.unparse(node.test)
        if test in ("__name__ == '__main__'", '__name__ == "__main__"'):
            self._nest("if __name__ == '__main__'", node)
        else:
            self.generic_visit(node)

    def visit_Call(self, node):
        name = _callee(node)
        if name in TARGETS:
            self._call_funcs.add(id(node.func))
            key = (self.relpath, "/".join(self.scope), ast.unparse(node))
            self.sites[key] = self.sites.get(key, 0) + 1
        if (isinstance(node.func, ast.Name) and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in TARGETS):
            self.references.append(
                (self.relpath, "/".join(self.scope),
                 f"getattr by name: {ast.unparse(node)}"))
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in TARGETS and id(node) not in self._call_funcs:
            self.references.append(
                (self.relpath, "/".join(self.scope),
                 f"non-call reference: {node.id} ({type(node.ctx).__name__})"))

    def visit_Attribute(self, node):
        if node.attr in TARGETS and id(node) not in self._call_funcs:
            self.references.append(
                (self.relpath, "/".join(self.scope),
                 f"non-call reference: {ast.unparse(node)}"))
        self.generic_visit(node)

    def _alias(self, node):
        for a in node.names:
            base = a.name.rsplit(".", 1)[-1]
            if base in TARGETS and a.asname and a.asname != base:
                self.references.append(
                    (self.relpath, "/".join(self.scope),
                     f"renaming import: {a.name} as {a.asname}"))

    def visit_Import(self, node):
        self._alias(node)

    def visit_ImportFrom(self, node):
        self._alias(node)


def scan(root):
    sites, references = {}, []
    for rel in production_files(root):
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=rel)
        s = _Scanner(rel)
        s.visit(tree)
        for k, n in s.sites.items():
            sites[k] = sites.get(k, 0) + n
        references.extend(s.references)
    return sites, references


def _load_inventory():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        return importlib.import_module("admission_call_inventory").CALL_SITES
    except (ImportError, AttributeError):
        return None


def register(root):
    sites, references = scan(root)
    print("CALL_SITES = {")
    for (rel, scope, src), n in sorted(sites.items()):
        print(f"    ({rel!r},\n     {scope!r},\n     {src!r}):\n        {n},")
    print("}")
    for r in references:
        print(f"# WARNING, not registrable: {r}")
    return 1 if references else 0


def main(root):
    inventory = _load_inventory()
    if inventory is None:
        print("  RESULT : NOT PROVEN -- call inventory table not loadable")
        return 2
    sites, references = scan(root)
    problems = []
    for key, n in sorted(sites.items()):
        want = inventory.get(key)
        if want is None:
            problems.append(f"UNREGISTERED CALL SITE {key[0]} [{key[1]}]\n"
                            f"    {key[2]}  ({n}x)")
        elif want != n:
            problems.append(f"OCCURRENCE COUNT MISMATCH {key[0]} [{key[1]}]\n"
                            f"    {key[2]}  registered {want}x, found {n}x")
    for key, want in sorted(inventory.items()):
        if key not in sites:
            problems.append(f"REGISTERED CALL SITE MISSING {key[0]} [{key[1]}]\n"
                            f"    {key[2]}  registered {want}x, found 0x")
    for rel, scope, what in references:
        problems.append(f"UNREGISTERED REFERENCE {rel} [{scope}]\n    {what}")
    print(f"  root       : {root}")
    print(f"  files      : {len(production_files(root))} production .py")
    print(f"  call sites : {sum(sites.values())} found, "
          f"{sum(inventory.values())} registered")
    for (rel, scope, src), n in sorted(sites.items()):
        if inventory.get((rel, scope, src)) == n:
            print(f"    accepted : {src}   [{rel} {scope}] {n}x")
    for p in problems:
        print(f"  PROBLEM: {p}")
    if problems:
        print("  RESULT : NOT PROVEN -- call-site inventory differs")
        return 1
    print("  RESULT : call-site inventory matches the registered table")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--register":
        sys.exit(register(argv[1] if len(argv) > 1 else DEFAULT_ROOT))
    if len(argv) > 1:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(argv[0] if argv else DEFAULT_ROOT))
