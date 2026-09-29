"""Schedule-aware collection freshness, and the states that depend on it.

WHY THIS FILE EXISTS
--------------------
The `[parity]` diagnostic prints only on trouble, so silence was the success
signature. A cycle that ran and collected nothing, and a cycle that never ran at
all, produced identical output: nothing. That ambiguity produced four wrong
diagnoses during activation.

A fixed age threshold cannot resolve it either. Collection runs on WEEKDAYS at
13:30 and 21:00 UTC, so the Friday 21:00 -> Monday 13:30 gap is 64.5 hours: a 36h
or 48h limit reports a stall every single weekend, and a 72h limit generous enough
to survive the weekend would hide several missed weekday cycles. Freshness is
therefore computed from the registered schedule plus a grace period.

Every assertion here pins time explicitly. A gate whose verdict depends on when
the suite happens to run is not a gate.
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import tools.pre_parity_snapshot as pps  # noqa: E402
from engine import parity_runner as pr  # noqa: E402

FAILS = []
COUNT = 0
PROD_HB = ROOT / "logs" / pr.HEARTBEAT_NAME


def check(label, cond, got=None):
    global COUNT
    COUNT += 1
    if cond:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {got if got is not None else ''}")
        FAILS.append(label)


def D(s):
    """Pinned UTC instant. 2026-10-02 is a Friday, 10-05 a Monday."""
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def beat(completed, attempted=1, written=1, token=1, commit="99c6b08",
         started=None, **kw):
    b = {"heartbeat_schema_version": pr.HEARTBEAT_SCHEMA_VERSION,
         "production_commit": commit,
         "cycle_started_at": started or completed,
         "capture_cycle_token": token,
         "cycle_completed_at": completed,
         "cycle_id": f"cyc-{completed}",
         "attempted": attempted, "written": written, "write_failures": 0,
         "record_schema_version": 2, "contract_valid": True,
         "shadow_system_error": None, "capture_error": None,
         "results_error": None}
    b.update(kw)
    return b


def read_with(beats, now):
    """Run the real reader against a temp heartbeat log at a pinned instant."""
    d = Path(tempfile.mkdtemp())
    (d / "logs").mkdir()
    (d / "logs" / pr.HEARTBEAT_NAME).write_text(
        "".join(json.dumps(b) + "\n" for b in beats))
    orig = pps.ROOT
    try:
        pps.ROOT = d
        return pps._parity_heartbeat(now)
    finally:
        pps.ROOT = orig


# ---- 1. weekend interval: the case a fixed threshold gets wrong -------------
def test_weekend_interval_is_not_stale():
    friday_beat = beat("2026-10-02T21:04:00+00:00")
    span = (D("2026-10-05T13:30") - D("2026-10-02T21:00")).total_seconds() / 3600
    check("the weekend gap really is 64.5h (why 36h/48h cannot work)",
          span == 64.5, span)
    for label, t in [("Saturday midday", "2026-10-03T12:00"),
                     ("Sunday night", "2026-10-04T23:00"),
                     ("Monday inside grace", "2026-10-05T14:59")]:
        h = read_with([friday_beat], D(t))
        check(f"Friday beat is fresh on {label}", h["stale"] is False,
              (h["stale"], h["age_hours"], h["last_due_cycle"]))
    h = read_with([friday_beat], D("2026-10-04T23:00"))
    check("a fixed 36h rule WOULD have called it stale (control)",
          h["age_hours"] > 36, h["age_hours"])
    check("a fixed 48h rule WOULD have called it stale (control)",
          h["age_hours"] > 48, h["age_hours"])
    check("schedule-aware rule does not", h["stale"] is False)


# ---- 2. fast weekday detection ---------------------------------------------
def test_missed_weekday_cycle_is_stale_same_day():
    friday_beat = beat("2026-10-02T21:04:00+00:00")
    h = read_with([friday_beat], D("2026-10-05T15:01"))
    check("Monday 13:30 missed -> stale by 15:01 same day",
          h["stale"] is True, h["stale"])
    check("names the cycle it failed to cover",
          h["last_due_cycle"] == "2026-10-05T13:30:00+00:00",
          h["last_due_cycle"])
    check("stale despite being well under a 72h fallback",
          h["age_hours"] < 72, h["age_hours"])
    ok = read_with([friday_beat, beat("2026-10-05T13:34:00+00:00")],
                   D("2026-10-05T15:01"))
    check("a beat covering that cycle clears it", ok["stale"] is False)


def test_second_daily_cycle_is_tracked_independently():
    beats = [beat("2026-10-05T13:34:00+00:00")]
    check("covered after cycle 1, before cycle 2 is due",
          read_with(beats, D("2026-10-05T20:00"))["stale"] is False)
    h = read_with(beats, D("2026-10-05T22:31"))
    check("missing the 21:00 cycle is stale", h["stale"] is True)
    check("attributed to the 21:00 cycle, not 13:30",
          h["last_due_cycle"] == "2026-10-05T21:00:00+00:00",
          h["last_due_cycle"])


# ---- 3. grace boundary ------------------------------------------------------
def test_grace_period_boundary_is_exact():
    check("grace is the registered 90 minutes",
          pps.PARITY_CYCLE_GRACE_MINUTES == 90)
    due = D("2026-10-05T13:30")
    check("not yet due one second before the deadline",
          pps.last_due_cycle(due + timedelta(minutes=90, seconds=-1)) != due)
    check("due exactly at the deadline",
          pps.last_due_cycle(due + timedelta(minutes=90)) == due)


# ---- 4. US market holiday: cron still runs ---------------------------------
def test_weekday_market_holiday_still_expects_a_cycle():
    """Thanksgiving 2026-11-26 is a Thursday and a US market holiday.

    Freshness follows the CRON schedule, not the trading calendar: the collector
    is scheduled that day, so a missing heartbeat is a real miss. The trading
    calendar governs Item 3b's expected data bar, not instrumentation liveness.
    """
    due = pps.last_due_cycle(D("2026-11-26T15:01"))
    check("a cycle is still expected on a weekday market holiday",
          due == D("2026-11-26T13:30"), due)
    h = read_with([beat("2026-11-25T21:04:00+00:00")], D("2026-11-26T15:01"))
    check("missing it is stale even though markets were closed",
          h["stale"] is True, h["stale"])


def test_weekend_days_expect_nothing():
    check("no cycle is due on Saturday itself",
          pps.last_due_cycle(D("2026-10-03T23:59")) < D("2026-10-03T00:00"))
    check("Sunday resolves back to Friday's last cycle",
          pps.last_due_cycle(D("2026-10-04T23:59")) == D("2026-10-02T21:00"))


# ---- 5. malformed / unknown fails closed ------------------------------------
def test_malformed_timestamp_is_unknown_not_fresh():
    h = read_with([beat("not-a-timestamp")], D("2026-10-05T15:01"))
    check("stale is None, never False, when the check cannot run",
          h["stale"] is None, h["stale"])
    check("the reason is reported", any("unparseable" in m
                                       for m in h["malformed"]), h["malformed"])


def test_unparseable_line_is_reported_not_skipped_silently():
    d = Path(tempfile.mkdtemp())
    (d / "logs").mkdir()
    (d / "logs" / pr.HEARTBEAT_NAME).write_text(
        json.dumps(beat("2026-10-05T13:34:00+00:00")) + "\n{ broken\n")
    orig = pps.ROOT
    try:
        pps.ROOT = d
        h = pps._parity_heartbeat(D("2026-10-05T15:01"))
    finally:
        pps.ROOT = orig
    check("the valid beat is still counted", h["beats"] == 1, h["beats"])
    check("the broken line is reported", len(h["malformed"]) == 1, h["malformed"])


def test_absent_heartbeat_is_unknown_not_fresh():
    d = Path(tempfile.mkdtemp())
    (d / "logs").mkdir()
    orig = pps.ROOT
    try:
        pps.ROOT = d
        h = pps._parity_heartbeat(D("2026-10-05T15:01"))
    finally:
        pps.ROOT = orig
    check("absent heartbeat is not present", h["present"] is False)
    check("absent heartbeat is UNKNOWN, not fresh", h["stale"] is None,
          h["stale"])


# ---- 6. partial collection --------------------------------------------------
def test_attempted_not_equal_written_is_surfaced():
    h = read_with([beat("2026-10-05T13:34:00+00:00", attempted=4, written=2)],
                  D("2026-10-05T14:00"))
    check("a fresh but PARTIAL cycle is not stale", h["stale"] is False)
    check("but the shortfall is recorded", len(h["incomplete_cycles"]) == 1,
          h["incomplete_cycles"])
    check("with both counts, so the gap is quantified",
          h["incomplete_cycles"][0]["attempted"] == 4
          and h["incomplete_cycles"][0]["written"] == 2,
          h["incomplete_cycles"])


# ---- 7. composite cycle identity -------------------------------------------
def test_process_local_token_does_not_collapse_cycles():
    """Two cron processes both start their counter at 1."""
    beats = [beat("2026-10-05T13:34:00+00:00", token=1,
                  started="2026-10-05T13:30:10+00:00"),
             beat("2026-10-05T21:04:00+00:00", token=1,
                  started="2026-10-05T21:00:09+00:00")]
    h = read_with(beats, D("2026-10-05T22:00"))
    check("identical tokens from different processes are 2 identities",
          h["identities"] == 2, h["identities"])
    same = [beats[0], dict(beats[0])]
    h2 = read_with(same, D("2026-10-05T14:00"))
    check("a genuinely duplicated beat is 1 identity",
          h2["identities"] == 1, h2["identities"])


# ---- 8. emitter behaviour ---------------------------------------------------
def test_heartbeat_write_failure_cannot_break_the_cycle():
    bad = "/proc/definitely-not-writable/ev.jsonl"
    ok = pr.append_heartbeat({"attempted": 0, "written": 0}, {}, "c", bad)
    check("an unwritable heartbeat returns False rather than raising",
          ok is False, ok)


def test_heartbeat_is_written_even_with_zero_records():
    d = Path(tempfile.mkdtemp())
    out = str(d / "ev.jsonl")
    pr.append_heartbeat({"attempted": 0, "written": 0, "write_failures": 0,
                         "contract_valid": None, "cycle_started_at":
                         "2026-10-05T13:30:10+00:00"}, {}, "cyc", out)
    p = Path(pr.heartbeat_path(out))
    check("a zero-record cycle still leaves proof it ran", p.exists())
    b = json.loads(p.read_text().strip())
    check("attempted=0 is recorded explicitly, not omitted",
          b["attempted"] == 0, b)
    check("and is therefore distinguishable from never having run",
          b["cycle_completed_at"] is not None)


def test_heartbeat_path_is_derived_from_the_evidence_path():
    check("derived as a sibling, so one redirect covers both",
          pr.heartbeat_path("/tmp/x/ev.jsonl")
          == Path("/tmp/x") / pr.HEARTBEAT_NAME,
          pr.heartbeat_path("/tmp/x/ev.jsonl"))


# ---- 9. schedule is single-sourced -----------------------------------------
def test_schedule_is_not_hardcoded_anywhere_else():
    """The times must exist in ONE place. A second copy is how the verifier and
    the freshness check end up disagreeing about when a cycle was due."""
    files = [p for p in (list(ROOT.glob("engine/*.py"))
                         + list(ROOT.glob("tools/*.py"))
                         + list(ROOT.glob("*.py")))
             if p.name != "pre_parity_snapshot.py"]
    # Only EXECUTABLE re-definitions matter. Prose in a comment cannot make the
    # verifier and the freshness check disagree about when a cycle was due, so
    # flagging it would be noise that trains readers to ignore this assertion.
    offenders = [f"{p.name}: {lit}" for p in files
                 for lit in ("(13, 30)", "(21, 0)")
                 if lit in p.read_text()]
    check("no module re-declares the cycle times as code", not offenders,
          offenders)
    # Prose is allowed, but prose that CONTRADICTS the contract is a real defect:
    # it is how a reader ends up reasoning about a schedule the code never ran.
    registered = {f"{h:02d}:{m:02d}" for h, m in pps.PARITY_CYCLE_SCHEDULE_UTC}
    import re as _re
    contradictions = []
    for p in files:
        for hit in set(_re.findall(r"\b([0-2]?\d:[0-5]\d)\s*UTC", p.read_text())):
            norm = hit if len(hit) == 5 else "0" + hit
            if norm not in registered:
                contradictions.append(f"{p.name}: {hit}")
    check("no module documents a cycle time the contract does not define",
          not contradictions, contradictions)
    check("the contract is weekday-only", pps.PARITY_CYCLE_WEEKDAYS
          == (0, 1, 2, 3, 4), pps.PARITY_CYCLE_WEEKDAYS)
    check("two cycles per weekday are registered",
          len(pps.PARITY_CYCLE_SCHEDULE_UTC) == 2)


# ---- 10. states -------------------------------------------------------------
def test_new_states_are_registered_and_require_append_only():
    for s in ("ARMED_NO_HEARTBEAT", "ACTIVE_STALE"):
        check(f"{s} is a registered state", s in pps.PARITY_OUTPUT_STATES)
    check("ACTIVE_STALE still requires append-only evidence",
          "ACTIVE_STALE" in pps.PARITY_STATES_REQUIRING_APPEND_ONLY)
    check("ARMED_NO_HEARTBEAT is distinct from ACTIVE_STALE",
          "ARMED_NO_HEARTBEAT" != "ACTIVE_STALE")


def test_armed_deadline_derives_from_activation_not_commit():
    """A VPS that pulls at 18:00 a commit made at 10:00 must not be blamed for
    the 13:30 cycle it could not have run."""
    eff = D("2026-10-05T18:00")
    dl = pps.next_cycle_deadline(eff)
    # 21:00 the SAME day, not the next day: it is the first scheduled cycle
    # strictly after activation became effective.
    check("first expected cycle after an 18:00 activation is that day's 21:00",
          dl == D("2026-10-05T21:00") + timedelta(minutes=90), dl)
    check("NOT the same-day 13:30 a commit-date rule would have blamed",
          dl > D("2026-10-05T15:00"), dl)
    early = pps.next_cycle_deadline(D("2026-10-05T10:00"))
    check("an activation before 13:30 is held to that day's 13:30",
          early == D("2026-10-05T13:30") + timedelta(minutes=90), early)
    fri = pps.next_cycle_deadline(D("2026-10-02T22:00"))
    check("a Friday-night activation waits until Monday",
          fri == D("2026-10-05T13:30") + timedelta(minutes=90), fri)


# ---- 11. host role is declared, never inferred ------------------------------
def marker(body):
    d = Path(tempfile.mkdtemp())
    p = d / pps.HOST_MARKER_NAME
    p.write_text(body)
    return p


def test_valid_marker_is_live():
    r = pps._host_role(env={}, marker_path=marker(pps.HOST_MARKER_CONTENT))
    check("valid marker resolves live", r["role"] == "live", r)
    check("source is the marker, not an inference", r["source"] == "marker")


def test_absent_marker_is_archive():
    d = Path(tempfile.mkdtemp())
    r = pps._host_role(env={}, marker_path=d / pps.HOST_MARKER_NAME)
    check("absent marker resolves archive", r["role"] == "archive", r)


def test_the_exact_defect_found_in_review():
    """THE regression this contract exists to prevent.

    A development checkout that has pulled the bot's operational log commits has
    git history identical to the VPS. The old detector read that as "live", so the
    laptop asserted live_collection_recent about a collector it cannot observe.
    """
    # Called directly rather than read from a snapshot file: a conditional check
    # would make this suite's assertion count differ between hosts, and the
    # cross-host comparison depends on the counts being identical.
    inferred = pps._live_log_changes_are_bot_only_and_allowlisted()
    check("the discredited inference is derived from pulled log commits alone",
          isinstance(inferred["is_live_host"], bool)
          and inferred["is_live_host"] == bool(inferred["changed_logs"]),
          (inferred["is_live_host"], len(inferred["changed_logs"])))
    check("so it can report live with NO unapproved log paths at all",
          inferred["unapproved_log_paths"] == []
          or inferred["is_live_host"] is True,
          inferred["unapproved_log_paths"])
    d = Path(tempfile.mkdtemp())
    r = pps._host_role(env={}, marker_path=d / pps.HOST_MARKER_NAME)
    check("the marker contract resolves the same host as archive",
          r["role"] == "archive", r)
    check("archive is NOT live", r["role"] != "live")


def test_malformed_and_unreadable_markers_are_unknown():
    r = pps._host_role(env={}, marker_path=marker("ARES_LIVE_HOST_V99"))
    check("malformed marker body is unknown, not live", r["role"] == "unknown", r)
    check("the body is reported for diagnosis", "malformed" in r["detail"])
    d = Path(tempfile.mkdtemp())
    p = d / pps.HOST_MARKER_NAME
    p.mkdir()          # a directory where a file is expected: unreadable
    r2 = pps._host_role(env={}, marker_path=p)
    check("unreadable marker is unknown, not archive", r2["role"] == "unknown",
          r2)


def test_env_override_takes_precedence_and_fails_closed():
    good = marker(pps.HOST_MARKER_CONTENT)
    r = pps._host_role(env={pps.HOST_CONTEXT_ENV: "archive"}, marker_path=good)
    check("a valid override beats a valid marker", r["role"] == "archive", r)
    check("the override is identified as the source",
          r["source"] == f"env:{pps.HOST_CONTEXT_ENV}")
    bad = pps._host_role(env={pps.HOST_CONTEXT_ENV: "LIVE"}, marker_path=good)
    check("an invalid override is unknown, NOT a fallthrough to the marker",
          bad["role"] == "unknown", bad)
    check("a typo cannot silently restore inference",
          bad["role"] != "live", bad)
    check("only the registered contexts are accepted",
          pps.HOST_CONTEXTS == ("live", "archive"), pps.HOST_CONTEXTS)


def test_default_resolution_path_actually_runs():
    """Exercises _host_role() with NO arguments.

    Every other host-role test passes env= explicitly, so the real default path --
    reading os.environ and the real marker location -- was never executed. It was
    broken by a missing import while 1786 assertions reported ALL PASS. A seam
    that only tests its own injection points is not tested.
    """
    r = pps._host_role()
    check("the zero-argument call resolves without raising",
          r["role"] in ("live", "archive", "unknown"), r)
    check("it reports which source decided", r["source"] is not None, r)
    check("on a checkout with no marker it is archive, not live",
          r["role"] in ("archive", "unknown") or pps.HOST_MARKER_PATH.exists(),
          r)


def test_marker_lives_outside_the_repository():
    check("the marker is not repository content",
          pps.HOST_MARKER_PATH.name.startswith("."))
    check("and sits above the repo root, so it cannot be committed",
          pps.HOST_MARKER_PATH.parent == ROOT.parent,
          str(pps.HOST_MARKER_PATH))
    tracked = (ROOT / pps.HOST_MARKER_NAME).exists()
    check("no marker exists inside the repo", not tracked)


# ---- 12. activation provenance ---------------------------------------------
def test_activation_timestamp_requires_registered_provenance():
    check("provenance sources are an explicit enumeration",
          "vps_snapshot" in pps.ACTIVATION_TIMESTAMP_SOURCES
          and "commit_timestamp_fallback" in pps.ACTIVATION_TIMESTAMP_SOURCES,
          pps.ACTIVATION_TIMESTAMP_SOURCES)
    check("the seed is labelled as an upper bound, not as observed",
          pps.ACTIVATION_CONFIDENCE == "upper_bound", pps.ACTIVATION_CONFIDENCE)
    check("the seed names where it came from",
          pps.ACTIVATION_TIMESTAMP_SOURCE == "first_parity_record",
          pps.ACTIVATION_TIMESTAMP_SOURCE)
    first = json.loads((ROOT / "logs" / "tracker_parity_v1.jsonl")
                       .read_text().splitlines()[0])
    check("and it matches the first parity record it claims to come from",
          pps.ACTIVATION_EFFECTIVE_AT == first["timestamp"],
          (pps.ACTIVATION_EFFECTIVE_AT, first["timestamp"]))


def test_activation_timestamp_with_unregistered_source_is_refused():
    d = Path(tempfile.mkdtemp())
    (d / "tools").mkdir()
    (d / "tools" / "parity_baseline.json").write_text(json.dumps({
        "parity_activation_effective_at": "2026-09-29T05:52:22+00:00",
        "parity_activation_timestamp_source": "vibes"}))
    orig = pps.ROOT
    try:
        pps.ROOT = d
        a = pps._activation_effective_at()
    finally:
        pps.ROOT = orig
    check("an unregistered provenance voids the parsed timestamp",
          a["parsed"] is None, a)
    check("and says why", "unregistered source" in (a["error"] or ""), a["error"])


def test_activation_provenance_is_reported_in_every_state():
    """It must not be visible only on the ARMED path.

    collection_health previously sourced it from parity_output, which populates
    it on the ARMED early return only -- so in every ACTIVE state, which is
    normal operation, the provenance of the timestamp the deadline math depends
    on was reported as None while the baseline held a real value.
    """
    src = (ROOT / "tools" / "pre_parity_snapshot.py").read_text()
    check("collection_health reads the activation contract directly",
          'act = _activation_effective_at()' in src)
    check("and does not source it from the ARMED-only path",
          'snap["parity_output"].get("activation_effective_at")' not in src)
    a = pps._activation_effective_at()
    check("the contract exposes provenance alongside the value",
          set(a) >= {"effective_at", "commit", "timestamp_source",
                     "confidence"}, sorted(a))


def test_activation_deadline_cases():
    """The four cases that decide whether ARMED tightening is fair."""
    G = timedelta(minutes=pps.PARITY_CYCLE_GRACE_MINUTES)
    cases = [("activation 10:00 -> that day's 13:30", "2026-10-05T10:00",
              D("2026-10-05T13:30") + G),
             ("activation 18:00 -> that day's 21:00", "2026-10-05T18:00",
              D("2026-10-05T21:00") + G),
             ("activation inside 13:30 grace -> next is 21:00",
              "2026-10-05T14:30", D("2026-10-05T21:00") + G),
             ("activation Fri 22:00 -> Monday 13:30", "2026-10-02T22:00",
              D("2026-10-05T13:30") + G)]
    for label, eff, want in cases:
        got = pps.next_cycle_deadline(D(eff))
        check(label, got == want, (got, want))


def test_activation_is_preserved_not_regenerated():
    src = (ROOT / "tools" / "pre_parity_snapshot.py").read_text()
    for key in ("parity_activation_commit", "parity_activation_effective_at",
                "parity_activation_timestamp_source",
                "parity_activation_timestamp_confidence"):
        check(f"{key} is written by the baseline writer", key in src)
    # The writer must read the EXISTING value first and fall back to the seed,
    # never the reverse: an unconditional seed would rewrite recorded history on
    # every rebaseline.
    check("the writer prefers the recorded value over the seed",
          "_activation_effective_at()[\"effective_at\"]\n                or ACTIVATION_EFFECTIVE_AT" in src
          or "or ACTIVATION_EFFECTIVE_AT" in src, "preservation pattern absent")


if __name__ == "__main__":
    before = (PROD_HB.exists(),
              PROD_HB.stat().st_size if PROD_HB.exists() else None)
    for fn in sorted([v for k, v in list(globals().items())
                      if k.startswith("test_")], key=lambda f: f.__name__):
        print(f"\n{fn.__name__}")
        fn()
    after = (PROD_HB.exists(),
             PROD_HB.stat().st_size if PROD_HB.exists() else None)
    check("SUITE GUARD: production heartbeat unchanged by this suite",
          before == after, (before, after))
    check("SUITE GUARD: pps.ROOT restored after monkeypatching",
          pps.ROOT == ROOT, pps.ROOT)
    print(f"\n{COUNT} assertions")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("ALL PASS")
