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

from engine import shadow_compare as sc

# Dedicated append-only output. NOT to be consumed by dashboards, exporters,
# training datasets, clean_v3 statistics, position restoration or Telegram
# summaries. Shadow data is migration evidence, not strategy evidence.
DEFAULT_OUTPUT = "logs/tracker_shadow_v1.jsonl"


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
                  params=None, lineage=None, cycle_id=None,
                  output_path=DEFAULT_OUTPUT, warn=None):
    """Run one shadow observation cycle around an inline tracker call.

    Returns (inline_result, summary). `inline_result` is whatever
    `inline_call()` returned, untouched.

    `load_state()` must return the list of trade dicts as persisted.
    `module_eval(cloned_trade, params)` must return the post-evaluation state
    dict for the canonical module plus adapter, or raise.
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
                                    captured, output_path, summary))
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


def _shadow_pass(tracker_module, load_state, module_eval, params, lineage,
                 cycle_id, before, captured, output_path, summary):
    out = {}
    contract = sc.verify_marker_contract(tracker_module)
    out["contract_valid"] = contract.get("valid")

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

        shadow_after, s_exc_t, s_exc_m = None, None, None
        try:
            shadow_after = module_eval(copy.deepcopy(pre), dict(params))
        except Exception as exc:                    # noqa: BLE001
            s_exc_t, s_exc_m = type(exc).__name__, str(exc)
        shadow_status = sc.classify_status(pre, shadow_after,
                                           s_exc_t is not None)

        records.append((symbol, pre, post, inline_status, shadow_status,
                        shadow_after, s_exc_t, s_exc_m, contract))

    hash_after = sc.canonical_hash(copy.deepcopy(load_state()))

    built = []
    for (symbol, pre, post, i_st, s_st, s_after, s_t, s_m, ctr) in records:
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
            contract=ctr)
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
