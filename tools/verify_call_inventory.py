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
    a call through a variable, an import that renames it (`import ... as`),
    or the name as a string constant (`getattr(m, "open_trade")`,
    `m.__dict__["open_trade"]`)
  * a reflective or process-launching construct that is not in
    DYNAMIC_SITES, matched exactly by (file, scope, source) with its count.
    The constructs are the builtins in REFLECTIVE_BUILTINS (getattr, vars,
    globals, eval, exec, __import__, ...), the attributes in REFLECTIVE_ATTRS
    (__dict__, __globals__, sys.modules, ...), any import of or attribute on a
    module in REFLECTIVE_MODULES (inspect, importlib, operator, types,
    subprocess, ...), a star import, and os.system/popen/fork/exec*/spawn*.
    A caller that builds the name at run time (`"open_" + "trade"`) still
    needs one of these to reach the function, so it fails as a new site.

`.eval` and `.query` are included because pandas resolves `@name` in them
from the caller's frame. The check is syntactic: a route that needs none of
the constructs above, such as a C extension or another third-party call that
looks a name up from a string, is outside it.

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

REFLECTIVE_BUILTINS = frozenset((
    "getattr", "setattr", "delattr", "hasattr", "vars", "globals", "locals",
    "dir", "__import__", "eval", "exec", "compile", "breakpoint",
    "__builtins__"))
REFLECTIVE_ATTRS = frozenset((
    "__dict__", "__globals__", "__builtins__", "__getattribute__",
    "__getattr__", "__code__", "__closure__", "__func__", "__self__",
    "__loader__", "__spec__", "__import__", "f_globals", "f_locals",
    "f_back", "f_builtins", "modules", "_getframe", "import_module",
    "eval", "query"))
REFLECTIVE_MODULES = frozenset((
    "importlib", "inspect", "operator", "types", "builtins", "gc", "ctypes",
    "runpy", "pkgutil", "pickle", "marshal", "copyreg", "code", "codeop",
    "zipimport", "subprocess", "multiprocessing", "concurrent", "pty"))
PROCESS_OS_ATTRS = ("system", "popen", "fork", "forkpty", "posix_spawn",
                    "posix_spawnp")
PROCESS_OS_PREFIXES = ("exec", "spawn")

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


def _os_process(attr):
    return attr in PROCESS_OS_ATTRS or attr.startswith(PROCESS_OS_PREFIXES)


class _Scanner(ast.NodeVisitor):
    def __init__(self, relpath, tree):
        self.relpath = relpath
        self.scope = ["<module>"]
        self.sites = {}
        self.dynamic = {}
        self.references = []
        self._call_funcs = set()
        self._dynamic_funcs = set()
        self.reflective_aliases = set()
        self.reflective_names = set()
        self.os_aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    bound = a.asname or top
                    if top in REFLECTIVE_MODULES:
                        self.reflective_aliases.add(bound)
                    if a.name == "os":
                        self.os_aliases.add(bound)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                for a in node.names:
                    if top in REFLECTIVE_MODULES or (
                            top == "os" and _os_process(a.name)) or (
                            a.name in REFLECTIVE_MODULES):
                        self.reflective_names.add(a.asname or a.name)

    def _where(self):
        return "/".join(self.scope)

    def _dyn(self, node):
        key = (self.relpath, self._where(), ast.unparse(node))
        self.dynamic[key] = self.dynamic.get(key, 0) + 1

    def _is_dynamic_func(self, f):
        if isinstance(f, ast.Name):
            return (f.id in REFLECTIVE_BUILTINS
                    or f.id in self.reflective_names
                    or f.id in self.reflective_aliases)
        if isinstance(f, ast.Attribute):
            if f.attr in REFLECTIVE_ATTRS:
                return True
            if isinstance(f.value, ast.Name):
                if f.value.id in self.reflective_aliases:
                    return True
                if f.value.id in self.os_aliases and _os_process(f.attr):
                    return True
        return False

    def visit_Constant(self, node):
        if isinstance(node.value, str) and node.value in TARGETS:
            self.references.append(
                (self.relpath, self._where(),
                 f"target name as a string constant: {node.value!r}"))

    def visit_Import(self, node):
        self._alias(node)
        if any(a.name.split(".")[0] in REFLECTIVE_MODULES
               for a in node.names):
            self._dyn(node)

    def visit_ImportFrom(self, node):
        self._alias(node)
        top = (node.module or "").split(".")[0]
        if top in REFLECTIVE_MODULES or any(
                a.name in REFLECTIVE_MODULES or a.name == "*"
                or (top == "os" and _os_process(a.name))
                for a in node.names):
            self._dyn(node)

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
            key = (self.relpath, self._where(), ast.unparse(node))
            self.sites[key] = self.sites.get(key, 0) + 1
        if self._is_dynamic_func(node.func):
            self._dynamic_funcs.add(id(node.func))
            self._dyn(node)
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in TARGETS and id(node) not in self._call_funcs:
            self.references.append(
                (self.relpath, self._where(),
                 f"non-call reference: {node.id} ({type(node.ctx).__name__})"))
        if (id(node) not in self._dynamic_funcs
                and (node.id in REFLECTIVE_BUILTINS
                     or node.id in self.reflective_names
                     or node.id in self.reflective_aliases)):
            self._dyn(node)

    def visit_Attribute(self, node):
        if node.attr in TARGETS and id(node) not in self._call_funcs:
            self.references.append(
                (self.relpath, self._where(),
                 f"non-call reference: {ast.unparse(node)}"))
        if (id(node) not in self._dynamic_funcs
                and self._is_dynamic_func(node)):
            self._dyn(node)
            self._dynamic_funcs.add(id(node))
            if isinstance(node.value, ast.Name):
                self._dynamic_funcs.add(id(node.value))
        elif id(node) in self._dynamic_funcs and isinstance(node.value,
                                                            ast.Name):
            self._dynamic_funcs.add(id(node.value))
        self.generic_visit(node)

    def _alias(self, node):
        for a in node.names:
            base = a.name.rsplit(".", 1)[-1]
            if base in TARGETS and a.asname and a.asname != base:
                self.references.append(
                    (self.relpath, self._where(),
                     f"renaming import: {a.name} as {a.asname}"))


def scan(root):
    sites, dynamic, references = {}, {}, []
    for rel in production_files(root):
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=rel)
        s = _Scanner(rel, tree)
        s.visit(tree)
        for k, n in s.sites.items():
            sites[k] = sites.get(k, 0) + n
        for k, n in s.dynamic.items():
            dynamic[k] = dynamic.get(k, 0) + n
        references.extend(s.references)
    return sites, dynamic, references


def _load_inventory():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        mod = importlib.import_module("admission_call_inventory")
        return mod.CALL_SITES, mod.DYNAMIC_SITES
    except (ImportError, AttributeError):
        return None


def _print_table(name, table):
    print(f"{name} = {{")
    for (rel, scope, src), n in sorted(table.items()):
        print(f"    ({rel!r},\n     {scope!r},\n     {src!r}):\n        {n},")
    print("}")


def register(root):
    sites, dynamic, references = scan(root)
    _print_table("CALL_SITES", sites)
    print()
    _print_table("DYNAMIC_SITES", dynamic)
    for r in references:
        print(f"# WARNING, not registrable: {r}")
    return 1 if references else 0


def _compare(found, registered, what):
    problems = []
    for key, n in sorted(found.items()):
        want = registered.get(key)
        if want is None:
            problems.append(f"UNREGISTERED {what} {key[0]} [{key[1]}]\n"
                            f"    {key[2]}  ({n}x)")
        elif want != n:
            problems.append(f"OCCURRENCE COUNT MISMATCH {key[0]} [{key[1]}]\n"
                            f"    {key[2]}  registered {want}x, found {n}x")
    for key, want in sorted(registered.items()):
        if key not in found:
            problems.append(f"REGISTERED {what} MISSING {key[0]} [{key[1]}]\n"
                            f"    {key[2]}  registered {want}x, found 0x")
    return problems


def main(root):
    tables = _load_inventory()
    if tables is None:
        print("  RESULT : NOT PROVEN -- call inventory table not loadable")
        return 2
    inventory, dyn_inventory = tables
    sites, dynamic, references = scan(root)
    problems = (_compare(sites, inventory, "CALL SITE")
                + _compare(dynamic, dyn_inventory, "DYNAMIC ACCESS"))
    for rel, scope, what in references:
        problems.append(f"UNREGISTERED REFERENCE {rel} [{scope}]\n    {what}")
    print(f"  root       : {root}")
    print(f"  files      : {len(production_files(root))} production .py")
    print(f"  call sites : {sum(sites.values())} found, "
          f"{sum(inventory.values())} registered")
    print(f"  dynamic    : {sum(dynamic.values())} found, "
          f"{sum(dyn_inventory.values())} registered")
    for table, ref in ((sites, inventory), (dynamic, dyn_inventory)):
        for (rel, scope, src), n in sorted(table.items()):
            if ref.get((rel, scope, src)) == n:
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
