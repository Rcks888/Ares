"""Phase 4 shadow comparison — status classification and record building.

Compares the live inline tracker exit chain against
canonical exit_policy + the tracker_v3_2dp compatibility adapter.

MEASUREMENT ONLY. The inline tracker stays authoritative. Nothing here places an
order, closes a position, persists production state, writes a trade log, touches
psi_state.json, changes queue behaviour, or sends an ordinary trade alert.

This module has no production effect unless explicitly wired. As of this commit
nothing imports it.

Scope. This module is PURE: classification, comparison and record building. It
does not invoke the tracker and does not capture stdout itself. Invocation and
capture are thin wiring, deliberately deferred to the Phase 4 deployment commit
so the risky logic — which pairings may count as agreement — is testable in
isolation.

WHY NO tracker.py CHANGE IS NEEDED
----------------------------------
The concern that motivated explicit status capture is real: tracker's blanket
`except Exception` converts an evaluation failure into a silent no-action, so
"no error observed" is NOT evidence that the inline path evaluated successfully.

But tracker's handler is not fully silent. It prints, per symbol:

    Error checking {symbol}: {e}

So inline failure is already externally observable. Phase 4 can therefore
determine conclusively whether the inline evaluation ran WITHOUT modifying
tracker.py, by capturing stdout around the call and matching that marker.

This keeps Phase 4 purely additive and preserves the separation the plan
requires:

    Phase 4   observe WHETHER failure occurred        (this module)
    Phase 0.5 improve HOW production responds to it   (separate change)

Nothing here adds a minimum-history guard, alert escalation, consecutive-failure
tracking, cache repair, or refined exception handling. Those are Phase 0.5.

FRAGILITY, STATED
-----------------
Marker matching depends on that print format. If the string changes, failures
would silently reclassify as valid no-action — the exact error this module
exists to prevent. Hence `MARKER_CONTRACT` below is asserted against the live
tracker source by the test suite, so a format change breaks the build rather
than corrupting the record.
"""

import hashlib
import inspect
import json

from engine.tracker_compat import CONTRACT_VERSION, STORED_2DP

RECORD_SCHEMA_VERSION = 1

# Asserted against engine/tracker.py by tests/test_parity_compare.py.
MARKER_CONTRACT = '  Error checking {trade[\'symbol\']}: {e}'
MARKER_PREFIX = "Error checking "

# --- evaluation status ------------------------------------------------------
NOT_ATTEMPTED = "NOT_ATTEMPTED"
SUCCEEDED_NO_ACTION = "SUCCEEDED_NO_ACTION"
SUCCEEDED_ACTION = "SUCCEEDED_ACTION"
FAILED = "FAILED"

# --- result classes. A missing evaluation is NOT a match. -------------------
MATCH = "MATCH"
NON_DECISION_STATE_DIFFERENCE = "NON_DECISION_STATE_DIFFERENCE"
DECISION_CHANGING_MISMATCH = "DECISION_CHANGING_MISMATCH"
INLINE_EVALUATION_FAILURE = "INLINE_EVALUATION_FAILURE"
SHADOW_EVALUATION_FAILURE = "SHADOW_EVALUATION_FAILURE"
BOTH_EVALUATIONS_FAILED = "BOTH_EVALUATIONS_FAILED"
INVALID_SHADOW_RECORD = "INVALID_SHADOW_RECORD"
# The loaded tracker source does not match the reviewed source, so failure
# detection cannot be trusted. Inline trading continues; coverage is void.
SHADOW_CONTRACT_INVALID = "SHADOW_CONTRACT_INVALID"
# The bar was not the deterministic daily Close, so any difference could be
# market movement rather than a migration difference. Not comparable.
PRICE_SOURCE_NONDETERMINISTIC = "PRICE_SOURCE_NONDETERMINISTIC"

PASSING_CLASSES = (MATCH, NON_DECISION_STATE_DIFFERENCE)

# Fields whose difference means the two paths would have ACTED differently.
DECISION_FIELDS = ("status", "exit_reason", "exit_date", "exit_price",
                   "scaled_out", "scale_out_date")

# Fields compared for persistent-state equivalence under tracker_v3_2dp.
STATE_FIELDS = STORED_2DP + ("stop_loss", "entry_price", "original_shares")


def canonical_hash(obj):
    """Stable hash of a state object.

    Serialised with sorted keys before hashing, because dict ordering and repr
    are not stable enough to compare raw. Used for the before/after production
    state hashes that evidence non-mutation.
    """
    return hashlib.md5(
        json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def verify_marker_contract(tracker_module):
    """Confirm the LOADED tracker source carries the error marker.

    Checking the working-tree file is not sufficient: Python may have loaded a
    different installed or cached module path. This inspects the module object
    actually in use and records its file and source hash, so a mismatch between
    the reviewed source and the executing source is visible rather than assumed
    away.

    Returns a dict; `valid` False means shadow classification must not claim
    coverage. The inline tracker is unaffected either way.
    """
    info = {"tracker_file": None, "tracker_source_hash": None,
            "marker_contract_match": False, "valid": False, "error": None}
    try:
        info["tracker_file"] = getattr(tracker_module, "__file__", None)
        src = inspect.getsource(tracker_module)
        info["tracker_source_hash"] = hashlib.md5(src.encode()).hexdigest()
        info["marker_contract_match"] = MARKER_CONTRACT in src
        info["valid"] = info["marker_contract_match"]
    except Exception as exc:                        # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def inline_failed_for(symbol, captured_stdout):
    """True if tracker printed its per-symbol error marker for `symbol`.

    Matched per symbol, not per run: one symbol failing must not mark another
    as failed when several are evaluated in the same call.
    """
    if not captured_stdout:
        return False
    needle = f"{MARKER_PREFIX}{symbol}:"
    return any(needle in line for line in captured_stdout.splitlines())


def classify_status(state_before, state_after, failed, attempted=True):
    """Derive an evaluation status. Never infers success from silence."""
    if not attempted:
        return NOT_ATTEMPTED
    if failed:
        return FAILED
    if state_before is None or state_after is None:
        return NOT_ATTEMPTED
    if _acted(state_before, state_after):
        return SUCCEEDED_ACTION
    return SUCCEEDED_NO_ACTION


def _acted(before, after):
    """An action is a closure, a scale-out, or any persisted state change."""
    if before.get("status") != after.get("status"):
        return True
    if bool(before.get("scaled_out")) != bool(after.get("scaled_out")):
        return True
    return any(before.get(f) != after.get(f) for f in STORED_2DP)


def _decisions_equivalent(inline_after, shadow_after):
    diffs = [f for f in DECISION_FIELDS
             if inline_after.get(f) != shadow_after.get(f)]
    return (not diffs), diffs


def _state_equivalent(inline_after, shadow_after):
    diffs = [f for f in STATE_FIELDS
             if f in inline_after or f in shadow_after
             if inline_after.get(f) != shadow_after.get(f)]
    return (not diffs), diffs


def classify_result(inline_status, shadow_status, inline_after=None,
                    shadow_after=None):
    """Decide the result class from the status pair, then the payloads.

    Returns (result_class, differing_fields).

    Failure pairings are resolved BEFORE any content comparison, so a failed
    evaluation can never be rescued into agreement by a matching payload.
    """
    if inline_status == FAILED and shadow_status == FAILED:
        return BOTH_EVALUATIONS_FAILED, []
    if inline_status == FAILED:
        return INLINE_EVALUATION_FAILURE, []
    if shadow_status == FAILED:
        return SHADOW_EVALUATION_FAILURE, []
    if NOT_ATTEMPTED in (inline_status, shadow_status):
        return INVALID_SHADOW_RECORD, []
    if inline_status not in (SUCCEEDED_ACTION, SUCCEEDED_NO_ACTION) or \
            shadow_status not in (SUCCEEDED_ACTION, SUCCEEDED_NO_ACTION):
        return INVALID_SHADOW_RECORD, []

    # One acted and the other did not: a decision difference by construction.
    if inline_status != shadow_status:
        return DECISION_CHANGING_MISMATCH, ["<action_produced>"]

    if inline_after is None or shadow_after is None:
        return INVALID_SHADOW_RECORD, []

    ok_dec, dec_diffs = _decisions_equivalent(inline_after, shadow_after)
    if not ok_dec:
        return DECISION_CHANGING_MISMATCH, dec_diffs

    ok_state, state_diffs = _state_equivalent(inline_after, shadow_after)
    if not ok_state:
        return NON_DECISION_STATE_DIFFERENCE, state_diffs
    return MATCH, []


def build_record(symbol, entry_date, timestamp, lineage,
                 inline_status, shadow_status,
                 inline_before=None, inline_after=None,
                 shadow_after=None, available_history_bars=None,
                 exception_type=None, exception_message=None,
                 cycle_id=None, shadow_exception_type=None,
                 shadow_exception_message=None,
                 production_state_hash_before=None,
                 production_state_hash_after=None,
                 contract=None, bar=None):
    """Assemble one comparison event. Pure; performs no I/O.

    `would_change_action` is True only for a decision-changing mismatch. It is
    deliberately NOT True for a state-only difference, and NOT False for a
    failure — a failure has no defined action to compare, so it is None.
    """
    result, diffs = classify_result(inline_status, shadow_status,
                                    inline_after, shadow_after)
    # A broken marker contract voids coverage regardless of payload agreement:
    # if failure detection cannot be trusted, neither can "no failure".
    if contract is not None and not contract.get("valid", False):
        result, diffs = SHADOW_CONTRACT_INVALID, ["<marker_contract>"]
    # Shadow execution must not mutate production state. If it did, the
    # comparison is void no matter what it concluded.
    if (production_state_hash_before is not None
            and production_state_hash_after is not None
            and production_state_hash_before != production_state_hash_after):
        result, diffs = INVALID_SHADOW_RECORD, ["<production_state_mutated>"]
    # Refuse rather than compare when the price was not deterministic.
    if bar is not None and bar.get("price_source") not in (None, "daily"):
        result, diffs = PRICE_SOURCE_NONDETERMINISTIC, ["<price_source>"]
    if result == DECISION_CHANGING_MISMATCH:
        would_change = True
    elif result in PASSING_CLASSES:
        would_change = False
    else:
        would_change = None

    def pick(d, key):
        return None if d is None else d.get(key)

    return {
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "timestamp": timestamp,
        "cycle_id": cycle_id,
        "symbol": symbol,
        "entry_date": entry_date,
        "production_commit": lineage.get("production_commit"),
        "exit_policy_md5": lineage.get("exit_policy_md5"),
        "compatibility_contract": lineage.get("compatibility_contract",
                                              CONTRACT_VERSION),
        "inline_evaluation_status": inline_status,
        "shadow_evaluation_status": shadow_status,
        "inline_decision": _decision_of(inline_before, inline_after,
                                        inline_status),
        "shadow_decision": _decision_of(inline_before, shadow_after,
                                        shadow_status),
        "inline_exit_reason": pick(inline_after, "exit_reason"),
        "shadow_exit_reason": pick(shadow_after, "exit_reason"),
        "inline_effective_stop": pick(inline_after, "effective_stop"),
        "shadow_effective_stop": pick(shadow_after, "effective_stop"),
        "inline_scaled_out": pick(inline_after, "scaled_out"),
        "shadow_scaled_out": pick(shadow_after, "scaled_out"),
        "inline_persistent_state": _state_of(inline_after),
        "shadow_compatible_state": _state_of(shadow_after),
        "difference_class": result,
        "differing_fields": diffs,
        "would_change_action": would_change,
        "available_history_bars": available_history_bars,
        # Named inline_* for symmetry with shadow_*. The unprefixed
        # exception_type/message are retained as aliases so existing readers
        # do not break.
        "inline_exception_type": exception_type,
        "inline_exception_message": exception_message,
        "exception_type": exception_type,
        "exception_message": exception_message,
        "shadow_exception_type": shadow_exception_type,
        "shadow_exception_message": shadow_exception_message,
        "tracker_source_hash": (contract or {}).get("tracker_source_hash"),
        "tracker_file": (contract or {}).get("tracker_file"),
        "marker_contract_match": (contract or {}).get("marker_contract_match"),
        "production_state_hash_before": production_state_hash_before,
        "production_state_hash_after": production_state_hash_after,
        "bar_date": (bar or {}).get("date"),
        "bar_price": (bar or {}).get("price"),
        "bar_rsi": (bar or {}).get("rsi"),
        "bar_bearish_div": (bar or {}).get("bearish_div"),
        "price_source": (bar or {}).get("price_source"),
        "abm_equality_boundary": _abm_boundary(symbol, inline_after
                                               or inline_before),
        "sdgr_dead_band_state": _sdgr_dead_band(symbol, inline_after
                                                or inline_before),
    }


def _abm_boundary(symbol, state):
    """trailing_stop == stop_loss, ABM's required-coverage boundary."""
    if symbol != "ABM" or not state:
        return None
    ts = state.get("trailing_stop", state.get("stop_loss"))
    sl = state.get("stop_loss")
    if ts is None or sl is None:
        return None
    return {"trailing_stop": ts, "stop_loss": sl, "equal": ts == sl,
            "ratcheted": ts > sl,
            "labels_stop_loss_if_stopped": ts <= sl}


# SDGR is registered REQUIRED coverage. SECZ is useful supplemental coverage --
# it is also in the giveback dead band -- but must not substitute for SDGR.
DEAD_BAND_WATCH = {"SDGR": "required", "SECZ": "supplemental"}


def _sdgr_dead_band(symbol, state):
    """Active trail below entry: the giveback dead-band condition."""
    if symbol not in DEAD_BAND_WATCH or not state:
        return None
    ts = state.get("trailing_stop", state.get("stop_loss"))
    sl, entry = state.get("stop_loss"), state.get("entry_price")
    if None in (ts, sl, entry):
        return None
    return {"coverage_role": DEAD_BAND_WATCH[symbol],
            "trailing_stop": ts, "entry_price": entry,
            "peak_price": state.get("peak_price"), "ratcheted": ts > sl,
            "in_dead_band": ts > sl and ts < entry,
            "gap_below_entry_pct": round((ts / entry - 1) * 100, 4)}


def _decision_of(before, after, status):
    """A short decision label, or None when no valid decision was produced."""
    if status not in (SUCCEEDED_ACTION, SUCCEEDED_NO_ACTION) or after is None:
        return None
    if status == SUCCEEDED_NO_ACTION:
        return "hold"
    if after.get("status") == "closed":
        return f"close:{after.get('exit_reason')}"
    if before is not None and not before.get("scaled_out") \
            and after.get("scaled_out"):
        return "scale_out"
    return "state_update"


def _state_of(d):
    if d is None:
        return None
    return {f: d[f] for f in STATE_FIELDS if f in d}


def summarize(records):
    """Aggregate for Phase 4 acceptance review. Counts, never verdicts."""
    counts = {}
    for r in records:
        counts[r["difference_class"]] = counts.get(r["difference_class"], 0) + 1
    by_symbol = {}
    for r in records:
        s = by_symbol.setdefault(r["symbol"], {"events": 0, "classes": {}})
        s["events"] += 1
        s["classes"][r["difference_class"]] = \
            s["classes"].get(r["difference_class"], 0) + 1
    blocking = [c for c in counts if c not in PASSING_CLASSES]
    return {
        "events": len(records),
        "by_class": counts,
        "by_symbol": by_symbol,
        "blocking_classes": sorted(blocking),
        "acceptance_clean": not blocking,
        "note": ("acceptance_clean covers mismatch conditions only. Coverage "
                 "conditions (ABM equality boundary, SDGR dead-band lifecycle, "
                 "both exit bars present, VPS-runtime tests, lineage "
                 "completeness) are separate and not asserted here."),
    }
