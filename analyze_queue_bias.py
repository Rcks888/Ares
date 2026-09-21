#!/usr/bin/env python3
"""Test whether queue expiry systematically discards the fastest movers.

The queue drops a signal when drift exceeds queue_max_drift_pct. Drift is not
random: a signal drifts positive because the stock moved in the predicted
direction before a slot freed up. If drops cluster in large positive drift while
slots are full, the surviving sample is biased toward slower, less extended
names -- smaller average wins and a weaker profit factor -- and nothing in the
accounting would contradict it. Selection bias leaves no arithmetic to trip over.

This script only reads. It answers three questions:

  1. Among drift-based drops, what is the sign? Positive drift means the trade
     ran away and was missed. Negative means the signal decayed and the drop was
     correct.
  2. Does the drift distribution of dropped signals differ from promoted ones?
  3. Do drops concentrate when the portfolio was full, which is what would make
     congestion the cause rather than a coincidence?

It refuses to draw a conclusion below a usable sample size, because with a
handful of events the difference between bias and noise is not decidable.
"""
import json
import sys
from datetime import date
from pathlib import Path

LOGS = Path(__file__).parent / "logs"
EVENTS = LOGS / "queue_events.jsonl"
TRADES = LOGS / "virtual_trades.json"
PARAMS = Path(__file__).parent / "config" / "strategy_params.json"

MIN_N = 10


def load_events():
    if not EVENTS.exists():
        return []
    out = []
    for line in EVENTS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def occupancy_at(trades, when):
    """How many positions were open on a given date."""
    if when is None:
        return None
    n = 0
    for t in trades:
        ed = _date(str(t.get('entry_date', '')).replace(' (next-bar)', ''))
        xd = _date(t.get('exit_date'))
        if ed is None or ed > when:
            continue
        if xd is None or xd > when:
            n += 1
    return n


def describe(name, vals):
    if not vals:
        print(f"    {name:<22} (none)")
        return
    vals = sorted(vals)
    n = len(vals)
    mean = sum(vals) / n
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    print(f"    {name:<22} n={n:<3} mean {mean:+.2f}%  median {med:+.2f}%  "
          f"range {vals[0]:+.2f}% to {vals[-1]:+.2f}%")


def main():
    events = load_events()
    if not events:
        print(f"  no queue events recorded at {EVENTS}")
        print(f"  Nothing to test yet. The queue writes an event per decision,")
        print(f"  so this becomes answerable once signals start being queued.")
        return 0

    trades = json.loads(TRADES.read_text()) if TRADES.exists() else []
    params = json.loads(PARAMS.read_text()) if PARAMS.exists() else {}
    max_pos = params.get('max_positions', 5)
    max_drift = params.get('queue_max_drift_pct', 5.0)

    by_action = {}
    for e in events:
        by_action.setdefault(e.get('action', '?'), []).append(e)

    print(f"\n  EVENTS: {len(events)} total")
    for a, evs in sorted(by_action.items(), key=lambda kv: -len(kv[1])):
        print(f"    {a:<14} {len(evs)}")

    dropped = by_action.get('dropped', []) + by_action.get('expired', [])
    promoted = by_action.get('promoted', [])

    print(f"\n  DROP REASONS")
    reasons = {}
    for e in dropped:
        reasons[e.get('drop_reason') or 'unspecified'] = \
            reasons.get(e.get('drop_reason') or 'unspecified', 0) + 1
    if reasons:
        for r, c in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {r:<28} {c}")
    else:
        print(f"    (no drops recorded)")

    print(f"\n  DRIFT DISTRIBUTION  (threshold +/-{max_drift}%)")
    d_drift = [e['drift_pct'] for e in dropped if e.get('drift_pct') is not None]
    p_drift = [e['drift_pct'] for e in promoted if e.get('drift_pct') is not None]
    describe("dropped/expired", d_drift)
    describe("promoted", p_drift)

    # The asymmetry that matters. A drop on positive drift is a missed runner;
    # a drop on negative drift is a decayed signal correctly discarded.
    ran_away = [d for d in d_drift if d > 0]
    decayed = [d for d in d_drift if d <= 0]
    print(f"\n  SIGN OF DROPPED DRIFT")
    print(f"    ran away (drift > 0)   {len(ran_away)}")
    print(f"    decayed  (drift <= 0)  {len(decayed)}")
    if d_drift:
        pct = len(ran_away) / len(d_drift) * 100
        print(f"    {pct:.0f}% of drops were signals moving in the predicted direction")

    print(f"\n  CONGESTION AT TIME OF DROP  (max_positions={max_pos})")
    full, notfull, unknown = 0, 0, 0
    for e in dropped:
        occ = occupancy_at(trades, _date(e.get('timestamp')))
        if occ is None:
            unknown += 1
        elif occ >= max_pos:
            full += 1
        else:
            notfull += 1
    print(f"    portfolio full       {full}")
    print(f"    slot was available   {notfull}")
    if unknown:
        print(f"    undetermined         {unknown}")

    print(f"\n  VERDICT")
    n = len(d_drift)
    if n < MIN_N:
        print(f"    UNDERPOWERED. {n} drift-bearing drop(s) recorded, need >={MIN_N}")
        print(f"    before the difference between bias and noise is decidable.")
        print(f"    Re-run this as the queue accumulates history. The numbers")
        print(f"    above are descriptive only -- do not act on them yet.")
    else:
        pct = len(ran_away) / n * 100
        if pct >= 70 and full > notfull:
            print(f"    BIAS INDICATED. {pct:.0f}% of drops were runners, and drops")
            print(f"    concentrate when the portfolio was full. The clean sample")
            print(f"    is likely skewed toward slower names.")
        elif pct >= 70:
            print(f"    ASYMMETRY PRESENT but not congestion-linked: {pct:.0f}% of")
            print(f"    drops were runners while slots were often available, so")
            print(f"    the drift threshold itself is the filter, not slot scarcity.")
        else:
            print(f"    NO CLEAR SKEW. {pct:.0f}% of drops were runners, consistent")
            print(f"    with drift cutting both directions roughly evenly.")

    print(f"\n  Read-only: nothing was modified.")
    return 0


if __name__ == '__main__':
    sys.exit(main())