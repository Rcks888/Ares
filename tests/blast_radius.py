"""THE single authoritative production/parity boundary contract.

Replaces four hand-maintained grep allow-lists that had to be edited every time a
Phase 0.5 test file was added, and that could be tripped by a comment, a
docstring, or a prose mention of a module name. Structural questions are answered
from syntax nodes; text never participates.

THE INVARIANT

    daily_report.py
        |
        v
    engine/parity_hook.py          <-- the ONLY production bridge
        |
        v
    parity_runner / parity_eval / parity_compare / tracker_compat

No other production module may import, reference or call the parity stack. Inside
the stack, normal parity-internal dependencies are unrestricted.

WHY THIS IS NOT A BOOLEAN
Every finding carries file, line, node type, referenced symbol and verdict, so a
failure says WHERE and WHAT rather than merely that something is wrong.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The parity stack. Membership here means "internal to parity", not "authorized
# for production use".
PARITY_MODULES = frozenset({
    "parity_hook", "parity_runner", "parity_eval", "parity_compare",
    "tracker_compat",
})

# The one production module permitted to cross the boundary, and the one module
# it is permitted to cross into.
AUTHORIZED_BRIDGE = ("daily_report.py", "parity_hook")

# Non-production consumers. Tests and the snapshot tool legitimately import the
# stack directly; they are not part of the production blast radius.
NON_PRODUCTION_DIRS = ("tests/", "tools/")

# The tracker surface parity_hook is pinned to. A new private dependency must
# fail until it is reviewed and added here deliberately.
PINNED_TRACKER_SURFACE = frozenset({
    "_load_params", "_LAST_EVAL", "check_open_trades", "load_trades",
})

SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules", "vendor"}


def python_files(root=None):
    root = Path(root or ROOT)
    for p in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def rel(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def is_production(relpath):
    return not any(relpath.startswith(d) for d in NON_PRODUCTION_DIRS)


def _parity_name(dotted):
    """Return the parity module named by a dotted path, or None."""
    if not dotted:
        return None
    for part in str(dotted).split("."):
        if part in PARITY_MODULES:
            return part
    return None


def scan_file(path, source=None):
    """Structural parity references in one file.

    Returns a list of findings; each is a dict with file, line, node, symbol.
    Only syntax nodes are inspected: Import, ImportFrom, Attribute access on an
    imported parity module, Call targets, and dynamic-import string arguments.
    """
    relpath = rel(path)
    src = source if source is not None else Path(path).read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [{"file": relpath, "line": exc.lineno or 0, "node": "SyntaxError",
                 "symbol": str(exc)}]

    found = []
    local_aliases = {}

    def add(node, kind, symbol):
        found.append({"file": relpath, "line": getattr(node, "lineno", 0),
                      "node": kind, "symbol": symbol})

    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                pm = _parity_name(a.name)
                if pm:
                    local_aliases[(a.asname or a.name).split(".")[0]] = pm
                    add(n, "Import", pm)
        elif isinstance(n, ast.ImportFrom):
            pm_mod = _parity_name(n.module)
            for a in n.names:
                pm = _parity_name(a.name) or pm_mod
                if pm:
                    local_aliases[a.asname or a.name] = pm
                    add(n, "ImportFrom", pm)
        elif isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ("import_module", "__import__"):
                for arg in n.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        pm = _parity_name(arg.value)
                        if pm:
                            add(n, "DynamicImport", pm)
            # getattr(module, "name") accesses an attribute through a STRING, so
            # it is invisible to an Attribute-node scan. This is a genuine blind
            # spot: parity_hook reads tracker._LAST_EVAL exactly this way, and an
            # earlier version of this scanner reported the pinned tracker surface
            # WITHOUT _LAST_EVAL as a result.
            elif name == "getattr" and len(n.args) >= 2:
                base = n.args[0]
                attr = n.args[1]
                if (isinstance(attr, ast.Constant)
                        and isinstance(attr.value, str)):
                    bname = getattr(base, "id", None) or getattr(base, "attr", None)
                    pm = _parity_name(bname)
                    if pm:
                        add(n, "GetattrString", f"{pm}.{attr.value}")

    # Attribute access through an alias bound above, e.g. pr.observe_cycle.
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            pm = local_aliases.get(n.value.id)
            if pm:
                add(n, "Attribute", f"{pm}.{n.attr}")
    return found


def verdict(finding):
    """authorized / unauthorized, with the reason."""
    f, sym = finding["file"], finding["symbol"]
    target = sym.split(".")[0]
    if not is_production(f):
        return "authorized", "non-production consumer"
    # A file that IS part of the parity stack may reference the stack freely.
    # These modules live under engine/, so a naive "is it production?" test
    # misclassifies them -- the boundary is about ENTERING parity, not about
    # dependencies inside it.
    if Path(f).stem in PARITY_MODULES and target in PARITY_MODULES:
        return "authorized", "parity-internal"
    if f == AUTHORIZED_BRIDGE[0] and target == AUTHORIZED_BRIDGE[1]:
        return "authorized", "the authorized production bridge"
    if is_production(f) and target in PARITY_MODULES:
        # A production module other than the bridge, or the bridge reaching past
        # parity_hook into the stack directly.
        return "unauthorized", (
            f"production module {f} references {target}; production may enter "
            f"parity only via {AUTHORIZED_BRIDGE[0]} -> {AUTHORIZED_BRIDGE[1]}")
    return "authorized", "no parity reference"


def audit(root=None):
    """Full-repo structural audit. Returns (findings, unauthorized)."""
    findings, seen = [], set()
    for p in python_files(root):
        for fi in scan_file(p):
            key = (fi["file"], fi["line"], fi["node"], fi["symbol"])
            if key not in seen:
                seen.add(key)
                findings.append(fi)
    bad = []
    for fi in findings:
        v, why = verdict(fi)
        fi["verdict"], fi["reason"] = v, why
        if v == "unauthorized":
            bad.append(fi)
    return findings, bad


def describe(findings):
    """Human-readable diagnostics, one per line."""
    return "\n".join(
        f"        {fi['file']}:{fi['line']} {fi['node']} {fi['symbol']}"
        f"  [{fi.get('verdict', '?')}] {fi.get('reason', '')}"
        for fi in findings)


def tracker_surface_used_by_hook():
    """Attributes parity_hook reads off engine.tracker.

    Covers BOTH access forms. Attribute nodes alone are insufficient:
    getattr(tracker, "_LAST_EVAL", None) names the attribute in a string, and
    omitting it would under-report the pinned surface -- making the pin look
    satisfied while an unreviewed private dependency existed.
    """
    tree = ast.parse((ROOT / "engine" / "parity_hook.py").read_text())
    surface = {n.attr for n in ast.walk(tree)
               if isinstance(n, ast.Attribute)
               and isinstance(n.value, ast.Name) and n.value.id == "tracker"}
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "getattr"
                and len(n.args) >= 2
                and getattr(n.args[0], "id", None) == "tracker"
                and isinstance(n.args[1], ast.Constant)
                and isinstance(n.args[1].value, str)):
            surface.add(n.args[1].value)
    return surface
