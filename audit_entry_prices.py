#!/usr/bin/env python3
"""Quantify how badly the stale-fill bug distorted recorded entry prices.

Before the fix, execute_pending_signals() ran ahead of refresh_watchlist() and
load_stock()'s 20h staleness window exceeded the 7.5h/16.5h gaps between scans,
so a fill could read the bar cached by the previous scan instead of today's.

The distortion is not uniform. A fill on the 21:00 UTC scan read a cache
written at 13:30 UTC the same day, so its open was correct. A fill on the 13:30
UTC scan read a cache written at 21:00 UTC the day before, so its open was one
session stale.

This compares each recorded entry_price against the true open on entry_date and
reports the error, so the sample can be triaged on measurement rather than
assumption. Read-only: it never modifies trade data.
"""
import json
from pathlib import Path

TRADES = Path(__file__).parent / "logs" / "virtual_trades.json"
SLIPPAGE = 0.001
TOL_PCT = 0.15          # recorded vs expected within this is a rounding match


def main():
    if not TRADES.exists():
        print(f"no trades file at {TRADES}")
        return 1

    import yfinance as yf
    trades = json.loads(TRADES.read_text())
    rows, clean, stale, unknown = [], 0, 0, 0

    for t in trades:
        sym = t.get('symbol')
        entry_date = str(t.get('entry_date', '')).replace(' (next-bar)', '')
        recorded = t.get('entry_price')
        if not (sym and entry_date and recorded):
            continue

        try:
            df = yf.download(sym, start=entry_date, period=None,
                             progress=False, auto_adjust=False)
            if df is None or df.empty:
                unknown += 1
                rows.append((sym, entry_date, recorded, None, None, "no data"))
                continue
            if hasattr(df.columns, 'get_level_values'):
                df.columns = df.columns.get_level_values(0)
            df = df[df['Open'].notna()]
            true_open = float(df.iloc[0]['Open'])
        except Exception as e:
            unknown += 1
            rows.append((sym, entry_date, recorded, None, None, f"error: {e}"))
            continue

        expected = true_open * (1 + SLIPPAGE)
        err_pct = (recorded - expected) / expected * 100
        if abs(err_pct) <= TOL_PCT:
            verdict = "clean"
            clean += 1
        else:
            verdict = "STALE"
            stale += 1
        rows.append((sym, entry_date, recorded, true_open, err_pct, verdict))

    print(f"\n  {'Symbol':<7} {'Entry date':<12} {'Recorded':>9} "
          f"{'True open':>10} {'Error':>8}  Verdict")
    print("  " + "-" * 62)
    for sym, d, rec, true_o, err, verdict in rows:
        if true_o is None:
            print(f"  {sym:<7} {d:<12} {rec:>9.2f} {'—':>10} {'—':>8}  {verdict}")
        else:
            print(f"  {sym:<7} {d:<12} {rec:>9.2f} {true_o:>10.2f} "
                  f"{err:>+7.2f}%  {verdict}")

    total = clean + stale
    print(f"\n  clean: {clean}   stale: {stale}   unresolved: {unknown}")
    if total:
        print(f"  {stale / total * 100:.0f}% of resolvable entries were filled "
              f"on a stale bar")
    print("\n  Note: a 'clean' verdict means the recorded price matches the true")
    print("  open for that date. It does not validate the exit path or the stop")
    print("  distance, which the queue stdev_20 bug affected separately.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())