#!/usr/bin/env python3
"""Controls for engine.admission_observer.classify_a2 (design §4.4, §7).

    python3 tests/run_a2_classification_controls.py

Value table (§4.4), O = 09:30 America/New_York on bar_date:

    after_open    same session and s >= O
    before_open   bar_date later than the event's session, or e < O
    unknown       s < O <= e, or e absent

and §3.3: a bracket with no t_end is open-ended, so any claim needing it is
unknown. Control 06 is implementation review finding 2, reproduced exactly.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from engine.admission_observer import classify_a2, session_open_utc  # noqa: E402


def _utc(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _case(start, end, bar):
    return classify_a2(start, end, bar)


def control_01_monitor_admission_wholly_after_open():
    s = _utc(2026, 10, 28, 16, 10)
    assert _case(s, s + timedelta(seconds=2), "2026-10-28") == "after_open"


def control_02_evening_admission_filled_next_session():
    s = _utc(2026, 10, 27, 21, 1)
    assert _case(s, s + timedelta(seconds=1), "2026-10-28") == "before_open"


def control_03_bracket_straddling_the_open():
    o = session_open_utc("2026-10-28")
    assert o == _utc(2026, 10, 28, 13, 30), o
    assert _case(o - timedelta(seconds=1), o, "2026-10-28") == "unknown"
    assert _case(o - timedelta(seconds=1), o + timedelta(seconds=1),
                 "2026-10-28") == "unknown"


def control_04_bracket_ending_before_open_same_session():
    s = _utc(2026, 10, 28, 13, 0)
    assert _case(s, s + timedelta(minutes=1), "2026-10-28") == "before_open"


def control_05_bracket_starting_exactly_at_open():
    o = session_open_utc("2026-10-28")
    assert _case(o, o + timedelta(seconds=1), "2026-10-28") == "after_open"


def control_06_open_ended_bracket_after_open_is_unknown():
    """Review finding 2: start after the open, no end, same session."""
    s = _utc(2026, 10, 28, 16, 10)
    got = _case(s, None, "2026-10-28")
    assert got == "unknown", f"open-ended bracket classified {got}"


def control_07_open_ended_bracket_every_position_is_unknown():
    for s, bar in ((_utc(2026, 10, 28, 13, 0), "2026-10-28"),
                   (_utc(2026, 10, 27, 21, 1), "2026-10-28"),
                   (_utc(2026, 10, 28, 16, 10), "2026-10-27")):
        got = _case(s, None, bar)
        assert got == "unknown", f"{s} / {bar}: open-ended gave {got}"


def control_08_missing_start_or_bar_date_is_unknown():
    e = _utc(2026, 10, 28, 16, 10)
    assert _case(None, e, "2026-10-28") == "unknown"
    assert _case(e, e, None) == "unknown"


def control_09_dst_end_monday_2_nov():
    """§7: 13:31 UTC on Mon 2 Nov is 08:31 EST; the open is 14:30 UTC."""
    assert session_open_utc("2026-11-02") == _utc(2026, 11, 2, 14, 30)
    s = _utc(2026, 11, 2, 13, 31)
    assert _case(s, s + timedelta(seconds=1), "2026-11-02") == "before_open"
    s = _utc(2026, 11, 2, 16, 10)
    assert _case(s, s + timedelta(seconds=1), "2026-11-02") == "after_open"


def control_10_bar_before_event_session_is_flagged():
    s = _utc(2026, 10, 28, 16, 10)
    got = _case(s, s + timedelta(seconds=1), "2026-10-27")
    assert got == "inconsistent_bar_before_event", got


CONTROLS = [v for k, v in sorted(globals().items()) if k.startswith("control_")]


def main():
    failures = []
    for fn in CONTROLS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failures.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {exc}")
    n = len(CONTROLS)
    print(f"\n  {n - len(failures)}/{n} A2 classification controls passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
