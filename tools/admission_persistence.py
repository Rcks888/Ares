"""Persistence checks for admission capture v1 (design §3.5, §4.2, §4.4).

Inspection side only. Production never imports this module, and nothing here
reads a file: every function takes facts and returns a verdict. The M4
inspection tool reads the committed state files and the recorder's records,
then calls these functions; the M3 persistence controls call them on what a
real failing run left in a scratch repository.

Vocabulary
----------
save fact      not_attempted | completed | raised             (§3.5)
membership     present | absent                               (pending, queue)
count          a non-negative int                             (trades, r5)
observed       a membership or count, or "unreadable"

Verdicts, from best to worst: consistent < incident < INCONCLUSIVE < FAIL.
An incident is consistent with the code (a save that did not complete) and
is reported by name; it is never read as a persisted effect.

Every table is a list of rows with explicit, mutually exclusive conditions.
`*_rows()` returns the rows that match one input; the public check returns
that row's result, or FAIL `table_gap` / `table_overlap` if zero or several
rows match, so a broken table cannot raise and cannot hide (C-ENUM, §7).
"""

import json

SAVE_VALUES = ("not_attempted", "completed", "raised")
MEMBERSHIP = ("present", "absent")
UNREADABLE = "unreadable"

CONSISTENT = "consistent"
INCIDENT = "incident"
INCONCLUSIVE = "INCONCLUSIVE"
FAIL = "FAIL"
_RANK = {CONSISTENT: 0, INCIDENT: 1, INCONCLUSIVE: 2, FAIL: 3}


def worst(*verdicts):
    """The worse of several verdicts (§4.4: an entry takes the worse of its
    two dimensions)."""
    return max(verdicts, key=lambda v: _RANK[v])


# --------------------------------------------------------------------------
# Save chains (§3.5 "Writers and save chains")
# --------------------------------------------------------------------------

def chain_save(owner, carriers=()):
    """One save value for a mutation's chain: completed if any save in it
    completed, else raised if one was attempted, else not_attempted.

    Returns (value, problem). A carrier attempted after its owner raised is
    impossible at the registered source (§3.5, writer inventory: no `try`
    around the owner's save), so it is reported as `carrier_after_raise`.
    An unknown save value is `save_fact_invalid`."""
    chain = (owner,) + tuple(carriers)
    if any(s not in SAVE_VALUES for s in chain):
        return None, "save_fact_invalid"
    if owner == "raised" and any(s != "not_attempted" for s in carriers):
        return None, "carrier_after_raise"
    if "completed" in chain:
        return "completed", None
    if "raised" in chain:
        return "raised", None
    return "not_attempted", None


# --------------------------------------------------------------------------
# The §3.5 table: one total function over (intent, baseline, observed, save)
# --------------------------------------------------------------------------
#
# Row conditions use four derived facts, so the same rows serve membership
# and counts:
#   readable   observed != "unreadable"
#   eq_intent  observed == intent
#   eq_base    observed == baseline
#   change     intent != baseline

def _facts(intent, baseline, observed):
    readable = observed != UNREADABLE
    return {"readable": readable,
            "eq_intent": readable and observed == intent,
            "eq_base": readable and observed == baseline,
            "change": intent != baseline}


STATE_ROWS = (
    ("save_fact_invalid",
     lambda f, s: s not in SAVE_VALUES,
     FAIL, "{file}_save_fact_invalid"),
    ("unreadable",
     lambda f, s: s in SAVE_VALUES and not f["readable"],
     INCONCLUSIVE, "{file}_unreadable"),
    ("eq_intent_saved",
     lambda f, s: f["readable"] and f["eq_intent"]
     and s in ("completed", "raised"),
     CONSISTENT, None),
    ("eq_intent_no_change",
     lambda f, s: f["readable"] and f["eq_intent"]
     and s == "not_attempted" and not f["change"],
     CONSISTENT, None),
    ("eq_intent_change_without_save",
     lambda f, s: f["readable"] and f["eq_intent"]
     and s == "not_attempted" and f["change"],
     FAIL, "{file}_changed_without_save"),
    ("eq_baseline_contradicts_completed",
     lambda f, s: f["readable"] and not f["eq_intent"] and f["eq_base"]
     and s == "completed",
     FAIL, "{file}_contradicts_completed_save"),
    ("eq_baseline_not_persisted",
     lambda f, s: f["readable"] and not f["eq_intent"] and f["eq_base"]
     and s in ("raised", "not_attempted"),
     INCIDENT, "{file}_not_persisted"),
    ("no_dump_matches",
     lambda f, s: s in SAVE_VALUES and f["readable"]
     and not f["eq_intent"] and not f["eq_base"],
     FAIL, "{file}_no_dump_matches"),
)


def state_rows(intent, baseline, observed, save, table=None):
    """Names of the §3.5 rows matching one input (C-ENUM: exactly one)."""
    f = _facts(intent, baseline, observed)
    return [r[0] for r in (table or STATE_ROWS) if r[1](f, save)]


def check_state(file, intent, baseline, observed, save, table=None):
    """(verdict, reason) for one key of one file. `file` names the reason:
    pending, queue or trade."""
    rows = table or STATE_ROWS
    f = _facts(intent, baseline, observed)
    hit = [r for r in rows if r[1](f, save)]
    if len(hit) != 1:
        return FAIL, "table_gap" if not hit else "table_overlap"
    _, _, verdict, reason = hit[0]
    return verdict, (reason.format(file=file) if reason else None)


def check_membership(file, intent, baseline, observed, save, table=None):
    """§3.5 for pending `(symbol, date)` or queue `symbol` membership."""
    if intent not in MEMBERSHIP or baseline not in MEMBERSHIP or \
            observed not in MEMBERSHIP + (UNREADABLE,):
        return FAIL, f"{file}_membership_invalid"
    return check_state(file, intent, baseline, observed, save, table)


def _is_count(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def check_trade_count(n, trade_appended, m, save, table=None):
    """§3.5 on trade counts (r5): baseline n, intent n + 1 if the entry
    appended, else n; observed count m or unreadable."""
    if not _is_count(n) or not isinstance(trade_appended, bool) or \
            not (m == UNREADABLE or _is_count(m)):
        return FAIL, "trade_count_invalid"
    return check_state("trade", n + 1 if trade_appended else n, n, m, save,
                       table)


# --------------------------------------------------------------------------
# Queue admission (§4.2): queue_memory against the committed queue
# --------------------------------------------------------------------------

QUEUE_MEMORY = {
    # queue_memory: (intent, implied baseline). `inserted` says the reloaded
    # queue (:323) did not hold the symbol; `already_queued` says it did.
    "kept": ("present", "absent"),
    "inserted_then_evicted": ("absent", "absent"),
    "already_queued": ("present", "present"),
}


def check_queue(queue_memory, observed, queue_save, baseline=None,
                table=None):
    """(verdict, reason) for one queue admission or demotion. `baseline`, when
    given, is the parent-commit fold; it must agree with the memory facts.
    `already_queued` has no owning save, so its save fact is None."""
    if queue_memory not in QUEUE_MEMORY:
        return FAIL, "queue_memory_unknown"
    intent, implied = QUEUE_MEMORY[queue_memory]
    if baseline is not None and baseline != implied:
        return FAIL, "baseline_memory_mismatch"
    if queue_memory == "already_queued":
        if queue_save is not None:
            return FAIL, "queue_save_without_mutation"
        queue_save = "not_attempted"
    elif queue_save is None:
        return FAIL, "queue_save_fact_missing"
    return check_membership("queue", intent, implied, observed, queue_save,
                            table)


# --------------------------------------------------------------------------
# Fill composition (§4.4): two dimensions, then the named combination
# --------------------------------------------------------------------------

# (executed_marked, trade_appended, pending observed, m - n, n >= 1) -> name.
# `None` in the last position matches any n. Only combinations whose two
# dimensions are both consistent or incident are named; every other input
# is FAIL or INCONCLUSIVE by a dimension check.
FILL_NAMES = (
    ((True, True, "absent", 1, None), "filled"),
    ((True, True, "absent", 0, None), "vanished_fill"),
    ((True, True, "present", 0, None), "fill_not_persisted"),
    ((False, True, "present", 1, None), "booked_pending_retained"),
    ((False, True, "present", 0, None), "trade_not_persisted"),
    ((True, False, "absent", 0, False), "removed"),
    ((True, False, "absent", 0, True), "removed_over_existing_trade"),
    ((True, False, "present", 0, None), "removal_not_persisted"),
    ((False, False, "present", 0, None), "retained"),
)


def fill_names(executed_marked, trade_appended, pending_observed, n, m,
               names=None):
    """Names matching one input (C-ENUM: exactly one when nameable)."""
    if not (_is_count(n) and _is_count(m)):
        return []
    key = (executed_marked, trade_appended, pending_observed, m - n)
    return [name for (k, name) in (names or FILL_NAMES)
            if k[:4] == key and (k[4] is None or k[4] == (n >= 1))]


def check_fill(executed_marked, trade_appended, pending_observed, n, m,
               pending_save, trades_save, outcome=None,
               pending_baseline="present", table=None, names=None):
    """One fill entry against its pass's two saves and the committed files.

    Returns a dict: verdict, name (only when the verdict is consistent or
    incident), reasons, new_link, and the two dimension results."""
    reasons = []
    out = {"pending": None, "trade": None, "name": None, "new_link": False}
    if not isinstance(executed_marked, bool) or \
            not isinstance(trade_appended, bool):
        out.update(verdict=FAIL, reasons=["fill_facts_invalid"])
        return out
    if pending_baseline != "present":
        reasons.append("baseline_memory_mismatch")
    if trades_save in SAVE_VALUES and trades_save != "not_attempted" and \
            pending_save != "completed":
        reasons.append("trades_save_before_pending_save_completed")
    pv = check_membership("pending", "absent" if executed_marked else
                          "present", "present", pending_observed,
                          pending_save, table)
    tv = check_trade_count(n, trade_appended, m, trades_save, table)
    out["pending"], out["trade"] = pv, tv
    verdict = worst(pv[0], tv[0], FAIL if reasons else CONSISTENT)
    reasons += [r for _, r in (pv, tv) if r]
    if verdict in (CONSISTENT, INCIDENT):
        hit = fill_names(executed_marked, trade_appended, pending_observed,
                         n, m, names)
        if len(hit) != 1:
            verdict = FAIL
            reasons.append("fill_unnamed" if not hit else "fill_names_overlap")
        else:
            name = hit[0]
            if name == "filled" and outcome == "dropped_exception":
                name = "booked_with_drop_event"
            out["name"] = name
    out["new_link"] = (verdict in (CONSISTENT, INCIDENT)
                       and tv[0] == CONSISTENT and _is_count(m)
                       and _is_count(n) and m - n == 1)
    out["verdict"] = verdict
    out["reasons"] = reasons
    return out


# --------------------------------------------------------------------------
# Observed state from a committed file (§3.5 "Observed state")
# --------------------------------------------------------------------------

def parse_state(text, parent_had_file):
    """The committed file as a list, or "unreadable". A file missing where
    the parent had it is unreadable; missing in both is an empty list (the
    loaders return [] for a missing file)."""
    if text is None:
        return UNREADABLE if parent_had_file else []
    try:
        value = json.loads(text)
    except ValueError:
        return UNREADABLE
    if not isinstance(value, list) or \
            not all(isinstance(x, dict) for x in value):
        return UNREADABLE
    return value


def pending_membership(state, symbol, signal_date):
    if state == UNREADABLE:
        return UNREADABLE
    return ("present" if any(p.get("symbol") == symbol
                             and p.get("date") == signal_date for p in state)
            else "absent")


def queue_membership(state, symbol):
    if state == UNREADABLE:
        return UNREADABLE
    return ("present" if any(q.get("symbol") == symbol for q in state)
            else "absent")


def trade_count(state, symbol, signal_date, entry_date):
    if state == UNREADABLE:
        return UNREADABLE
    return sum(1 for t in state if t.get("symbol") == symbol
               and t.get("signal_date") == signal_date
               and t.get("entry_date") == entry_date)
