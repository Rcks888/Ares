#!/usr/bin/env python3
"""Activation rehearsal: the admission recorder under production parity mode.

    python3 tests/run_admission_parity_rehearsal.py

Production runs the report with ARES_PARITY=1 (run_ares.sh), so
check_open_trades runs inside the Phase 4 parity observer. Every other
admission suite runs with parity off. This rehearsal runs the real
daily_report.py twice per chain, parity on, in scratch git repositories:

  P1  the branch code (admission recorder active)
  P0  the pre-admission code (tests/fixtures/*_preadmission.py, observer
      removed), the code main runs today

Same seed, same stubs (run_admission_inspection_controls: network modules,
identity indicators, queue validator, fill window). After each run:

  r01  both runs exit 0 and the parity collector ran in both (parity
       evidence and heartbeat appended)
  r02  production state is identical between P0 and P1 (pending, trades,
       queue, ranked queue, queue events, trade report), timestamps aside
  r03  parity evidence is identical between P0 and P1, time and lineage
       fields aside: the recorder neither writes to it nor changes it. The
       two state hashes are excluded from that comparison (they cover
       virtual_trades.json, which carries fill_run_ts) and checked instead:
       one unchanged value per cycle, equal to the committed trades file in
       every run that closes no trade. A run that closes one is exempt from
       the second part, because check_shadow_trades() (tracker.py:1356)
       legitimately rewrites the closed trade's shadow block later in the
       same report
  r04  the admission files exist only in P1, and no admission record or
       heartbeat names a parity path
  r05  tools/inspect_admission_cycles.py passes P1's chain with the
       expected per-key results

n01-n05 are negative controls: each replaces one committed file in P1 with a
damaged copy and requires the named r-check to report it.

Indicators are a deterministic stub (rsi 50, no bearish divergence) rather
than pandas_ta, so the inline and shadow exit evaluations both run and the
rehearsal reproduces without pandas_ta. OPD's stop sits above its price, so
the report closes it, frees a slot, and check_open_trades' own expiry and a
scan promotion run under parity.

ARES_REHEARSAL_KEEP=1 keeps the scratch repositories.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import run_admission_inspection_controls as m  # noqa: E402
import run_report_e2e_smoke as e2e  # noqa: E402

PARITY = "logs/tracker_parity_v1.jsonl"
PARITY_HB = "logs/parity_heartbeat_v1.jsonl"
STATE = (m.PENDING, m.TRADES, m.QUEUE, m.RANKED, "logs/queue_events.jsonl",
         "logs/trades_report.csv")
ADMISSION = (m.RECORDS, m.HEARTBEAT, m.HEALTH)


def _indicators(df):
    """Deterministic indicators: the two columns check_open_trades reads,
    neutral values, so the inline and shadow evaluations both run."""
    df = df.copy()
    df["rsi"] = 50.0
    df["bearish_div"] = False
    return df


def drive(entry, src, opts_json):
    """m.drive, with parity on as in run_ares.sh."""
    opts = json.loads(opts_json)
    os.environ["ARES_SCHEDULED"] = "1"
    os.environ["ARES_PARITY"] = "1"
    sys.path.insert(0, src)
    m._install(src, opts)
    import types
    ind = types.ModuleType("engine.indicators")
    ind.add_indicators = _indicators
    sys.modules["engine.indicators"] = ind
    from engine import tracker
    tracker._fill_window_ok = lambda params: (opts.get("window", True),
                                              "control")
    tracker._validate_queued = m._validator(opts)
    import runpy
    runpy.run_path(os.path.join(src, entry), run_name="__main__")


class Chain(m.Chain):
    @classmethod
    def create(cls, tmp, name, variant, seed):
        os.makedirs(os.path.join(tmp, name))
        src = e2e._prepare(variant, os.path.join(tmp, name))
        for path, obj in seed.items():
            Path(src, path).write_text(json.dumps(obj, indent=2))
        m._git(src, "init", "-q")
        for k, v in (("user.email", "controls@localhost"),
                     ("user.name", "parity rehearsal"),
                     ("commit.gpgsign", "false")):
            m._git(src, "config", k, v)
        Path(src, ".gitignore").write_text("__pycache__/\n.chain.json\n")
        m._git(src, "add", "-A")
        m._git(src, "commit", "-q", "-m", "seed")
        c = cls(src)
        c.refs["R"] = m._git(src, "rev-parse", "HEAD")
        return c

    def run(self, label, entry, opts, commit=True):
        p = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--drive", entry, self.dir, json.dumps(opts)],
                           capture_output=True, text=True, cwd=self.dir,
                           timeout=300)
        m._git(self.dir, "add", "-A", "logs")
        stamp = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
        m._git(self.dir, "commit", "-q", "--allow-empty", "-m",
               f"Ares V3 report {stamp}")
        self.refs[label] = m._git(self.dir, "rev-parse", "HEAD")
        self.refs[label + ".rc"] = p.returncode
        self.refs[label + ".err"] = p.stderr[-1500:]
        self.refs[label + ".out"] = p.stdout
        return p


def seed():
    opd = m._open_trade("OPD")
    opd["stop_loss"] = opd["trailing_stop"] = round(m.price("OPD") * 1.05, 2)
    return {
        m.TRADES: [m._open_trade(s) for s in ("OPA", "OPB", "OPC")] + [opd],
        m.PENDING: [m._pending("PRE"), m._pending("PRT")],
        m.QUEUE: [m._qentry("QAA"), m._qentry("QBB"), m._qentry("QIN"),
                  m._qentry("QOLD", days_ago=12)],
        m.RANKED: []}


RUNS = (("rep1", {"signals": ["SCA"], "validate": {"QIN": "invalid"}}),
        ("rep2", {"signals": ["SCB"]}))


def build(tmp):
    out = {}
    for name, variant in (("P0", "A"), ("P1", "B")):
        c = Chain.create(tmp, name, variant, seed())
        for label, opts in RUNS:
            c.run(label, "daily_report.py", opts)
        out[name] = c
    return out


LABELS = [label for label, _ in RUNS]
PREV = dict(zip(LABELS, ["R"] + LABELS[:-1]))

# Fields that differ between two otherwise identical runs: wall-clock time,
# per-cycle ids, the scratch commit, and the source identity of tracker.py
# (P0 runs a different file by construction). The two parity state hashes
# cover virtual_trades.json, which carries fill_run_ts, so they are excluded
# here and bound to the committed trades file by r03 instead.
VOLATILE = {
    m.TRADES: {"fill_run_ts"},
    m.RANKED: {"checked_at"},
    "logs/queue_events.jsonl": {"timestamp"},
    PARITY: {"timestamp", "cycle_id", "production_commit",
             "production_state_hash_before", "production_state_hash_after",
             "tracker_file", "tracker_source_hash"},
    PARITY_HB: {"cycle_started_at", "cycle_completed_at", "cycle_id",
                "production_commit"},
}
CLOCK = re.compile(r"\d{2}:\d{2}(:\d{2})?")


def load(blob, path):
    if blob is None:
        return None
    text = blob.decode()
    if path.endswith(".jsonl"):
        return [json.loads(x) for x in text.splitlines() if x]
    if path.endswith(".json"):
        return json.loads(text)
    return text


def strip(obj, keys):
    if isinstance(obj, list):
        return [strip(x, keys) for x in obj]
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if k not in keys}
    return obj


def view(c, label, path):
    return strip(load(c.show(label, path), path), VOLATILE.get(path, ()))


def state_hash(obj):
    """engine/parity_compare.py canonical_hash."""
    return hashlib.md5(json.dumps(obj, sort_keys=True,
                                  default=str).encode()).hexdigest()


class Edited:
    """A chain with some (label, path) blobs replaced, for negative controls."""

    def __init__(self, base, changes):
        self.base, self.changes, self.refs = base, changes, base.refs
        self.dir = base.dir

    def show(self, ref, path):
        if (ref, path) in self.changes:
            return self.changes[(ref, path)]
        return self.base.show(ref, path)

    def lines(self, ref, path):
        return [json.loads(x) for x in (self.show(ref, path) or b"")
                .decode().splitlines() if x]


def r01(c, ch):
    for name, x in ch.items():
        for label in LABELS:
            rc = x.refs[label + ".rc"]
            c(rc == 0, f"{name} {label}: exit {rc}: {x.refs[label + '.err']}")
            hb = x.lines(label, PARITY_HB)
            c(len(hb) == len(x.lines(PREV[label], PARITY_HB)) + 1,
              f"{name} {label}: parity heartbeat not appended once")
            new = x.lines(label, PARITY)[len(x.lines(PREV[label], PARITY)):]
            c(new, f"{name} {label}: no parity records")
            last = hb[-1] if hb else {}
            c(last.get("attempted") == last.get("written") == len(new)
              and last.get("write_failures") == 0
              and last.get("contract_valid") is True
              and last.get("capture_error") is None
              and last.get("results_error") is None
              and last.get("shadow_system_error") is None,
              f"{name} {label}: parity heartbeat {last}")
            for r in new:
                c(r.get("difference_class") == "MATCH"
                  and str(r.get("inline_evaluation_status")).startswith(
                      "SUCCEEDED"),
                  f"{name} {label}: parity {r.get('symbol')} "
                  f"{r.get('difference_class')} "
                  f"{r.get('inline_evaluation_status')}")
        decisions = [r.get("inline_decision")
                     for r in x.lines(LABELS[-1], PARITY)]
        c("close:stop_loss" in decisions,
          f"{name}: no exit evaluated under parity: {decisions}")


def r02(c, ch):
    a, b = ch["P0"], ch["P1"]
    for label in LABELS:
        for path in STATE:
            c(view(a, label, path) == view(b, label, path),
              f"{label} {path}: P0 and P1 differ")
        x, y = (CLOCK.sub("HH:MM", z.refs[label + ".out"]) for z in (a, b))
        c(x == y, f"{label}: report stdout differs between P0 and P1")


def r03(c, ch):
    a, b = ch["P0"], ch["P1"]
    for label in LABELS:
        for path in (PARITY, PARITY_HB):
            c(view(a, label, path) == view(b, label, path),
              f"{label} {path}: P0 and P1 differ")
    bound = 0
    for name, x in ch.items():
        for label in LABELS:
            new = x.lines(label, PARITY)[len(x.lines(PREV[label], PARITY)):]
            got = {(r.get("production_state_hash_before"),
                    r.get("production_state_hash_after")) for r in new}
            c(len(got) == 1 and len({h for pair in got for h in pair}) == 1,
              f"{name} {label}: parity state hashes not one unchanged "
              f"value per cycle: {sorted(got)}")
            if any(str(r.get("inline_decision")).startswith("close")
                   for r in new):
                continue
            bound += 1
            want = state_hash(load(x.show(label, m.TRADES), m.TRADES))
            c(got == {(want, want)},
              f"{name} {label}: parity state hashes {sorted(got)} "
              f"!= committed trades {want}")
    c(bound >= 2, f"state hash bound to committed trades in {bound} runs")


def r04(c, ch):
    a, b = ch["P0"], ch["P1"]
    fix = os.path.join(HERE, "fixtures")
    for f, ref in (("engine/tracker.py", "tracker_preadmission.py"),
                   ("daily_report.py", "daily_report_preadmission.py")):
        c(Path(a.dir, f).read_bytes() == Path(fix, ref).read_bytes(),
          f"P0 {f} is not tests/fixtures/{ref}")
        c(Path(b.dir, f).read_bytes() == Path(ROOT, f).read_bytes(),
          f"P1 {f} is not the branch file")
    c(not Path(a.dir, "engine/admission_observer.py").exists(),
      "P0 has engine/admission_observer.py")
    for label in LABELS:
        for path in ADMISSION:
            c(a.show(label, path) is None, f"P0 {label}: {path} exists")
        for path in (m.RECORDS, m.HEARTBEAT):
            new = len(b.lines(label, path)) - len(b.lines(PREV[label], path))
            c(new > 0, f"P1 {label}: nothing appended to {path}")
        c(b.show(label, m.HEALTH) is None, f"P1 {label}: health line written")
        for path in (m.RECORDS, m.HEARTBEAT):
            text = (b.show(label, path) or b"").decode()
            for p in (PARITY, PARITY_HB):
                c(p not in text and os.path.basename(p) not in text,
                  f"P1 {label}: {path} names {p}")
        for path in (PARITY, PARITY_HB):
            c(b"admission" not in (b.show(label, path) or b""),
              f"P1 {label}: {path} mentions admission")


R05_LINES = (
    "queue consistent QOLD (expired)",
    "fill filled ('PRE', ",
    "fill removed ('PRT', ",
    "pending admitted ('QAA', ",
    "queue consistent SCA (insert)",
    "fill filled ('QAA', ",
    "queue consistent SCB (insert)",
    "open episodes: 0 (0 linked), unknown: 0",
    "RESULT: PASS",
)


def r05(c, ch):
    b = ch["P1"]
    shas = [b.refs[x] for x in ["R"] + LABELS]
    p = subprocess.run([sys.executable, m.TOOL, "--verbose", *shas],
                       capture_output=True, text=True, cwd=b.dir, timeout=300)
    out = p.stdout + p.stderr
    c(p.returncode == 0, f"inspection exit {p.returncode}:\n{out}")
    for s in R05_LINES:
        c(s in out, f"inspection output lacks {s!r}")
    c("FAIL" not in out and "INCONCLUSIVE" not in out,
      f"inspection output has FAIL or INCONCLUSIVE:\n{out}")


CONTROLS = (("r01_parity_ran_in_both", r01),
            ("r02_production_state_identical", r02),
            ("r03_parity_evidence_identical", r03),
            ("r04_admission_files_separate", r04),
            ("r05_inspection_passes", r05))


def _edit_jsonl(blob, fn):
    rows = [json.loads(x) for x in blob.decode().splitlines() if x]
    rows = fn(rows)
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


def _edit_json(blob, fn):
    return json.dumps(fn(json.loads(blob)), indent=2).encode()


def _set_decision(rows):
    for r in rows:
        if r.get("inline_decision") == "close:stop_loss":
            r["inline_decision"] = "hold"
    return rows


def _set_fill_ts(rows):
    for t in rows:
        if t.get("fill_run_ts"):
            t["fill_run_ts"] = "2000-01-01T00:00:00"
    return rows


def _name_parity(rows):
    rows[-1]["note"] = PARITY
    return rows


NEGATIVE = (
    ("n01_queue_entry_lost", "r02_production_state_identical",
     "rep2", m.QUEUE, _edit_json, lambda q: q[1:], "P0 and P1 differ"),
    ("n02_parity_decision_changed", "r03_parity_evidence_identical",
     "rep1", PARITY, _edit_jsonl, _set_decision, "P0 and P1 differ"),
    ("n03_trades_hash_unbound", "r03_parity_evidence_identical",
     "rep2", m.TRADES, _edit_json, _set_fill_ts, "!= committed trades"),
    ("n04_admission_names_parity", "r04_admission_files_separate",
     "rep1", m.HEARTBEAT, _edit_jsonl, _name_parity, "names"),
    ("n05_parity_heartbeat_missing", "r01_parity_ran_in_both",
     "rep2", PARITY_HB, _edit_jsonl, lambda h: h[:-1], "not appended"),
)


def main():
    tmp = tempfile.mkdtemp(prefix="adm_parity_")
    results = []
    try:
        ch = build(tmp)
        fns = dict(CONTROLS)
        for name, fn in CONTROLS:
            c = m.Check()
            try:
                fn(c, ch)
            except Exception as e:
                c.fails.append(f"raised {type(e).__name__}: {e}")
            results.append((name, c.fails))
        for name, target, label, path, edit, fn, sub in NEGATIVE:
            blob = ch["P1"].show(label, path)
            bad = dict(ch, P1=Edited(ch["P1"], {(label, path): edit(blob, fn)}))
            c = m.Check()
            try:
                fns[target](c, bad)
            except Exception as e:
                c.fails.append(f"raised {type(e).__name__}: {e}")
            hit = [f for f in c.fails if sub in f]
            results.append((f"{name} -> {target}", [] if hit else
                            [f"not detected (fails: {c.fails[:3]})"]))
    finally:
        if os.environ.get("ARES_REHEARSAL_KEEP") == "1":
            print("kept", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    for name, fails in results:
        print(("PASS  " if not fails else "FAIL  ") + name)
        for f in fails[:8]:
            print("        " + f.replace("\n", "\n        "))
    ok = sum(1 for _, f in results if not f)
    print(f"\n{ok}/{len(results)} parity rehearsal checks passed")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--drive":
        drive(*sys.argv[2:5])
        sys.exit(0)
    sys.exit(main())
