"""Monitor-path decision recorder — observation only.

Records what monitor_trades.py decided, using the values the monitor itself
used. Covers the second decision path discovered on 2026-10-07, which writes
production state with no durable evidence. See
INCIDENT_2026-10-07_monitor_decision_path.md and
DESIGN_monitor_recorder_v1.md.

MEASUREMENT ONLY. This module must not change a monitor decision, the operator
output, exception propagation, or production state.

WHAT THIS IS NOT
----------------
Not a comparator. There is no reference policy agreed for the monitor path, so
there is no MATCH/MISMATCH label, no difference_class, no would_change_action,
and no shadow_* field anywhere in this schema. Adding one would imply the report
path is authoritative, which is the still-open governing question. This records
one path's decisions; it does not adjudicate between two.

Not a repair. The monitor's missing entry-day skip, missing per-trade exception
guard, divergent trailing-stop default, two-precision peak_price, naive exit
date, and opaque get_live_price failures are all RECORDED here and fixed
nowhere. A recorder that silently corrected them would destroy the evidence it
exists to collect.

Not a merge. Monitor observation is a separate stream from report-path parity
(logs/tracker_parity_v1.jsonl). One combined "latest heartbeat" is insufficient
because a report cycle at 13:30 UTC would otherwise satisfy a freshness check
for a monitor cycle at 16:10 that never ran.

SPLIT OF WORK
-------------
The monitor loop stores RAW values into a plain dict and does nothing else: no
imports, no formatting, no arithmetic, no I/O. Every derivation, validation and
serialisation happens here, after the loop, where a failure cannot reach a
decision. This is the same division that makes tracker._LAST_EVAL safe.
"""

import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOGS_DIR = ROOT / "logs"

OBSERVATION_NAME = "monitor_observation_v1.jsonl"
HEARTBEAT_NAME = "monitor_heartbeat_v1.jsonl"
RECORD_SCHEMA_VERSION = 1
HEARTBEAT_SCHEMA_VERSION = 1

# The decision path this stream describes. Coverage and freshness are tracked
# PER PATH; a record or heartbeat from another path can never satisfy this one.
DECISION_PATH = "monitor"


def _now():
    return datetime.now(timezone.utc).isoformat()


def observation_path():
    return LOGS_DIR / OBSERVATION_NAME


def heartbeat_path():
    return LOGS_DIR / HEARTBEAT_NAME


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------
# Membership follows RUNTIME AUTHORITY, not filename.
#
# DECISION: code that can admit, fill or exit a position. Both authoritative
# implementations appear here, because there are two.
#
# CLASSIFICATION: code that decides clean/contaminated sample membership. Audited
# 2026-10-09: no decision path reads phase or contaminated back.
#
# OBSERVATION: the recorder, the parity comparison, and evidence validation.
# tracker_compat.py sits here ONLY while it serves the non-authoritative parity
# branch; if Phase 5 ever makes it part of live execution it must also enter the
# decision manifest. exit_policy.py is the REFERENCE implementation and carries
# no authority — its presence in a manifest must not be read as live authority.
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
    "engine/monitor_observer.py",
    "engine/parity_hook.py",
    "engine/parity_runner.py",
    "engine/parity_compare.py",
    "engine/parity_eval.py",
    "engine/tracker_compat.py",
    "engine/exit_policy.py",
)


def _fingerprint(relpaths):
    """Hash each listed path. A MISSING path is a failure, not an omission.

    A manifest that silently skips what it cannot find reports a clean
    fingerprint for an incomplete set — which is how config/params.json, a path
    that never existed, hashed nothing while appearing to be covered. The real
    file is config/strategy_params.json. Absence is therefore named and
    `complete` goes false.
    """
    digests, missing = {}, []
    for rel in relpaths:
        p = ROOT / rel
        try:
            digests[rel] = hashlib.md5(p.read_bytes()).hexdigest()
        except Exception as exc:                          # noqa: BLE001
            digests[rel] = None
            missing.append(f"{rel}: {type(exc).__name__}")
    return {
        "digests": digests,
        "missing": missing,
        "complete": not missing,
    }


def manifests():
    return {
        "decision": _fingerprint(DECISION_MANIFEST),
        "classification": _fingerprint(CLASSIFICATION_MANIFEST),
        "observation": _fingerprint(OBSERVATION_MANIFEST),
    }


def _git_commit():
    """Repository commit, acknowledged weak: the bot commits logs/ every cycle,
    so HEAD moves for reasons unrelated to decision code. Recorded anyway as the
    only cross-process-unique identity component, since the invocation token
    restarts at 1 in every cron process."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(ROOT), capture_output=True, text=True,
                             timeout=10)
        return out.stdout.strip() or None
    except Exception:                                     # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Effective configuration — resolved value AND origin, per call site
# --------------------------------------------------------------------------
# A single resolved value per key would conceal that the monitor and the report
# path carry DIFFERENT code defaults for the same parameter. Observed 2026-10-07:
# the monitor ratcheted at 10%, so the config value was read and neither default
# fired. The disagreement is real, masked, and must stay visible while masked.
#
# `default_at_site` is the default written at THIS path's call site. It is not a
# global property of the key.
REGISTERED_DEFAULTS = {
    # key: (default at monitor_trades.py, default at the report path)
    "trailing_stop_pct": (0.08, 0.10),
    "rsi_extreme_high": (90, 90),
}

# Keys with no entry in config/strategy_params.json at all: they resolve from
# code on every cycle, and both set every booked price.
CODE_ONLY_DEFAULTS = {
    "slippage_pct": 0.001,
    "commission_per_trade": 1.00,
}


def effective_config(params, site=DECISION_PATH):
    """Resolve each parameter this path reads, recording WHERE it came from."""
    resolved = {}
    params = params or {}
    for key, (monitor_default, other_default) in REGISTERED_DEFAULTS.items():
        present = key in params
        site_default = monitor_default if site == "monitor" else other_default
        resolved[key] = {
            "resolved_value": params.get(key, site_default),
            "source": "config" if present else "code_default",
            "default_at_site": site_default,
            "default_at_other_site": other_default,
            "defaults_agree": monitor_default == other_default,
            # True when the two sites disagree but config supplies the value, so
            # neither default is read and the disagreement cannot be observed in
            # behaviour. Removing or renaming the config key exposes it.
            "masked_divergence": (monitor_default != other_default) and present,
            # True only if this path is CURRENTLY running on a default that
            # disagrees with the other site.
            "currently_active": (monitor_default != other_default) and not present,
        }
    for key, default in CODE_ONLY_DEFAULTS.items():
        present = key in params
        resolved[key] = {
            "resolved_value": params.get(key, default),
            "source": "config" if present else "code_default",
            "default_at_site": default,
            "default_at_other_site": default,
            "defaults_agree": True,
            "masked_divergence": False,
            "currently_active": not present,
        }
    return resolved


def config_review_gate(params):
    """Flag configuration states that require explicit review before acceptance.

    Deleting or renaming trailing_stop_pct would not look like a policy change in
    any diff, yet it would silently put the monitor on 8% while the report path
    stays on 10%. That transition must be refused by a human, not absorbed.
    """
    flags = []
    params = params or {}
    for key, (a, b) in REGISTERED_DEFAULTS.items():
        if a != b and key not in (params or {}):
            flags.append({
                "key": key,
                "condition": "divergent_defaults_now_active",
                "monitor_default": a,
                "report_default": b,
                "requires": "explicit review; do not reconcile inside the "
                            "recorder or treat as a recorder defect",
            })
    return flags


# --------------------------------------------------------------------------
# Record schema
# --------------------------------------------------------------------------
# EXACT set, matching the parity convention. An unregistered key means capture
# changed without review; a missing key means a producer did not run. Both are
# reported rather than silently accepted, and NO key here lacks a producer.
REGISTERED_OBSERVATION_FIELDS = frozenset({
    "record_schema_version", "decision_path", "timestamp",
    "invocation_token", "cycle_id", "repository_commit",
    "symbol", "entry_date", "holding_basis",
    "observed_live", "live_available", "skip_reason",
    "entry_price", "stop_loss",
    "trailing_stop_before", "trailing_stop_after_unrounded",
    "trailing_stop_rounded_2dp", "peak_price_before", "peak_price_after",
    "peak_price_precision_basis", "peak_advanced", "trail_advanced",
    "take_profit", "effective_stop_unrounded", "effective_stop_rounded_2dp",
    "decision", "exit_reason",
    # Four separate facts about a close. Collapsing any pair of them would let a
    # REQUESTED close read as a COMPLETED one, or a pre-slippage decision price
    # read as a booked fill. _close_trade applies slippage, rounds, and can fail
    # after partially mutating the trade.
    "close_requested", "close_price_argument", "close_function_completed",
    "booked_exit_price",
    "shares_at_decision", "unrealized_pct_at_decision",
    "exit_date_recorded", "exit_date_basis",
    "record_complete", "missing_fields", "contract_violations",
})

# Values the monitor can actually produce. A decision outside this set means the
# monitor grew a branch the recorder has not been reviewed against.
DECISIONS = frozenset({
    "hold", "state_update", "closed", "skipped", "incomplete",
})

ALLOWED_EXIT_REASONS = frozenset({"stop_loss", "trailing_stop", "take_profit"})

# Required of every outcome, by VALUE not by presence. Filling an absent key
# with None and then accepting the key's presence as evidence is the exact
# failure this replaces.
_ALWAYS_NON_NULL = frozenset({
    "record_schema_version", "decision_path", "timestamp", "invocation_token",
    "cycle_id", "symbol", "entry_date", "holding_basis", "decision",
    "entry_price", "stop_loss", "trailing_stop_before", "peak_price_before",
    "shares_at_decision", "peak_price_precision_basis", "exit_date_basis",
})

# Legitimately null. `take_profit` may genuinely not exist on a trade;
# `repository_commit` absence is reported separately as lineage_complete.
_ALWAYS_NULLABLE = frozenset({"take_profit", "repository_commit"})

_PRICED_FIELDS = frozenset({
    "observed_live", "effective_stop_unrounded", "effective_stop_rounded_2dp",
    "trailing_stop_after_unrounded", "trailing_stop_rounded_2dp",
    "peak_price_after", "unrealized_pct_at_decision", "exit_date_recorded",
})

_CLOSE_FIELDS = frozenset({
    "exit_reason", "close_price_argument", "booked_exit_price",
    "close_function_completed",
})

# FROZEN OUTCOME CONTRACT.
#
# For each outcome: which fields must carry a real value, which must be null
# because the outcome could not have produced them, and which booleans must hold
# a specific value. A record satisfying none of these is `incomplete` — which is
# a label for a defective record, never a passing outcome.
#
# `must_be_null` is what stops a skipped symbol from claiming a stop was
# evaluated, and stops a hold from carrying close evidence.
OUTCOME_CONTRACT = {
    "skipped": {
        "non_null": frozenset({"skip_reason"}),
        # The monitor reaches its skip branch via `if not live`, so a price of
        # 0.0 also skips. Falsy rather than strictly None, because 0.0 is a
        # real captured observation and must not be rewritten to None.
        "falsy": frozenset({"observed_live"}),
        "null": _PRICED_FIELDS - {"observed_live"} | _CLOSE_FIELDS,
        "false": frozenset({"live_available", "peak_advanced",
                            "trail_advanced", "close_requested"}),
    },
    "hold": {
        "non_null": _PRICED_FIELDS,
        "null": frozenset({"skip_reason"}) | _CLOSE_FIELDS,
        "true": frozenset({"live_available"}),
        "false": frozenset({"peak_advanced", "trail_advanced",
                            "close_requested"}),
    },
    "state_update": {
        "non_null": _PRICED_FIELDS,
        "null": frozenset({"skip_reason"}) | _CLOSE_FIELDS,
        # peak_advanced is what distinguishes this from hold. trail_advanced is
        # deliberately unconstrained: a new peak need not advance the trail.
        "true": frozenset({"live_available", "peak_advanced"}),
        "false": frozenset({"close_requested"}),
    },
    "closed": {
        "non_null": _PRICED_FIELDS | frozenset({
            "exit_reason", "close_price_argument", "booked_exit_price"}),
        "null": frozenset({"skip_reason"}),
        "true": frozenset({"live_available", "close_requested",
                           "close_function_completed"}),
    },
    # No contract. `incomplete` can never be complete, by construction.
    "incomplete": None,
}


def _derive(obs):
    """Turn one raw in-loop capture into a complete, validated record.

    All derivation lives here. The loop stored raw references only, so a
    malformed value becomes a defective RECORD rather than a monitor exception.
    """
    rec = dict(obs)

    live = rec.get("observed_live")
    rec["live_available"] = bool(live)
    if not live:
        # get_live_price collapses dead gateway, timeout, unqualified contract
        # and genuinely-no-bars into a single None (data_feed.py:61-62). The
        # cause is NOT recoverable here, so it is named as unknown rather than
        # guessed. The monitor `continue`s, so no stop was evaluated at all.
        rec.setdefault("skip_reason", "live_price_unavailable_cause_unknown")
        rec.setdefault("decision", "skipped")

    # peak_price carries TWO precisions: 2dp once the monitor has ratcheted it,
    # and the full unrounded entry price when it never has (ITUB held
    # 10.120109656333922 on 2026-10-07). Comparing the two forms naively would
    # show a spurious difference, so the basis is declared.
    peak = rec.get("peak_price_before")
    try:
        if peak is None:
            rec["peak_price_precision_basis"] = "absent"
        elif float(peak) == round(float(peak), 2):
            rec["peak_price_precision_basis"] = "two_dp_or_equal"
        else:
            rec["peak_price_precision_basis"] = "unrounded_initialisation_value"
    except Exception:                                     # noqa: BLE001
        rec["peak_price_precision_basis"] = "unreadable"

    # The monitor persists round(trailing_stop, 2) while deciding against the
    # UNROUNDED local, so a ratchet to 11.6640 stores 11.66 and no function of
    # stored state recovers what was used. Both are recorded.
    eff = rec.get("effective_stop_unrounded")
    try:
        rec["effective_stop_rounded_2dp"] = (
            None if eff is None else round(float(eff), 2))
    except Exception:                                     # noqa: BLE001
        rec["effective_stop_rounded_2dp"] = None

    try:
        ts = rec.get("trailing_stop_after_unrounded")
        rec["trailing_stop_rounded_2dp"] = (
            None if ts is None else round(float(ts), 2))
    except Exception:                                     # noqa: BLE001
        rec["trailing_stop_rounded_2dp"] = None

    # The monitor's exit date comes from datetime.now() with no timezone, i.e.
    # the process-local civil date, which is NOT the bar date the report path
    # uses. Recorded as a named basis so the two are never silently compared.
    rec.setdefault("exit_date_basis", "process_local_naive_civil_date")

    # Stabilise the key set FIRST, then judge on values. Every registered key is
    # present in the serialised record so no consumer has to handle an absent
    # key -- but presence is never evidence, so completeness is decided below by
    # what the values actually are.
    got = set(rec)
    unregistered = sorted(got - REGISTERED_OBSERVATION_FIELDS)
    for key in REGISTERED_OBSERVATION_FIELDS - got:
        rec[key] = None

    violations = []
    # The claim is captured BEFORE normalisation. Overwriting an unregistered
    # decision with "incomplete" first would discard the only evidence of what
    # the producer actually claimed.
    claimed = rec.get("decision")
    decision = claimed if claimed in DECISIONS else "incomplete"
    rec["decision"] = decision

    for key in sorted(_ALWAYS_NON_NULL):
        if rec.get(key) is None:
            violations.append(f"{key}: required for every outcome, is null")
    for key in unregistered:
        violations.append(f"{key}: not in the registered schema")

    contract = OUTCOME_CONTRACT.get(decision)
    if claimed not in DECISIONS:
        violations.append(
            f"decision: {claimed!r} is not a registered outcome; normalised to "
            f"'incomplete'")
    if contract is None:
        # `incomplete` -- already a defective record. Do not add noise.
        pass
    else:
        for key in sorted(contract.get("non_null", ())):
            if rec.get(key) is None:
                violations.append(
                    f"{key}: required non-null for outcome {decision!r}, is null")
        for key in sorted(contract.get("null", ())):
            if rec.get(key) is not None:
                violations.append(
                    f"{key}: must be null for outcome {decision!r}, is "
                    f"{rec.get(key)!r} -- this outcome cannot have produced it")
        for key in sorted(contract.get("falsy", ())):
            if rec.get(key):
                violations.append(
                    f"{key}: must be falsy for outcome {decision!r}, is "
                    f"{rec.get(key)!r}")
        for key in sorted(contract.get("true", ())):
            if rec.get(key) is not True:
                violations.append(
                    f"{key}: must be True for outcome {decision!r}, is "
                    f"{rec.get(key)!r}")
        for key in sorted(contract.get("false", ())):
            if rec.get(key) is not False:
                violations.append(
                    f"{key}: must be False for outcome {decision!r}, is "
                    f"{rec.get(key)!r}")
        if decision == "closed" and rec.get("exit_reason") not in ALLOWED_EXIT_REASONS:
            violations.append(
                f"exit_reason: {rec.get('exit_reason')!r} is not one of "
                f"{sorted(ALLOWED_EXIT_REASONS)}")

    # Retained for readability of what a producer failed to emit at all. It is
    # NOT the completeness criterion.
    rec["missing_fields"] = sorted(
        k for k in REGISTERED_OBSERVATION_FIELDS - got
        if k not in ("record_complete", "missing_fields", "contract_violations"))
    rec["contract_violations"] = violations
    rec["record_complete"] = bool(contract is not None and not violations)
    return rec


def _append(path, payload):
    """Append one JSONL line. Returns True on success, never raises.

    Append mode per call so a concurrent cycle cannot truncate another's output.
    Deterministic serialisation via sort_keys, fsync so a cycle that is killed
    after deciding does not lose the record of what it decided.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except Exception:                                     # noqa: BLE001
        return False


EVALUATED_DECISIONS = frozenset({"hold", "state_update", "closed"})


def reconcile_population(open_positions_seen, attempted, records):
    return _reconcile(open_positions_seen, attempted, records)


def _reconcile(open_positions_seen, attempted, records):
    """Account for the STARTING population, not just the attempted subset.

    Required because `attempted == written` is satisfiable by a cycle that
    recorded every position it attempted and silently never attempted the rest.
    A loop that dies on position 2 of 5 writes 2 of 2 and looks internally
    consistent; the three positions it never reached are invisible in that tally
    and must be named.

    `not_reached` is the residual: positions that were open when the cycle
    started and never entered the loop body. Coverage cannot be claimed while it
    is non-zero, and the residual is computed rather than counted so it cannot be
    under-reported by a producer that also failed to run.
    """
    seen = open_positions_seen if isinstance(open_positions_seen, int) else None

    # Counted by record_complete, NOT by the claimed decision label. A record
    # labelled "hold" or "closed" that fails its outcome contract is incomplete
    # evidence, and counting it as evaluated is how incomplete observations were
    # certified as complete coverage.
    complete = [r for r in records if r.get("record_complete") is True]
    incomplete_records = [r for r in records if r.get("record_complete") is not True]

    evaluated = sum(1 for r in complete
                    if r.get("decision") in EVALUATED_DECISIONS)
    skipped = sum(1 for r in complete if r.get("decision") == "skipped")

    symbols = [r.get("symbol") for r in records]
    duplicates = sorted({s for s in symbols if symbols.count(s) > 1})

    pop = {
        "open_positions_seen": seen,
        "attempted": attempted,
        "evaluated": evaluated,
        "skipped": skipped,
        # Two different quantities, deliberately both reported. `incomplete` is
        # how many records failed their contract; `incomplete_labelled` is how
        # many admitted it in their decision field. A gap between them means a
        # producer claimed an outcome it could not substantiate.
        "incomplete": len(incomplete_records),
        "incomplete_labelled": sum(1 for r in records
                                   if r.get("decision") == "incomplete"),
        "not_reached": None if seen is None else seen - attempted,
        # An attempted count that disagrees with the number of captured records
        # means the store itself is inconsistent, which is a recorder defect and
        # must not be absorbed into one of the outcome buckets.
        "attempted_matches_records": attempted == len(records),
        # One position cannot be observed twice in one invocation. A duplicate
        # means the loop or the store is wrong, and the buckets would otherwise
        # sum correctly while describing the wrong population.
        "duplicate_symbols": duplicates,
    }
    if seen is None:
        pop["reconciled"] = False
        return pop
    pop["reconciled"] = (
        evaluated + skipped + len(incomplete_records) + pop["not_reached"] == seen
        and pop["attempted_matches_records"]
        and pop["not_reached"] >= 0
        and not duplicates)
    return pop


def flush(store):
    """Serialise one monitor invocation. NEVER raises, and is idempotent.

    Called at the end of monitor() on the normal path and again from a finally
    block, so that a monitor that raised mid-loop still publishes the partial
    observations it had already captured plus a heartbeat saying the cycle ran
    and did not complete. The `flushed` sentinel prevents a double write.
    """
    summary = {"attempted": 0, "written": 0, "write_failures": 0,
               "heartbeat_written": False, "flush_error": None,
               "skipped_already_flushed": False}
    try:
        if store.get("flushed"):
            summary["skipped_already_flushed"] = True
            return summary
        store["flushed"] = True

        cycle_id = store.get("cycle_id") or str(uuid.uuid4())[:8]
        commit = _git_commit()
        mans = manifests()
        params = store.get("params_resolved") or {}
        observations = list(store.get("observations") or [])
        summary["attempted"] = store.get("attempted", len(observations))

        written_records = []
        for obs in observations:
            # Envelope first, then derive ONCE. Deriving twice is not harmless:
            # the first pass fills absent registered keys with None, so a second
            # pass sees a complete key set and reports record_complete=true for
            # a record that is in fact partial. Found by its own control.
            rec = dict(obs)
            rec["record_schema_version"] = RECORD_SCHEMA_VERSION
            rec["decision_path"] = DECISION_PATH
            rec["timestamp"] = _now()
            rec["cycle_id"] = cycle_id
            rec["invocation_token"] = store.get("invocation_token")
            rec["repository_commit"] = commit
            rec = _derive(rec)
            written_records.append(rec)
            if _append(observation_path(), rec):
                summary["written"] += 1
            else:
                summary["write_failures"] += 1

        population = reconcile_population(
            store.get("open_positions_seen"), summary["attempted"],
            written_records)
        summary["population"] = population

        beat = {
            "heartbeat_schema_version": HEARTBEAT_SCHEMA_VERSION,
            "record_schema_version": RECORD_SCHEMA_VERSION,
            # PATH-SCOPED. A report-path heartbeat must never satisfy a
            # freshness check for this path, and vice versa.
            "decision_path": DECISION_PATH,
            "cycle_id": cycle_id,
            "invocation_token": store.get("invocation_token"),
            "repository_commit": commit,
            "lineage_complete": bool(commit),
            "cycle_started_at": store.get("cycle_started_at"),
            "cycle_completed_at": _now(),
            # Distinguishes "no open positions" from "the recorder never ran",
            # which are otherwise identical silence. This ambiguity caused four
            # wrong diagnoses during parity activation.
            "open_positions_seen": store.get("open_positions_seen"),
            "early_return_reason": store.get("early_return_reason"),
            "attempted": summary["attempted"],
            "written": summary["written"],
            "write_failures": summary["write_failures"],
            # Reconciles the STARTING population into evaluated, skipped,
            # incomplete and not-reached. attempted == written alone is
            # satisfiable by a cycle that never attempted later positions.
            "population": population,
            # Persistence is an INVOCATION fact, not a per-record one.
            # save_trades succeeding and promote_queue then raising is not the
            # same as the loop raising before save_trades: the first persisted
            # trade updates despite an aborted cycle.
            "trade_state_save_attempted": store.get(
                "trade_state_save_attempted"),
            "trade_state_save_completed": store.get(
                "trade_state_save_completed"),
            # The only field a reader should consult to decide whether this
            # cycle's monitor coverage is complete. Deliberately conjunctive and
            # fail-closed: a missing or incomplete observation must never be
            # able to read as success, and an unverifiable manifest must not
            # either.
            "coverage_complete": bool(
                population.get("reconciled")
                and population.get("not_reached") == 0
                and population.get("incomplete") == 0
                and population.get("attempted_matches_records")
                and not population.get("duplicate_symbols")
                and summary["attempted"] == summary["written"]
                and summary["write_failures"] == 0
                and all(m["complete"] for m in mans.values())
                and store.get("monitor_completed") is True),
            # Set when the monitor loop raised. The monitor has no per-trade
            # try/except, so one failure aborts the loop and leaves later
            # positions unevaluated AND unsaved. That gap is now visible.
            "loop_exception": store.get("loop_exception"),
            "monitor_completed": store.get("monitor_completed", False),
            "decision_manifest": mans["decision"]["digests"],
            "classification_manifest": mans["classification"]["digests"],
            "observation_manifest": mans["observation"]["digests"],
            "manifests_complete": all(m["complete"] for m in mans.values()),
            "manifest_missing": sum((m["missing"] for m in mans.values()), []),
            "effective_config": params,
            "config_review_required": config_review_gate(
                store.get("params_raw")),
            "recorder_version": RECORD_SCHEMA_VERSION,
        }
        summary["heartbeat_written"] = _append(heartbeat_path(), beat)
    except Exception as exc:                              # noqa: BLE001
        summary["flush_error"] = f"{type(exc).__name__}: {exc}"
    return summary
