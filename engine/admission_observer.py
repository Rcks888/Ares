"""Admission-capture observer v1: serialisation of tracker._ADMISSION_EVAL.

Design: archive/DESIGN_admission_capture_v1.md (r5, accepted for implementation
review 2026-10-21). Not activated.

Records every admission (`open_trade` call), every promotion pass, every fill
pass and every pending examined at fill, plus the queue writes around them, on
both decision paths. The capture sites in engine/tracker.py, daily_report.py
and monitor_trades.py store raw values only. Everything else happens here,
after the run, called from each entrypoint's `finally`.

MEASUREMENT ONLY. This module must not change a decision, operator output,
exception propagation or any production state file.

WHAT THIS MODULE NEVER CLAIMS
-----------------------------
The disk state. A save fact here is `not_attempted` | `completed` | `raised`
and says nothing about the file: a save that raised may have left the old file,
an unparseable prefix or the complete new file (design §3.5). Disk effects are
read by the inspection tool from the committed state files. Episodes, trade
links and A2 across runs are likewise inspection work (design §5); this module
only provides the pure classification helper for A2.

RECORD LAYOUT
-------------
One output record per store record, in capture order, each with `seq` (its
position in the store). Child records carry `parent_seq`: a queue insertion
points at the admission or fill entry that caused it, a fill entry at its fill
pass, a promotion admission at its promotion pass. Links are established by
object identity of the signal dict, never by symbol, so a duplicate symbol
cannot be mislinked.
"""

import hashlib
import json
import os
import subprocess
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOGS_DIR = ROOT / "logs"

RECORDS_NAME = "admission_v1.jsonl"
HEARTBEAT_NAME = "admission_heartbeat_v1.jsonl"
RECORD_SCHEMA_VERSION = 1
HEARTBEAT_SCHEMA_VERSION = 1

DECISION_PATHS = frozenset({"report", "monitor"})

DECISION_MANIFEST = (
    "monitor_trades.py",
    "daily_report.py",
    "engine/tracker.py",
    "engine/data_feed.py",
    "engine/indicators.py",
    "engine/signals.py",
    "engine/screener.py",
    "config/strategy_params.json",
)
CLASSIFICATION_MANIFEST = (
    "engine/sample.py",
)
OBSERVATION_MANIFEST = (
    "engine/admission_observer.py",
    "engine/monitor_observer.py",
)

SIGNAL_FIELDS = ("symbol", "date", "price", "strategy", "confluence",
                 "strength", "stdev_20", "regime", "trigger", "category",
                 "rsi", "vol_ratio", "from_queue", "fill_attempts")
QUEUE_ENTRY_FIELDS = ("symbol", "date_added", "price_at_signal", "confluence",
                      "strategy", "check_failures")

ADMISSION_BRANCHES = frozenset({"duplicate_open", "queued",
                                "duplicate_pending", "pending"})
ADMISSION_RETURNS = {"duplicate_open": "duplicate", "queued": "queued",
                     "duplicate_pending": "duplicate", "pending": "pending"}
FILL_OUTCOMES = frozenset({
    "demoted_to_queue", "discarded_already_open", "dropped_aged",
    "retained_no_data", "dropped_no_data", "retained_bar_not_today",
    "filled", "retained_exception", "dropped_exception"})
FILL_REMOVES = frozenset({
    "demoted_to_queue", "discarded_already_open", "dropped_aged",
    "dropped_no_data", "filled", "dropped_exception"})
CANDIDATE_OUTCOMES = frozenset({"held_dropped", "deferred_transient",
                                "dropped_invalid", "promoted",
                                "dropped_open_trade"})
PASS_RETURNS = frozenset({"no_free_slots", "queue_empty",
                          "fill_window_closed", "completed"})


def records_path():
    return LOGS_DIR / RECORDS_NAME


def heartbeat_path():
    return LOGS_DIR / HEARTBEAT_NAME


def _now():
    return datetime.now(timezone.utc).isoformat()


def _iso(t):
    """A captured `time.time()` float as an aware UTC ISO string, else None."""
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        return None
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def _plain(v):
    """JSON-safe scalar view. Never raises."""
    try:
        if v is None or isinstance(v, (bool, int, float, str)):
            return v
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        if isinstance(v, BaseException):
            return f"{type(v).__name__}: {v}"
        return repr(v)
    except Exception as exc:                              # noqa: BLE001
        return f"<unrepresentable {type(v).__name__}: {type(exc).__name__}>"


def _view(obj, fields):
    """Selected fields of a production dict. Absent keys are listed, never
    filled in (design Q3: a missing original field stays explicitly absent)."""
    if not isinstance(obj, dict):
        return {"fields": {}, "absent": list(fields),
                "unavailable": type(obj).__name__}
    out, absent = {}, []
    for f in fields:
        if f in obj:
            out[f] = _plain(obj[f])
        else:
            absent.append(f)
    return {"fields": out, "absent": absent}


def _save_fact(attempted, completed):
    """The only save vocabulary (design §3.5). Implies nothing about the file."""
    if attempted is True and completed is True:
        return "completed"
    if attempted is True:
        return "raised"
    if completed is True:
        return "contract_violation_completed_without_attempt"
    return "not_attempted"


def _len_or_none(v):
    try:
        return len(v)
    except Exception:                                     # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Manifests and lineage (same construction as monitor_observer)
# --------------------------------------------------------------------------

def _fingerprint(relpaths):
    digests, missing = {}, []
    for rel in relpaths:
        p = ROOT / rel
        try:
            digests[rel] = hashlib.md5(p.read_bytes()).hexdigest()
        except Exception as exc:                          # noqa: BLE001
            digests[rel] = None
            missing.append(f"{rel}: {type(exc).__name__}")
    return {"digests": digests, "missing": missing, "complete": not missing}


def manifests():
    return {
        "decision": _fingerprint(DECISION_MANIFEST),
        "classification": _fingerprint(CLASSIFICATION_MANIFEST),
        "observation": _fingerprint(OBSERVATION_MANIFEST),
    }


def _git_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(ROOT), capture_output=True, text=True,
                             timeout=10)
        return out.stdout.strip() or None
    except Exception:                                     # noqa: BLE001
        return None


def _run_kind():
    """sample.fill_context(), the same classifier the fill path uses."""
    try:
        from engine import sample
        kind, detect = sample.fill_context()
        return kind, detect
    except Exception as exc:                              # noqa: BLE001
        return None, f"unavailable: {type(exc).__name__}"


# --------------------------------------------------------------------------
# A2 classification (design §4.4). Pure; used by the inspection tool, which
# owns the cross-run link from a fill to its admission.
# --------------------------------------------------------------------------

SESSION_OPEN_BASIS = "NYSE 09:30 America/New_York"


def session_open_utc(bar_date):
    """09:30 America/New_York on `bar_date`, as an aware UTC datetime."""
    from zoneinfo import ZoneInfo
    if isinstance(bar_date, str):
        bar_date = date.fromisoformat(bar_date)
    local = datetime(bar_date.year, bar_date.month, bar_date.day, 9, 30,
                     tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(timezone.utc)


def classify_a2(bracket_start, bracket_end, bar_date):
    """Three-valued: did the booked open precede the event bracketed by
    [bracket_start, bracket_end]? Arguments are aware datetimes (end may be
    None for a call that raised) and the fill's bar date.

    A bracket with no end is open-ended (design §3.3), so every claim about
    it is `unknown`. That is decided first, before the start is compared
    with anything."""
    from zoneinfo import ZoneInfo
    if bracket_start is None or bracket_end is None or bar_date is None:
        return "unknown"
    if isinstance(bar_date, str):
        bar_date = date.fromisoformat(bar_date)
    o = session_open_utc(bar_date)
    event_session = bracket_start.astimezone(
        ZoneInfo("America/New_York")).date()
    if bar_date < event_session:
        return "inconsistent_bar_before_event"
    if bar_date > event_session:
        return "before_open"
    if bracket_end < o:
        return "before_open"
    if bracket_start >= o:
        return "after_open"
    return "unknown"


# --------------------------------------------------------------------------
# Derivation: one function per record kind. Each returns a new dict.
# --------------------------------------------------------------------------

def _queue_event_match(store_events, start, symbol, action):
    """Write outcome of the first captured (symbol, action) event at or after
    index `start`: written | write_failed | unobserved."""
    for ev in store_events[start:]:
        if (isinstance(ev, tuple) and len(ev) == 3 and ev[0] == symbol
                and ev[1] == action):
            return "written" if ev[2] is True else "write_failed"
    return "unobserved"


def _derive_queue_insert(raw, events, violations):
    sig = raw.get("signal")
    symbol = sig.get("symbol") if isinstance(sig, dict) else None
    evicted = [e.get("symbol") if isinstance(e, dict) else None
               for e in (raw.get("evicted") or [])]
    insert = raw.get("insert")
    if insert == "inserted":
        memory = ("inserted_then_evicted" if symbol in evicted else "kept")
        write = _queue_event_match(events, raw.get("qe_index", 0) or 0,
                                   symbol, "queued")
        if write == "unobserved":
            violations.append("queued_event_unobserved")
    elif insert == "already_queued":
        memory = "already_queued"
        write = None
    else:
        memory = None
        write = None
        violations.append("queue_insert_outcome_missing")
    return {
        "symbol": symbol,
        "insert": insert,
        "queue_memory": memory,
        "queue_event_write": write,
        "evicted": evicted,
        "queue_save": (None if insert == "already_queued" else
                       _save_fact(raw.get("queue_save_attempted"),
                                  raw.get("queue_save_completed"))),
        "returned": raw.get("returned") is True,
    }


def _admission_call_state(raw):
    if raw.get("returned") is not None:
        return "returned"
    if (raw.get("branch") == "pending"
            and raw.get("pending_save_attempted") is True
            and raw.get("pending_save_completed") is not True):
        return "raised_in_pending_save"
    return "raised_elsewhere"


def _derive_admission(raw, route, child_qi, violations):
    sig = _view(raw.get("signal"), SIGNAL_FIELDS)
    branch = raw.get("branch")
    if branch is not None and branch not in ADMISSION_BRANCHES:
        violations.append(f"unknown_branch:{branch}")
    call_state = _admission_call_state(raw)
    if (call_state == "raised_elsewhere" and branch == "queued"
            and child_qi is not None
            and child_qi.get("queue_save") == "raised"):
        call_state = "raised_in_queue_save"
    returned = raw.get("returned")
    if returned is not None and branch in ADMISSION_RETURNS \
            and ADMISSION_RETURNS[branch] != returned:
        violations.append("returned_disagrees_with_branch")
    if returned is not None and branch is None:
        violations.append("returned_without_branch")
    if branch == "queued" and child_qi is None:
        violations.append("queued_without_queue_insert")
    if branch == "pending" and returned == "pending" and not (
            raw.get("pending_appended") is True
            and raw.get("pending_save_completed") is True):
        violations.append("pending_returned_without_completed_save")
    out = {
        "symbol": sig["fields"].get("symbol"),
        "signal_date": sig["fields"].get("date"),
        "decision_price": sig["fields"].get("price"),
        "signal": sig["fields"],
        "signal_fields_absent": sig["absent"],
        "from_queue": raw.get("from_queue"),
        "route": route,
        "open_positions_before": _len_or_none(raw.get("open_trades_seen")),
        "pending_before": _len_or_none(raw.get("pending_seen")),
        "max_positions": _plain(raw.get("max_positions")),
        "branch": branch,
        "pending_appended": raw.get("pending_appended") is True,
        "pending_save": _save_fact(raw.get("pending_save_attempted"),
                                   raw.get("pending_save_completed")),
        "returned": returned,
        "call_state": call_state,
        "t_enter": _iso(raw.get("t_enter")),
        "t_return": _iso(raw.get("t_return")),
    }
    if child_qi is not None:
        out["queue"] = child_qi
    return out


def _derive_fill(raw, child_qi, violations):
    sig = _view(raw.get("sig"), SIGNAL_FIELDS)
    outcome = raw.get("outcome")
    if outcome is not None and outcome not in FILL_OUTCOMES:
        violations.append(f"unknown_outcome:{outcome}")
    completed = raw.get("t_exit") is not None
    if completed and outcome is None:
        violations.append("completed_without_outcome")
    if completed and outcome in FILL_OUTCOMES:
        if (outcome in FILL_REMOVES) != (raw.get("executed_marked") is True):
            violations.append("executed_marked_disagrees_with_outcome")
        if outcome == "filled" and raw.get("trade_appended") is not True:
            violations.append("filled_without_trade_appended")
    if outcome == "demoted_to_queue" and child_qi is None:
        violations.append("demotion_without_queue_insert")
    out = {
        "symbol": sig["fields"].get("symbol"),
        "signal_date": sig["fields"].get("date"),
        "from_queue": sig["fields"].get("from_queue"),
        "signal": sig["fields"],
        "signal_fields_absent": sig["absent"],
        "outcome": outcome,
        "entry_state": "completed" if completed else "reached_not_completed",
        "fill_attempts": _plain(raw.get("fill_attempts")),
        "trade_appended": raw.get("trade_appended") is True,
        "executed_marked": raw.get("executed_marked") is True,
        "exception": _plain(raw.get("exception")),
        "t_enter": _iso(raw.get("t_enter")),
        "t_exit": _iso(raw.get("t_exit")),
    }
    for k in ("bar_date", "today_utc", "bar_open", "entry_price",
              "slippage_pct", "shares", "stop_loss", "take_profit",
              "stdev_fallback", "fill_source", "entry_date"):
        if k in raw:
            out[k] = _plain(raw[k])
    if isinstance(raw.get("t_enter"), (int, float)) and \
            isinstance(sig["fields"].get("date"), str):
        try:
            run_day = datetime.fromtimestamp(raw["t_enter"]).date()
            out["pending_age_days"] = (
                run_day - date.fromisoformat(sig["fields"]["date"])).days
        except Exception:                                 # noqa: BLE001
            out["pending_age_days"] = None
    if child_qi is not None:
        out["queue"] = child_qi
    return out


def _derive_fill_pass(raw, violations):
    rr = raw.get("return_reason")
    if rr not in (None, "no_pending", "completed"):
        violations.append(f"unknown_return_reason:{rr}")
    return {
        "pending_seen": _plain(raw.get("pending_seen")),
        "open_count_before": _plain(raw.get("open_count_before")),
        "max_positions": _plain(raw.get("max_positions")),
        "pending_save": _save_fact(raw.get("pending_save_attempted"),
                                   raw.get("pending_save_completed")),
        "trades_save": _save_fact(raw.get("trades_save_attempted"),
                                  raw.get("trades_save_completed")),
        "return_reason": rr,
        "t_enter": _iso(raw.get("t_enter")),
        "t_exit": _iso(raw.get("t_exit")),
    }


def _derive_candidate(c, violations):
    q = _view(c.get("q"), QUEUE_ENTRY_FIELDS)
    outcome = c.get("outcome")
    if outcome is not None and outcome not in CANDIDATE_OUTCOMES:
        violations.append(f"unknown_candidate_outcome:{outcome}")
    checks = c.get("checks")
    out = {
        "symbol": q["fields"].get("symbol"),
        "queued_at": q["fields"].get("date_added"),
        "price_at_signal": q["fields"].get("price_at_signal"),
        "queue_entry": q["fields"],
        "queue_entry_fields_absent": q["absent"],
        "held": c.get("held") is True,
        "valid": c.get("valid"),
        "reason": _plain(c.get("reason")),
        "checks": ({k: _plain(v) for k, v in checks.items()}
                   if isinstance(checks, dict) else None),
        "t_validate_start": _iso(c.get("t_validate_start")),
        "t_validate_end": _iso(c.get("t_validate_end")),
        "open_trade_expected": c.get("open_trade_expected") is True,
        "open_trade_result": c.get("open_trade_result"),
        "outcome": outcome,
    }
    if out["open_trade_expected"] and isinstance(c.get("signal"), dict):
        s = c["signal"]
        out["promotion_signal_date"] = _plain(s.get("date"))
        out["promotion_price"] = _plain(s.get("price"))
    return out


def _derive_promotion_pass(raw, violations):
    rr = raw.get("return_reason")
    if rr is not None and rr not in PASS_RETURNS:
        violations.append(f"unknown_return_reason:{rr}")
    cands = [_derive_candidate(c, violations)
             for c in (raw.get("candidates") or []) if isinstance(c, dict)]
    return {
        "source": raw.get("source"),
        "use_live": raw.get("use_live"),
        "free_slots": _plain(raw.get("free_slots")),
        "ranked_basis": raw.get("ranked_basis"),
        "ranked_count": _plain(raw.get("ranked_count")),
        "candidates_examined": len(cands),
        "fill_window_ok": raw.get("fill_window_ok"),
        "fill_window_reason": _plain(raw.get("fill_window_reason")),
        "early_return_reason": rr if rr in ("no_free_slots", "queue_empty",
                                             "fill_window_closed") else None,
        "candidates": cands,
        "queue_save": _save_fact(raw.get("queue_save_attempted"),
                                 raw.get("queue_save_completed")),
        "ranked_save": _save_fact(raw.get("ranked_save_attempted"),
                                  raw.get("ranked_save_completed")),
        "return_reason": rr,
        "t_enter": _iso(raw.get("t_enter")),
        "t_exit": _iso(raw.get("t_exit")),
    }


def _derive_expiry(raw, events, violations):
    """Expiry removals in capture order. `expired` carries a symbol;
    `expiry_parse_failed` carries len(active) at the failure, because the
    symbol itself may be the failing expression (design §2.2.3)."""
    start = raw.get("qe_index", 0) or 0
    end = raw.get("qe_end")
    if not isinstance(end, int):
        end = None
        if raw.get("returned") is True:
            violations.append("expiry_returned_without_event_window")
    removals = []
    for ev in events[start:end]:
        if not (isinstance(ev, tuple) and len(ev) == 3):
            continue
        if ev[1] == "expired":
            removals.append({"action": "expired", "symbol": _plain(ev[0]),
                             "event_write": ("written" if ev[2] is True
                                             else "write_failed")})
        elif ev[1] == "expiry_parse_failed":
            removals.append({"action": "expiry_parse_failed",
                             "active_len": _plain(ev[0])})
    return {
        "removals": removals,
        "event_window": [start, end],
        "queue_save": _save_fact(raw.get("queue_save_attempted"),
                                 raw.get("queue_save_completed")),
        "returned": raw.get("returned") is True,
    }


def _derive_maintenance(raw, violations):
    return {
        "queue_save": _save_fact(raw.get("queue_save_attempted"),
                                 raw.get("queue_save_completed")),
        "ranked_save": _save_fact(raw.get("ranked_save_attempted"),
                                  raw.get("ranked_save_completed")),
        "ranked_save_site": raw.get("ranked_save_site"),
        "returned": raw.get("returned") is True,
        "qe_index": raw.get("qe_index"),
    }


# --------------------------------------------------------------------------
# Linking within one run (identity, never symbol)
# --------------------------------------------------------------------------

def _link(records, store):
    """parent_seq for every record, and the route of every admission."""
    parent, route = {}, {}
    scan_signals = store.get("signals")
    scan_ids = ({id(s) for s in scan_signals}
                if isinstance(scan_signals, list) else set())
    last_pass = None
    last_fill_pass = None
    for i, r in enumerate(records):
        kind = r.get("kind")
        if kind == "promotion_pass":
            last_pass = i
        elif kind == "fill_pass":
            last_fill_pass = i
        elif kind == "fill":
            if last_fill_pass is not None:
                parent[i] = last_fill_pass
        elif kind == "queue_insert":
            if i > 0:
                prev = records[i - 1]
                prev_sig = prev.get("signal") if prev.get("kind") == \
                    "admission" else prev.get("sig")
                if prev.get("kind") in ("admission", "fill") and \
                        prev_sig is r.get("signal"):
                    parent[i] = i - 1
        elif kind == "admission":
            sig = r.get("signal")
            if r.get("from_queue") is False:
                route[i] = ("scan_direct" if id(sig) in scan_ids
                            else "scan_direct_unmatched")
            elif r.get("from_queue") is True:
                route[i] = "unknown_enclosing_pass"
                if last_pass is not None:
                    pp = records[last_pass]
                    for c in pp.get("candidates") or []:
                        if isinstance(c, dict) and c.get("signal") is sig:
                            parent[i] = last_pass
                            route[i] = f"queue_{pp.get('source')}"
                            break
            else:
                route[i] = "from_queue_not_boolean"
    return parent, route


# --------------------------------------------------------------------------
# Coverage: expected / reached / completed (design §4.5)
# --------------------------------------------------------------------------

def _site_state(expected, reached, completed, aborted, not_expected_reason):
    if reached and not expected:
        return {"state": "reached_unexpected", "verdict": "FAIL"}
    if expected and not reached:
        return {"state": "expected_not_reached", "verdict": "FAIL"}
    if reached and not completed:
        return {"state": "reached_not_completed", "verdict": "INCONCLUSIVE"}
    if reached:
        return {"state": "completed", "verdict": "EVALUATE"}
    return {"state": "not_expected", "reason": not_expected_reason,
            "verdict": "INCONCLUSIVE" if aborted else "PASS"}


def coverage(store, decision_path, records, derived):
    """Per registered call site. `records` raw, `derived` serialised."""
    done_key = ("report_completed" if decision_path == "report"
                else "monitor_completed")
    completed_run = store.get(done_key) is True
    aborted = not completed_run
    passes = {k: [d for r, d in zip(records, derived) if r.get("kind") == k]
              for k in ("fill_pass", "promotion_pass")}
    sites = {}
    if decision_path == "report":
        fps = passes["fill_pass"]
        sites["fill_pass"] = _site_state(
            store.get("fill_pass_expected") is True, bool(fps),
            bool(fps) and all(p.get("return_reason") for p in fps),
            aborted, "aborted_before")
        sites["fill_pass"]["count"] = len(fps)
        pps = [p for p in passes["promotion_pass"]
               if p.get("source") == "scan"]
        sites["promotion_scan"] = _site_state(
            store.get("promotion_expected") is True, bool(pps),
            bool(pps) and all(p.get("return_reason") for p in pps),
            aborted, "aborted_before")
        sites["promotion_scan"]["count"] = len(pps)
        signals = store.get("signals")
        signals_seen = _len_or_none(signals) if signals is not None else None
        expected_n = store.get("scan_open_trade_expected")
        scan_adm = [d for r, d in zip(records, derived)
                    if r.get("kind") == "admission"
                    and r.get("from_queue") is False]
        reached_n = len(scan_adm)
        completed_n = sum(1 for d in scan_adm if d.get("returned") is not None)
        if signals is None:
            st = {"state": "not_expected",
                  "reason": "aborted_before" if aborted else "scan_not_run",
                  "verdict": "INCONCLUSIVE" if aborted else "FAIL"}
        elif signals_seen == 0 and expected_n == 0 and reached_n == 0:
            st = {"state": "not_expected", "reason": "no_signals",
                  "verdict": "PASS"}
        elif expected_n != reached_n:
            st = {"state": "expected_not_reached"
                  if (expected_n or 0) > reached_n else "reached_unexpected",
                  "verdict": "FAIL"}
        elif completed_n != reached_n:
            st = {"state": "reached_not_completed", "verdict": "INCONCLUSIVE"}
        elif signals_seen != expected_n:
            st = {"state": "signals_not_all_admitted",
                  "verdict": "INCONCLUSIVE" if aborted else "FAIL"}
        else:
            st = {"state": "completed", "verdict": "EVALUATE"}
        st.update({"signals_seen": signals_seen, "expected": expected_n,
                   "reached": reached_n, "completed": completed_n})
        sites["scan_admissions"] = st
        stray = [p for p in passes["promotion_pass"]
                 if p.get("source") != "scan"]
        if stray:
            sites["unregistered_promotion_sources"] = {
                "state": "reached_unexpected", "verdict": "FAIL",
                "count": len(stray)}
    else:
        pps = [p for p in passes["promotion_pass"]
               if p.get("source") == "monitor"]
        sites["promotion_monitor"] = _site_state(
            store.get("promotion_expected") is True, bool(pps),
            bool(pps) and all(p.get("return_reason") for p in pps),
            aborted, "aborted_before" if aborted else "no_state_update")
        sites["promotion_monitor"]["count"] = len(pps)
        if passes["fill_pass"]:
            sites["unregistered_fill_pass"] = {
                "state": "reached_unexpected", "verdict": "FAIL",
                "count": len(passes["fill_pass"])}
    expected_cands = sum(
        1 for d in derived if d.get("kind") == "promotion_pass"
        for c in d.get("candidates", []) if c.get("open_trade_expected"))
    queue_adm = [d for r, d in zip(records, derived)
                 if r.get("kind") == "admission" and r.get("from_queue") is True]
    linked = [d for d in queue_adm if d.get("parent_seq") is not None]
    st = {"expected": expected_cands, "reached": len(queue_adm),
          "linked": len(linked),
          "completed": sum(1 for d in queue_adm
                           if d.get("returned") is not None)}
    if expected_cands == len(queue_adm) == len(linked):
        st["state"] = ("completed" if st["completed"] == len(queue_adm)
                       else "reached_not_completed")
        st["verdict"] = ("EVALUATE" if st["state"] == "completed"
                         else "INCONCLUSIVE")
        if expected_cands == 0:
            st.update({"state": "not_expected", "verdict": "PASS"})
    else:
        st.update({"state": "expected_not_reached"
                   if expected_cands > len(linked) else "reached_unexpected",
                   "verdict": "FAIL"})
    sites["promotion_admissions"] = st
    return sites


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def _append(path, payload):
    """Append one JSONL line. True on success, never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, sort_keys=True, default=str)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except Exception:                                     # noqa: BLE001
        return False


def derive_all(store):
    """Serialisable records for one store, in capture order. Pure."""
    raw_records = [r for r in (store.get("records") or [])
                   if isinstance(r, dict)]
    events = [e for e in (store.get("queue_events") or [])]
    parent, route = _link(raw_records, store)
    children = {}
    for i, p in parent.items():
        children.setdefault(p, []).append(i)
    qi_views = {}
    out = []
    for i, r in enumerate(raw_records):
        if r.get("kind") == "queue_insert":
            v = []
            qi_views[i] = (_derive_queue_insert(r, events, v), v)
    for i, r in enumerate(raw_records):
        violations = []
        kind = r.get("kind")
        child_qi = None
        for c in children.get(i, []):
            if c in qi_views:
                child_qi = qi_views[c][0]
        if kind == "admission":
            body = _derive_admission(r, route.get(i), child_qi, violations)
            if route.get(i) in ("scan_direct_unmatched",
                                "unknown_enclosing_pass",
                                "from_queue_not_boolean"):
                violations.append(f"route:{route.get(i)}")
        elif kind == "queue_insert":
            body, violations = qi_views[i]
            if i not in parent:
                violations = violations + ["queue_insert_without_parent"]
        elif kind == "fill":
            body = _derive_fill(r, child_qi, violations)
            if i not in parent:
                violations.append("fill_without_pass")
        elif kind == "fill_pass":
            body = _derive_fill_pass(r, violations)
        elif kind == "promotion_pass":
            body = _derive_promotion_pass(r, violations)
        elif kind == "queue_expiry":
            body = _derive_expiry(r, events, violations)
        elif kind == "queue_maintenance":
            body = _derive_maintenance(r, violations)
        else:
            body = {}
            violations.append(f"unknown_kind:{kind}")
        rec = {"kind": kind, "seq": i, "parent_seq": parent.get(i)}
        rec.update(body)
        rec["contract_violations"] = sorted(set(violations))
        rec["record_complete"] = not rec["contract_violations"]
        out.append(rec)
    return raw_records, out


def flush(store, decision_path, peer=None):
    """Serialise one process run. NEVER raises, and is idempotent."""
    summary = {"attempted": 0, "written": 0, "write_failures": 0,
               "heartbeat_written": False, "flush_error": None,
               "skipped_already_flushed": False}
    try:
        if store.get("flushed"):
            summary["skipped_already_flushed"] = True
            summary["heartbeat_written"] = True
            return summary
        store["flushed"] = True
        cycle_id = str(uuid.uuid4())[:8]
        commit = _git_commit()
        mans = manifests()
        run_kind, run_kind_detect = _run_kind()
        envelope = {
            "record_schema_version": RECORD_SCHEMA_VERSION,
            "decision_path": decision_path,
            "cycle_id": cycle_id,
            "invocation_token": store.get("invocation_token"),
            "run_kind": run_kind,
            "run_kind_detect": run_kind_detect,
            "repository_commit": commit,
        }
        raw_records, derived = derive_all(store)
        summary["attempted"] = len(derived)
        for rec in derived:
            full = dict(envelope)
            full.update(rec)
            if _append(records_path(), full):
                summary["written"] += 1
            else:
                summary["write_failures"] += 1
        events = list(store.get("queue_events") or [])
        peer_started, peer_token, peer_status = None, None, "not_applicable"
        if decision_path == "monitor":
            try:
                peer_started = peer.get("cycle_started_at")
                peer_token = peer.get("invocation_token")
                peer_status = ("identified" if peer_started
                               else "peer_unidentified")
            except Exception:                             # noqa: BLE001
                peer_status = "peer_unavailable"
        sites = coverage(store, decision_path, raw_records, derived)
        beat = {
            "heartbeat_schema_version": HEARTBEAT_SCHEMA_VERSION,
            "record_schema_version": RECORD_SCHEMA_VERSION,
            "decision_path": decision_path,
            "decision_path_valid": decision_path in DECISION_PATHS,
            "cycle_id": cycle_id,
            "invocation_token": store.get("invocation_token"),
            "run_kind": run_kind,
            "run_kind_detect": run_kind_detect,
            "repository_commit": commit,
            "lineage_complete": bool(commit),
            "started": _iso(store.get("t_reset")),
            "flushed_at": _now(),
            "report_completed": store.get("report_completed") is True,
            "monitor_completed": store.get("monitor_completed") is True,
            "loop_exception": _plain(store.get("loop_exception")),
            "records_attempted": summary["attempted"],
            "records_written": summary["written"],
            "write_failures": summary["write_failures"],
            "records_incomplete": sum(1 for d in derived
                                      if not d["record_complete"]),
            "queue_events": [[_plain(x) for x in e] if isinstance(e, tuple)
                             else _plain(e) for e in events],
            "queue_event_write_failures": sum(
                1 for e in events if isinstance(e, tuple) and len(e) == 3
                and e[2] is False),
            "call_sites": sites,
            "t_scan_start": _iso(store.get("t_scan_start")),
            "t_scan_end": _iso(store.get("t_scan_end")),
            "promotion_expected": store.get("promotion_expected") is True,
            "peer_cycle_started_at": peer_started,
            "peer_invocation_token": peer_token,
            "peer_status": peer_status,
            "decision_manifest": mans["decision"]["digests"],
            "classification_manifest": mans["classification"]["digests"],
            "observation_manifest": mans["observation"]["digests"],
            "manifests_complete": all(m["complete"] for m in mans.values()),
            "manifest_missing": sum((m["missing"] for m in mans.values()), []),
        }
        summary["heartbeat_written"] = _append(heartbeat_path(), beat)
    except Exception as exc:                              # noqa: BLE001
        summary["flush_error"] = f"{type(exc).__name__}: {exc}"
    return summary
