"""Phase 4 shadow wiring — the smallest additive layer.

Orchestrates one observation cycle: capture inline stdout, derive explicit
inline status, run the canonical module plus tracker_v3_2dp on isolated cloned
state, classify, append one JSONL record per symbol, and return the inline
tracker's result UNCHANGED.

MEASUREMENT ONLY. Dormant — nothing imports this as of this commit.

FAIL-OPEN CONTRACT
------------------
The inline result is obtained first and returned no matter what happens
afterwards. Every shadow concern — cloning, invocation, classification,
serialisation, JSONL append, file locking, disk-full — is wrapped so it cannot
raise into the live cycle. A JSONL write failure must never become a position
evaluation failure.

But fail-open operational behaviour must not become fail-silent research
evidence. Every attempt is counted, so missing records are visible in the
coverage report rather than absent from it: compare `attempted` against
`written` in the returned summary.

STATE-MUTATION EVIDENCE
-----------------------
The production state hash pair brackets SHADOW execution only, not the whole
cycle. The inline tracker is *supposed* to mutate state — it is authoritative.
What must be proven is that the shadow path changed nothing, so the baseline is
taken AFTER inline completes and again after shadow finishes. A difference
voids the comparison via INVALID_SHADOW_RECORD.

INJECTION
---------
`inline_call`, `load_state` and `module_eval` are injected rather than imported
so this layer is testable without market data, and so the exact canonical call
sequence — already proven in the Phase 0 harness — is supplied at the wiring
site rather than duplicated here.

OUT OF SCOPE
------------
No minimum-history guard, no alert escalation, no consecutive-failure tracking,
no cache repair, no exception-handling change. Those are Phase 0.5. This layer
also never replaces the inline return value, prevents a close or scale-out,
writes live trade state or strategy logs, submits or cancels an order, sends a
routine trade alert, updates psi_state.json, or mutates the price cache.
"""

import copy
import io
import json
import os
import sys
import tempfile
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from engine import parity_compare as sc

# Dedicated append-only output. NOT to be consumed by dashboards, exporters,
# training datasets, clean_v3 statistics, position restoration or Telegram
# summaries. This is migration evidence, not strategy evidence.
#
# NAMING. Called "parity", not "shadow", deliberately. tracker.py already owns a
# live and unrelated shadow concept: trade['shadow'] and check_shadow_trades()
# track price for 30 days AFTER a position closes, which IS strategy evidence.
# Reusing the word for migration evidence would make the
# migration-evidence-vs-strategy-evidence rule unenforceable and would invite a
# future reader or analytics job to conflate the two. The inline_*/shadow_* FIELD
# names are kept inside records, where the two roles are unambiguous and the
# terminology is standard for differential testing.
#
# CONCURRENCY. stdout redirection is process-global, so per-symbol marker
# attribution is only safe while position evaluation is sequential in a single
# process. Verified: no threading, multiprocessing or asyncio anywhere in Ares
# code, no background jobs in run_ares.sh, and one call site at
# daily_report.py check_open_trades(). If that ever changes, stdout capture is
# no longer safe for parity observation.
DEFAULT_OUTPUT = "logs/tracker_parity_v1.jsonl"


def _now():
    return datetime.now(timezone.utc).isoformat()


HEARTBEAT_NAME = "parity_heartbeat_v1.jsonl"
HEARTBEAT_SCHEMA_VERSION = 1


def heartbeat_path(output_path=None):
    """Sibling of the evidence file, so ONE redirect seam covers both.

    Deriving it rather than giving it an independent default matters for tests:
    the live-path fixtures redirect DEFAULT_OUTPUT to a temp file, and a
    separately-defaulted heartbeat would have kept writing into the production
    log while the fixtures appeared isolated.
    """
    return Path(output_path or DEFAULT_OUTPUT).with_name(HEARTBEAT_NAME)


def append_heartbeat(summary, lineage, cycle_id, output_path=None):
    """Record that an observed cycle RAN, regardless of what it produced.

    Zero records is ambiguous without this: a cycle with no open positions and a
    cycle where the bridge never executed are indistinguishable, because the
    diagnostic print fires only on trouble. Silence is the success signature, and
    that ambiguity produced four wrong diagnoses during activation.

    Never raises: a heartbeat failure must not affect the inline result, and must
    not be able to turn an observation-only path into a production incident.
    """
    try:
        beat = {
            "heartbeat_schema_version": HEARTBEAT_SCHEMA_VERSION,
            # Composite identity. capture_cycle_token alone is NOT unique: it is
            # a process-local counter that restarts at 1 in every new Python
            # process, which is exactly what cron creates twice a day. Two
            # different cycles would otherwise share token 1.
            # SAME key the evidence records read (parity_compare.build_record).
            # This was "ares_commit" -- a key no producer ever emits -- so .get()
            # returned None on every cycle while contract_valid still said true.
            # One lineage source, read one way, or the two diverge unnoticed.
            "production_commit": (lineage or {}).get("production_commit"),
            # Absence is recorded as a NAMED defect rather than an empty field.
            # A heartbeat whose only cross-process-unique identity component is
            # missing is not a healthy heartbeat: capture_cycle_token restarts at
            # 1 in every cron process, so without the commit two distinct cycles
            # are distinguishable only by wall-clock proximity.
            "lineage_complete": bool((lineage or {}).get("production_commit")),
            "cycle_started_at": summary.get("cycle_started_at"),
            "capture_cycle_token": summary.get("capture_cycle_token"),
            "cycle_completed_at": _now(),
            "cycle_id": cycle_id,
            # Field names match the summary rather than renaming to
            # open_positions/records_written -- two names for one quantity is how
            # a reader ends up checking the wrong one.
            "attempted": summary.get("attempted"),
            "written": summary.get("written"),
            "write_failures": summary.get("write_failures"),
            "record_schema_version": sc.RECORD_SCHEMA_VERSION,
            "contract_valid": summary.get("contract_valid"),
            "shadow_system_error": summary.get("shadow_system_error"),
            "capture_error": summary.get("capture_error"),
            "results_error": summary.get("results_error"),
        }
        p = heartbeat_path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(beat, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except Exception:                               # noqa: BLE001
        return False


def append_record(record, output_path=DEFAULT_OUTPUT):
    """Append one JSONL record. Returns True on success, never raises.

    Opened in append mode per call so a concurrent cron cycle cannot truncate
    another's output. os.replace is not used: appending is the atomic-enough
    operation here and a rewrite would risk losing prior evidence.
    """
    try:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, default=str)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except Exception:                               # noqa: BLE001
        return False


def observe_cycle(tracker_module, inline_call, load_state, module_eval,
                  packets_fn=None, results_fn=None, params=None, params_fn=None,
                  lineage=None, cycle_id=None,
                  output_path=None, warn=None):
    """Run one shadow observation cycle around an inline tracker call.

    Returns (inline_result, summary). `inline_result` is whatever
    `inline_call()` returned, untouched.

    `load_state()` must return the list of trade dicts as persisted.
    `module_eval(cloned_trade, params, bar)` must return the post-evaluation
    state dict for the canonical module plus adapter, or raise.
    `packets_fn()` is called AFTER the inline call and returns
    `(cycle_token, packets)` — the tracker's Phase 0.5 decision-input capture for
    the invocation that just completed. Only packets whose embedded cycle_token
    matches the returned token are accepted; anything else is
    INLINE_INPUT_NOT_CAPTURED. It must be read after, not before: for a
    tracker-owned token, the token for a call does not exist until that call runs.

    This REPLACES the withdrawn Phase 4 bar_fn/bar_recheck reconstruction. Parity
    no longer derives inputs, so there is nothing to go stale and no premise to
    check -- see TRACKER_MIGRATION_PLAN_PHASE_0_5.md.

    `params_fn()` is preferred over `params`. Passing an already-evaluated
    `params` means the caller evaluated it BEFORE entering this function, so a
    config-load failure would raise at the call site and prevent the inline
    tracker from running at all -- the exact failure mode fail-open exists to
    prevent. A callable is resolved inside the protected section instead.

    """
    # Resolved at INVOCATION time, not at function-definition time.
    #
    # The previous signature was `output_path=DEFAULT_OUTPUT`, and Python
    # evaluates default arguments once when the function is defined. Rebinding
    # parity_runner.DEFAULT_OUTPUT afterwards therefore had NO effect, so a test
    # that redirected output that way silently appended fixture records to the
    # real logs/tracker_parity_v1.jsonl migration evidence while appearing to
    # pass. Resolving here makes runtime redirection work as it reads.
    #
    # There is deliberately NO fallback to DEFAULT_OUTPUT when an EXPLICIT path
    # fails: a failed write must stay visible as a missing record, not land
    # somewhere the caller did not ask for.
    if output_path is None:
        output_path = DEFAULT_OUTPUT
    params = params or {}
    cycle_id = cycle_id or str(uuid.uuid4())[:8]
    summary = {"cycle_id": cycle_id, "attempted": 0, "written": 0,
               "write_failures": 0, "records": [], "shadow_system_error": None,
               "contract_valid": None, "cycle_started_at": _now()}

    # --- inline path first, outside all shadow protection --------------------
    before = None
    try:
        before = copy.deepcopy(load_state())
    except Exception as exc:                        # noqa: BLE001
        summary["shadow_system_error"] = f"pre-state: {type(exc).__name__}: {exc}"

    buf = io.StringIO()
    try:
        with redirect_stdout(_Tee(buf, sys.stdout)):
            inline_result = inline_call()
    except Exception:
        # The inline path raised past its own handlers. That is a production
        # event, not a shadow event: re-raise so live behaviour is unchanged.
        sys.stdout.write(buf.getvalue())
        raise
    captured = buf.getvalue()

    # --- everything below is shadow-only and must not raise -----------------
    try:
        summary.update(_shadow_pass(tracker_module, load_state, module_eval,
                                    params, lineage or {}, cycle_id, before,
                                    captured, output_path, summary,
                                    params_fn, packets_fn, results_fn))
    except Exception as exc:                        # noqa: BLE001
        summary["shadow_system_error"] = f"{type(exc).__name__}: {exc}"

    # Written even when the shadow pass raised, and even when nothing was
    # observed: the question the heartbeat answers is "did this cycle run", which
    # is exactly the question that has no answer when the cycle produced no
    # records and printed nothing.
    summary["heartbeat_written"] = append_heartbeat(summary, lineage, cycle_id,
                                                   output_path)

    if warn and (summary["shadow_system_error"] or summary["write_failures"]):
        try:
            warn(f"shadow system degraded: cycle={cycle_id} "
                 f"err={summary['shadow_system_error']} "
                 f"write_failures={summary['write_failures']}")
        except Exception:                           # noqa: BLE001
            pass
    return inline_result, summary


# The registered Phase 0.5 packet schema. EXACT set, not a minimum: an extra key
# means the tracker's capture changed without this contract being reviewed, and a
# missing key means the evaluator would read a default in place of a real decision
# input. Both invalidate the packet until the schema is re-registered.
REGISTERED_PACKET_FIELDS = frozenset(
    {"cycle_token", "price", "price_source", "rsi", "bearish_div", "bar_date"})


def _packet_defect(packets, symbol, cycle_token):
    """Why this symbol's packet is unusable, or None if it is valid.

    Precedence is fixed and must not be reordered: store readability, then cycle
    identity, then symbol presence, then token match, then schema. A payload that
    looks plausible must never override an earlier integrity failure, so the
    schema is checked LAST and only on a packet already proven current.
    """
    if cycle_token is None:
        return ("no_cycle_token",
                f"the completed invocation published no cycle token, so no "
                f"packet can be authenticated as current (symbol {symbol})")
    p = (packets or {}).get(symbol)
    if p is None:
        return ("packet_absent",
                f"no decision-input packet for {symbol} in the completed "
                f"invocation (token {cycle_token!r}); the inline path did not "
                f"reach the capture point")
    if not isinstance(p, dict):
        return ("packet_not_a_mapping",
                f"packet for {symbol} is {type(p).__name__}, not a mapping")
    if p.get("cycle_token") != cycle_token:
        return ("packet_stale",
                f"packet for {symbol} carries token {p.get('cycle_token')!r}, "
                f"not the completed invocation's {cycle_token!r}; refusing "
                f"stale inline inputs")
    got = set(p)
    if got != REGISTERED_PACKET_FIELDS:
        missing = sorted(REGISTERED_PACKET_FIELDS - got)
        extra = sorted(got - REGISTERED_PACKET_FIELDS)
        return ("packet_schema_drift",
                f"packet for {symbol} does not match the registered schema: "
                f"missing {missing}, unregistered {extra}; refusing rather than "
                f"evaluating against defaulted decision inputs")
    return None


def _valid_packet(packets, symbol, cycle_token):
    """Return the packet only if it is current AND schema-exact."""
    if _packet_defect(packets, symbol, cycle_token) is not None:
        return None
    return packets[symbol]


def _capture_reason(packets, symbol, cycle_token):
    defect = _packet_defect(packets, symbol, cycle_token)
    return defect[1] if defect else None


def _shadow_pass(tracker_module, load_state, module_eval, params, lineage,
                 cycle_id, before, captured, output_path, summary,
                 params_fn=None, packets_fn=None, results_fn=None):
    out = {}
    contract = sc.verify_marker_contract(tracker_module)
    out["contract_valid"] = contract.get("valid")

    # Resolved here, not at the call site: a failure must degrade the parity
    # record, never the inline cycle that has already completed above.
    if params_fn is not None:
        try:
            params = dict(params_fn())
        except Exception as exc:                    # noqa: BLE001
            out["params_error"] = f"{type(exc).__name__}: {exc}"
            params = None

    # Read the tracker's capture for the invocation that just completed. Read
    # AFTER the inline call: a tracker-owned token does not exist before it.
    cycle_token, packets, capture_error = None, {}, None
    if packets_fn is not None:
        try:
            cycle_token, packets = packets_fn()
            packets = dict(packets or {})
        except Exception as exc:                    # noqa: BLE001
            capture_error = f"{type(exc).__name__}: {exc}"
    out["capture_cycle_token"] = cycle_token
    out["capture_error"] = capture_error

    # Decision-RESULT capture, read on the same terms as the packets: after the
    # inline call, read-only, and validated against the same invocation token.
    results, results_error = {}, None
    if results_fn is not None:
        try:
            _rtok, results = results_fn()
            results = dict(results or {})
            # A results token that disagrees with the packets token means the two
            # halves of the capture describe different invocations. Discard the
            # results rather than pair them with the wrong inputs.
            if _rtok != cycle_token:
                results, results_error = {}, (
                    f"results token {_rtok!r} != packets token {cycle_token!r}")
        except Exception as exc:                    # noqa: BLE001
            results, results_error = {}, f"{type(exc).__name__}: {exc}"
    out["results_error"] = results_error

    after_inline = copy.deepcopy(load_state())
    hash_before = sc.canonical_hash(after_inline)

    by_symbol = {t.get("symbol"): t for t in (after_inline or [])}
    records = []
    for pre in (before or []):
        if pre.get("status") != "open":
            continue
        symbol = pre.get("symbol")
        summary["attempted"] += 1
        post = by_symbol.get(symbol)

        inline_failed = sc.inline_failed_for(symbol, captured)
        inline_status = sc.classify_status(pre, post, inline_failed)

        defect = _packet_defect(packets, symbol, cycle_token)
        bar = None if defect else packets[symbol]
        defect_kind = defect[0] if defect else None
        shadow_after, s_exc_t, s_exc_m = None, None, None
        if params is None:
            s_exc_t = "ParamsUnavailable"
            s_exc_m = out.get("params_error", "params unavailable")
        elif bar is None:
            # No packet, or a packet from a superseded invocation. Never fall
            # back to an older value: that is the stale-record failure the
            # cycle token exists to prevent.
            s_exc_t = "INLINE_INPUT_NOT_CAPTURED"
            s_exc_m = capture_error or defect[1]
            if capture_error:
                defect_kind = "store_unreadable"
        else:
            try:
                shadow_after = module_eval(copy.deepcopy(pre), dict(params), bar)
            except Exception as exc:                # noqa: BLE001
                s_exc_t, s_exc_m = type(exc).__name__, str(exc)
        shadow_status = sc.classify_status(pre, shadow_after,
                                           s_exc_t is not None)

        eff_stop, eff_basis = sc.effective_stop_evidence(
            results, symbol, cycle_token, bar, pre.get("entry_date"),
            inline_failed)

        records.append((symbol, pre, post, inline_status, shadow_status,
                        shadow_after, s_exc_t, s_exc_m, contract, bar,
                        defect_kind, eff_stop, eff_basis))

    hash_after = sc.canonical_hash(copy.deepcopy(load_state()))

    built = []
    for (symbol, pre, post, i_st, s_st, s_after, s_t, s_m, ctr, bar,
         defect_kind, eff_stop, eff_basis) in records:
        rec = sc.build_record(
            symbol=symbol, entry_date=pre.get("entry_date"),
            timestamp=_now(), lineage=lineage,
            inline_status=i_st, shadow_status=s_st,
            inline_before=pre, inline_after=post, shadow_after=s_after,
            cycle_id=cycle_id,
            exception_type=_marker_exc(symbol, captured) if i_st == sc.FAILED else None,
            exception_message=_marker_msg(symbol, captured) if i_st == sc.FAILED else None,
            shadow_exception_type=s_t, shadow_exception_message=s_m,
            production_state_hash_before=hash_before,
            production_state_hash_after=hash_after,
            contract=ctr, bar=bar, capture_defect=defect_kind,
            inline_effective_stop=eff_stop,
            inline_effective_stop_basis=eff_basis)
        built.append(rec)
        if append_record(rec, output_path):
            summary["written"] += 1
        else:
            summary["write_failures"] += 1
    out["records"] = built
    out["production_state_unchanged"] = hash_before == hash_after
    return out


def _marker_line(symbol, captured):
    needle = f"{sc.MARKER_PREFIX}{symbol}:"
    for line in (captured or "").splitlines():
        if needle in line:
            return line.split(needle, 1)[1].strip()
    return None


def _marker_msg(symbol, captured):
    return _marker_line(symbol, captured)


def _marker_exc(symbol, captured):
    """Exception CLASS from the printed marker — almost always None.

    MEASURED LIMITATION. tracker prints str(e), not repr(e) or the class name:

        print(f"  Error checking {trade['symbol']}: {e}")

    so an AttributeError surfaces only as
    "'NoneType' object has no attribute 'iloc'" with no type anywhere in the
    line. The class is therefore NOT recoverable from stdout in general. This
    returns a type only when the message text happens to contain one — which is
    the exception rather than the rule — and None otherwise, because asserting a
    guessed type would be worse than admitting the gap.

    Consequence for Phase 4: inline_exception_message is reliable,
    inline_exception_type is usually None. Failure DETECTION is unaffected, since
    that depends on the marker's presence, not its content. Recovering the type
    requires structured status from Phase 0.5; do not add repr(e) to tracker as
    part of Phase 4.
    """
    msg = _marker_line(symbol, captured)
    if not msg:
        return None
    for known in ("AttributeError", "KeyError", "TypeError", "ValueError",
                  "IndexError", "ZeroDivisionError"):
        if known in msg:
            return known
    return None


class _Tee:
    """Mirror stdout so capture does not suppress live operator output."""

    def __init__(self, buf, original):
        self._buf, self._orig = buf, original

    def write(self, data):
        self._buf.write(data)
        try:
            self._orig.write(data)
        except Exception:                           # noqa: BLE001
            pass
        return len(data)

    def flush(self):
        for s in (self._buf, self._orig):
            try:
                s.flush()
            except Exception:                       # noqa: BLE001
                pass
