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
                  bar_fn=None, bar_recheck=None, params=None, params_fn=None,
                  premise_fn=None, lineage=None, cycle_id=None,
                  output_path=DEFAULT_OUTPUT, warn=None):
    """Run one shadow observation cycle around an inline tracker call.

    Returns (inline_result, summary). `inline_result` is whatever
    `inline_call()` returned, untouched.

    `load_state()` must return the list of trade dicts as persisted.
    `module_eval(cloned_trade, params, bar)` must return the post-evaluation
    state dict for the canonical module plus adapter, or raise.
    `bar_fn(symbol)` must return the bar dict, and is called in the PRE-inline
    phase so both paths are evaluated against the same market data. A bar_fn
    failure is per-symbol and does not abort the cycle.

    `bar_recheck(symbol, bar)` is called AFTER the inline call and returns a
    reason string if the pre-captured bar turned out not to be the bar the inline
    path evaluated -- e.g. the inline path refreshed the underlying cache
    mid-cycle. Capturing bars pre-inline keeps parity from seeing a LATER bar,
    but it cannot by itself prove the inline path did not move to a newer one, so
    the staleness direction is checked here instead of assumed.

    `params_fn()` is preferred over `params`. Passing an already-evaluated
    `params` means the caller evaluated it BEFORE entering this function, so a
    config-load failure would raise at the call site and prevent the inline
    tracker from running at all -- the exact failure mode fail-open exists to
    prevent. A callable is resolved inside the protected section instead.

    `premise_fn()` is resolved AFTER the inline call, because it reports what the
    inline path actually experienced (e.g. whether a live price source turned out
    to be reachable). Returning anything other than a dict with
    ok=True refuses the cycle rather than comparing against a premise that did
    not hold.
    """
    params = params or {}
    cycle_id = cycle_id or str(uuid.uuid4())[:8]
    summary = {"cycle_id": cycle_id, "attempted": 0, "written": 0,
               "write_failures": 0, "records": [], "shadow_system_error": None,
               "contract_valid": None}

    # --- inline path first, outside all shadow protection --------------------
    before = None
    try:
        before = copy.deepcopy(load_state())
    except Exception as exc:                        # noqa: BLE001
        summary["shadow_system_error"] = f"pre-state: {type(exc).__name__}: {exc}"

    # Bars are captured BEFORE the inline call, alongside the pre-inline state,
    # so the parity side cannot be handed a later bar than the inline path saw.
    bars = {}
    try:
        if bar_fn:
            for t in (before or []):
                if t.get("status") != "open":
                    continue
                try:
                    bars[t.get("symbol")] = bar_fn(t.get("symbol"))
                except Exception as exc:            # noqa: BLE001
                    bars[t.get("symbol")] = {"_error":
                                             f"{type(exc).__name__}: {exc}"}
    except Exception as exc:                        # noqa: BLE001
        summary["shadow_system_error"] = f"bars: {type(exc).__name__}: {exc}"

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
                                    captured, output_path, summary, bars,
                                    params_fn, premise_fn, bar_recheck))
    except Exception as exc:                        # noqa: BLE001
        summary["shadow_system_error"] = f"{type(exc).__name__}: {exc}"

    if warn and (summary["shadow_system_error"] or summary["write_failures"]):
        try:
            warn(f"shadow system degraded: cycle={cycle_id} "
                 f"err={summary['shadow_system_error']} "
                 f"write_failures={summary['write_failures']}")
        except Exception:                           # noqa: BLE001
            pass
    return inline_result, summary


def _recheck_failed(bar_recheck, symbol, bar):
    """Return a reason string if the pre-captured bar is no longer trustworthy."""
    if not bar_recheck:
        return None
    try:
        return bar_recheck(symbol, bar) or None
    except Exception as exc:                        # noqa: BLE001
        return f"recheck failed: {type(exc).__name__}: {exc}"


def _shadow_pass(tracker_module, load_state, module_eval, params, lineage,
                 cycle_id, before, captured, output_path, summary, bars=None,
                 params_fn=None, premise_fn=None, bar_recheck=None):
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

    premise = {"ok": True, "source": "not_checked"}
    if premise_fn is not None:
        try:
            premise = dict(premise_fn())
        except Exception as exc:                    # noqa: BLE001
            premise = {"ok": False,
                       "reason": f"{type(exc).__name__}: {exc}"}
    out["premise"] = premise

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

        bar = (bars or {}).get(symbol)
        shadow_after, s_exc_t, s_exc_m = None, None, None
        if not premise.get("ok", True):
            s_exc_t = "PremiseNotHeld"
            s_exc_m = str(premise.get("reason", "premise refused"))
        elif params is None:
            s_exc_t = "ParamsUnavailable"
            s_exc_m = out.get("params_error", "params unavailable")
        elif bar is None or bar.get("_error"):
            s_exc_t = "BarUnavailable"
            s_exc_m = (bar or {}).get("_error", "no bar supplied")
        elif _recheck_failed(bar_recheck, symbol, bar):
            s_exc_t = "BarStale"
            s_exc_m = _recheck_failed(bar_recheck, symbol, bar)
        else:
            try:
                shadow_after = module_eval(copy.deepcopy(pre), dict(params), bar)
            except Exception as exc:                # noqa: BLE001
                s_exc_t, s_exc_m = type(exc).__name__, str(exc)
        shadow_status = sc.classify_status(pre, shadow_after,
                                           s_exc_t is not None)

        records.append((symbol, pre, post, inline_status, shadow_status,
                        shadow_after, s_exc_t, s_exc_m, contract, bar))

    hash_after = sc.canonical_hash(copy.deepcopy(load_state()))

    built = []
    for (symbol, pre, post, i_st, s_st, s_after, s_t, s_m, ctr, bar) in records:
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
            contract=ctr, bar=bar)
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
