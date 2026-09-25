#!/usr/bin/env python3
"""One-off repair: apply GIVEBACK_MIN_MFE_PCT to post-mortems written before the
floor existed.

The first giveback guard tested mfe_pct > 0, a sign test where an economic one was
needed. NEOG (entered 2026-09-23, stopped 2026-09-24) peaked at +0.03% -- 0.4 cents on
a $14.22 stock -- cleared that guard and stored gave_back_pct 3.41, which is its entire
loss relabelled as surrendered gains. NEOG is the first clean_v3 trade, so the bad
value sits inside the official sample and would pollute any future average.

Only recomputes gave_back_pct, and only where the field is already present. Trades
whose post-mortems predate the field entirely are LEFT ABSENT, not backfilled with
zero: missing must not be read as zero, and those four are all pre_clean anyway.

Follows repair_scaled_pnl.py: idempotent, prints every change, writes nothing when
there is nothing to change.

    python3 repair_giveback_floor.py [--apply]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from engine.tracker import GIVEBACK_MIN_MFE_PCT

TRADES = Path(__file__).parent / "logs" / "virtual_trades.json"


def main():
    apply = "--apply" in sys.argv
    trades = json.loads(TRADES.read_text())
    changes = []

    for t in trades:
        pm = t.get("post_mortem")
        if not pm or "gave_back_pct" not in pm:
            continue
        mfe = pm.get("max_favorable_excursion_pct")
        locked = pm.get("locked_pct")
        if mfe is None or locked is None:
            continue
        want = round(mfe - locked, 2) if mfe >= GIVEBACK_MIN_MFE_PCT else 0.0
        have = pm["gave_back_pct"]
        if abs(have - want) > 1e-9:
            changes.append((t["symbol"], t.get("sample_phase"), mfe, locked, have, want))
            if apply:
                pm["gave_back_pct"] = want

    print(f"floor = {GIVEBACK_MIN_MFE_PCT}%   scanned {len(trades)} trades")
    if not changes:
        print("  nothing to repair -- already consistent")
        return
    for sym, phase, mfe, locked, have, want in changes:
        print(f"  {sym:6s} [{phase}]  mfe {mfe:+.2f}%  locked {locked:+.2f}%"
              f"   gave_back {have:.2f}% -> {want:.2f}%")
    if apply:
        TRADES.write_text(json.dumps(trades, indent=2))
        print(f"\n  WROTE {TRADES}")
    else:
        print(f"\n  dry run -- re-run with --apply to write")


if __name__ == "__main__":
    main()
