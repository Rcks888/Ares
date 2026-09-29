"""The parity output-path contract.

WHY THIS FILE EXISTS
--------------------
observe_cycle's signature was `output_path=DEFAULT_OUTPUT`. Python evaluates
default arguments ONCE, at function-definition time, so reassigning
parity_runner.DEFAULT_OUTPUT afterwards had no effect. A test redirecting output
that way would silently append fixture records to the real
logs/tracker_parity_v1.jsonl migration evidence while reporting a pass.

The signature is now `output_path=None`, resolved at invocation. This file pins
that contract, including the part that must NOT exist: there is no fallback to the
default when an explicit path fails.
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from engine import parity_compare as pc  # noqa: E402
from engine import parity_runner as pr  # noqa: E402

FAILS = []
COUNT = 0
PROD_FILE = ROOT / "logs" / "tracker_parity_v1.jsonl"


def check(label, cond, got=None):
    global COUNT
    COUNT += 1
    if cond:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {got if got is not None else ''}")
        FAILS.append(label)


def fingerprint(p=PROD_FILE):
    """Existence + size + checksum. Absence is itself a valid state."""
    p = Path(p)
    if not p.exists():
        return ("absent", None, None)
    data = p.read_bytes()
    return ("present", len(data), hashlib.md5(data).hexdigest())


def st(symbol="ABM"):
    return {"symbol": symbol, "status": "open", "strategy": "momentum_breakout",
            "entry_date": "2026-09-09", "entry_price": 50.65, "shares": 2.94,
            "original_shares": 2.94, "stop_loss": 48.67, "trailing_stop": 48.67,
            "peak_price": 50.91, "take_profit": 59.76, "scaled_out": False}


TOKEN = 3
GOOD = {"cycle_token": TOKEN, "price": 49.00, "price_source": "IBKR",
        "rsi": 55.0, "bearish_div": False, "bar_date": "2026-09-26"}


def cycle(output_path="__omit__", symbols=("ABM",), packets=None):
    """Run observe_cycle. output_path='__omit__' means do not pass it at all."""
    trades = [st(s) for s in symbols]
    state = {"cur": [dict(t) for t in trades]}

    def inline_call():
        return "INLINE_RESULT"

    def load_state():
        return [dict(t) for t in state["cur"]]

    pf = packets or (lambda: (TOKEN, {s: dict(GOOD) for s in symbols}))
    kw = {} if output_path == "__omit__" else {"output_path": output_path}
    return pr.observe_cycle(object(), inline_call, load_state,
                            lambda t, p, b: dict(t), packets_fn=pf,
                            params={"exit_policy": "A"},
                            lineage={"production_commit": "test"}, **kw)


def test_production_heartbeat_is_never_created_by_tests():
    """Structural guard, not a convention.

    The heartbeat writes on every observed cycle including zero-record and failed
    ones, so any observe_cycle call that forgets output_path appends to the
    production heartbeat. append_record never had this exposure, because it only
    fires per record -- which is precisely why the omission went unnoticed until
    the heartbeat existed.
    """
    prod = ROOT / "logs" / pr.HEARTBEAT_NAME

    def state():
        if not prod.exists():
            return ("absent", None, None)
        b = prod.read_bytes()
        return ("present", len(b), hashlib.md5(b).hexdigest())

    before = state()
    tmp = Path(tempfile.mkdtemp())
    cycle(output_path=str(tmp / "iso.jsonl"), symbols=("ABM",))
    after = state()
    # Unchanged, not absent: absence would be phase-dependent once the live host
    # has a heartbeat, and this suite must count the same on both hosts.
    check("a redirected cycle leaves the production heartbeat unchanged",
          before == after, (before, after))
    check("the redirected heartbeat was written instead",
          (tmp / pr.HEARTBEAT_NAME).exists())
    beats = (tmp / pr.HEARTBEAT_NAME).read_text().strip().splitlines()
    check("exactly one heartbeat per observed cycle", len(beats) == 1,
          len(beats))
    beat = json.loads(beats[0])
    # Field names match the summary (attempted/written) rather than renaming to
    # open_positions/records_written: two names for one quantity is how a reader
    # ends up asserting on the wrong one.
    check("heartbeat records what the suppressed print would have said",
          beat["attempted"] == 1 and beat["written"] == 1,
          (beat["attempted"], beat["written"]))
    check("heartbeat carries a resolvable schema version",
          beat["heartbeat_schema_version"] == pr.HEARTBEAT_SCHEMA_VERSION,
          beat["heartbeat_schema_version"])


# ---- 1. explicit path is authoritative ------------------------------------
def test_explicit_path_receives_every_record():
    before = fingerprint()
    tmp = Path(tempfile.mkdtemp())
    out = tmp / "explicit.jsonl"
    res, summ = cycle(output_path=str(out), symbols=("ABM", "TMO", "WBD"))
    check("explicit: inline result preserved", res == "INLINE_RESULT", res)
    check("explicit: file created at the requested path", out.exists(), str(out))
    lines = out.read_text().strip().splitlines() if out.exists() else []
    check("explicit: all 3 records written there", len(lines) == 3, len(lines))
    check("explicit: summary agrees", summ["written"] == 3, summ["written"])
    # The heartbeat is a DERIVED sibling of the evidence path, so exactly two
    # files are expected. Stated as an exact set rather than loosened to "at
    # least the evidence file": the point of this assertion is that nothing
    # UNREGISTERED appears beside the evidence, and a subset test would accept a
    # third unexplained file.
    check("explicit: only the evidence file and its heartbeat exist",
          sorted(p.name for p in tmp.iterdir())
          == ["explicit.jsonl", pr.HEARTBEAT_NAME],
          sorted(p.name for p in tmp.iterdir()))
    check("explicit: heartbeat landed beside the redirected evidence, "
          "not in production",
          (tmp / pr.HEARTBEAT_NAME).exists())
    check("explicit: production file unchanged", fingerprint() == before,
          (before, fingerprint()))


def test_explicit_path_also_receives_refusal_records():
    tmp = Path(tempfile.mkdtemp())
    out = tmp / "refusals.jsonl"
    res, summ = cycle(output_path=str(out), packets=lambda: (TOKEN, {}))
    recs = [json.loads(l) for l in out.read_text().strip().splitlines()]
    check("explicit: refusal record went to the explicit path", len(recs) == 1,
          len(recs))
    check("explicit: it is a capture refusal",
          recs[0]["shadow_exception_type"] == "INLINE_INPUT_NOT_CAPTURED",
          recs[0]["shadow_exception_type"])


# ---- 2. runtime default reassignment is a SUPPORTED contract --------------
def test_runtime_default_reassignment_is_honoured():
    """This is the behaviour the bound default silently prevented."""
    before = fingerprint()
    saved = pr.DEFAULT_OUTPUT
    tmp = Path(tempfile.mkdtemp()) / "runtime.jsonl"
    try:
        pr.DEFAULT_OUTPUT = str(tmp)
        res, summ = cycle()                       # no explicit path
        check("runtime default: honoured at invocation time", tmp.exists(),
              str(tmp))
        check("runtime default: record written there", summ["written"] == 1,
              summ["written"])
    finally:
        pr.DEFAULT_OUTPUT = saved
    check("runtime default: restored afterwards", pr.DEFAULT_OUTPUT == saved)
    check("runtime default: production file untouched",
          fingerprint() == before, (before, fingerprint()))


def test_signature_does_not_bind_the_default_at_definition_time():
    """Structural: the regression that made redirection a no-op."""
    import inspect
    sig = inspect.signature(pr.observe_cycle)
    default = sig.parameters["output_path"].default
    check("output_path defaults to None, not to a bound path string",
          default is None, repr(default))
    check("DEFAULT_OUTPUT is still the resolved production value",
          pr.DEFAULT_OUTPUT == "logs/tracker_parity_v1.jsonl", pr.DEFAULT_OUTPUT)


# ---- 3. production default resolves correctly, WITHOUT creating the file ---
def test_production_default_path_resolves_but_is_not_created():
    check("the production default is logs/tracker_parity_v1.jsonl",
          pr.DEFAULT_OUTPUT == "logs/tracker_parity_v1.jsonl", pr.DEFAULT_OUTPUT)
    # Phase-aware. Asserting the production file is ABSENT was only true before
    # activation; after the first declared cycle it legitimately exists, and the
    # assertion would fail forever. The invariant in both phases is that merely
    # resolving the default neither creates nor modifies it.
    before = fingerprint()
    resolved = Path(pr.DEFAULT_OUTPUT)
    check("resolving the default performs no filesystem mutation",
          fingerprint() == before, (before, fingerprint()))
    check("the default resolves to the production evidence path",
          resolved.resolve() == PROD_FILE.resolve(), str(resolved))


# ---- 4. no test may write to the production parity file -------------------
def test_no_test_writes_to_the_production_parity_file():
    """Runs the other suites' write paths and fingerprints the real file."""
    before = fingerprint()
    tmp = Path(tempfile.mkdtemp())
    cycle(output_path=str(tmp / "a.jsonl"))
    cycle(output_path=str(tmp / "b.jsonl"), packets=lambda: (None, {}))
    saved = pr.DEFAULT_OUTPUT
    try:
        pr.DEFAULT_OUTPUT = str(tmp / "c.jsonl")
        cycle()
    finally:
        pr.DEFAULT_OUTPUT = saved
    after = fingerprint()
    check("production parity file: existence/size/checksum unchanged",
          before == after, (before, after))
    # Negative control for the check above. If fingerprint() ever returned a
    # constant -- or silently swallowed a stat error -- then before == after
    # would pass vacuously and every non-contamination assertion in this suite
    # would report green while tests wrote freely to the real evidence file.
    probe = tmp / "fp_probe.jsonl"
    probe.write_text('{"a": 1}\n')
    s1 = (probe.stat().st_size, hashlib.md5(probe.read_bytes()).hexdigest())
    probe.write_text('{"a": 1}\n{"b": 2}\n')
    s2 = (probe.stat().st_size, hashlib.md5(probe.read_bytes()).hexdigest())
    check("size+checksum fingerprinting discriminates a changed file",
          s1 != s2, (s1, s2))


def test_unwritable_explicit_path_does_not_fall_back_to_the_default():
    """A failed explicit write must NOT land in the default file.

    Otherwise a deployment or test misconfiguration would quietly contaminate the
    migration evidence with records the caller directed elsewhere.
    """
    before = fingerprint()
    saved = pr.DEFAULT_OUTPUT
    sentinel = Path(tempfile.mkdtemp()) / "sentinel.jsonl"
    try:
        pr.DEFAULT_OUTPUT = str(sentinel)
        # Parent is a FILE, so mkdir/open fails for any uid including root.
        blocker = Path(tempfile.mkdtemp()) / "blocker"
        blocker.write_text("not a directory")
        res, summ = cycle(output_path=str(blocker / "x" / "out.jsonl"))
        check("unwritable: inline result still authoritative",
              res == "INLINE_RESULT", res)
        check("unwritable: attempted incremented", summ["attempted"] == 1,
              summ["attempted"])
        check("unwritable: written did NOT increment", summ["written"] == 0,
              summ["written"])
        check("unwritable: write_failures recorded",
              summ.get("write_failures") == 1, summ.get("write_failures"))
        check("unwritable: no fallback write to the runtime default",
              not sentinel.exists(), str(sentinel))
    finally:
        pr.DEFAULT_OUTPUT = saved
    check("unwritable: production file untouched", fingerprint() == before,
          (before, fingerprint()))


def test_warning_failure_is_swallowed_and_inline_survives():
    saved = pr.DEFAULT_OUTPUT
    tmp = Path(tempfile.mkdtemp()) / "warn.jsonl"

    def bad_warn(*a, **k):
        raise RuntimeError("warn exploded")

    try:
        pr.DEFAULT_OUTPUT = str(tmp)
        trades = [st()]
        state = {"cur": [dict(t) for t in trades]}
        res, summ = pr.observe_cycle(
            object(), lambda: "INLINE_RESULT",
            lambda: [dict(t) for t in state["cur"]],
            lambda t, p, b: dict(t),
            packets_fn=lambda: (TOKEN, {"ABM": dict(GOOD)}),
            params={"exit_policy": "A"}, lineage={}, warn=bad_warn)
        check("warn failure: inline result survives", res == "INLINE_RESULT", res)
    finally:
        pr.DEFAULT_OUTPUT = saved


# ---- 5. parity output has no inbound edge to strategy logic ---------------
def test_parity_output_is_not_read_by_strategy_logic():
    """Structurally confirm no production consumer reads the parity JSONL."""
    import ast

    import blast_radius as br

    needles = ("tracker_parity_v1", "DEFAULT_OUTPUT")
    consumers = []
    for p in br.python_files():
        rel = str(p.relative_to(ROOT))
        # Reuse the ONE shared definition of what is production rather than
        # keeping a second local list -- duplicating it is what made the old
        # grep guards drift. tools/ is verification tooling: pre_parity_snapshot
        # calls .exists() on the evidence file to report activation state, which
        # is not a strategy-logic read of its contents.
        if not br.is_production(rel):
            continue
        if rel.startswith("engine/parity_") or rel == "engine/tracker_compat.py":
            continue                              # the producer itself
        src = p.read_text(errors="replace")
        tree = ast.parse(src)
        # Only STRING CONSTANTS and attribute names count, never comments.
        strings = {n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        if any(any(nd in s for s in strings) for nd in needles) or \
                (attrs & {"DEFAULT_OUTPUT"}):
            consumers.append(rel)
    check("no production module outside the parity stack references the "
          "parity output path", not consumers, consumers)

    for name in ("daily_report.py", "engine/tracker.py"):
        src = (ROOT / name).read_text(errors="replace")
        tree = ast.parse(src)
        strings = {n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        check(f"{name} does not read the parity output",
              not any("tracker_parity" in s for s in strings),
              sorted(s for s in strings if "parity" in s))


def test_parity_output_is_append_only():
    tmp = Path(tempfile.mkdtemp()) / "append.jsonl"
    cycle(output_path=str(tmp))
    first = tmp.read_text()
    cycle(output_path=str(tmp))
    second = tmp.read_text()
    check("append-only: earlier content preserved verbatim",
          second.startswith(first), (len(first), len(second)))
    check("append-only: a second record was added",
          len(second.strip().splitlines()) == 2,
          len(second.strip().splitlines()))


if __name__ == "__main__":
    start = fingerprint()
    for fn in sorted([v for k, v in list(globals().items())
                      if k.startswith("test_")], key=lambda f: f.__name__):
        print(f"\n{fn.__name__}")
        fn()
    end = fingerprint()
    print(f"\n  production parity file: before={start} after={end}")
    check("SUITE GUARD: production parity file unchanged by this suite",
          start == end, (start, end))
    print(f"\n{COUNT} assertions")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("ALL PASS")
