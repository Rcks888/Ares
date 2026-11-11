#!/usr/bin/env python3
"""Writer inventory and save data flow (design §6.2, extension 3; §3.5).

    python3 tools/verify_writer_inventory.py [ROOT]
    python3 tools/verify_writer_inventory.py --register [ROOT]

Production files: ROOT/*.py and ROOT/engine/*.py, as for the call-site
inventory. The registered tables are in tools/admission_writer_inventory.py.

W-A  Save sites. The calls to save_pending, save_trades, save_queue and
     save_ranked equal SAVE_SITES, keyed by (file, enclosing scope,
     normalised call source including the argument). Any other reference to
     the four names (alias, getattr by name, renaming import) fails. The eight
     save_*/load_* names are defined once, at module level of
     engine/tracker.py, never rebound, and imported only from engine.tracker.
W-B  Save functions. Each save_* definition equals SAVE_FUNCTIONS: it opens
     its own state file with 'w' and json.dump()s its parameter. §3.5's
     reading of the disk state assumes exactly that.
W-C  File writers. Every write-capable call (open/.open with a mode other
     than read, write_text, unlink, to_csv, os/shutil/subprocess/tempfile
     calls, through `import x as y` or `from x import f as g` as well, ...)
     equals FILE_WRITERS. Syntactic only: getattr dispatch, exec, or a
     write inside a third-party library is not seen. A write-mode open of a state file
     (PENDING_FILE, TRADES_FILE, QUEUE_FILE, RANKED_FILE or the four file
     names) outside its own save function fails regardless of registration.
W-D  Save data flow. For every save call, the provenance of its argument may
     only be: the matching load_*() call in the same function (save_ranked:
     load_ranked or load_queue), an empty list literal, or a registered
     carrier edge (CARRIER_EDGES) that names this save. Provenance follows
     local bindings, loop and comprehension targets, slices, `+`, list(),
     sorted(), dict(), .copy(), .items(). A list created empty in the
     function carries what is appended to it; a loaded list's appended
     entries are the mutation its save owns and are not traced. A parameter,
     a global, any other call's return value, or a non-empty literal fails.
W-E  Carrier premise. Each registered carrier edge occurs exactly once,
     outside every try and with in its function; every save call in the
     owning function is outside every try and with.
W-F  Trades list. In every production function, a name bound from
     load_trades() is not rebound, aliased, captured by a nested scope,
     passed to a call other than a read-only builtin or save_trades, or
     mutated (method call, item/slice store, del, +=), except the sites in
     TRADE_LIST_MUTATIONS (today: execute_pending_signals' trades.append).
     `return <name>` is allowed (check_open_trades): in the caller the list
     is a return value, so a save of it fails W-D, and a mutation without a
     save changes no file.

Exit 0 only if every check holds; 1 on any problem; 2 if the registered
tables cannot be loaded.
"""

import ast
import glob
import importlib
import os
import sys

SAVES = ("save_pending", "save_trades", "save_queue", "save_ranked")
LOADS = ("load_pending", "load_trades", "load_queue", "load_ranked")
SAVE_LOADS = {"save_pending": ("load_pending",),
              "save_trades": ("load_trades",),
              "save_queue": ("load_queue",),
              "save_ranked": ("load_ranked", "load_queue")}
STATE_CONSTANTS = {"PENDING_FILE": "save_pending", "TRADES_FILE": "save_trades",
                   "QUEUE_FILE": "save_queue", "RANKED_FILE": "save_ranked"}
STATE_FILENAMES = {"pending_signals.json": "save_pending",
                   "virtual_trades.json": "save_trades",
                   "signal_queue.json": "save_queue",
                   "queue_ranked.json": "save_ranked"}
TRACKER = "engine/tracker.py"
TRACKER_MODULES = ("engine.tracker",)

WRITE_METHODS = {"write_text", "write_bytes", "touch", "unlink", "rename",
                 "rmdir", "symlink_to", "hardlink_to", "chmod", "to_csv",
                 "to_json", "to_pickle", "to_excel", "to_parquet", "to_hdf",
                 "to_feather", "savefig"}
OS_WRITERS = {"remove", "unlink", "rename", "renames", "replace", "rmdir",
              "removedirs", "truncate", "write", "system", "popen", "link",
              "symlink", "chmod", "chown", "ftruncate"}
SHUTIL_READERS = {"disk_usage", "which", "get_terminal_size"}
WHOLE_MODULE_WRITERS = ("subprocess", "tempfile")

PROVENANCE_CALLS = ("dict", "list", "sorted", "tuple", "reversed")
PROVENANCE_METHODS = ("copy", "items", "values", "keys")
TRADE_READ_METHODS = ("index", "count", "copy")
TRADE_READ_CALLS = {"len", "save_trades", "enumerate", "sorted", "list",
                    "reversed", "zip", "sum", "any", "all", "min", "max", "iter"}
OPAQUE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(HERE)


def production_files(root):
    files = sorted(glob.glob(os.path.join(root, "*.py"))
                   + glob.glob(os.path.join(root, "engine", "*.py")))
    return [os.path.relpath(f, root) for f in files]


def _callee(call):
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _is_main(node):
    return (isinstance(node, ast.If) and ast.unparse(node.test)
            in ("__name__ == '__main__'", '__name__ == "__main__"'))


def _local_nodes(fn):
    """Nodes of fn's own scope: nested defs, lambdas and classes excluded."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, OPAQUE):
                stack.append(child)


def _nested_nodes(fn):
    for node in _local_nodes(fn):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, OPAQUE):
                yield from ast.walk(child)


def _mentions_state(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in STATE_CONSTANTS:
            return STATE_CONSTANTS[sub.id]
        if isinstance(sub, ast.Attribute) and sub.attr in STATE_CONSTANTS:
            return STATE_CONSTANTS[sub.attr]
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            for name, save in STATE_FILENAMES.items():
                if name in sub.value:
                    return save
    return None


def _module_writer(module, name):
    if module == "os" and name in OS_WRITERS:
        return "os"
    if module == "shutil" and name not in SHUTIL_READERS:
        return "shutil"
    if module in WHOLE_MODULE_WRITERS:
        return module
    return None


def _open_mode(call, method):
    args = call.args[0:] if method else call.args[1:]
    mode = None
    if args:
        mode = args[0]
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    if mode is None:
        return "r"
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return mode.value
    return None


class _Scanner(ast.NodeVisitor):
    """W-A, W-C and the definition checks for one production file."""

    def __init__(self, relpath, tree):
        self.relpath = relpath
        self.scope = ["<module>"]
        self.saves = {}
        self.writers = {}
        self.problems = []
        self.defs = []
        self.save_defs = {}
        self.modules = {}
        self.imported = {}
        self._call_funcs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.modules[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    self.imported[a.asname or a.name] = (node.module, a.name)

    def _where(self):
        return "/".join(self.scope)

    def _problem(self, what):
        self.problems.append(f"{what} {self.relpath} [{self._where()}]")

    def _nest(self, label, node):
        self.scope.append(label)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        if node.name in SAVES + LOADS:
            self.defs.append((self.relpath, self._where(), node.name))
            if node.name in SAVES and self.scope == ["<module>"]:
                self.save_defs[node.name] = ast.unparse(node)
        self._nest(f"def {node.name}()", node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        if node.name in SAVES + LOADS:
            self.defs.append((self.relpath, self._where(), node.name))
        self._nest(f"class {node.name}", node)

    def visit_If(self, node):
        if _is_main(node):
            self._nest("if __name__ == '__main__'", node)
        else:
            self.generic_visit(node)

    def _writer_kind(self, node):
        f = node.func
        if isinstance(f, ast.Name) and f.id == "open":
            mode = _open_mode(node, method=False)
            return None if mode is not None and not set(mode) & set("wax+") else "open"
        if isinstance(f, ast.Name) and f.id in self.imported:
            module, name = self.imported[f.id]
            return _module_writer(module.split(".")[0], name)
        if not isinstance(f, ast.Attribute):
            return None
        if f.attr == "open":
            mode = _open_mode(node, method=True)
            return None if mode is not None and not set(mode) & set("wax+") else "open"
        kind = _module_writer(self.modules.get(ast.unparse(f.value)), f.attr)
        if kind:
            return kind
        if f.attr in WRITE_METHODS:
            return "method"
        return None

    def visit_Call(self, node):
        name = _callee(node)
        if name in SAVES:
            self._call_funcs.add(id(node.func))
            key = (self.relpath, self._where(), ast.unparse(node))
            self.saves[key] = self.saves.get(key, 0) + 1
        kind = self._writer_kind(node)
        if kind:
            key = (self.relpath, self._where(), ast.unparse(node))
            self.writers[key] = self.writers.get(key, 0) + 1
            state = _mentions_state(node)
            if state and kind == "open" and not (
                    self.relpath == TRACKER
                    and self.scope == ["<module>", f"def {state}()"]):
                self._problem(f"STATE FILE OPENED FOR WRITING OUTSIDE "
                              f"{state}(): {ast.unparse(node)} in")
            elif state and kind != "open":
                self._problem(f"STATE FILE WRITTEN BY {kind}: "
                              f"{ast.unparse(node)} in")
        if (isinstance(node.func, ast.Name) and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in SAVES + LOADS):
            self._problem(f"getattr by name: {ast.unparse(node)} in")
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in SAVES and id(node) not in self._call_funcs:
            self._problem(f"non-call reference: {node.id} "
                          f"({type(node.ctx).__name__}) in")
        elif node.id in LOADS and not isinstance(node.ctx, ast.Load):
            self._problem(f"rebinding: {node.id} in")

    def visit_Attribute(self, node):
        if node.attr in SAVES and id(node) not in self._call_funcs:
            self._problem(f"non-call reference: {ast.unparse(node)} in")
        self.generic_visit(node)

    def _imports(self, node, module):
        for a in node.names:
            base = a.name.rsplit(".", 1)[-1]
            if base not in SAVES + LOADS:
                continue
            if a.asname and a.asname != base:
                self._problem(f"renaming import: {a.name} as {a.asname} in")
            elif module not in TRACKER_MODULES:
                self._problem(f"import of {base} from {module} in")

    def visit_Import(self, node):
        self._imports(node, None)

    def visit_ImportFrom(self, node):
        self._imports(node, node.module)


def _parents(fn):
    parent = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
    return parent


def _guarded(node, fn, parent):
    """The try or with statement around node inside fn, if any."""
    cur = parent.get(id(node))
    while cur is not None and cur is not fn:
        if isinstance(cur, (ast.Try, ast.With, ast.AsyncWith)) or (
                hasattr(ast, "TryStar") and isinstance(cur, ast.TryStar)):
            return cur
        cur = parent.get(id(cur))
    return None


class _Flow:
    """Flow-insensitive provenance of names in one function (W-D)."""

    def __init__(self, relpath, fn, carriers):
        self.fn = fn
        self.params = {a.arg for a in (fn.args.posonlyargs + fn.args.args
                                       + fn.args.kwonlyargs)}
        for a in (fn.args.vararg, fn.args.kwarg):
            if a:
                self.params.add(a.arg)
        self.declared = set()
        self.bindings = {}
        self.appended = {}
        self.fresh = set()
        self.carrier_hits = {}
        edges = {k[3]: k for k in carriers
                 if k[0] == relpath and k[1] == fn.name}
        self.edges = {k[2]: (src, k) for src, k in edges.items()}
        for node in _local_nodes(fn):
            self._collect(node)

    def _bind(self, target, kind, expr):
        if isinstance(target, ast.Name):
            if (kind == "value" and isinstance(expr, ast.Call)
                    and target.id in self.edges
                    and ast.unparse(expr) == self.edges[target.id][0]):
                key = self.edges[target.id][1]
                self.carrier_hits[key] = self.carrier_hits.get(key, 0) + 1
                self.bindings.setdefault(target.id, []).append(("carrier", key))
                return
            if (kind == "value" and isinstance(expr, ast.List)
                    and not expr.elts):
                self.fresh.add(target.id)
            self.bindings.setdefault(target.id, []).append((kind, expr))
        elif isinstance(target, (ast.Tuple, ast.List)):
            if (kind == "value" and isinstance(expr, (ast.Tuple, ast.List))
                    and len(expr.elts) == len(target.elts)
                    and not any(isinstance(e, ast.Starred)
                                for e in target.elts + expr.elts)):
                for t, e in zip(target.elts, expr.elts):
                    self._bind(t, "value", e)
            else:
                for t in target.elts:
                    self._bind(t, "elem", expr)
        elif isinstance(target, ast.Starred):
            self._bind(target.value, "elem", expr)

    def _collect(self, node):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            self.declared.update(node.names)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                self._bind(t, "value", node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            self._bind(node.target, "value", node.value)
        elif isinstance(node, ast.AugAssign):
            self._bind(node.target, "aug", node.value)
        elif isinstance(node, ast.NamedExpr):
            self._bind(node.target, "value", node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._bind(node.target, "elem", node.iter)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    self._bind(item.optional_vars, "other", item.context_expr)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self.bindings.setdefault(node.name, []).append(("other", node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                name = a.asname or a.name.split(".")[0]
                self.bindings.setdefault(name, []).append(("other", node))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name)
              and node.func.attr in ("append", "insert", "extend")
              and node.args):
            self.appended.setdefault(node.func.value.id, []).append(node.args[-1])
        for child in ast.iter_child_nodes(node):
            if isinstance(child, OPAQUE) and getattr(child, "name", None):
                self.bindings.setdefault(child.name, []).append(("other", child))

    def of(self, expr, env=None, stack=()):
        env = env or {}
        P = lambda e: self.of(e, env, stack)                  # noqa: E731
        if isinstance(expr, ast.Name):
            n = expr.id
            if n in env:
                return env[n]
            if n in self.declared:
                return {f"global {n}"}
            if n in self.params:
                return {f"parameter {n}"}
            if n not in self.bindings:
                return {f"global {n}"}
            if n in stack:
                return set()
            inner = stack + (n,)
            tags = set()
            for kind, e in self.bindings[n]:
                if kind == "carrier":
                    tags.add(f"carrier {e[1]}.{e[2]} = {e[3]}")
                elif kind == "other":
                    tags.add(f"opaque binding of {n}")
                else:
                    tags |= self.of(e, env, inner)
            if n in self.fresh:
                for e in self.appended.get(n, ()):
                    tags |= self.of(e, env, inner)
            return tags
        if isinstance(expr, ast.Call):
            name = _callee(expr)
            if name in LOADS and not expr.args and not expr.keywords:
                return {f"load {name}"}
            if (isinstance(expr.func, ast.Name) and name in PROVENANCE_CALLS
                    and len(expr.args) == 1
                    and not isinstance(expr.args[0], ast.Starred)
                    and all(k.arg == "key" for k in expr.keywords)):
                return P(expr.args[0])
            if (isinstance(expr.func, ast.Attribute)
                    and name in PROVENANCE_METHODS
                    and not expr.args and not expr.keywords):
                return P(expr.func.value)
            return {f"return value of {ast.unparse(expr.func)}()"}
        if isinstance(expr, (ast.List, ast.Tuple)):
            return {"empty"} if not expr.elts else {"non-empty literal"}
        if isinstance(expr, ast.Subscript):
            return P(expr.value)
        if isinstance(expr, ast.Starred):
            return P(expr.value)
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return P(expr.left) | P(expr.right)
        if isinstance(expr, ast.IfExp):
            return P(expr.body) | P(expr.orelse)
        if isinstance(expr, (ast.ListComp, ast.SetComp, ast.GeneratorExp,
                             ast.DictComp)):
            local = dict(env)
            tags = set()
            for gen in expr.generators:
                it = self.of(gen.iter, local, stack)
                tags |= it
                for t in ast.walk(gen.target):
                    if isinstance(t, ast.Name):
                        local[t.id] = it
            if isinstance(expr, ast.DictComp):
                parts = (expr.key, expr.value)
            else:
                parts = (expr.elt,)
            for p in parts:
                tags |= self.of(p, local, stack)
            return tags
        return {f"{type(expr).__name__} expression"}


def _functions(tree):
    """(scope label, FunctionDef) for every function, nested ones included."""
    out = []

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                label = scope + [f"def {child.name}()"]
                out.append(("/".join(label), child))
                walk(child, label)
            elif isinstance(child, ast.ClassDef):
                walk(child, scope + [f"class {child.name}"])
            elif _is_main(child):
                walk(child, scope + ["if __name__ == '__main__'"])
            else:
                walk(child, scope)
    walk(tree, ["<module>"])
    return out


def _check_flow(rel, tree, carriers, flows, problems, edge_hits):
    for scope, fn in _functions(tree):
        calls = [n for n in _local_nodes(fn)
                 if isinstance(n, ast.Call) and _callee(n) in SAVES]
        if not calls and not any(k[0] == rel and k[1] == fn.name
                                 for k in carriers):
            continue
        flow = _Flow(rel, fn, carriers)
        for k, n in flow.carrier_hits.items():
            edge_hits[k] = edge_hits.get(k, 0) + n
        for call in calls:
            save = _callee(call)
            src = ast.unparse(call)
            if len(call.args) != 1 or call.keywords:
                problems.append(f"SAVE CALL NOT OF ONE POSITIONAL ARGUMENT "
                                f"{rel} [{scope}]\n    {src}")
                continue
            tags = flow.of(call.args[0])
            allowed = {f"load {x}" for x in SAVE_LOADS[save]} | {"empty"}
            for key, edge in carriers.items():
                if key[0] == rel and key[1] == fn.name and save in edge["saves"]:
                    allowed.add(f"carrier {key[1]}.{key[2]} = {key[3]}")
            bad = sorted(tags - allowed)
            if bad or not tags:
                problems.append(f"SAVE ARGUMENT NOT FROM A LOAD IN THE SAME "
                                f"FUNCTION {rel} [{scope}]\n    {src}  <- "
                                f"{', '.join(bad) or 'no provenance'}")
            else:
                flows.append((rel, scope, src, sorted(tags)))
    for scope, fn in _functions(tree):
        parent = _parents(fn)
        for key, edge in carriers.items():
            if key[0] != rel:
                continue
            if fn.name == key[1]:
                for node in _local_nodes(fn):
                    if (isinstance(node, ast.Assign)
                            and ast.unparse(node.value) == key[3]
                            and any(isinstance(t, ast.Name) and t.id == key[2]
                                    for t in node.targets)):
                        g = _guarded(node, fn, parent)
                        if g is not None:
                            problems.append(
                                f"CARRIER EDGE INSIDE {type(g).__name__.upper()} "
                                f"{rel} [{scope}]\n    {ast.unparse(node)}")
            if fn.name == edge["owner"] and scope == f"<module>/def {fn.name}()":
                owned = [n for n in _local_nodes(fn)
                         if isinstance(n, ast.Call) and _callee(n) in SAVES]
                if not owned:
                    problems.append(f"CARRIER OWNER HAS NO SAVE CALL "
                                    f"{rel} [{scope}]")
                for call in owned:
                    g = _guarded(call, fn, parent)
                    if g is not None:
                        problems.append(
                            f"CARRIER OWNER SAVE INSIDE {type(g).__name__.upper()} "
                            f"{rel} [{scope}]\n    {ast.unparse(call)}")


def _check_trades(rel, tree, mutations, problems):
    for scope, fn in _functions(tree):
        names = set()
        for node in _local_nodes(fn):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and _callee(node.value) == "load_trades"):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        if not names:
            continue
        flow = _Flow(rel, fn, {})
        parent = _parents(fn)
        where = f"{rel} [{scope}]"
        for n in sorted(names):
            for kind, e in flow.bindings.get(n, ()):
                if not (kind == "value" and isinstance(e, ast.Call)
                        and _callee(e) == "load_trades" and not e.args):
                    problems.append(f"TRADES LIST REBOUND {where}\n    {n} <- "
                                    f"{ast.unparse(e) if isinstance(e, ast.AST) else kind}")
        for node in _nested_nodes(fn):
            if isinstance(node, ast.Name) and node.id in names:
                problems.append(f"TRADES LIST CAPTURED BY A NESTED SCOPE "
                                f"{where}\n    {node.id}")
        for node in _local_nodes(fn):
            if not (isinstance(node, ast.Name) and node.id in names
                    and isinstance(node.ctx, ast.Load)):
                continue
            up = parent.get(id(node))
            stmt = up
            while stmt is not None and not isinstance(stmt, ast.stmt):
                stmt = parent.get(id(stmt))
            text = ast.unparse(stmt) if stmt is not None else node.id
            text = text.splitlines()[0]
            if isinstance(up, ast.Attribute) and up.value is node:
                call = parent.get(id(up))
                if isinstance(call, ast.Call) and call.func is up:
                    if up.attr in TRADE_READ_METHODS:
                        continue
                    key = (rel, scope, ast.unparse(call))
                    mutations[key] = mutations.get(key, 0) + 1
                    continue
                problems.append(f"TRADES LIST ATTRIBUTE USE {where}\n    {text}")
            elif isinstance(up, ast.Subscript) and up.value is node:
                if not isinstance(up.ctx, ast.Load):
                    problems.append(f"TRADES LIST ITEM STORE OR DEL {where}\n"
                                    f"    {text}")
            elif isinstance(up, (ast.For, ast.AsyncFor)) and up.iter is node:
                continue
            elif isinstance(up, ast.comprehension) and up.iter is node:
                continue
            elif (isinstance(up, ast.Call) and node in up.args
                  and isinstance(up.func, ast.Name)
                  and up.func.id in TRADE_READ_CALLS):
                continue
            elif isinstance(up, (ast.Compare, ast.BoolOp, ast.UnaryOp,
                                 ast.FormattedValue)):
                continue
            elif isinstance(up, ast.Return) and up.value is node:
                continue
            elif isinstance(up, (ast.If, ast.While, ast.IfExp, ast.Assert)) \
                    and up.test is node:
                continue
            else:
                problems.append(f"TRADES LIST ESCAPES OR IS ALIASED {where}\n"
                                f"    {text}")
        for node in _local_nodes(fn):
            if (isinstance(node, ast.AugAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id in names):
                problems.append(f"TRADES LIST AUGMENTED {where}\n    "
                                f"{ast.unparse(node)}")


def _compare(label, found, registered, problems):
    for key, n in sorted(found.items()):
        want = registered.get(key)
        if want is None:
            problems.append(f"UNREGISTERED {label} {key[0]} [{key[1]}]\n"
                            f"    {key[2]}  ({n}x)")
        elif want != n:
            problems.append(f"{label} COUNT MISMATCH {key[0]} [{key[1]}]\n"
                            f"    {key[2]}  registered {want}x, found {n}x")
    for key, want in sorted(registered.items()):
        if key not in found:
            problems.append(f"REGISTERED {label} MISSING {key[0]} [{key[1]}]\n"
                            f"    {key[2]}  registered {want}x, found 0x")


def scan(root, carriers):
    res = {"saves": {}, "writers": {}, "defs": [], "save_defs": {},
           "mutations": {}, "flows": [], "edge_hits": {}, "problems": []}
    for rel in production_files(root):
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=rel)
        s = _Scanner(rel, tree)
        s.visit(tree)
        for k, n in s.saves.items():
            res["saves"][k] = res["saves"].get(k, 0) + n
        for k, n in s.writers.items():
            res["writers"][k] = res["writers"].get(k, 0) + n
        res["defs"].extend(s.defs)
        if rel == TRACKER:
            res["save_defs"] = s.save_defs
        res["problems"].extend(s.problems)
        _check_flow(rel, tree, carriers, res["flows"], res["problems"],
                    res["edge_hits"])
        _check_trades(rel, tree, res["mutations"], res["problems"])
    return res


def _load_tables():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        mod = importlib.import_module("admission_writer_inventory")
        return (mod.SAVE_SITES, mod.SAVE_FUNCTIONS, mod.FILE_WRITERS,
                mod.CARRIER_EDGES, mod.TRADE_LIST_MUTATIONS)
    except (ImportError, AttributeError):
        return None


def _print_table(name, table):
    print(f"{name} = {{")
    for (rel, scope, src), n in sorted(table.items()):
        print(f"    ({rel!r},\n     {scope!r},\n     {src!r}):\n        {n},")
    print("}")


def register(root):
    tables = _load_tables()
    carriers = tables[3] if tables else {}
    res = scan(root, carriers)
    _print_table("SAVE_SITES", res["saves"])
    print("SAVE_FUNCTIONS = {")
    for name, src in sorted(res["save_defs"].items()):
        print(f"    {name!r}:\n        {src!r},")
    print("}")
    _print_table("FILE_WRITERS", res["writers"])
    _print_table("TRADE_LIST_MUTATIONS", res["mutations"])
    for p in res["problems"]:
        print(f"# WARNING, not registrable: {p}")
    return 1 if res["problems"] else 0


def main(root):
    tables = _load_tables()
    if tables is None:
        print("  RESULT : NOT PROVEN -- writer inventory tables not loadable")
        return 2
    save_sites, save_functions, file_writers, carriers, trade_mutations = tables
    res = scan(root, carriers)
    problems = list(res["problems"])
    _compare("SAVE SITE", res["saves"], save_sites, problems)
    _compare("FILE WRITER", res["writers"], file_writers, problems)
    _compare("TRADES LIST MUTATION", res["mutations"], trade_mutations, problems)
    for name in SAVES + LOADS:
        where = [d for d in res["defs"] if d[2] == name]
        if where != [(TRACKER, "<module>", name)]:
            problems.append(f"DEFINITION OF {name} NOT EXACTLY ONCE AT MODULE "
                            f"LEVEL OF {TRACKER}: {where}")
    for name in SAVES:
        if res["save_defs"].get(name) != save_functions.get(name):
            problems.append(f"SAVE FUNCTION DIFFERS FROM REGISTERED SOURCE "
                            f"{TRACKER} {name}")
    for key in carriers:
        n = res["edge_hits"].get(key, 0)
        if n != 1:
            problems.append(f"REGISTERED CARRIER EDGE FOUND {n}x, EXPECTED 1x "
                            f"{key[0]} [{key[1]}]\n    {key[2]} = {key[3]}")
    print(f"  root        : {root}")
    print(f"  files       : {len(production_files(root))} production .py")
    print(f"  save sites  : {sum(res['saves'].values())} found, "
          f"{sum(save_sites.values())} registered")
    print(f"  writers     : {sum(res['writers'].values())} found, "
          f"{sum(file_writers.values())} registered")
    print(f"  save funcs  : {len(save_functions)} registered")
    print(f"  carriers    : {len(carriers)} registered")
    print(f"  trades muts : {sum(res['mutations'].values())} found, "
          f"{sum(trade_mutations.values())} registered")
    for rel, scope, src, tags in res["flows"]:
        print(f"    flow     : {src}   [{rel} {scope}] <- {' | '.join(tags)}")
    for p in problems:
        print(f"  PROBLEM: {p}")
    if problems:
        print("  RESULT : NOT PROVEN -- writer inventory or save data flow differs")
        return 1
    print("  RESULT : writer inventory, save functions and save data flow "
          "match the registered tables")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--register":
        sys.exit(register(argv[1] if len(argv) > 1 else DEFAULT_ROOT))
    if len(argv) > 1:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(argv[0] if argv else DEFAULT_ROOT))
