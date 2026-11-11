#!/usr/bin/env python3
"""Statement whitelist for admission capture v1 (design §3.2, §6.2 extension 1).

    python3 tools/verify_statement_whitelist.py [ROOT]

For each of the three admission files, the statements added relative to its
pre-admission base (the production file on main) are found with the same
alignment as tools/verify_recorder_isolation.py, and every one must match a
permitted form. ROOT is the repository root by default.

    engine/tracker.py   base tests/fixtures/tracker_preadmission.py
    daily_report.py     base tests/fixtures/daily_report_preadmission.py
    monitor_trades.py   base tests/fixtures/monitor_trades_preadmission.py

The monitor base is the activated monitor recorder (5fd13e9), not the
pre-activation fixture of the isolation pair, so only the admission additions
are checked. Each base is pinned by sha256; a different base is exit 2.

Statements inside a function (capture statements) must be one of:

  store assignment   T = v     T a subscript chain with str-constant keys,
                               rooted at _ADMISSION_EVAL or at a record created
                               in the same function; one target only
  record creation    r = {...} r a name that the base function never uses;
                               str-constant keys; values as v, or [] / {}
  record append      T.append(a)   a is v or a tuple of v
  counter            T += 1    int constant only
  reset              _ADMISSION_EVAL.clear() then
                     _ADMISSION_EVAL.update(_new_admission_store(n)),
                     consecutive, in an entry function, before any other
                     statement except the token sequence and an existing reset
  token sequence     global n / n += 1 with n a new private module name, in an
                     entry function before the reset (listed for review: not in
                     the §3.2 table)

  v is a constant, a definitely bound name, a str/name subscript of a read, an attribute
  of a name, the clock read _time.time(), or len/dict/list of a name or a
  constant-key subscript. Nothing else: no other call, method call,
  comparison, operator, formatting, branch or loop.

Definitely bound (W5, M3 review round 1 finding 2). Every name a capture
reads, and the root of its target, must be bound on every path that reaches
the statement, so the capture cannot raise NameError or UnboundLocalError. A
name local to the function must be definitely bound before the statement
(class _Definite: a loop body may not run, an except or finally block may
start before any statement of its try body, a with body may be cut short, a
walrus is ignored); any other name must be definitely bound at module end or
be a builtin the module does not rebind. len/dict/list and _time must not be
local. In the entry-point block the module body is the flow.

Module-level additions must be imports, assignments, function definitions or
try blocks of those, and every name they bind must be private and absent from
the base file, bound by one statement only. `_time` must be bound by
`import time as _time`. That shape rule is necessary but does not admit a
statement (W2): every module-level addition, and the entry-point flush
wrapper (W3), must also be registered exactly, by (file, scope, sha256, count),
in tools/admission_whitelist_infrastructure.py (`--register` prints it), and
each registered entry is source-reviewed. `_p = save_trades([])` passes the
shape rule and fails here.

Because no name introduced by the capture appears in the base, and
verify_recorder_isolation.py proves every other statement equals the base, no
production statement can read the store, a record or the clock value.

The entry-point block (`if __name__ == '__main__'`) may contain store
assignments and exactly one other added statement, the flush wrapper. The
wrapper is outside §3.2: it is the M2-reviewed wrapper, admitted only by its
exact registration (W3); the report and monitor admission entrypoint controls
cover its behaviour.

Two rules are stricter than the §3.2 table. Targets are one level deep
(root['key']), because a record holds production objects by reference. A
counter or an append must act on a key that every store factory (including
an import fallback) or the record literal initialises as an int or [], and
that no capture assignment rebinds in any of the three files.

Exit 0 only if every addition matches; 1 on any violation; 2 if a base is
missing or its digest differs, or the infrastructure table cannot be loaded.
"""

import ast
import builtins
import hashlib
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import verify_recorder_isolation as _iso                    # noqa: E402

INFRA_TABLE = "admission_whitelist_infrastructure"

STORE = "_ADMISSION_EVAL"
FACTORY = "_new_admission_store"
CLOCK_MODULE = "_time"
BUILTIN_READS = ("len", "dict", "list")
ENTRY = _iso.ENTRYPOINT_SCOPE

PAIRS = (
    ("engine/tracker.py", "tests/fixtures/tracker_preadmission.py",
     "2fda82de09432dec25e5d215d60e8f4cfb5888b6838fccf4f22c2b3850ed98db",
     ()),
    ("daily_report.py", "tests/fixtures/daily_report_preadmission.py",
     "d79a0b529f83ba170ddfff18939c51cab027eaa1a11497d2551fa68e97069609",
     ("generate_report",)),
    ("monitor_trades.py", "tests/fixtures/monitor_trades_preadmission.py",
     "1023c0aa0b1008aee514225305844aa9bb4af56e216b2b62a5b28523434edb45",
     ("monitor",)),
)


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _function_of(scope):
    parts = scope.split("/")
    if len(parts) > 1 and parts[1].startswith("def "):
        return parts[1][4:].split("(", 1)[0]
    return None


def _top_functions(tree):
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _identifiers(node):
    """Every identifier a subtree binds or reads, as written."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.alias):
            out.add(n.asname or n.name.split(".", 1)[0])
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
    return out


def _module_bindings(stmt):
    """Names a module-level statement binds, excluding function bodies and
    except-handler names (Python unbinds those when the handler ends)."""
    out = []

    def visit(s):
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            for a in s.names:
                out.append(a.asname or a.name.split(".", 1)[0])
        elif isinstance(s, ast.Assign):
            for t in s.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        out.append(n.id)
        elif isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(s.name)
        elif isinstance(s, ast.Try):
            for b in s.body + s.orelse + s.finalbody:
                visit(b)
            for h in s.handlers:
                for b in h.body:
                    visit(b)
    visit(stmt)
    return out


def _module_form_ok(stmt):
    if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
        return True
    if isinstance(stmt, ast.Assign):
        return all(isinstance(t, ast.Name) for t in stmt.targets)
    if isinstance(stmt, ast.Try):
        inner = stmt.body + stmt.orelse + stmt.finalbody + [
            b for h in stmt.handlers for b in h.body]
        return all(_module_form_ok(b) for b in inner)
    return False


def _bound_in(node):
    """Names bound anywhere in a function or module body."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.alias):
            out.add(n.asname or n.name.split(".", 1)[0])
    return out


def _local_names(fn):
    """Names local to a function: bound in its own scope (not in a nested
    def, class, lambda or comprehension) and not declared global."""
    out, declared = set(), set()
    a = fn.args
    out |= {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    out |= {x.arg for x in (a.vararg, a.kwarg) if x}

    def visit(node):
        for c in ast.iter_child_nodes(node):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
                out.add(c.name)
                continue
            if isinstance(c, (ast.Lambda, ast.ListComp, ast.SetComp,
                              ast.DictComp, ast.GeneratorExp)):
                continue
            if isinstance(c, ast.Name) and isinstance(c.ctx, (ast.Store,
                                                              ast.Del)):
                out.add(c.id)
            elif isinstance(c, ast.ExceptHandler) and c.name:
                out.add(c.name)
            elif isinstance(c, ast.alias):
                out.add(c.asname or c.name.split(".", 1)[0])
            elif isinstance(c, (ast.Global, ast.Nonlocal)):
                declared.update(c.names)
            visit(c)

    for s in fn.body:
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            out.add(s.name)
        elif isinstance(s, (ast.Global, ast.Nonlocal)):
            declared.update(s.names)
        else:
            visit(ast.Module(body=[s], type_ignores=[]))
    return out - declared


def _targets(t):
    if isinstance(t, ast.Name):
        return {t.id}
    if isinstance(t, (ast.Tuple, ast.List)):
        return set().union(set(), *map(_targets, t.elts))
    if isinstance(t, ast.Starred):
        return _targets(t.value)
    return set()


_TERMINAL = (ast.Return, ast.Raise, ast.Continue, ast.Break)


class _Definite:
    """Names definitely bound before each statement of a body (W5, review
    round 1 finding 2). Conservative: a loop body may not run, an except
    handler or finally block may start before any statement of its try body,
    a with body may be cut short by a suppressing manager, and a walrus
    binding is ignored. A branch that ends in return, raise, continue or
    break does not reach the code after it.

    at[unparse(stmt)] lists the bound set at every occurrence of stmt."""

    def __init__(self, body, params=()):
        self.at = {}
        self.end, _live = self.block(body, frozenset(params))

    def block(self, stmts, d):
        for s in stmts:
            self.at.setdefault(ast.unparse(s), []).append(d)
            d, live = self.stmt(s, d)
            if not live:
                return d, False
        return d, True

    def stmt(self, s, d):
        if isinstance(s, _TERMINAL):
            return d, False
        if isinstance(s, ast.Assign):
            return d | set().union(*map(_targets, s.targets)), True
        if isinstance(s, ast.AugAssign) or (isinstance(s, ast.AnnAssign)
                                            and s.value is not None):
            return d | _targets(s.target), True
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            return d | {s.name}, True
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            return d | {a.asname or a.name.split(".", 1)[0]
                        for a in s.names}, True
        if isinstance(s, ast.Delete):
            return d - set().union(*map(_targets, s.targets)), True
        if isinstance(s, ast.If):
            outs = [o for o, live in (self.block(s.body, d),
                                      self.block(s.orelse, d)) if live]
            return (frozenset.intersection(*outs), True) if outs else (d, False)
        if isinstance(s, (ast.For, ast.AsyncFor)):
            self.block(s.body, d | _targets(s.target))
            self.block(s.orelse, d)
            return d, True
        if isinstance(s, ast.While):
            self.block(s.body, d)
            self.block(s.orelse, d)
            return d, True
        if isinstance(s, (ast.With, ast.AsyncWith)):
            v = set().union(set(), *(_targets(i.optional_vars)
                                     for i in s.items if i.optional_vars))
            self.block(s.body, d | v)
            return d | v, True
        if isinstance(s, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            body, live = self.block(s.body, d)
            outs = []
            if live:
                o, olive = self.block(s.orelse, body)
                if olive:
                    outs.append(o)
            for h in s.handlers:
                named = {h.name} if h.name else set()
                o, hlive = self.block(h.body, d | named)
                if hlive:
                    outs.append(o - named)
            fin, flive = self.block(s.finalbody, d)
            if not (outs and flive):
                return d, False
            return frozenset.intersection(*outs) | (fin - d), True
        if isinstance(s, ast.Match):
            for c in s.cases:
                self.block(c.body, d)
            return d, True
        return d, True

    def before(self, stmt):
        """Bound set before stmt, intersected over its occurrences; None if
        stmt does not occur."""
        sets = self.at.get(ast.unparse(stmt))
        return frozenset.intersection(*sets) if sets else None


def _chain_root(node):
    """Root name of a subscript chain with str-constant keys, else None."""
    if not isinstance(node, ast.Subscript):
        return None
    while isinstance(node, ast.Subscript):
        s = node.slice
        if not (isinstance(s, ast.Constant) and isinstance(s.value, str)):
            return None
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _depth1(node):
    """(root, key) for `root['key']`, else None. Capture targets are one
    level deep: a deeper chain can reach a production object that a record
    holds by reference, e.g. adm['signal']['x']."""
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)):
        return node.value.id, node.slice.value
    return None


def _init_kind(value):
    if isinstance(value, ast.List) and not value.elts:
        return "list"
    if isinstance(value, ast.Constant) and type(value.value) is int:
        return "int"
    return None


def _literal_inits(d):
    return {k.value: _init_kind(v) for k, v in zip(d.keys, d.values)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _factories(tree):
    """Initial key kinds of every `_new_admission_store` the file defines,
    including a fallback defined inside an import handler."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == FACTORY:
            rets = [r for r in ast.walk(n) if isinstance(r, ast.Return)]
            if len(rets) == 1 and isinstance(rets[0].value, ast.Dict):
                out.append(_literal_inits(rets[0].value))
            else:
                out.append({})
    return out


class _FileCheck:
    def __init__(self, rel, base_tree, after_tree, entry_functions):
        self.rel = rel
        self.base = base_tree
        self.after = after_tree
        self.entry_functions = entry_functions
        self.base_names = _identifiers(base_tree)
        self.base_functions = _top_functions(base_tree)
        self.after_functions = _top_functions(after_tree)
        self.module_bound = _bound_in(ast.Module(
            body=[s for s in after_tree.body
                  if not isinstance(s, (ast.FunctionDef,
                                        ast.AsyncFunctionDef))],
            type_ignores=[])) | set(self.after_functions)
        self.builtins = set(dir(builtins))
        self.problems = []
        self.forms = {}
        self.new_module_names = set()
        self.store_assigned = set()
        self.record_assigned = set()
        self.record_inits = {}
        self.container_uses = []
        self.factories = _factories(after_tree)
        self.shadowing = set()
        self.infrastructure = []
        self.module_flow = _Definite(after_tree.body)
        self.module_definite = self.module_flow.end | set(self.after_functions)
        self.flows = {}

    def bound_before(self, scope, stmt, fname):
        """Names a capture may read at its own position (W5): a local name
        only if it is definitely bound on every path to the statement; any
        other name only if it is definitely bound at module end (functions
        run after import) or a builtin that the module does not rebind.
        In the entry-point block the module flow itself is used. Returns
        (bound, locals) or None if the statement cannot be located."""
        if fname is None:
            d = self.module_flow.before(stmt)
            if d is None:
                return None
            mod_locals = _bound_in(self.after)
            return d | (self.builtins - mod_locals), mod_locals
        fn = self.after_functions.get(fname)
        if fn is None:
            return None
        if fname not in self.flows:
            a = fn.args
            params = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
            params |= {x.arg for x in (a.vararg, a.kwarg) if x}
            self.flows[fname] = (_Definite(fn.body, params), _local_names(fn))
        flow, local = self.flows[fname]
        d = flow.before(stmt)
        if d is None:
            return None
        outer = (self.module_definite | (self.builtins - self.module_bound))
        return (d & local) | (outer - local), local

    def fail(self, scope, src, why):
        self.problems.append(
            f"{self.rel} [{scope}]\n    {src.splitlines()[0][:100]}\n"
            f"    {why}")

    def count(self, form):
        self.forms[form] = self.forms.get(form, 0) + 1

    def _value(self, node, bound, containers=False):
        if isinstance(node, ast.Constant):
            if node.value is None or type(node.value) in (str, int, float,
                                                          bool):
                return None
            return f"constant of type {type(node.value).__name__}"
        if isinstance(node, ast.Name):
            if node.id in bound:
                return None
            return f"name {node.id!r} is not definitely bound here"
        if isinstance(node, ast.Subscript):
            s = node.slice
            if not (isinstance(s, ast.Constant) and type(s.value) in (str, int)
                    or isinstance(s, ast.Name)):
                return f"subscript key {ast.unparse(s)!r} is not a constant or name"
            if isinstance(s, ast.Name) and s.id not in bound:
                return f"name {s.id!r} is not definitely bound here"
            if not isinstance(node.value, (ast.Name, ast.Subscript,
                                           ast.Attribute)):
                return f"subscript of {type(node.value).__name__}"
            return self._value(node.value, bound)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                return self._value(node.value, bound)
            return "attribute of a non-name"
        if isinstance(node, ast.Call):
            return self._call_value(node, bound)
        if containers and isinstance(node, (ast.List, ast.Tuple)) \
                and not node.elts:
            return None
        if containers and isinstance(node, ast.Dict) and not node.keys:
            return None
        return f"{type(node).__name__} is not a permitted value"

    def _call_value(self, node, bound):
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "time"
                and isinstance(f.value, ast.Name)
                and f.value.id == CLOCK_MODULE):
            if CLOCK_MODULE in self.shadowing:
                return f"{CLOCK_MODULE} is local to this function"
            if node.args or node.keywords:
                return "clock read with arguments"
            self.count("clock read")
            return None
        if isinstance(f, ast.Name) and f.id in BUILTIN_READS:
            if f.id in self.shadowing:
                return f"{f.id} is local to this function"
            if node.keywords or len(node.args) != 1:
                return f"{f.id}() must take exactly one positional argument"
            a = node.args[0]
            if isinstance(a, ast.Name) or _chain_root(a) is not None:
                why = self._value(a, bound)
                if why is None:
                    self.count("builtin read")
                return why
            return f"{f.id}() argument is not a name or constant-key subscript"
        return f"call {ast.unparse(f)!r} is not permitted"

    def check_module(self, scope, stmt, src, binders):
        if not _module_form_ok(stmt):
            self.fail(scope, src, "module-level addition of a "
                      f"non-permitted kind ({type(stmt).__name__})")
            return
        for name in set(_module_bindings(stmt)):
            if not name.startswith("_"):
                self.fail(scope, src, f"binds public name {name!r}")
            elif name in self.base_names:
                self.fail(scope, src, f"binds {name!r}, which the base "
                          "file already uses")
            binders.setdefault(name, []).append(src)
            self.new_module_names.add(name)
        self.count("module addition")

    def check_entry_function_prologue(self, fname):
        fn = self.after_functions.get(fname)
        if fn is None:
            self.problems.append(f"{self.rel}: entry function {fname}() "
                                 "not found")
            return
        body = fn.body
        idx = [i for i, s in enumerate(body) if self._is_reset(s, "clear")]
        upd = [i for i, s in enumerate(body) if self._is_reset(s, "update")]
        if len(idx) != 1 or len(upd) != 1 or upd[0] != idx[0] + 1:
            self.problems.append(
                f"{self.rel} [def {fname}()]\n    reset must be one "
                f"{STORE}.clear() immediately followed by one "
                f"{STORE}.update({FACTORY}(n)); found clear at {idx}, "
                f"update at {upd}")
            return
        for s in body[:idx[0]]:
            if isinstance(s, ast.Global):
                continue
            if (isinstance(s, ast.AugAssign) and isinstance(s.target, ast.Name)
                    and isinstance(s.op, ast.Add)
                    and isinstance(s.value, ast.Constant)
                    and type(s.value.value) is int):
                continue
            if (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
                    and isinstance(s.value.func, ast.Attribute)
                    and s.value.func.attr in ("clear", "update")
                    and isinstance(s.value.func.value, ast.Name)
                    and s.value.func.value.id in self.module_bound):
                continue
            self.problems.append(
                f"{self.rel} [def {fname}()]\n    {ast.unparse(s)[:100]}\n"
                "    precedes the reset; only the token sequence and an "
                "existing reset may")

    def _is_reset(self, s, which):
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)):
            return False
        c = s.value
        f = c.func
        if not (isinstance(f, ast.Attribute) and f.attr == which
                and isinstance(f.value, ast.Name) and f.value.id == STORE):
            return False
        if which == "clear":
            return not c.args and not c.keywords
        return (len(c.args) == 1 and not c.keywords
                and isinstance(c.args[0], ast.Call)
                and isinstance(c.args[0].func, ast.Name)
                and c.args[0].func.id == FACTORY
                and len(c.args[0].args) == 1 and not c.args[0].keywords
                and isinstance(c.args[0].args[0], ast.Name))

    def check_capture(self, scope, stmt, src, fname, records, bound,
                      in_entry_block):
        roots = {STORE} | records
        if isinstance(stmt, ast.Expr) and (self._is_reset(stmt, "clear")
                                           or self._is_reset(stmt, "update")):
            c = stmt.value
            used = {STORE} | ({FACTORY, c.args[0].args[0].id}
                              if c.args else set())
            if fname not in self.entry_functions:
                self.fail(scope, src, "reset outside an entry function")
            elif used - bound:
                self.fail(scope, src, f"name {sorted(used - bound)[0]!r} is "
                          "not definitely bound here")
            else:
                self.count("reset")
            return
        if isinstance(stmt, ast.Global):
            bad = [n for n in stmt.names
                   if not (n.startswith("_") and n not in self.base_names)]
            if fname not in self.entry_functions or bad:
                self.fail(scope, src, "global declaration is permitted only "
                          "for a new private token name in an entry function")
            else:
                self.count("token sequence")
            return
        if isinstance(stmt, ast.AugAssign):
            if not (isinstance(stmt.op, ast.Add)
                    and isinstance(stmt.value, ast.Constant)
                    and type(stmt.value.value) is int):
                self.fail(scope, src, "counter must add an int constant")
                return
            if isinstance(stmt.target, ast.Name):
                n = stmt.target.id
                if (fname in self.entry_functions and n.startswith("_")
                        and n not in self.base_names):
                    if n not in bound:
                        self.fail(scope, src, f"name {n!r} is not definitely "
                                  "bound at module level")
                        return
                    self.count("token sequence")
                else:
                    self.fail(scope, src, "counter on a name is permitted "
                              "only for a new private token name in an entry "
                              "function")
                return
            rk = _depth1(stmt.target)
            if rk and rk[0] in roots and rk[0] not in bound:
                self.fail(scope, src, f"root {rk[0]!r} is not definitely "
                          "bound here")
                return
            if rk and rk[0] in roots:
                self.container_uses.append(("int", fname, rk, scope, src))
                self.count("counter")
            else:
                self.fail(scope, src, "counter target is not root['key'] "
                          "with the store or a record of this function as "
                          "root")
            return
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1:
                self.fail(scope, src, "more than one assignment target")
                return
            t = stmt.targets[0]
            if isinstance(t, ast.Name) and not in_entry_block:
                if not isinstance(stmt.value, ast.Dict):
                    self.fail(scope, src, "assignment to a bare name that is "
                              "not a record creation")
                    return
                if t.id not in records:
                    self.fail(scope, src, f"record name {t.id!r} is used by "
                              "the base function")
                    return
                for k, v in zip(stmt.value.keys, stmt.value.values):
                    if not (isinstance(k, ast.Constant)
                            and isinstance(k.value, str)):
                        self.fail(scope, src, "record key is not a str "
                                  "constant")
                        return
                    why = self._value(v, bound, containers=True)
                    if why:
                        self.fail(scope, src, f"record value: {why}")
                        return
                if (fname, t.id) in self.record_inits:
                    self.fail(scope, src, f"record {t.id!r} is created "
                              "twice in this function")
                    return
                self.record_inits[(fname, t.id)] = _literal_inits(stmt.value)
                self.count("record creation")
                return
            rk = _depth1(t)
            if not (rk and rk[0] in roots):
                self.fail(scope, src, "assignment target is not root['key'] "
                          "with the store or a record of this function as "
                          "root")
                return
            if rk[0] not in bound:
                self.fail(scope, src, f"root {rk[0]!r} is not definitely "
                          "bound here")
                return
            why = self._value(stmt.value, bound)
            if why:
                self.fail(scope, src, f"assigned value: {why}")
                return
            if rk[0] == STORE:
                self.store_assigned.add(rk[1])
            else:
                self.record_assigned.add((fname, rk[0], rk[1]))
            self.count("store assignment")
            return
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            c = stmt.value
            f = c.func
            rk = _depth1(f.value) if isinstance(f, ast.Attribute) else None
            if (isinstance(f, ast.Attribute) and f.attr == "append"
                    and rk and rk[0] in roots):
                if c.keywords or len(c.args) != 1:
                    self.fail(scope, src, "append takes exactly one "
                              "positional argument")
                    return
                if rk[0] not in bound:
                    self.fail(scope, src, f"root {rk[0]!r} is not definitely "
                              "bound here")
                    return
                a = c.args[0]
                elts = a.elts if isinstance(a, ast.Tuple) else [a]
                for e in elts:
                    why = self._value(e, bound)
                    if why:
                        self.fail(scope, src, f"appended value: {why}")
                        return
                self.container_uses.append(("list", fname, rk, scope, src))
                self.count("record append")
                return
            self.fail(scope, src, f"call {ast.unparse(f)!r} is not permitted")
            return
        self.fail(scope, src, f"{type(stmt).__name__} is not a permitted "
                  "capture statement")


def check_pair(root, rel, base_rel, pinned, entry_functions):
    base_path = os.path.join(root, base_rel)
    after_path = os.path.join(root, rel)
    if not os.path.exists(base_path):
        return None, [f"{base_rel}: base missing"], {}
    digest = _sha256(base_path)
    if digest != pinned:
        return None, [f"{base_rel}: sha256 {digest}, pinned {pinned}"], {}
    with open(base_path, encoding="utf-8") as fh:
        base_tree = ast.parse(fh.read())
    with open(after_path, encoding="utf-8") as fh:
        after_tree = ast.parse(fh.read())
    aligned = _iso.analyse(base_path, after_path)
    fc = _FileCheck(rel, base_tree, after_tree, entry_functions)

    records = {}
    for scope, _dig, src in aligned.additions:
        fname = _function_of(scope)
        stmt = ast.parse(src).body[0]
        if fname and isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name) \
                and isinstance(stmt.value, ast.Dict):
            name = stmt.targets[0].id
            base_fn = fc.base_functions.get(fname)
            if base_fn is None or name not in _identifiers(base_fn):
                records.setdefault(fname, set()).add(name)

    binders = {}
    entry_other = []
    for scope, dig, src in aligned.additions:
        stmt = ast.parse(src).body[0]
        fname = _function_of(scope)
        if scope == "<module>":
            fc.infrastructure.append((scope, dig, src))
            fc.check_module(scope, stmt, src, binders)
        elif scope == ENTRY or scope.startswith(ENTRY + "/"):
            if isinstance(stmt, ast.Assign):
                located = fc.bound_before(scope, stmt, None)
                if located is None:
                    fc.fail(scope, src, "statement not located in the "
                            "module body")
                    continue
                fc.shadowing = set()
                fc.check_capture(scope, stmt, src, None, set(), located[0],
                                 True)
            else:
                fc.infrastructure.append((scope, dig, src))
                entry_other.append((scope, src))
        elif fname:
            located = fc.bound_before(scope, stmt, fname)
            if located is None:
                fc.fail(scope, src, f"statement not located in {fname}()")
                continue
            fc.shadowing = located[1]
            fc.check_capture(scope, stmt, src, fname,
                             records.get(fname, set()), located[0], False)
        else:
            fc.fail(scope, src, "addition in a module-level compound "
                    "statement")

    for name, srcs in binders.items():
        if len(srcs) > 1:
            fc.problems.append(f"{rel}: {name!r} bound by {len(srcs)} "
                               "module-level additions")
    if CLOCK_MODULE in fc.new_module_names:
        imports = [s for s in after_tree.body if isinstance(s, ast.Import)
                   and any(a.name == "time" and a.asname == CLOCK_MODULE
                           for a in s.names)]
        if len(imports) != 1:
            fc.problems.append(f"{rel}: {CLOCK_MODULE} must be bound by "
                               "exactly one `import time as _time`")
    elif fc.forms.get("clock read"):
        fc.problems.append(f"{rel}: clock read without a registered "
                           f"`import time as {CLOCK_MODULE}`")
    for name in BUILTIN_READS:
        if name in fc.module_bound or name in fc.new_module_names:
            fc.problems.append(f"{rel}: builtin {name!r} is rebound")
    for fname in entry_functions:
        fc.check_entry_function_prologue(fname)
    if len(entry_other) > 1:
        for scope, src in entry_other:
            fc.fail(scope, src, "more than one non-capture addition in the "
                    "entry-point block")
    elif entry_other:
        fc.count("entry-point flush wrapper")
    summary = {"additions": len(aligned.additions),
               "deletions": len(aligned.deletions),
               "records": sorted(set().union(*records.values()))
               if records else [],
               "forms": fc.forms}
    return summary, fc.problems, fc


def check_containers(checks):
    """A counter or append must act on a key that every store factory (or the
    record literal) initialises as an int or an empty list, and that no
    capture assignment rebinds, in any of the three files. Otherwise the key
    could hold a production object (an append would mutate it) or be missing
    (a KeyError would change control flow)."""
    problems = []
    store_assigned = set().union(*(fc.store_assigned for fc in checks))
    tracker = next(fc for fc in checks if fc.rel == "engine/tracker.py")
    for fc in checks:
        factories = tracker.factories + (
            [] if fc is tracker else fc.factories)
        for kind, fname, (root, key), scope, src in fc.container_uses:
            if root == STORE:
                inits = factories
                rebound = key in store_assigned
            else:
                inits = [fc.record_inits.get((fname, root), {})]
                rebound = (fname, root, key) in fc.record_assigned
            if not inits or any(i.get(key) != kind for i in inits):
                problems.append(
                    f"{fc.rel} [{scope}]\n    {src.splitlines()[0][:100]}\n"
                    f"    {root}[{key!r}] is not initialised as "
                    f"{'an int' if kind == 'int' else 'an empty list'} by "
                    f"every {'store factory' if root == STORE else 'record literal'}")
            if rebound:
                problems.append(
                    f"{fc.rel} [{scope}]\n    {src.splitlines()[0][:100]}\n"
                    f"    {root}[{key!r}] is also assigned by a capture "
                    "statement, so it may not hold its initial container")
    return problems


def _load_infrastructure():
    try:
        return importlib.import_module(INFRA_TABLE).INFRASTRUCTURE
    except (ImportError, AttributeError):
        return None


def _label(src):
    lines = [x.strip() for x in src.splitlines() if x.strip()]
    if lines[0] == "try:" and len(lines) > 1:
        return f"try: {lines[1]}"[:70]
    return lines[0][:70]


def check_infrastructure(checks, table):
    """W2 and W3 (review round 1): module-level additions and the
    entry-point flush wrapper are accepted only as an exact, separately
    source-reviewed registration. Each (file, scope, digest) must be in
    INFRASTRUCTURE with its count; a fresh private name alone is not enough,
    because it says nothing about side effects (`_p = save_trades([])`)."""
    problems = []
    found, labels = {}, {}
    for fc in checks:
        for scope, dig, src in fc.infrastructure:
            key = (fc.rel, scope, dig)
            found[key] = found.get(key, 0) + 1
            labels[key] = _label(src)
    rels = {fc.rel for fc in checks}
    for key, n in sorted(found.items()):
        want = table.get(key)
        if want is None:
            problems.append(f"UNREGISTERED INFRASTRUCTURE {key[0]} [{key[1]}]"
                            f"\n    {labels[key]}  ({n}x)  {key[2]}")
        elif want[1] != n:
            problems.append(f"INFRASTRUCTURE COUNT MISMATCH {key[0]} "
                            f"[{key[1]}]\n    {labels[key]}  registered "
                            f"{want[1]}x, found {n}x")
    for key, (label, n) in sorted(table.items()):
        if key[0] in rels and key not in found:
            problems.append(f"REGISTERED INFRASTRUCTURE MISSING {key[0]} "
                            f"[{key[1]}]\n    {label}  registered {n}x, "
                            "found 0x")
    return problems


def register(root):
    checks = []
    for rel, base_rel, pinned, entries in PAIRS:
        summary, problems, fc = check_pair(root, rel, base_rel, pinned,
                                           entries)
        if summary is None:
            for p in problems:
                print(f"# BASE: {p}")
            return 2
        checks.append(fc)
    found, srcs = {}, {}
    for fc in checks:
        for scope, dig, src in fc.infrastructure:
            key = (fc.rel, scope, dig)
            found[key] = found.get(key, 0) + 1
            srcs[key] = src
    print("INFRASTRUCTURE = {")
    for key, n in sorted(found.items()):
        label = _label(srcs[key])
        print(f"    ({key[0]!r},\n     {key[1]!r},\n     {key[2]!r}):\n"
              f"        ({label!r}, {n}),")
    print("}")
    return 0


def main(root):
    total_problems = []
    checks = []
    missing = False
    infra = _load_infrastructure()
    if infra is None:
        print("  RESULT : NOT PROVEN -- infrastructure table not loadable")
        return 2
    print(f"  root       : {root}")
    for rel, base_rel, pinned, entries in PAIRS:
        summary, problems, fc = check_pair(root, rel, base_rel, pinned,
                                           entries)
        if summary is None:
            missing = True
            for p in problems:
                print(f"  BASE: {p}")
            continue
        checks.append(fc)
        forms = ", ".join(f"{k} {v}" for k, v in sorted(summary["forms"].items()))
        print(f"  {rel}: {summary['additions']} additions, "
              f"{summary['deletions']} deletions vs {base_rel}")
        print(f"    forms    : {forms}")
        if summary["records"]:
            print(f"    records  : {', '.join(summary['records'])}")
        total_problems.extend(problems)
    if not missing:
        total_problems.extend(check_containers(checks))
        total_problems.extend(check_infrastructure(checks, infra))
        n_infra = sum(len(fc.infrastructure) for fc in checks)
        print(f"  infrastructure : {n_infra} found, "
              f"{sum(n for _l, n in infra.values())} registered")
    for p in total_problems:
        print(f"  PROBLEM: {p}")
    if missing:
        print("  RESULT : NOT PROVEN -- a pinned base is missing or differs")
        return 2
    if total_problems:
        print("  RESULT : NOT PROVEN -- a capture statement is outside the "
              "whitelist")
        return 1
    print("  RESULT : every admission addition matches a permitted form")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--register":
        sys.exit(register(argv[1] if len(argv) > 1 else DEFAULT_ROOT))
    if len(argv) > 1:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(argv[0] if argv else DEFAULT_ROOT))
