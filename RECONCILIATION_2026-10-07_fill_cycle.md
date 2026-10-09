# Post-cycle reconciliation — 2026-10-07 fill cycle

Read-only observation. Captured 2026-10-09 against the §8.3 fixed list in
`DESIGN_monitor_recorder_v1.md`, reconciled under the §8.3.1 rules, bounded by
§8.4.

**No runtime change was made. The recorder remains unimplemented.** Nothing
below authorises a repair.

---

## 1. Verdict on the prediction

| | Predicted | **Booked** | Error |
|---|---|---|---|
| Cash | $171.35 | **$171.46** | **+$0.11** |
| Reserve breach | $78.65 | **$78.54** | −$0.11 |

```
outlay 745.1608   comm 5.00   realised −78.38
cash = 1000 − 78.38 − 745.1608 − 5.00 = 171.46
breach = 250 − 171.46 = 78.54
```

**The booked value stands. The prediction was wrong by $0.11 and the method is
not adjusted to recover it** (rule 2).

### Cause of the error, exactly

`shares` is **floored** to 2dp, not rounded.

| | `149.00 / entry` | Rounded | **Booked** | Outlay | Shortfall |
|---|---|---|---|---|---|
| EROC | 11.8160 | 11.82 | **11.81** | 148.9241 | −0.0759 |
| ITUB | 14.7233 | 14.72 | **14.72** | 148.9664 | −0.0336 |
| | | | | **297.8905** | **−0.1095** |

EROC is the discriminator: 11.8160 rounds to 11.82 but was booked 11.81. The
two shortfalls sum to $0.1095, which is the entire prediction error. The
prediction assumed each fill consumed exactly `position_size`.

Legacy basis confirmed: `745.1608 − 297.8905 = 447.2703`.

### `position_size` is not the outlay

Both records carry `position_size: 149.0` while consuming 148.92 and 148.97. It
is the **pre-floor notional**. Any cash computation reading it reproduces this
error. Rule 2 exists for this reason and is now empirically justified.

---

## 2. §8.3 items, closed

| # | Item | Result |
|---|---|---|
| 1 | Pending before / after | Both admitted; pending file cleared |
| 2 | Per-symbol status | **Both FILLED** on the 13:30 UTC cycle. No retention, no date-guard deferral, no drop |
| 3 | Fill price, quantity, commission | EROC 11.81 @ 12.61, $1.00 · ITUB 14.72 @ 10.12, $1.00 |
| 4 | Bar date used by the fill guard | Did not defer — the session bar existed at 13:30. Precedent (AMP, TWST) held |
| 5 | Resulting cash and reserve | $171.46 vs $250 nominal → **breach $78.54** |
| 6 | Executing path and source evidence | Fill by the **report** path (Stage 3 is report-only). `initiating_source: unknown` for both |

Supporting observations, all as predicted:

- `stdev_fallback: False` both — stops derived from real `stdev_20`. Verified
  arithmetically: EROC `2 × 5.85% → 11.13 ≈ 11.14`; ITUB `2 × 3.95% → 9.32` ✓
- `sample_phase: clean_v3` both — `ARES_SCHEDULED=1` read as authoritative
- Targets `× 1.18` momentum both ✓
- Parity records **64 → 84**, exactly `4 cycles × 5 positions`. All four report
  cycles wrote complete sets; EROC and ITUB received entry-day records

---

## 3. The incident, demonstrated end to end

The monitor wrote authoritative production state, unobserved, and the report path
then consumed it:

```
Oct 7 13:30 UTC  report fills EROC    → TS 11.14, peak = entry
Oct 7 16:10 UTC  MONITOR  live 12.96  → TS 11.66    (no evidence written)
Oct 7 17:30 UTC  MONITOR  live 13.07  → TS 11.76    (no evidence written)
Oct 7 21:00 UTC  report dashboard     → TS 11.76, peak 3.6%   ← monitor's value
```

`12.96 × 0.90 = 11.664` and `13.07 × 0.90 = 11.763`. This is the strongest
available demonstration of the coverage gap: an unobserved path mutating state
that the observed path depends on. The only artefact is Telegram text.

### The masked divergence is confirmed masked

The monitor ratcheted at **10%**, not 8%. So `trailing_stop_pct` resolved from
config at both sites and neither code default was read. The §7.3 record stands
exactly as written:

```
resolved value 0.10 · source config · defaults_agree false
masked_divergence true · currently_active false
```

Empirical confirmation that the divergence exists and is not firing. **Still not
to be fixed inside the recorder** (§7.3).

### Entry-day skip divergence, now observed

EROC was Day 0. The report path skips entry-day evaluation; the monitor has no
such skip. The monitor ratcheted a trailing stop on a position the report path
would not have touched. No longer theoretical.

### `get_live_price` opacity, observed four times

WBD returned **"No live price"** on all four monitor checks across Oct 7–8 →
`continue` → **no stop evaluation, no trace**. WBD went unevaluated by the
monitor on every cycle for two days. Cause remains indistinguishable between
dead gateway, timeout, unqualified contract and genuinely no bars. The report
path did evaluate it at 13:30 and 21:00, so the position was not unmanaged — but
the monitor's coverage gap is invisible in every durable record.

---

## 4. `peak_price` has two precisions

```
EROC  peak_price 13.07                  ← monitor-written, round(live, 2)
ITUB  peak_price 10.120109656333922     ← initialisation value, never touched
```

ITUB never made a new high, so the field still holds its init value: the
**unrounded** entry price, at full float precision. EROC's was overwritten by the
monitor at 2dp.

Two consequences:

1. **`peak_price` is not consistently typed.** Its precision depends on whether
   the monitor ever ratcheted it. Any evidence schema must declare which, and a
   comparison across the two forms would show a spurious difference.
2. **It leaks the discarded unrounded entry.** `entry_price` stores `10.12`;
   `peak_price` reveals the true value `10.1201096...`, hence an open of `$10.11`.
   `shares` is computed from the unrounded entry while `entry_price` is stored
   rounded — two derivations of the same quantity at different precisions.

---

## 5. New finding — no drift guard on the pending path

EROC filled at `12.61`, i.e. an open of `12.599`, against a signal price of
`13.64`. **A momentum breakout filled 7.6% below the breakout level.** The signal
premise was already broken at fill.

The queue has `queue_max_drift_pct` for precisely this condition, and
`_validate_queued` enforces it at promotion. The pending path appears to enforce
only `pending_max_gap_hours`, `pending_max_age_days` and the bar-date guard.

**Flagged to verify from source, not asserted.** Observation only — no repair,
and this does not authorise adding a guard.

---

## 6. Correction to the recorder design — §3.5 admission evidence

`grep -E 'EROC|ITUB' logs/queue_events.jsonl` returned **nothing**. Both are
`from_queue: False`; they never entered the queue, so no queue event exists.

**`logs/queue_events.jsonl` therefore cannot be the sole admission-evidence
stream.** §3.5 proposed reusing it "where sufficient". For these two admissions
it is not insufficient — it is **empty**. Direct scan admissions produce no
record at all, and the stream covers only queue-routed admissions.

This is a gap in the design, found by observation, and must be resolved before
implementation review. It does not change the recorder's exit-observation scope.

---

## 7. §8.4 limits, respected

The capture did **not** establish:

- **Which path initiated** EROC and ITUB. `from_queue: False` is consistent with
  scan initiation on the current call graph but asserts nothing, and
  `promote_queue` is reachable from both paths. Recorded `unknown`, permanently.
- **Whether the fill prices were executable.** `open × 1.001` is a model. EROC's
  7.6% adverse gap makes the question sharper, not answerable.
- **Whether the reserve breach is harmful.** It establishes that the computed
  reserve does not track cash. Whether that matters is a separate risk decision.

---

## 8. Still open, unchanged

Governing question (which exit policy is authoritative) · disposition of the five
lost monitor exits (**blocking**) · `MIN_CLOSED_SAMPLE` unregistered ·
`get_live_price` opacity · missing per-trade exception guard in the monitor ·
monitor exit-date basis · PCVX fill realism (permanently unanswerable).

Phase 5, policy unification and AI modelling remain **prohibited**.

**Next checkpoint: recorder implementation review.**
