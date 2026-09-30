"""Pre-shadow state snapshot — Phase 4 gate artifact.

    python3 tools/pre_parity_snapshot.py            # print
    python3 tools/pre_parity_snapshot.py --write     # also save timestamped JSON

Read-only. Touches no production state, sends nothing, and is not referenced by
any scheduled entry point. Re-run immediately before Phase 4 starts and again
before Phase 5 wiring: these values move daily, so a stale snapshot is worse
than none.

Captures the Phase 3 gate evidence plus the boundary state that makes ABM and
SDGR valuable shadow subjects.
"""
import ast
import hashlib
import json
import math
import platform
import re
import subprocess
import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import parity_compare as sc  # noqa: E402
from engine.tracker_compat import CONTRACT_VERSION  # noqa: E402

SUITES = {
    "adapter": "tests/test_tracker_compat.py",
    "classifier": "tests/test_parity_compare.py",
    "wiring": "tests/test_parity_runner.py",
    "evaluator": "tests/test_parity_eval.py",
    "bridge": "tests/test_parity_hook.py",
    # Phase 0.5. Registered here so the baseline cannot silently under-report:
    # an unregistered suite contributes zero assertions and its failures never
    # reach the gate, which is the same false-pass shape as an unregistered test.
    "phase05_capture": "tests/test_phase05_capture.py",
    "phase05_live_path": "tests/test_phase05_live_path.py",
    "phase05_integrity": "tests/test_phase05_integrity.py",
    "phase05_network": "tests/test_phase05_network.py",
    "blast_radius": "tests/test_blast_radius.py",
    "output_path": "tests/test_output_path.py",
    "tracker_diff": "tests/test_tracker_diff.py",
    "vps_verify": "tests/test_vps_verify_failure_modes.py",
    "parity_output": "tests/test_parity_output_gate.py",
    # Schedule-aware collection freshness and the states derived from it.
    "parity_freshness": "tests/test_parity_freshness.py",
    # Phase 5 readiness gates and required-coverage dispositions.
    "phase5_gates": "tests/test_phase5_gates.py",
}

PRE_CLEAN = {"ABM": "2026-09-09", "SDGR": "2026-09-18"}
ROLLBACK_TAG = "pre-tracker-swap"

# Fixed registry. Membership is the contract: a gate missing from the computed
# set is a blocker, so deleting a check can never quietly reduce the requirement.
PHASE5_GATE_REGISTRY = (
    "deployment_integrity",
    "evidence_valid",
    "collection_recent",
    "phase2_clearance",
    "required_coverage_resolution",
    "minimum_closed_sample",
    "explicit_operator_authorization",
)

# UNREGISTERED ON PURPOSE. A threshold picked now, knowing the closed count, would
# be chosen to be met. None means "not preregistered", and the gate reports
# evaluated=False, which blocks.
MIN_CLOSED_SAMPLE = None
MIN_CLOSED_SAMPLE_POPULATION = (
    "undeclared: must specify clean_v3 vs parity-covered exits, the exact "
    "integer, contamination exclusions, and which implementation epoch counts")

# Human decision, never computed. Kept separate from phase5_gates_satisfied so an
# all-green evidence state cannot become an authorization by itself.
OPERATOR_AUTHORIZATION = False

COVERAGE_STATES = ("satisfied", "pending", "unobtainable")
COVERAGE_DISPOSITIONS = ("active", "retired", "replaced")

# What each registered symbol must DEMONSTRATE, not merely appear in. Entry dates
# come from PRE_CLEAN so the cohort labelling and the coverage requirement cannot
# disagree about which position is meant.
REQUIRED_COVERAGE = {
    "ABM": {"entry_date": PRE_CLEAN["ABM"],
            "required_event": "natural exit at the stop/trail equality boundary",
            "required_action": "exit"},
    "SDGR": {"entry_date": PRE_CLEAN["SDGR"],
             "required_event": "dead-band lifecycle through natural exit",
             "required_action": "exit"},
}

# Retirement is an OPERATOR DECISION recorded as data, never an inference. It
# changes the disposition only: coverage_state stays "unobtainable" forever,
# because rewriting missing evidence as satisfied is the one thing a retirement
# must never be able to do.
COVERAGE_RETIREMENTS = {
    "SDGR": {
        "disposition": "retired",
        "decided_at": "2026-09-30",
        "required_coverage": "dead-band lifecycle through natural exit",
        "entry_date": "2026-09-18",
        "exit_date": "2026-09-28",
        "exit_reason_recorded_by_inline": "trailing_stop",
        "exit_executed_on_cycle": "2026-09-29T05:35Z run (commit 14a324e)",
        "parity_activation_effective_at": "2026-09-29T05:52:22.528410+00:00",
        "required_exit_record": "absent",
        "future_observability": False,
        "reason": ("the position closed before Phase 4 collection was active, "
                   "roughly 17 minutes before the first parity record; the "
                   "required exit cycle can never be re-observed"),
        "acknowledgement": ("this retirement ACKNOWLEDGES MISSING EVIDENCE and "
                            "does not classify the requirement as satisfied"),
        "substitute_accepted": None,
        "note": ("SECZ dead-band observations are supplemental and do NOT "
                 "retroactively satisfy SDGR's registered requirement"),
    },
}


def _evidence_records():
    """Parsed evidence records, or [] when the file is absent or unreadable.

    Returning [] on absence is safe HERE because every coverage state derived
    from an empty set is non-satisfied: no records means pending or unobtainable,
    never satisfied. Schema validity is proven separately by the validator, which
    fails closed on the same file.
    """
    p = ROOT / PARITY_OUTPUT
    if not p.exists():
        return []
    recs = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    return recs


def _coverage_evidence(sym, spec, records):
    """Find a record that actually DEMONSTRATES the required event.

    Presence of the symbol is not coverage. An ABM record written while the
    position was still open says nothing about its exit, so the search is for the
    required ACTION with an affirmative comparison result.
    """
    for r in records:
        if r.get("symbol") != sym:
            continue
        if r.get("parity_action") != spec["required_action"]:
            continue
        if r.get("record_schema_version") not in RECORD_FIELDS_BY_VERSION:
            continue
        if r.get("difference_class") != "MATCH":
            continue
        if r.get("would_change_action") is not False:
            continue
        if r.get("inline_exit_reason") != r.get("shadow_exit_reason"):
            continue
        if r.get("inline_effective_stop") != r.get("shadow_effective_stop"):
            continue
        if r.get("inline_effective_stop") is None:
            continue
        return r
    return None


def required_coverage(records, open_symbols):
    """Resolve every registered obligation to exactly one of three states.

    The distinction this exists to preserve: once a symbol is closed, satisfied
    and unobtainable are both "not open", and a still_open test collapses them.
    ABM exited WITH evidence; SDGR exited WITHOUT. Those must never look alike.
    """
    out = {}
    for sym, spec in REQUIRED_COVERAGE.items():
        rec = _coverage_evidence(sym, spec, records)
        still_open = sym in open_symbols
        if rec is not None:
            state = "satisfied"
        elif still_open:
            state = "pending"
        else:
            # Closed with no demonstrating record. The required event cannot
            # recur, so this is not a "not yet" -- it is a permanent absence.
            state = "unobtainable"
        retirement = COVERAGE_RETIREMENTS.get(sym)
        disposition = (retirement or {}).get("disposition", "active")
        if disposition not in COVERAGE_DISPOSITIONS:
            disposition = "active"          # unrecognised never weakens anything
        # A retirement can only stop an UNOBTAINABLE requirement from blocking.
        # Applied to a pending one it would excuse a still-collectable gap.
        blocking = state != "satisfied" and not (
            state == "unobtainable" and disposition in ("retired", "replaced"))
        entry = {
            "required_event": spec["required_event"],
            "required_action": spec["required_action"],
            "expected_entry_date": spec["entry_date"],
            "still_open": still_open,
            "coverage_state": state,
            "requirement_disposition": disposition,
            "blocking": blocking,
            "retirement": retirement,
            "evidence": None,
        }
        if rec is not None:
            entry["evidence"] = {
                "cycle_id": rec.get("cycle_id"),
                "timestamp": rec.get("timestamp"),
                "entry_date": rec.get("entry_date"),
                "observed_action": rec.get("parity_action"),
                "inline_exit_reason": rec.get("inline_exit_reason"),
                "shadow_exit_reason": rec.get("shadow_exit_reason"),
                "inline_effective_stop": rec.get("inline_effective_stop"),
                "shadow_effective_stop": rec.get("shadow_effective_stop"),
                "inline_effective_stop_basis":
                    rec.get("inline_effective_stop_basis"),
                "difference_class": rec.get("difference_class"),
                "would_change_action": rec.get("would_change_action"),
                "record_schema_version": rec.get("record_schema_version"),
                "production_commit": rec.get("production_commit"),
                "boundary": rec.get("abm_equality_boundary"),
            }
            if rec.get("entry_date") != spec["entry_date"]:
                # A matching symbol from a LATER re-entry is a different position
                # and cannot discharge the registered obligation.
                entry["coverage_state"] = "pending" if still_open else \
                    "unobtainable"
                entry["blocking"] = True
                entry["evidence"]["rejected"] = (
                    f"entry_date {rec.get('entry_date')} != registered "
                    f"{spec['entry_date']}: different position")
    # Denominator stays visible. Reporting "1 of 1 satisfied" after quietly
    # dropping SDGR would be the misreading this accounting prevents.
        out[sym] = entry
    return out


def coverage_tally(cov):
    return {
        "obligations": len(cov),
        "satisfied": sum(1 for v in cov.values()
                         if v["coverage_state"] == "satisfied"),
        "pending": sum(1 for v in cov.values()
                       if v["coverage_state"] == "pending"),
        "retired_unobtainable": sum(
            1 for v in cov.values()
            if v["coverage_state"] == "unobtainable"
            and v["requirement_disposition"] in ("retired", "replaced")),
        "unresolved_unobtainable": sum(
            1 for v in cov.values()
            if v["coverage_state"] == "unobtainable"
            and v["requirement_disposition"] == "active"),
        "empirically_complete": all(v["coverage_state"] == "satisfied"
                                    for v in cov.values()),
        "administratively_resolved": all(not v["blocking"]
                                         for v in cov.values()),
    }


IMPLEMENTATION_GLOBS = ("engine/", "tests/", "tools/pre_parity_snapshot.py",
                        "daily_report.py")

# Operational state the live host legitimately rewrites every cycle. On a live
# host, logs_unchanged_since_tag is a GUARANTEED failure, and a permanently
# failing gate teaches reviewers to ignore the suite. It is therefore scoped to
# development checkouts, and the live host gets a gate that actually discriminates.
ALLOWED_RUNTIME_LOGS = (
    "logs/psi_state.json",
    "logs/virtual_trades.json",
    "logs/trades_report.csv",
    "logs/signal_queue.json",
    "logs/queue_ranked.json",
    # Unignored by a scoped .gitignore negation and committed by the bot every
    # cycle it changes, but never registered here -- so the first cycle that
    # touched it failed the allow-list gate. Latent since before Phase 4;
    # activation only exposed it.
    "logs/pending_signals.json",
    "logs/queue_events.jsonl",
    "logs/last_scan_summary.txt",
    # Phase 4 migration evidence. Append-only, bot-committed for off-host backup.
    # Validated by _parity_output_state(), not merely tolerated: allow-listing a
    # path only stops the log gate from flagging it, and on its own that would
    # replace one check with no check.
    "logs/tracker_parity_v1.jsonl",
    # Phase 4 collection heartbeat, appended every observed cycle. Registered
    # here BEFORE the first cycle writes it: pending_signals.json taught us that
    # an unignored, bot-committed, unregistered log fails the allow-list gate the
    # first time it changes, which looks like a regression in unrelated work.
    "logs/parity_heartbeat_v1.jsonl",
)
RUNTIME_COMMIT_AUTHORS = ("ares-bot@users.noreply.github.com",)

PARITY_OUTPUT = "logs/tracker_parity_v1.jsonl"
ACTIVATION_ENTRY_POINT = "daily_report.py"

# The phase-aware state contract. Single source: the display in
# tools/vps_phase3_verify.sh validates against THIS tuple rather than keeping its
# own list, so a state added here cannot be silently unrecognised there.
PARITY_OUTPUT_STATES = (
    "NOT_DECLARED_ABSENT",
    "ARMED_NOT_STARTED",
    # Collection never began: the first scheduled cycle after activation became
    # effective has passed its grace deadline with no heartbeat ever recorded.
    # Distinct from ACTIVE_STALE, which means collection began and later stopped.
    # Conflating them would hide which of two different faults occurred.
    "ARMED_NO_HEARTBEAT",
    "ACTIVE_VALID",
    # Evidence integrity intact, collection not current. Deliberately NOT folded
    # into ACTIVE_INVALID: the records already written remain valid and must not
    # be impeached because the collector stopped afterwards.
    "ACTIVE_STALE",
    "UNDECLARED_OUTPUT_PRESENT",
    "DECLARATION_MALFORMED",
    "UNPARSEABLE_JSONL",
    "SCHEMA_UNDERIVABLE",
    "SCHEMA_DRIFT",
    "LINEAGE_INCOMPLETE",
    "DECISION_CHANGING_MISMATCH_PRESENT",
    "EVIDENCE_TRUNCATED",
    "EVIDENCE_REWRITTEN",
    "DECLARED_BUT_EMPTY",
)
# States for which an append_only block is expected. Before the first cycle no
# file exists, so requiring it unconditionally would fail a legitimate state.
# ACTIVE_STALE included: it is reached only from a fully-validated ACTIVE_VALID,
# so omitting it would silently DROP the append-only requirement at the exact
# moment collection stopped -- turning one check into no check.
PARITY_STATES_REQUIRING_APPEND_ONLY = ("ACTIVE_VALID", "ACTIVE_STALE")

PARITY_HEARTBEAT = "logs/parity_heartbeat_v1.jsonl"

# THE registered collection schedule -- the single source for expected-cycle math.
# Nothing else in the tree may hardcode these times; tools/vps_phase3_verify.sh
# reads this contract and diffs it against the live crontab, because a schedule
# asserted only in the repo is unfalsifiable: it would compute freshness against a
# fiction and pass while the real cron said something else.
#
# Weekday-only, UTC, minutes past midnight. Deliberately NOT market-calendar
# aware: the question is whether the COLLECTOR ran, and cron fires on US market
# holidays too. The trading calendar governs Item 3b's expected data bar, not
# instrumentation liveness.
PARITY_CYCLE_SCHEDULE_UTC = ((13, 30), (21, 0))
PARITY_CYCLE_WEEKDAYS = (0, 1, 2, 3, 4)          # Mon-Fri, datetime.weekday()
# Time allowed for a scheduled cycle to complete before its heartbeat is overdue.
# A fixed hour-count threshold cannot work: Friday 21:00 to Monday 13:30 is 64.5h,
# so 36h and 48h both cry wolf every weekend, and 72h would hide several missed
# weekday cycles.
PARITY_CYCLE_GRACE_MINUTES = 90


def _scheduled_cycles(day):
    """The datetimes at which collection is expected on `day`, or () if none."""
    if day.weekday() not in PARITY_CYCLE_WEEKDAYS:
        return ()
    return tuple(datetime(day.year, day.month, day.day, h, m,
                          tzinfo=timezone.utc)
                 for h, m in sorted(PARITY_CYCLE_SCHEDULE_UTC))


def last_due_cycle(now):
    """Most recent scheduled cycle whose grace period has already expired.

    Returns None when no scheduled cycle is yet overdue -- which is the correct
    answer all weekend, and the reason this is schedule-aware rather than a fixed
    age limit. Searches back 10 days to cross a long holiday weekend.
    """
    deadline_delta = timedelta(minutes=PARITY_CYCLE_GRACE_MINUTES)
    for back in range(0, 10):
        day = (now - timedelta(days=back))
        for cyc in reversed(_scheduled_cycles(day)):
            if now >= cyc + deadline_delta:
                return cyc
    return None


def next_cycle_deadline(after):
    """Deadline of the first scheduled cycle strictly after `after`.

    Used for ARMED_NOT_STARTED: until this moment passes, a missing heartbeat is
    a legitimate armed state rather than a failed collector.
    """
    deadline_delta = timedelta(minutes=PARITY_CYCLE_GRACE_MINUTES)
    for fwd in range(0, 10):
        day = after + timedelta(days=fwd)
        for cyc in _scheduled_cycles(day):
            if cyc > after:
                return cyc + deadline_delta
    return None


# Host role is an EXPLICIT contract, never an inference. The previous detector
# read "operational logs changed" as "this is the live VPS", which is false: a
# development checkout that pulls the bot's log commits has identical git history.
# Repository contents cannot identify the machine executing the verifier.
#
# Hostname is also rejected: it can be changed, cloned, or reproduced in a
# container, and a wrong "live" reading would let a laptop assert the collector is
# healthy when it cannot observe the collector at all.
HOST_MARKER_NAME = ".ares_live_host"
HOST_MARKER_CONTENT = "ARES_LIVE_HOST_V1"
HOST_MARKER_PATH = ROOT.parent / HOST_MARKER_NAME   # outside the repo, uncommitted
HOST_CONTEXT_ENV = "ARES_HOST_CONTEXT"
HOST_CONTEXTS = ("live", "archive")


def _host_role(env=None, marker_path=None):
    """Resolve host role. Precedence: env override, marker, absence, malformed.

    Returns 'live', 'archive', or 'unknown'. 'unknown' fails closed for phase
    readiness but must NOT block a repair deployment -- a host that cannot
    identify itself is still allowed to install the fix that makes it identifiable.
    """
    env = os.environ if env is None else env
    p = Path(marker_path) if marker_path is not None else HOST_MARKER_PATH
    out = {"role": None, "source": None, "marker_path": str(p),
           "marker_present": None, "detail": None}

    override = env.get(HOST_CONTEXT_ENV)
    if override is not None:
        out["source"] = f"env:{HOST_CONTEXT_ENV}"
        if override in HOST_CONTEXTS:
            out["role"] = override
            out["detail"] = "explicit override (test seam)"
        else:
            # An unrecognised override is a configuration error. Falling through
            # to marker detection would let a typo silently restore inference.
            out["role"] = "unknown"
            out["detail"] = (f"invalid {HOST_CONTEXT_ENV}={override!r}; "
                             f"expected one of {HOST_CONTEXTS}")
        return out

    out["source"] = "marker"
    try:
        out["marker_present"] = p.exists()
    except Exception as exc:                        # noqa: BLE001
        out["role"] = "unknown"
        out["detail"] = f"marker unstattable: {type(exc).__name__}: {exc}"
        return out
    if not out["marker_present"]:
        # Only the live host is provisioned. A checkout without the marker is
        # naturally an archive consumer -- including one holding bot log commits.
        out["role"] = "archive"
        out["detail"] = "no marker; development or archive checkout"
        return out
    try:
        body = p.read_text().strip()
    except Exception as exc:                        # noqa: BLE001
        out["role"] = "unknown"
        out["detail"] = f"marker unreadable: {type(exc).__name__}: {exc}"
        return out
    if body == HOST_MARKER_CONTENT:
        out["role"] = "live"
        out["detail"] = "valid live-host marker"
    else:
        out["role"] = "unknown"
        out["detail"] = f"malformed marker body {body[:40]!r}"
    return out


def _as_utc(v):
    """Coerce str/datetime/None to an aware UTC datetime, or None."""
    if v is None:
        return None
    d = datetime.fromisoformat(v) if isinstance(v, str) else v
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


# Explicit enumeration, strongest first. Prevents a future reader from treating a
# git author timestamp as equivalent to an observed VPS activation.
ACTIVATION_TIMESTAMP_SOURCES = ("vps_snapshot", "first_parity_record",
                                "deployment_log", "commit_timestamp_fallback")

# Seed values, used ONLY when the baseline does not already carry them. Once
# written they are preserved, so these literals never override recorded history.
#
# The declaration commit that set ARES_PARITY=1 in run_ares.sh.
ACTIVATION_COMMIT = "64f6b9c"
# The first observed parity record's timestamp. This is an UPPER BOUND, not the
# transition itself: collection was definitely active by then, but became
# effective at some point between the declaration deploy and this cycle. The
# declaration commit was authored 2026-09-29T03:30:28Z, so the true transition
# lies in that ~2h22m window. Recorded as an upper bound rather than narrowed by
# guesswork -- inventing precision here would corrupt the ARMED deadline math that
# depends on it. Upgrade to source=vps_snapshot, confidence=observed if the 5B
# activation snapshot showing ARMED_NOT_STARTED is recovered from the VPS.
ACTIVATION_EFFECTIVE_AT = "2026-09-29T05:52:22.528410+00:00"
ACTIVATION_TIMESTAMP_SOURCE = "first_parity_record"
ACTIVATION_CONFIDENCE = "upper_bound"


def _activation_effective_at():
    """When the VPS first VERIFIED the active declaration in the deployed tree.

    Not the declaration commit date. The commit identifies the code version; only
    a VPS-side snapshot identifies when that code actually became effective
    there. Using the commit date would let the verifier blame the collector for
    the pull latency between commit and deploy.
    """
    rel = "tools/parity_baseline.json"
    out = {"effective_at": None, "parsed": None, "baseline": rel,
           "commit": None, "timestamp_source": None, "confidence": None,
           "error": None}
    try:
        data = json.loads((ROOT / rel).read_text())
        out["effective_at"] = data.get("parity_activation_effective_at")
        out["commit"] = data.get("parity_activation_commit")
        out["timestamp_source"] = data.get("parity_activation_timestamp_source")
        out["confidence"] = data.get("parity_activation_timestamp_confidence")
        out["parsed"] = _as_utc(out["effective_at"])
        # An unlabelled timestamp is refused. Provenance is the whole point: a git
        # author timestamp and an observed VPS transition are not interchangeable,
        # and a bare datetime invites a future reader to treat them as equal.
        if out["effective_at"] and out["timestamp_source"] not in \
                ACTIVATION_TIMESTAMP_SOURCES:
            out["parsed"] = None
            out["error"] = (
                f"activation timestamp has unregistered source "
                f"{out['timestamp_source']!r}; expected one of "
                f"{ACTIVATION_TIMESTAMP_SOURCES}")
    except Exception as exc:                        # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _parity_heartbeat(now=None):
    """Read the collection heartbeat. Answers 'did a cycle RUN', not 'did it
    produce records'.

    `now` is injectable: a gate whose verdict depends on the wall clock is not
    reproducible, and the tests must be able to pin time rather than pass or fail
    according to when the suite happens to run.
    """
    p = ROOT / PARITY_HEARTBEAT
    out = {"path": PARITY_HEARTBEAT, "present": p.exists(), "beats": 0,
           "last_timestamp": None, "last_cycle_id": None,
           "last_production_commit": None, "age_hours": None,
           "stale": None, "grace_minutes": PARITY_CYCLE_GRACE_MINUTES,
           "schedule_utc": PARITY_CYCLE_SCHEDULE_UTC,
           "last_due_cycle": None, "covers_last_due_cycle": None,
           "identities": 0, "malformed": [], "last_errors": {}}
    if not p.exists():
        return out
    beats = []
    for i, line in enumerate(p.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            beats.append(json.loads(line))
        except Exception as exc:
            out["malformed"].append(f"heartbeat line {i}: {exc}")
    out["beats"] = len(beats)
    if not beats:
        return out
    last = beats[-1]
    out["last_timestamp"] = last.get("cycle_completed_at")
    out["last_cycle_id"] = last.get("cycle_id")
    out["last_production_commit"] = last.get("production_commit")
    out["last_attempted"] = last.get("attempted")
    out["last_written"] = last.get("written")
    # Composite identity: the capture token restarts at 1 in each cron process,
    # so counting distinct tokens would collapse every cycle into one.
    out["identities"] = len({(b.get("production_commit"),
                              b.get("cycle_started_at"),
                              b.get("capture_cycle_token")) for b in beats})
    # attempted != written means positions were observed but not all recorded --
    # a silent partial collection that a bare freshness check would call healthy.
    out["incomplete_cycles"] = [
        {"cycle_id": b.get("cycle_id"), "attempted": b.get("attempted"),
         "written": b.get("written")}
        for b in beats
        if isinstance(b.get("attempted"), int)
        and isinstance(b.get("written"), int)
        and b.get("attempted") != b.get("written")]
    out["last_errors"] = {
        k: last.get(k) for k in ("shadow_system_error", "capture_error",
                                 "results_error", "write_failures")
        if last.get(k)}
    ts = last.get("cycle_completed_at")
    try:
        when = datetime.fromisoformat(str(ts))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        ref = now or datetime.now(timezone.utc)
        if isinstance(ref, str):
            ref = datetime.fromisoformat(ref)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        out["age_hours"] = round((ref - when).total_seconds() / 3600.0, 2)
        due = last_due_cycle(ref)
        out["last_due_cycle"] = due.isoformat() if due else None
        if due is None:
            # No scheduled cycle is overdue -- true all weekend. Not stale, and
            # not "unknown" either: the schedule says nothing was expected.
            out["covers_last_due_cycle"] = True
            out["stale"] = False
        else:
            out["covers_last_due_cycle"] = when >= due
            out["stale"] = not out["covers_last_due_cycle"]
    except Exception as exc:
        # Unparseable timestamp leaves stale as None -- UNKNOWN, never False. A
        # freshness check that cannot run has not passed.
        out["malformed"].append(
            f"unparseable cycle_completed_at {ts!r}: {exc}")
    return out


# Schema v1, frozen as an immutable literal on 2026-09-29 from the AST derivation
# below, at the commit that produced the first four live records.
#
# It CANNOT stay AST-derived. The derivation returns whatever build_record emits
# today, so the moment v2 adds a field the v1 record set would silently widen and
# the four existing records -- which must remain valid byte-for-byte -- would be
# judged against a schema that did not exist when they were written. History needs
# a literal; only the CURRENT version can be derived. _builder_record_fields()
# below is asserted equal to the newest registered version, so the single-source
# property is kept exactly where it still applies.
V1_RECORD_FIELDS = frozenset({
    "abm_equality_boundary", "available_history_bars", "bar_bearish_div",
    "bar_date", "bar_price", "bar_rsi", "capture_cycle_token", "capture_defect",
    "compatibility_contract", "cycle_id", "difference_class", "differing_fields",
    "entry_date", "exception_message", "exception_type", "exit_policy_md5",
    "inline_decision", "inline_effective_stop", "inline_evaluation_status",
    "inline_exception_message", "inline_exception_type", "inline_exit_reason",
    "inline_persistent_state", "inline_scaled_out", "marker_contract_match",
    "parity_action", "price_source", "production_commit",
    "production_state_hash_after", "production_state_hash_before",
    "record_schema_version", "sdgr_dead_band_state", "shadow_compatible_state",
    "shadow_decision", "shadow_effective_stop", "shadow_evaluation_status",
    "shadow_exception_message", "shadow_exception_type", "shadow_exit_reason",
    "shadow_scaled_out", "symbol", "timestamp", "tracker_file",
    "tracker_source_hash", "would_change_action",
})

# v2 = v1 plus the effective-stop evidence. V1 is now frozen forever and is
# NEVER re-derived from the builder: the four records written before this field
# existed must keep being judged against the schema they were written under.
V2_RECORD_FIELDS = frozenset(V1_RECORD_FIELDS | {
    "inline_effective_stop_basis",
})

RECORD_FIELDS_BY_VERSION = {1: V1_RECORD_FIELDS, 2: V2_RECORD_FIELDS}
CURRENT_RECORD_SCHEMA = max(RECORD_FIELDS_BY_VERSION)

# Permitted values of inline_effective_stop_basis in v2. Each maps to a
# demonstrated control-flow path in tracker.check_open_trades; there is no
# catch-all, so an unexplained absence carries basis None and is REJECTED rather
# than silently labelled.
V2_EFFECTIVE_STOP_BASES = frozenset({
    "captured_inline_local",
    "not_computed_entry_day_skip",
    "not_computed_inline_failure",
})


def _is_ancestor_or_same(candidate, base):
    """Is `candidate` the commit `base`, or a descendant of it?

    Ancestry, not lexical comparison: SHAs have no meaningful ordering, so a
    string >= test would be arbitrary. Returns None when the question cannot be
    answered -- an unknown or ambiguous ref must not be reported as either
    satisfied or violated.
    """
    if not candidate or not base:
        return None
    try:
        full_c = _git("rev-parse", "--verify", f"{candidate}^{{commit}}").strip()
        full_b = _git("rev-parse", "--verify", f"{base}^{{commit}}").strip()
    except Exception:
        return None
    if not full_c or not full_b:
        return None
    if full_c == full_b:
        return True
    r = subprocess.run(["git", "-C", str(ROOT), "merge-base",
                        "--is-ancestor", full_b, full_c],
                       capture_output=True, text=True)
    if r.returncode not in (0, 1):
        return None
    return r.returncode == 0


def schema_activation_consistency(records, activation_commit):
    """Cross-check each record's schema version against the code that wrote it.

    A record's OWN record_schema_version governs field validation; this is a
    separate consistency question: did a v1 record get written by post-activation
    code, or a v2 record by pre-activation code? Either means the declared schema
    and the executing code disagree.

    Returns (failures, unresolved). When the activation commit is unknown the
    check is reported as UNRESOLVED rather than passing -- a check that cannot
    run has not succeeded.
    """
    failures, unresolved = [], []
    if not activation_commit:
        return failures, ["schema-v2 activation commit not recorded"]
    for n, r in enumerate(records, 1):
        pc = r.get("production_commit")
        ver = r.get("record_schema_version")
        after = _is_ancestor_or_same(pc, activation_commit)
        if after is None:
            # Short SHAs resolve against repository history; an ambiguous or
            # missing ref is unresolved, never assumed.
            unresolved.append(
                f"record {n} production_commit {pc!r} not resolvable against "
                f"activation {activation_commit[:7]}")
            continue
        if ver == 1 and after:
            failures.append(
                f"record {n} declares schema 1 but was written at or after the "
                f"v2 activation commit ({pc})")
        elif ver is not None and ver >= 2 and not after:
            failures.append(
                f"record {n} declares schema {ver} but was written before the "
                f"v2 activation commit ({pc})")
    return failures, unresolved


def frozen_record_fields(version):
    """Required field set for ONE schema version. Fails closed on unknown.

    An unrecognised version must never be validated against the newest known
    field set: that would let a future writer emit anything and have it approved
    by a validator that does not understand it.
    """
    try:
        return RECORD_FIELDS_BY_VERSION[version]
    except (KeyError, TypeError):
        raise RuntimeError(
            f"unsupported record_schema_version {version!r}; "
            f"registered: {sorted(RECORD_FIELDS_BY_VERSION)}")


def _builder_record_fields():
    """The record schema, DERIVED from build_record itself.

    Not a local list. A hand-copied field set drifts the moment build_record
    changes, and the gate would keep validating a schema that no longer exists
    while still reporting success. Raises if the structure cannot be found --
    an underivable schema is a failure, not an empty requirement.
    """
    src = (ROOT / "engine" / "parity_compare.py").read_text()
    fn = [n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.FunctionDef) and n.name == "build_record"]
    if not fn:
        raise RuntimeError("build_record not found in engine/parity_compare.py")
    dicts = [n for n in ast.walk(fn[0])
             if isinstance(n, ast.Dict) and len(n.keys) > 10]
    if not dicts:
        raise RuntimeError("build_record's record dict not found")
    d = max(dicts, key=lambda n: len(n.keys))
    keys = {k.value for k in d.keys if isinstance(k, ast.Constant)}
    if len(keys) != len(d.keys):
        raise RuntimeError("build_record has computed keys; schema not derivable")
    return keys


def _collection_declared():
    """Is Phase 4 collection declared, AND declared where it takes effect?

    Placement is the whole point. run_ares.sh sources /root/ares/.env AFTER
    daily_report.py runs, so a declaration below the Python invocation -- or in
    .env -- never reaches the trading process. Parity would stay off while
    appearing configured, and the only symptom would be an empty evidence file
    indistinguishable from "no positions to compare".

    A loose search for the export anywhere in the file cannot tell those apart,
    so this walks the script in line order and tracks the value as the shell
    would, up to the entry point.
    """
    out = {"declared": False, "valid": True, "failures": [],
           "export_lines": [], "entry_point_line": None, "value": None}
    path = ROOT / "run_ares.sh"
    if not path.exists():
        out["valid"] = False
        out["failures"].append("run_ares.sh absent")
        return out

    lines = path.read_text().splitlines()
    entry = None
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        if re.search(rf"python3?\s+{re.escape(ACTIVATION_ENTRY_POINT)}\b",
                     stripped):
            entry = i
            break
    out["entry_point_line"] = entry
    if entry is None:
        out["valid"] = False
        out["failures"].append(
            f"{ACTIVATION_ENTRY_POINT} invocation not found; placement of the "
            "declaration cannot be verified")
        return out

    # Assignments and unsets in line order, tracking the effective value.
    value = None
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        m = re.match(r"(?:export\s+)?ARES_PARITY=(\S*)", stripped)
        if m:
            after = i > entry
            out["export_lines"].append(
                {"line": i, "value": m.group(1).strip('"\''),
                 "after_entry_point": after})
            if not after:
                value = m.group(1).strip('"\'')
            continue
        if re.match(r"unset\s+ARES_PARITY\b", stripped):
            out["export_lines"].append(
                {"line": i, "value": "<unset>", "after_entry_point": i > entry})
            if i <= entry:
                value = None

    out["value"] = value
    out["declared"] = value == "1"
    pre = [e for e in out["export_lines"] if not e["after_entry_point"]]
    post = [e for e in out["export_lines"] if e["after_entry_point"]]

    assigns = [e for e in pre if e["value"] != "<unset>"]
    if len(assigns) > 1:
        out["valid"] = False
        out["failures"].append(
            f"{len(assigns)} pre-invocation ARES_PARITY assignments at lines "
            f"{[e['line'] for e in assigns]}; exactly one is required")
    if post and any(e["value"] == "1" for e in post):
        out["valid"] = False
        out["failures"].append(
            f"ARES_PARITY=1 at line {[e['line'] for e in post if e['value']=='1']} "
            f"is AFTER the {ACTIVATION_ENTRY_POINT} invocation at line {entry}; "
            "it cannot reach the trading process")
    if assigns and value is None:
        out["valid"] = False
        out["failures"].append(
            "ARES_PARITY is unset before the invocation after being assigned; "
            "the declaration has no effect")
    return out


def _git(*args):
    """git stdout, or '' on failure. Never raises for a missing ref."""
    r = subprocess.run(["git", "-C", str(ROOT)] + list(args),
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _schema_v2_activation():
    """The commit that activated schema v2, as recorded in the baseline.

    Read from the baseline rather than hardcoded, because the activating commit's
    own SHA does not exist until it has been made. A placeholder in the
    implementation would have to be amended afterwards, rewriting a commit whose
    whole purpose is to be the stable anchor.
    """
    try:
        with open(ROOT / "tools" / "parity_baseline.json") as fh:
            return json.load(fh).get("schema_v2_activation_commit")
    except Exception:
        return None


def _git_show(rev, path):
    """Bytes of path at rev, or None when absent there."""
    try:
        return subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT,
                              capture_output=True, check=True).stdout
    except subprocess.CalledProcessError:
        return None


def _append_only_history():
    """Append-only, anchored to Git rather than to a mutable sidecar count.

    A recorded line count stored next to the evidence can be rewritten together
    with the evidence. Byte-prefix containment across committed history cannot:
    it detects truncation, rewriting of old records, reordering, replacement and
    mid-file insertion, none of which a line count necessarily changes.
    """
    out = {"prefix_preserved": None, "history_append_only": None,
           "committed_records": 0, "working_tree_records": 0,
           "new_records": 0, "violations": []}
    wt = ROOT / PARITY_OUTPUT
    head = _git_show("HEAD", PARITY_OUTPUT)
    if wt.exists():
        cur = wt.read_bytes()
        out["working_tree_records"] = len(
            [l for l in cur.splitlines() if l.strip()])
        base = head or b""
        out["committed_records"] = len(
            [l for l in base.splitlines() if l.strip()])
        out["prefix_preserved"] = cur.startswith(base)
        out["new_records"] = (out["working_tree_records"]
                              - out["committed_records"])
        if not out["prefix_preserved"]:
            out["violations"].append(
                "working tree is not a byte-extension of the committed "
                "evidence: history was truncated, reordered or rewritten")

    revs = subprocess.run(
        ["git", "log", "--format=%H", "--", PARITY_OUTPUT],
        cwd=ROOT, capture_output=True, text=True).stdout.split()
    revs.reverse()
    ok = True
    prev = b""
    for rev in revs:
        cur = _git_show(rev, PARITY_OUTPUT)
        if cur is None:
            ok = False
            out["violations"].append(f"{rev[:7]} deleted the evidence file")
            continue
        if not cur.startswith(prev):
            ok = False
            out["violations"].append(
                f"{rev[:7]} is not a byte-extension of its parent")
        prev = cur
    out["history_append_only"] = ok if revs else None
    return out


def _parity_output_state(now=None):
    """Phase-aware evidence gate.

    parity_output_absent encoded "Phase 4 has not started", so the first write
    would have made every later run fail permanently -- a known-false gate needing
    manual interpretation, which is what replacing logs_unchanged_since_tag was
    meant to end. The states below are explicit so ARMED_NOT_STARTED is not
    reported as a footnote inside a failure list.
    """
    decl = _collection_declared()
    p = ROOT / PARITY_OUTPUT
    # Set FIRST, so it is reported on every early-return path too. A freshness
    # signal that disappears whenever something else fails is useless precisely
    # when it is most needed.
    heartbeat = _parity_heartbeat(now)
    out = {"heartbeat": heartbeat,
           "collection_declared": decl["declared"],
           "declaration": decl, "exists": p.exists(), "records": 0,
           "state": None, "valid": False, "failures": [], "notes": []}

    if not decl["valid"]:
        out["state"] = "DECLARATION_MALFORMED"
        out["failures"] = list(decl["failures"])
        return out

    if not decl["declared"]:
        if p.exists():
            out["state"] = "UNDECLARED_OUTPUT_PRESENT"
            out["failures"].append(
                "parity output present but collection is not declared where it "
                "takes effect in run_ares.sh")
            return out
        out["state"] = "NOT_DECLARED_ABSENT"
        out["valid"] = True
        out["notes"].append("Phase 0.5: collection disabled, no evidence file")
        return out

    if not p.exists():
        # Armed is only legitimate UNTIL the first scheduled cycle after
        # activation became effective has had its grace period. Past that, a
        # missing heartbeat means the collector never started -- previously
        # indistinguishable from "armed, about to run", which is the ambiguity
        # this state split exists to remove.
        eff = _activation_effective_at()
        out["activation_effective_at"] = eff.get("effective_at")
        deadline = None
        if eff.get("parsed") is not None:
            deadline = next_cycle_deadline(eff["parsed"])
        out["first_expected_heartbeat_deadline"] = (
            deadline.isoformat() if deadline else None)
        ref = _as_utc(now) or datetime.now(timezone.utc)
        if deadline is None:
            # Cannot be tightened. Reported as unresolved rather than silently
            # granted the benign reading: the commit date alone is NOT a
            # substitute, because the VPS may pull hours after the commit and a
            # commit-derived deadline would blame the collector for that gap.
            out["state"] = "ARMED_NOT_STARTED"
            out["valid"] = True
            out["armed_deadline_unresolved"] = True
            out["notes"].append(
                "collection declared; no cycle recorded; armed deadline "
                "UNRESOLVED (no activation_effective_at recorded) -- cannot "
                "distinguish 'about to run' from 'never started'")
        elif ref >= deadline:
            out["state"] = "ARMED_NO_HEARTBEAT"
            out["valid"] = False
            out["failures"].append(
                f"collection declared and effective at {eff.get('effective_at')}"
                f" but no heartbeat by first expected deadline "
                f"{deadline.isoformat()}")
        else:
            out["state"] = "ARMED_NOT_STARTED"
            out["valid"] = True
            out["notes"].append(
                f"collection declared; no cycle recorded; first expected "
                f"heartbeat deadline {deadline.isoformat()} not yet reached")
        return out

    recs = []
    for i, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            recs.append(json.loads(line))
        except Exception as exc:
            out["failures"].append(f"line {i} unparseable: {exc}")
    out["records"] = len(recs)
    if out["failures"]:
        out["state"] = "UNPARSEABLE_JSONL"
        return out

    # Each record is validated against ITS OWN declared version, not against the
    # newest one. The four v1 records predate every later field and must keep
    # passing untouched.
    try:
        _builder_record_fields()
    except Exception as exc:
        out["state"] = "SCHEMA_UNDERIVABLE"
        out["failures"].append(f"record schema not derivable: {exc}")
        return out

    lineage = ("production_commit", "exit_policy_md5", "compatibility_contract",
               "tracker_source_hash", "cycle_id", "timestamp")
    drift, mismatches, incomplete = [], [], []
    seen = set()
    for n, r in enumerate(recs, 1):
        try:
            required = frozen_record_fields(r.get("record_schema_version"))
        except Exception as exc:
            drift.append(f"record {n} {exc}")
            continue
        missing = required - set(r)
        if missing:
            drift.append(f"record {n} missing {sorted(missing)}")
        extra = set(r) - required
        if extra:
            drift.append(f"record {n} has unregistered {sorted(extra)}")
        # Constant comes from the classifier. The field is difference_class --
        # NOT verdict and NOT result_class, both of which would have made
        # r.get(...) return None and silently pass every real mismatch.
        if r.get("difference_class") == sc.DECISION_CHANGING_MISMATCH:
            mismatches.append(f"record {n} {r.get('symbol')}")
        if [k for k in lineage if r.get(k) in (None, "")]:
            incomplete.append(
                f"record {n} lineage incomplete: "
                f"{[k for k in lineage if r.get(k) in (None, '')]}")
        # v2 effective-stop evidence. Checked per record against its OWN
        # version, so v1 records are not retroactively required to carry it.
        ver = r.get("record_schema_version")
        if ver is not None and ver >= 2:
            basis = r.get("inline_effective_stop_basis")
            stop = r.get("inline_effective_stop")
            if "inline_effective_stop_basis" not in r:
                drift.append(f"record {n} missing inline_effective_stop_basis")
            elif basis not in V2_EFFECTIVE_STOP_BASES:
                drift.append(
                    f"record {n} unregistered effective-stop basis {basis!r}")
            elif basis == "captured_inline_local":
                # A captured basis asserts a value was copied. None here would
                # mean the record claims evidence it does not carry.
                #
                # bool is EXCLUDED explicitly: it is a subclass of int, so
                # isinstance(True, (int, float)) is True and a boolean would have
                # been accepted as a price. Non-finite floats are excluded too --
                # NaN compares unequal to itself, so a NaN effective stop would
                # make every equality comparison in the parity record silently
                # false rather than flagged.
                if isinstance(stop, bool) or not isinstance(stop, (int, float)):
                    drift.append(
                        f"record {n} basis {basis} but inline_effective_stop "
                        f"is {stop!r}")
                elif not math.isfinite(stop):
                    drift.append(
                        f"record {n} non-finite inline_effective_stop {stop!r}")
            elif stop is not None:
                # A not_computed basis asserts the tracker never produced a
                # value; a number alongside it is self-contradictory.
                drift.append(
                    f"record {n} basis {basis} but carries a value {stop!r}")
        ident = (r.get("cycle_id"), r.get("symbol"))
        if ident in seen:
            drift.append(f"record {n} duplicate cycle/symbol identity {ident}")
        seen.add(ident)

    # Commit-aware consistency: the record's own version governs field
    # validation; this asks whether that version agrees with the code that wrote
    # it. Kept separate from field validation so one cannot mask the other.
    activation = _schema_v2_activation()
    act_fail, act_unresolved = schema_activation_consistency(recs, activation)
    has_v2 = any((r.get("record_schema_version") or 0) >= 2 for r in recs)
    out["schema_activation"] = {
        "activation_commit": activation,
        "failures": act_fail,
        "unresolved": act_unresolved,
        # Before any v2 record exists the anchor is legitimately unrecorded, so
        # an absent activation commit is informational. Once a v2 record is
        # present an unresolvable anchor is a real hole and fails closed: the
        # check would otherwise be reported green while never having run.
        "enforced": bool(has_v2),
    }
    # Only genuine version/code CONTRADICTIONS are record drift. An unresolvable
    # or unrecorded anchor is not a property of any record, so folding it into
    # drift made a missing baseline field masquerade as record corruption and
    # displaced the more specific LINEAGE_INCOMPLETE state. It is surfaced as its
    # own gate instead, which still fails closed without mislabelling records.
    drift.extend(act_fail)

    hist = _append_only_history()
    out["append_only"] = hist

    # Failures are CUMULATIVE and the state is the most severe one present.
    # Selecting a single branch would have let unrelated schema drift mask a
    # DECISION_CHANGING_MISMATCH: the drift branch would report only its own
    # failures and the mismatch would never appear anywhere in the output.
    out["failures"].extend(mismatches + drift + incomplete + hist["violations"])
    if mismatches:
        out["state"] = "DECISION_CHANGING_MISMATCH_PRESENT"
    elif drift:
        out["state"] = "SCHEMA_DRIFT"
    elif incomplete:
        out["state"] = "LINEAGE_INCOMPLETE"
    elif hist["prefix_preserved"] is False:
        out["state"] = "EVIDENCE_TRUNCATED"
    elif hist["history_append_only"] is False:
        out["state"] = "EVIDENCE_REWRITTEN"
    elif not recs:
        out["state"] = "DECLARED_BUT_EMPTY"
        out["failures"].append("evidence file exists but contains no records")
    else:
        out["state"] = "ACTIVE_VALID"
        out["valid"] = True
        # Reached ONLY from a fully-validated ACTIVE_VALID, so `valid` stays True:
        # the records already written are not impeached by the collector stopping
        # afterwards. Freshness is reported separately and gates phase advancement.
        if heartbeat["present"] and heartbeat["stale"] is True:
            out["state"] = "ACTIVE_STALE"
            out["notes"].append(
                f"evidence integrity intact; collection not current -- last "
                f"heartbeat {heartbeat['last_timestamp']} does not cover "
                f"expected cycle {heartbeat['last_due_cycle']}")
        elif not heartbeat["present"]:
            # Records exist but no heartbeat: they predate the heartbeat contract.
            # Not stale, but freshness is UNKNOWN, never assumed fresh.
            out["notes"].append(
                "records present but no heartbeat log -- evidence predates the "
                "heartbeat contract; freshness UNKNOWN")
    # Self-check: a state not in the contract would render as unrecognised
    # downstream, so catch it here rather than at the display.
    if out["state"] not in PARITY_OUTPUT_STATES:
        out["valid"] = False
        out["failures"].append(
            f"state {out['state']!r} is not in PARITY_OUTPUT_STATES")
    return out


def _parity_output_trackable():
    """Would an ordinary `git add logs/` stage the evidence?

    .gitignore has `logs/*`, and allow-listing a path in ALLOWED_RUNTIME_LOGS
    does not override it. Without a scoped negation the bot's `git add logs/`
    silently skips the file, so the evidence would exist on one host with no
    backup while the allow-list entry suggested it was being archived.
    """
    out = {"ignored": None, "trackable": None, "newly_trackable_others": []}
    r = subprocess.run(["git", "check-ignore", "-q", PARITY_OUTPUT],
                       cwd=ROOT, capture_output=True)
    out["ignored"] = r.returncode == 0
    out["trackable"] = not out["ignored"]
    return out


def _live_log_changes_are_bot_only_and_allowlisted():
    """Runtime-aware replacement for logs_unchanged_since_tag on a live host.

    Proves four separable things, so a real fault cannot hide behind the
    legitimate churn of scheduled trading:
      1. every changed log path is on the approved operational allow-list
      2. commits that touch logs/ are authored by the runtime bot
      3. runtime-authored commits do NOT touch engine/, tests/ or tools/
      4. Phase 0.5 (non-bot) commits do NOT touch logs/
    """
    base = f"{ROLLBACK_TAG}^{{commit}}"
    out = {"is_live_host": None, "changed_logs": [], "unapproved_log_paths": [],
           "runtime_commits_touching_code": [], "review_commits_touching_logs": [],
           "worktree_clean": None, "valid": False}

    changed = [l for l in sh("git", "diff", "--name-only", base,
                             "HEAD").splitlines() if l.strip()]
    out["changed_logs"] = sorted(f for f in changed if f.startswith("logs/"))
    out["is_live_host"] = bool(out["changed_logs"])
    out["unapproved_log_paths"] = sorted(
        f for f in out["changed_logs"] if f not in ALLOWED_RUNTIME_LOGS)

    # Per-commit authorship vs touched paths.
    log = sh("git", "log", "--format=%H%x1f%ae", f"{base}..HEAD").splitlines()
    for line in log:
        if "\x1f" not in line:
            continue
        sha, email = line.split("\x1f", 1)
        files = [f for f in sh("git", "show", "--name-only", "--format=",
                               sha).splitlines() if f.strip()]
        is_bot = email.strip() in RUNTIME_COMMIT_AUTHORS
        code = [f for f in files
                if any(f.startswith(g) for g in IMPLEMENTATION_GLOBS)]
        logs = [f for f in files if f.startswith("logs/")]
        if is_bot and code:
            out["runtime_commits_touching_code"].append(
                {"commit": sha[:7], "files": code})
        if not is_bot and logs:
            out["review_commits_touching_logs"].append(
                {"commit": sha[:7], "files": logs})

    out["worktree_clean"] = sh("git", "status", "--porcelain") == ""
    out["valid"] = (not out["unapproved_log_paths"]
                    and not out["runtime_commits_touching_code"]
                    and not out["review_commits_touching_logs"]
                    and out["worktree_clean"])
    out["claim"] = ("Phase 0.5 work did not modify operational logs. Live "
                    "trading logs have advanced legitimately through scheduled "
                    "Ares Bot cycles and are validated separately as "
                    "allow-listed runtime state.")
    return out
# Changes permitted between generated_from_commit and HEAD without invalidating
# the baseline. Everything else means the baseline no longer describes HEAD.
BASELINE_DRIFT_ALLOWED = ("tools/parity_baseline.json",)


def _baseline_still_describes_head():
    """Ancestor-based validity, not equality.

    Requiring HEAD == generated_from_commit is unsatisfiable under the two-commit
    workflow: the baseline necessarily lands in the commit AFTER the one it
    describes. Instead require that the recorded commit is an ancestor of HEAD
    and that nothing but the baseline itself changed since.
    """
    dest = ROOT / "tools" / "parity_baseline.json"
    if not dest.exists():
        return {"valid": False, "reason": "baseline absent"}
    try:
        base = json.loads(dest.read_text())
    except Exception as e:
        return {"valid": False, "reason": f"unreadable baseline: {e}"}
    commit = base.get("generated_from_commit") or base.get(
        "generated_on_commit")          # tolerate the pre-rename field on read
    if not commit:
        return {"valid": False, "reason": "no generated_from_commit recorded"}
    anc = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", commit, "HEAD"],
        capture_output=True, text=True).returncode == 0
    changed = [l for l in sh("git", "diff", "--name-only", commit,
                             "HEAD").splitlines() if l.strip()]
    relevant = [f for f in changed
                if any(f.startswith(g) for g in IMPLEMENTATION_GLOBS)
                and f not in BASELINE_DRIFT_ALLOWED]
    return {"valid": bool(anc) and not relevant,
            "generated_from_commit": commit,
            "is_ancestor_of_head": anc,
            "files_changed_since": changed,
            "relevant_changes_since": relevant,
            "reason": ("ok" if anc and not relevant
                       else "not an ancestor of HEAD" if not anc
                       else f"implementation changed since baseline: {relevant}")}


def _tracker_diff_evidence():
    """Structured evidence from the registered Phase 0.5 tracker-diff gate."""
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import tracker_diff as td
        v = td.evaluate()
    except Exception as e:                                # pragma: no cover
        return {"verdict": "ERROR", "error": f"{type(e).__name__}: {e}"}
    # The unified diff is for human review and is intentionally not embedded.
    return {k: v[k] for k in (
        "rollback_tag", "rollback_commit", "rollback_tag_kind",
        "rollback_commit_matches_registered", "reference_md5", "candidate_md5",
        "source_differs", "registered_telemetry_nodes", "packet_fields",
        "normalized_ast_identical", "telemetry_readback_lines",
        "parity_references", "verdict", "failures") if k in v}


def _tracker_diff_gate():
    return _tracker_diff_evidence().get("verdict") == "PASS"
EXPECTED_TAG_COMMIT = "d6cbd55"


def sh(*args):
    """Run a command and ALWAYS return its output, pass or fail.

    The first version returned only stderr on a non-zero exit. A failing test
    suite exits 1, so its entire stdout -- including which assertion failed --
    was discarded and reported as "0 assertions", indistinguishable from the
    file never having run. The diagnosis was destroyed by the diagnostic tool.
    stdout is now always preserved, with stderr appended.
    """
    try:
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           timeout=300)
        out = (r.stdout or "").strip()
        if r.returncode != 0 and (r.stderr or "").strip():
            out = (out + "\n" if out else "") + \
                f"[exit {r.returncode}] {r.stderr.strip()}"
        return out
    except Exception as exc:                        # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {exc}"


def md5(path):
    p = Path(path)
    if not p.exists():
        return "MISSING"
    return hashlib.md5(p.read_bytes()).hexdigest()


def pkg_version(name):
    """importlib.metadata, because some packages lack __version__.

    pandas_ta is exactly that case: a naive probe reports it missing.
    """
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:                               # noqa: BLE001
        return "UNKNOWN"


CONCURRENCY_MODULES = {"threading", "multiprocessing", "asyncio",
                       "concurrent", "concurrent.futures", "_thread"}
CONCURRENCY_CALLS = {"Thread", "Process", "ThreadPoolExecutor",
                     "ProcessPoolExecutor", "Pool", "start_new_thread"}


def _concurrency_findings():
    """Files introducing concurrency, which would void stdout-capture safety.

    Parses the AST rather than grepping text. A string scan is wrong here and
    was measurably wrong: the first version flagged parity_runner.py because a
    COMMENT in it says "no threading, multiprocessing or asyncio anywhere".
    Prose about concurrency is not concurrency. Only real imports and call
    targets count.

    First-party code only; venv dependencies legitimately use threads.
    """
    import ast as _ast
    hits = []
    files = sorted(set(list((ROOT / "engine").glob("*.py"))
                       + list(ROOT.glob("*.py"))))
    for p in files:
        try:
            tree = _ast.parse(p.read_text(errors="replace"))
        except Exception:                           # noqa: BLE001
            continue
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in CONCURRENCY_MODULES:
                        hits.append(f"{p.name}: import {a.name}")
            elif isinstance(node, _ast.ImportFrom):
                if node.module and node.module.split(".")[0] in CONCURRENCY_MODULES:
                    hits.append(f"{p.name}: from {node.module} import ...")
            elif isinstance(node, _ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in CONCURRENCY_CALLS:
                    hits.append(f"{p.name}:{node.lineno}: {name}()")
    return hits


def _has_concurrency():
    return bool(_concurrency_findings())


def main():
    trades_path = ROOT / "logs" / "virtual_trades.json"
    trades = json.loads(trades_path.read_text()) if trades_path.exists() else []
    open_t = [t for t in trades if t.get("status") == "open"]
    closed = [t for t in trades if t.get("status") == "closed"]

    def net(t):
        return round((t.get("pnl") or 0) - (t.get("total_commission") or 0), 2)

    snap = {
        "snapshot_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Phase 4 pre-shadow gate artifact. Re-run before Phase 5.",
        "lineage": {
            "ares_commit": sh("git", "rev-parse", "--short", "HEAD"),
            "ares_branch": sh("git", "rev-parse", "--abbrev-ref", "HEAD"),
            "working_tree_clean": sh("git", "status", "--porcelain") == "",
            "uncommitted": sh("git", "status", "--porcelain").splitlines(),
            "rollback_tag": ROLLBACK_TAG,
            "rollback_tag_commit": sh("git", "rev-parse", "--short",
                                      f"{ROLLBACK_TAG}^{{commit}}"),
            "rollback_tag_expected": EXPECTED_TAG_COMMIT,
            "exit_policy_md5": md5(ROOT / "engine" / "exit_policy.py"),
            "tracker_md5": md5(ROOT / "engine" / "tracker.py"),
            "compatibility_contract": CONTRACT_VERSION,
            "record_schema_version": sc.RECORD_SCHEMA_VERSION,
            "tracker_compat_md5": md5(ROOT / "engine" / "tracker_compat.py"),
            "parity_compare_md5": md5(ROOT / "engine" / "parity_compare.py"),
        },
        "checksums": {
            "virtual_trades.json": md5(trades_path),
            "psi_state.json": md5(ROOT / "logs" / "psi_state.json"),
            "signal_queue.json": md5(ROOT / "logs" / "signal_queue.json"),
        },
        "portfolio": {
            "open_count": len(open_t),
            "closed_count": len(closed),
            "realised_net": round(sum(net(t) for t in closed), 2),
            "open_positions": [
                {
                    "symbol": t.get("symbol"),
                    "entry_date": t.get("entry_date"),
                    "entry_price": t.get("entry_price"),
                    "shares": t.get("shares"),
                    "original_shares": t.get("original_shares", t.get("shares")),
                    "stop_loss": t.get("stop_loss"),
                    "trailing_stop": t.get("trailing_stop", t.get("stop_loss")),
                    "peak_price": t.get("peak_price", t.get("entry_price")),
                    "take_profit": t.get("take_profit"),
                    "scaled_out": t.get("scaled_out", False),
                    "phase_field": t.get("phase"),
                    "cohort_by_entry_date": ("pre_clean"
                                             if t.get("symbol") in PRE_CLEAN
                                             else "clean_v3"),
                    "trail_equals_stop": (
                        t.get("trailing_stop", t.get("stop_loss"))
                        == t.get("stop_loss")),
                    # Dead band requires the trail to have RATCHETED above the
                    # initial stop and still sit below entry. A bare
                    # trail < entry is true for every unratcheted position too,
                    # since the initial stop is below entry by construction --
                    # reporting that as dead band would show five positions in
                    # the band when only two are.
                    "trail_ratcheted": (
                        t.get("trailing_stop", t.get("stop_loss"))
                        > t.get("stop_loss")),
                    "in_giveback_dead_band": (
                        t.get("trailing_stop", t.get("stop_loss"))
                        > t.get("stop_loss")
                        and t.get("trailing_stop", t.get("stop_loss"))
                        < t.get("entry_price")),
                    "will_label_stop_loss_if_stopped": (
                        t.get("trailing_stop", t.get("stop_loss"))
                        <= t.get("stop_loss")),
                }
                for t in open_t
            ],
        },
        # Three-state resolution. The previous form tested only still_open and
        # entry_date_matches, which was adequate while both symbols were open and
        # became actively misleading the moment they closed: it could not tell an
        # obligation discharged by a captured exit from one lost forever.
        "required_shadow_coverage": required_coverage(
            _evidence_records(), {t.get("symbol") for t in open_t}),
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "float_mant_dig": sys.float_info.mant_dig,
            "rounding_fingerprint": {
                s: round(float(s), 2)
                for s in ("2.675", "0.125", "100.015", "109.985", "0.135")
            },
            "pandas": pkg_version("pandas"),
            "numpy": pkg_version("numpy"),
            "pandas_ta": pkg_version("pandas_ta"),
        },
        "phase3_gate": {},
    }

    # Marker contract against the ACTUALLY LOADED tracker module, not the
    # working-tree file: Python may have loaded another installed or cached
    # path, and stdout-based failure detection would then be unverified.
    try:
        from engine import tracker as _tracker
        snap["marker_contract"] = sc.verify_marker_contract(_tracker)
    except Exception as exc:                        # noqa: BLE001
        snap["marker_contract"] = {"valid": False,
                                   "error": f"{type(exc).__name__}: {exc}"}

    lin = snap["lineage"]
    # --no-suites exists ONLY to break a recursion: test_phase5_gates needs the
    # assembled gate block, and running the full snapshot from inside a suite that
    # the snapshot itself runs never terminates. The mode is made unable to
    # manufacture a green gate -- suites_skipped is recorded and all_suites_pass is
    # forced False below -- so it can never stand in for a real verification run.
    skip_suites = "--no-suites" in sys.argv
    snap["suites_skipped"] = skip_suites
    tests = {}
    for name, path in ({} if skip_suites else SUITES).items():
        out = sh(sys.executable, path)
        lines = out.splitlines()
        tests[name] = {
            "path": path,
            "passed": out.rstrip().endswith("ALL PASS"),
            "assertions": out.count("  pass  "),
            "failures": [l.strip() for l in lines if l.lstrip().startswith("FAIL")],
            "tail": lines[-1].strip() if lines else "NO OUTPUT",
        }
    snap["test_suites"] = tests
    snap["assertion_total"] = sum(t["assertions"] for t in tests.values())
    snap["assertion_note"] = (
        "The suites are DISJOINT files; assertion_total is their sum, "
        "not a superset relationship. Do not read it as requiring any other "
        "count to pass independently.")
    snap["tracker_diff"] = _tracker_diff_evidence()
    snap["baseline_validity"] = _baseline_still_describes_head()
    snap["live_logs"] = _live_log_changes_are_bot_only_and_allowlisted()
    snap["parity_output"] = _parity_output_state()
    snap["parity_output_tracking"] = _parity_output_trackable()
    snap["phase3_gate"] = {
        "baseline_describes_head": snap["baseline_validity"]["valid"],
        "rollback_tag_resolves": lin["rollback_tag_commit"].startswith(
            EXPECTED_TAG_COMMIT),
        # Renamed from tracker_unchanged_since_tag, which became objectively
        # false once Phase 0.5 telemetry landed: the tracker source IS changed.
        # The supportable claim is narrower -- tracker decision behavior remains
        # structurally identical to pre-tracker-swap, and the source differs only
        # through the registered Phase 0.5 inline decision-input telemetry.
        # A text diff cannot establish that, so this delegates to the AST gate.
        "tracker_diff_is_registered_phase05_only": _tracker_diff_gate(),
        # Was logs_unchanged_since_tag, an absence test. That is wrong in BOTH
        # environments now: once review commits are rebased onto bot commits, the
        # dev checkout carries the same log history as the live host, so "did any
        # log change" no longer distinguishes them and would fail everywhere.
        # The invariant that actually matters is authorship -- no reviewed commit
        # may touch operational logs -- and it holds on either host for the same
        # reason, which is what makes it worth gating on.
        "logs_unmodified_by_review_commits":
            not snap["live_logs"]["review_commits_touching_logs"],
        "live_log_changes_are_bot_only_and_allowlisted":
            snap["live_logs"]["valid"],
        # all() over an empty dict is True, so --no-suites would otherwise report
        # every suite passing precisely because none ran.
        "all_suites_pass": bool(tests) and not skip_suites and all(
            t["passed"] for t in tests.values()),
        "marker_contract_valid": bool(snap["marker_contract"].get("valid")),
        "working_tree_clean": lin["working_tree_clean"],
        # Was parity_output_absent, a bare .exists() that encoded "Phase 4 has
        # not started" and would therefore have failed permanently from the first
        # write onward. Now phase-aware: see _parity_output_state().
        "parity_output_state_valid": snap["parity_output"]["valid"],
        # Separate gate, not record drift: once any v2 record exists the
        # activation anchor must be resolvable, or the commit-awareness check has
        # silently not run. False only when it is both required and unavailable.
        "schema_v2_activation_resolvable": not (
            snap["parity_output"].get("schema_activation", {}).get("enforced")
            and snap["parity_output"]["schema_activation"].get("unresolved")),
        "parity_output_trackable": snap["parity_output_tracking"]["trackable"],
        # stdout capture is only safe while evaluation is sequential.
        "single_threaded_verified": not _has_concurrency(),
        "concurrency_findings": _concurrency_findings(),
        "NOTE": ("Tests here ran in THIS runtime. The gate requires them to "
                 "pass in the VPS-equivalent runtime; run this same script on "
                 "the VPS and diff the 'runtime' and 'marker_contract' blocks "
                 "before authorizing Phase 4."),
    }
    # DEPLOYMENT INTEGRITY. Remains the exit-code authority so a maintenance
    # deployment whose PURPOSE is to repair collection can still be installed.
    # Collection freshness is deliberately NOT a member of this dict: every bool
    # here joins the all() below, so putting it here would create the deadlock
    # where a stale collector blocks deploying the fix for the stale collector.
    snap["phase3_gate"]["all_local_checks_pass"] = all(
        v for k, v in snap["phase3_gate"].items()
        if isinstance(v, bool) and k != "all_local_checks_pass")
    snap["phase3_gate"]["deployment_integrity_valid"] = \
        snap["phase3_gate"]["all_local_checks_pass"]

    # COLLECTION OPERATIONAL HEALTH. Blocking for evidence acceptance and Phase 5,
    # non-blocking for deployment and repair.
    hb = snap["parity_output"]["heartbeat"]
    st = snap["parity_output"]["state"]
    # Host context decides what freshness even MEASURES. The heartbeat is
    # bot-committed, so a development checkout reads the last ARCHIVED VPS
    # activity; a delayed or failed bot push would otherwise make the laptop
    # announce that the VPS is stale when only the archival lagged.
    role = _host_role()
    act = _activation_effective_at()
    recent = None if hb["stale"] is None else (not hb["stale"])
    if st == "ARMED_NO_HEARTBEAT":
        recent = False
    if role["role"] == "unknown":
        # A host that cannot identify itself cannot say what its freshness
        # measures, so the measurement is void rather than optimistic.
        recent = None
    label = {"live": "live_collection_recent",
             "archive": "archived_collection_recent"}.get(
                 role["role"], "collection_recency_unmeasurable")
    snap["collection_health"] = {
        "measures": label,
        "host_context": role["role"],
        "host_role_source": role["source"],
        "host_role_detail": role["detail"],
        "host_marker_path": role["marker_path"],
        # Retained for comparison ONLY. It is the discredited inference: a
        # development checkout holding bot log commits reports True. Kept visible
        # so a future reader can see the two disagree rather than rediscovering it.
        "legacy_is_live_host_inference": snap["live_logs"]["is_live_host"],
        "interpretation": (
            "current liveness of this host's collector"
            if role["role"] == "live" else
            "last VPS activity that reached git; NOT the live host state -- a "
            "lagging or failed bot push presents here as staleness"
            if role["role"] == "archive" else
            "host role unresolved; freshness is not measurable here"),
        "parity_collection_recent": recent,
        "state": st,
        "last_heartbeat": hb["last_timestamp"],
        "last_due_cycle": hb["last_due_cycle"],
        "covers_last_due_cycle": hb["covers_last_due_cycle"],
        "distinct_cycle_identities": hb["identities"],
        "incomplete_cycles": hb.get("incomplete_cycles", []),
        "malformed": hb["malformed"],
        # Read from the activation contract directly, NOT from parity_output.
        # parity_output only populates it on the ARMED early-return path, so in
        # every ACTIVE state -- i.e. normal operation -- the provenance of the
        # activation timestamp was invisible in the report that depends on it.
        "activation": {k: act[k] for k in
                       ("effective_at", "commit", "timestamp_source",
                        "confidence", "error")},
        "activation_effective_at": act["effective_at"],
    }

    # PHASE READINESS. None (unknown) is NOT truthy, so an unrunnable freshness
    # check fails closed here rather than defaulting to ready.
    # PHASE 5 ELIGIBILITY. Freshness must VISIBLY prevent Phase 5 rather than be
    # informational: a stalled collector means the evidence backing the swap is
    # not current, whatever the already-written records say.
    cov = snap.get("required_shadow_coverage") or {}
    tally = coverage_tally(cov)
    gates = {}

    def gate(name, evaluated, passed, evidence, blocker=None):
        gates[name] = {"evaluated": bool(evaluated), "passed": bool(passed),
                       "evidence": evidence, "blocker": blocker}

    gate("deployment_integrity",
         True, snap["phase3_gate"]["deployment_integrity_valid"],
         "phase3 gate aggregate",
         None if snap["phase3_gate"]["deployment_integrity_valid"]
         else "deployment_integrity_valid is False")
    gate("evidence_valid", True, snap["parity_output"]["valid"],
         f"state {st}", None if snap["parity_output"]["valid"]
         else f"evidence state {st}")
    gate("collection_recent", recent is not None, recent is True,
         {"measures": snap["collection_health"]["measures"],
          "last_heartbeat": hb["last_timestamp"]},
         None if recent is True else
         f"parity_collection_recent is {recent} "
         f"({snap['collection_health']['measures']})")
    # PHASE 2 CLEARANCE answers a DIFFERENT question from coverage: are any
    # pre-migration positions still running under the legacy tracker? It can pass
    # while the empirical evidence is incomplete, and conflating the two would let
    # a closed-but-unobserved position read as cleared AND covered.
    p2_open = sorted(s for s in PRE_CLEAN if s in {t.get("symbol")
                                                  for t in open_t})
    gate("phase2_clearance", True, not p2_open,
         {"pre_clean_still_open": p2_open,
          "question": "are pre-migration positions still under the legacy "
                      "tracker (NOT whether their evidence was collected)"},
         None if not p2_open else
         f"pre-clean positions still open: {', '.join(p2_open)}")
    # An EMPTY coverage set is not resolution. all() over nothing is True, so
    # without this the gate reads passed whenever the block is missing entirely.
    cov_complete = set(cov) == set(REQUIRED_COVERAGE) and bool(cov)
    gate("required_coverage_resolution", cov_complete,
         cov_complete and tally["administratively_resolved"],
         tally,
         (f"coverage set {sorted(cov)} != registered "
          f"{sorted(REQUIRED_COVERAGE)}") if not cov_complete else
         None if tally["administratively_resolved"] else
         "; ".join(f"{s}: {d['coverage_state']}/{d['requirement_disposition']}"
                   for s, d in sorted(cov.items()) if d["blocking"]))
    # MINIMUM CLOSED SAMPLE is deliberately UNREGISTERED. Choosing a threshold
    # now, with the closed count already known, would be fitting the bar to the
    # data. Unevaluated is reported as unevaluated, and an unevaluated gate blocks.
    gate("minimum_closed_sample", MIN_CLOSED_SAMPLE is not None,
         False if MIN_CLOSED_SAMPLE is None else False,
         {"threshold": MIN_CLOSED_SAMPLE,
          "population": MIN_CLOSED_SAMPLE_POPULATION,
          "note": "retired/unobtainable coverage does not contribute"},
         "threshold not registered" if MIN_CLOSED_SAMPLE is None
         else "sample gate not yet implemented")
    gate("explicit_operator_authorization", True, OPERATOR_AUTHORIZATION,
         {"granted": OPERATOR_AUTHORIZATION},
         None if OPERATOR_AUTHORIZATION else
         "explicit operator authorization absent")

    # An ABSENT gate is itself a blocker: the satisfied flag must never be derived
    # from an empty blocker list alone, which is precisely how it read True while
    # nothing but freshness had ever been wired in.
    missing = [g for g in PHASE5_GATE_REGISTRY if g not in gates]
    blockers = [f"registered gate not evaluated: {g}" for g in missing]
    for name in PHASE5_GATE_REGISTRY:
        g = gates.get(name)
        if not g:
            continue
        if not g["evaluated"]:
            blockers.append(f"gate {name} not evaluated"
                            + (f": {g['blocker']}" if g["blocker"] else ""))
        elif not g["passed"]:
            blockers.append(g["blocker"] or f"gate {name} failed")

    snap["collection_health"]["phase5_gate_registry"] = list(
        PHASE5_GATE_REGISTRY)
    snap["collection_health"]["phase5_gates"] = gates
    snap["collection_health"]["coverage_tally"] = tally
    snap["collection_health"]["phase5_gates_satisfied"] = bool(
        not blockers
        and not missing
        and all(gates[g]["evaluated"] and gates[g]["passed"]
                for g in PHASE5_GATE_REGISTRY))
    snap["collection_health"]["phase5_blockers"] = blockers
    snap["collection_health"]["phase5_authorization"] = (
        "PROHIBITED -- Phase 5 requires explicit operator sign-off regardless "
        "of gate state")

    snap["collection_health"]["phase4_operational_ready"] = bool(
        snap["phase3_gate"]["deployment_integrity_valid"]
        and snap["parity_output"]["valid"]
        and recent is True
        and not snap["collection_health"]["incomplete_cycles"]
        and not hb["malformed"])

    print(json.dumps(snap, indent=2))

    if "--update-baseline" in sys.argv:
        # Deliberate, reviewable regeneration. The baseline is the ONE source of
        # truth for cross-host comparison; vps_phase3_verify.sh reads it instead
        # of embedding literals, which is what went stale before.
        td_ev = snap["tracker_diff"]
        if td_ev.get("verdict") != "PASS":
            print("\nREFUSING to update the baseline: the tracker-diff gate is "
                  f"{td_ev.get('verdict')}.\n  " +
                  "\n  ".join(td_ev.get("failures") or ["(no detail)"]),
                  file=sys.stderr)
            return 1
        tp = ROOT / "engine" / "tracker.py"
        base = {
            "baseline_schema_version": 1,
            "generated_at": snap["snapshot_utc"],
            # The commit whose implementation and test population this baseline
            # DESCRIBES -- not the commit that CONTAINS this file. A baseline
            # cannot name the commit containing itself without self-reference:
            # recording it would change the file, producing another commit.
            # Provenance is therefore: generated_from_commit = implementation
            # commit; the baseline artifact lives in the following commit, which
            # git history already records.
            "generated_from_commit": lin["ares_commit"],
            # The commit that activated record schema v2, i.e. the first commit
            # whose code emits v2 records. Recorded here rather than hardcoded in
            # the implementation because a commit cannot contain its own SHA; a
            # placeholder would require amending the very commit meant to be the
            # stable anchor. Preserved across rebaselines once set, so a later
            # regeneration cannot silently move the activation point.
            "schema_v2_activation_commit": (
                _schema_v2_activation() or lin["ares_commit"]),
            # Phase 4 activation provenance. PRESERVED, never regenerated: this
            # records a historical operational transition, so a rebaseline that
            # recomputed it would silently rewrite when collection is believed to
            # have become active. Re-arming after a disable requires a NEW
            # collection epoch with its own timestamp -- it must not overwrite
            # these fields.
            "parity_activation_commit":
                _activation_effective_at()["commit"] or ACTIVATION_COMMIT,
            "parity_activation_effective_at":
                _activation_effective_at()["effective_at"]
                or ACTIVATION_EFFECTIVE_AT,
            "parity_activation_timestamp_source":
                _activation_effective_at()["timestamp_source"]
                or ACTIVATION_TIMESTAMP_SOURCE,
            "parity_activation_timestamp_confidence":
                _activation_effective_at()["confidence"]
                or ACTIVATION_CONFIDENCE,
            # Three hashes of ONE file, named by SERIALIZATION rather than by
            # role, because a mismatch between them is not evidence of tampering.
            # Each answers a different question and must never be cross-compared.
            "tracker_raw_bytes_md5": hashlib.md5(tp.read_bytes()).hexdigest(),
            "tracker_normalized_text_md5": hashlib.md5(
                tp.read_text().encode()).hexdigest(),
            "tracker_loaded_source_md5": snap["marker_contract"].get(
                "tracker_source_hash"),
            "tracker_hash_bases": {
                "tracker_raw_bytes_md5": (
                    "md5(Path.read_bytes()) -- CRLF preserved as stored on disk; "
                    "file-integrity evidence"),
                "tracker_normalized_text_md5": (
                    "md5(Path.read_text().encode()) -- CRLF normalised to LF; "
                    "the AST/diff-gate input, equals tracker_diff "
                    "candidate_tracker_md5"),
                "tracker_loaded_source_md5": (
                    "md5(inspect.getsource(tracker).encode()) -- LF plus a "
                    "trailing newline getsource appends; marker-contract and "
                    "runtime-loaded-source evidence"),
                "note": ("engine/tracker.py has no trailing newline, so "
                         "tracker_loaded_source_md5 differs from "
                         "tracker_normalized_text_md5 by exactly one byte. Do "
                         "not normalise one into another to make them agree."),
            },
            # THREE different md5 values legitimately describe the same file.
            # Recorded explicitly because a reviewer comparing them would
            # otherwise reasonably suspect a mismatch:
            #   raw bytes                  c0e9e9bc...  CRLF preserved on disk
            #   read_text()                465fe06f...  CRLF normalised to LF
            #   inspect.getsource()        12bd27d3...  LF + trailing newline
            # engine/tracker.py has no trailing newline, and getsource appends
            # one, so the marker-contract hash and the tracker-diff candidate
            # hash differ by exactly one byte. Neither is wrong; they answer
            # different questions and must not be cross-compared.

            "exit_policy_md5": lin["exit_policy_md5"],
            "compatibility_contract": lin["compatibility_contract"],
            "record_schema_version": lin["record_schema_version"],
            "rounding_fingerprint": snap["runtime"]["rounding_fingerprint"],
            "suite_assertions": {n: t["assertions"] for n, t in tests.items()},
            "assertion_total": snap["assertion_total"],
            # INFORMATIONAL PROVENANCE, deliberately NOT a compared field.
            # The state legitimately advances ARMED_NOT_STARTED -> ACTIVE_VALID
            # at the first cycle and records only grow, so comparing these to a
            # later run would manufacture a failure on correct behaviour. Recorded
            # so a baseline can be placed in the activation timeline; validity is
            # judged by _parity_output_state() against the live repository, never
            # against these values.
            "parity_collection_at_generation": {
                "state": snap["parity_output"]["state"],
                "collection_declared":
                    snap["parity_output"]["collection_declared"],
                "records": snap["parity_output"]["records"],
                "NOTE": ("informational only; the state advances and records "
                         "grow, so these are never compared"),
            },
            # Reconciled, not absorbed. The bridge suite DECREASED 86 -> 66
            # because Phase 4 reconstruction was withdrawn: 10 premise/bar tests
            # were deleted and 5 packet tests added (17 -> 12 functions). Every
            # deleted test targeted a name now listed under
            # withdrawn_phase4_dependencies.must_be_absent, so the decrease is
            # the expected consequence of the withdrawal. The zero-network
            # coverage those tests provided did not vanish -- it moved to
            # phase05_network (89 assertions) with negative controls.
            "suite_count_changes_since_previous_baseline": {
                "wiring": {"was": 95, "now": 132,
                           "reason": "registered 8 omitted refusal tests plus "
                                     "the registration-completeness guard"},
                "evaluator": {"was": 61, "now": 63,
                              "reason": "packet-schema refusal coverage"},
                "bridge": {"was": 86, "now": 66,
                           "reason": "Phase 4 premise/bar reconstruction tests "
                                     "deleted with the architecture they tested; "
                                     "replaced by packet-capture tests"},
            },
            # The reviewed Phase 0.5 contract, recorded as EXPECTATIONS rather
            # than as whatever the working tree happened to produce. The
            # tracker-diff gate replaces tracker_unchanged_since_tag, which
            # became objectively false once telemetry landed.
            "tracker_diff_is_registered_phase05_only": {
                "rollback_tag": ROLLBACK_TAG,
                "rollback_commit": td_ev.get("rollback_commit"),
                "rollback_tag_kind": td_ev.get("rollback_tag_kind"),
                "reference_tracker_md5": td_ev.get("reference_md5"),
                "candidate_tracker_md5": td_ev.get("candidate_md5"),
                "source_differs": td_ev.get("source_differs"),
                "registered_telemetry_node_count": len(
                    td_ev.get("registered_telemetry_nodes") or []),
                "registered_telemetry_nodes": td_ev.get(
                    "registered_telemetry_nodes"),
                "packet_fields": list(td_ev.get("packet_fields") or ()),
                "normalized_non_telemetry_ast": (
                    "identical" if td_ev.get("normalized_ast_identical")
                    else "DIFFERS"),
                "telemetry_readback": (
                    "none" if not td_ev.get("telemetry_readback_lines")
                    else td_ev.get("telemetry_readback_lines")),
                "tracker_parity_references": (
                    "none" if not td_ev.get("parity_references")
                    else td_ev.get("parity_references")),
                "expected_verdict": "PASS",
            },
            # Phase 4 reconstruction was DELETED, not disabled. These names must
            # not reappear as execution dependencies. PRICE_SOURCE_NONDETERMINISTIC
            # survives as an UNASSIGNED name only, so historical records stay
            # readable -- that is deliberately distinguished from an active
            # dependency below.
            "withdrawn_phase4_dependencies": {
                "must_be_absent": [
                    "_bar_fn", "_bar_recheck", "_cache_file", "BarStale",
                    "BarUnavailable", "_premise", "PREMISE_DAILY_ONLY",
                    "_ib_connection", "make_bar", "NonDeterministicPrice",
                    "daily_only_price_premise", "cache_mtime_sentinel",
                ],
                "readable_but_unassigned": ["PRICE_SOURCE_NONDETERMINISTIC"],
                "note": ("An unassigned enum name kept for record readability is "
                         "NOT an execution dependency. The distinction matters: "
                         "absence of the name would break historical reads, while "
                         "assignment of it would resurrect a withdrawn premise."),
            },
            "note": ("Regenerate deliberately with "
                     "'python3 tools/pre_parity_snapshot.py --update-baseline' "
                     "when assertions are added or tracker.py legitimately "
                     "changes. A diff here must be reviewed, never auto-synced."),
        }
        dest = ROOT / "tools" / "parity_baseline.json"
        dest.write_text(json.dumps(base, indent=2) + "\n")
        print(f"\nbaseline written: {dest}", file=sys.stderr)

    if "--write" in sys.argv:
        out = ROOT / "logs" / "snapshots"
        out.mkdir(parents=True, exist_ok=True)
        stamp = snap["snapshot_utc"].replace(":", "").replace("-", "")[:15]
        dest = out / f"pre_parity_{stamp}.json"
        dest.write_text(json.dumps(snap, indent=2))
        print(f"\nwritten: {dest}", file=sys.stderr)
    return 0 if snap["phase3_gate"]["all_local_checks_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
