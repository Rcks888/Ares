#!/usr/bin/env python3
"""Backfill stdev_20 into queue records written before queue_signal stored it.

queue_signal() did not carry stdev_20 originally, so a signal queued before that
fix reaches open_trade() without it and receives the 0.05 fallback -- a 10% stop
where the real figure would give roughly 4-5%. That is not a reporting error: the
stop decides when the position exits, so it changes behaviour for as long as the
trade is open.

The pipeline is fixed for new signals. This exists for records already sitting
in the queue, which would otherwise be promoted carrying a fabricated stop and,
because open_trade stamps the sample phase from the fill context, be labelled
clean while carrying pre-fix data.

stdev_20 is recomputed through the same code path the scanner uses --
load_stock() then add_indicators() -- rather than reimplementing the formula, so
the value cannot drift from what a fresh signal would have carried.

Dry run by default. Pass --apply to write, which backs up first.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from engine.data_feed import load_stock
from engine.indicators import add_indicators

ROOT = Path(__file__).parent
LOGS = ROOT / "logs"
PARAMS = ROOT / "config" / "strategy_params.json"
QUEUE_FILES = [LOGS / "signal_queue.json", LOGS / "queue_ranked.json"]
FALLBACK = 0.05


def main():
    apply = '--apply' in sys.argv
    params = json.loads(PARAMS.read_text()) if PARAMS.exists() else {}
    sl_mult = params.get('stop_loss_multiplier', 2.0)

    resolved = {}
    changed_files = []

    for path in QUEUE_FILES:
        if not path.exists():
            print(f"  {path.name}: absent, skipped")
            continue
        try:
            entries = json.loads(path.read_text())
        except Exception as e:
            print(f"  {path.name}: unreadable ({e}), skipped")
            continue

        touched = 0
        for q in entries:
            sym = q.get('symbol')
            if not sym:
                continue
            cur = q.get('stdev_20')
            if cur and cur > 0:
                print(f"  {path.name}: {sym} already has "
                      f"stdev_20={cur:.4f}, left alone")
                continue

            if sym not in resolved:
                try:
                    df = load_stock(sym)
                    if df is None or df.empty:
                        resolved[sym] = None
                    else:
                        df = add_indicators(df)
                        val = df.iloc[-1].get('stdev_20')
                        resolved[sym] = (float(val) if val is not None
                                         and val == val else None)
                except Exception as e:
                    print(f"  ! {sym}: could not compute ({e})")
                    resolved[sym] = None

            val = resolved[sym]
            if val is None or val <= 0:
                print(f"  ! {sym}: stdev_20 unresolvable — left as None. It "
                      f"would still take the fallback and be flagged at fill.")
                continue

            price = q.get('price_at_signal') or 0
            old_stop_pct = FALLBACK * sl_mult * 100
            new_stop_pct = val * sl_mult * 100
            print(f"\n  {path.name}: {sym}")
            print(f"    stdev_20     None  ->  {val:.4f}")
            print(f"    stop would be  {old_stop_pct:.2f}%  ->  "
                  f"{new_stop_pct:.2f}%  below entry")
            if price:
                print(f"    at signal price ${price:.2f}: stop "
                      f"${price * (1 - FALLBACK * sl_mult):.2f}  ->  "
                      f"${price * (1 - val * sl_mult):.2f}")
            if new_stop_pct > old_stop_pct:
                print(f"    note: real volatility is HIGHER than the fallback, "
                      f"so the corrected stop is wider, not tighter")
            q['stdev_20'] = round(val, 4)
            q['stdev_20_backfilled'] = True
            touched += 1

        if touched:
            changed_files.append((path, entries, touched))

    if not changed_files:
        print("\n  nothing to backfill")
        return 0

    total = sum(n for _, _, n in changed_files)
    print(f"\n  {total} queue record(s) across {len(changed_files)} file(s)")
    print(f"  Promotion will now derive a volatility-scaled stop, so the trade")
    print(f"  can legitimately enter the clean sample.")

    if apply:
        for path, entries, _ in changed_files:
            shutil.copy2(path, path.with_suffix('.json.stdev-bak'))
            path.write_text(json.dumps(entries, indent=2))
            print(f"  written {path.name} (backup .json.stdev-bak)")
    else:
        print(f"  DRY RUN — re-run with --apply to write")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())