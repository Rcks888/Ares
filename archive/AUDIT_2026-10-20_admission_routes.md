# Admission routes — code map and retro audit (2026-10-20)

Input to `DESIGN_admission_capture_v1.md` (step 3). Read-only: no code, config
or VPS change. Repository state `b810a21`. All times UTC (VPS clock); the US
regular session opens 13:30 UTC while EDT holds (until 1 Nov).

---

## 1. Code map

### 1.1 Stages

```
STAGE 1 select   R1 scan signal           daily_report.py:119  open_trade(s)
                 R2 queue -> scan         daily_report.py:64   promote_queue(source="scan")
                 R3 queue -> monitor      monitor_trades.py:280 promote_queue(source="monitor", use_live=True)
STAGE 2 pending  open_trade               tracker.py:612-658   -> 'pending' | 'queued' | 'duplicate'
STAGE 3 fill     execute_pending_signals  tracker.py:59-249    called from daily_report.py:54 only
```

Queue entry (feeds R2/R3) has two producers, both through `queue_signal`
(`tracker.py:319`):

- Q1 scan signal with slots full: `open_trade` -> `queue_signal` (`tracker.py:625-627`)
- Q2 pending demoted at fill time because slots are full (`tracker.py:82-85`)

### 1.2 What each step records today

| Step | Evidence written | Missing |
|---|---|---|
| R1 `open_trade(s)` -> pending | **nothing** (pending file only, overwritten) | route, run, decision time, outcome |
| R1 -> `duplicate` | nothing | outcome |
| Q1 / Q2 -> `queued` | `queue_events` `queued` | which producer (Q1 and Q2 are indistinguishable) |
| R2/R3 validation | `kept` / `dropped` / `check_retry` | `source`, `use_live` |
| R2/R3 promotion | `promoted` or `dropped` `open_trade=<result>` | `source`, `use_live`, free slots |
| Fill | trade record (`from_queue`, `fill_source`, `fill_run_ts`); `fill_retry` / `fill_dropped` | link to the admission |

`_log_queue_event` swallows write errors (`tracker.py:287-291`): a lost event
is silent.

### 1.3 Code facts the design must respect

1. **R3 is not close-only.** `promote_queue` runs whenever `updated` is set,
   including a peak advance with no close (`monitor_trades.py:218`). The
   comment at `:278` ("A close freed a slot") describes the motive, not the
   condition. R3 fires on any cycle that saved state while a slot was free.
2. **Queue promotion re-bases the signal.** `promote_queue` passes
   `date = today` and `price = live` (`tracker.py:588-589`). For every
   `from_queue` trade, `signal_date` / `signal_price` are the promotion date and
   validation price, not the scan's. The original scan date survives only as
   `queued_at` in `queue_events`.
3. **`strength` is lost on the queue route.** `queue_signal` does not store it
   and `promote_queue` does not pass it: all 11 `from_queue` trades carry
   `strength: unknown`; all 10 direct trades carry `high`/`medium`.
4. **Unranked fallback.** When `queue_ranked.json` is empty, `promote_queue`
   promotes from the raw queue (`tracker.py:543-544`). `maintain_queue` says an
   unverified entry "can never be promoted". Promotion still re-validates, so
   nothing is admitted unchecked, but ranking is bypassed.
5. **Admission context is not judged.** Contamination is decided at fill only
   (`fill_source`, `tracker.py:168-172`). A manual off-schedule run that
   *admits* a position is not labelled if a scheduled run fills it.

---

## 2. Retro audit — all 21 trades

Method: for each trade, the first commit whose `logs/pending_signals.json`
contains the symbol with the trade's `signal_date` (the run that admitted it),
cross-checked against `queue_events` `promoted` timestamps where they exist.
`queue_events.jsonl` starts 2026-09-17; `pending_signals.json` history starts
2026-09-07 (`866eed2`).

| Trade | `from_queue` | Admitting run (commit) | Route | Fill run | Same-session fill |
|---|---|---|---|---|---|
| HAFN | F | 09-07 13:31 report V2 (`866eed2`) | R1 | 09-08 | no |
| DYN | F | 09-08 13:31 report (`c116e8f`) | R1 | 09-08 | **yes** |
| ABM | F | 09-08 21:01 report (`5f5b59b`) | R1 | 09-09 | no |
| PINS | F | 09-09 21:01 report (`d0bb8b1`) | R1 | 09-10 | no |
| ECO | F | 09-14 21:01 report (`3422781`) | R1 | 09-15 | no |
| SDGR | F | 09-17 21:01 report (`6580e01`) | R1 | 09-18 | no |
| TMO | T | 09-21 13:32 report (`77c3616`) | R2 | 09-21 21:01 | **yes** |
| WBD | T | 09-22 17:30 monitor (`8a4a07b`) | R3 | 09-22 21:00 | **yes** |
| NEOG | T | 09-23 16:10 monitor (`3dfc2ea`) | R3 | 09-23 21:01 | **yes** |
| SECZ | F | 09-24 21:01 report (`d8ba88f`) | R1 | 09-25 13:30 | no |
| AMP | T | 09-29 05:35 report, **manual** (`14a324e`) | R2 | 09-29 13:30 | no |
| TWST | T | 09-29 21:01 report (`5fa3eb2`) | R2 | 09-30 13:30 | no |
| BCE | T | 09-30 16:10 monitor (`cec6808`) | R3 | 09-30 21:00 | **yes** |
| GMAB | T | 10-01 16:10 monitor (`60fba24`) | R3 | 10-01 21:01 | **yes** |
| PCVX | F | 10-05 13:32 report (`fb5a5f0`) | R1 | 10-05 21:00 | **yes** |
| EROC | F | 10-06 21:01 report (`9ad1259`) | R1 | 10-07 13:30 | no |
| ITUB | F | 10-06 21:01 report (`9ad1259`) | R1 | 10-07 13:30 | no |
| MANE | T | 10-12 13:32 report (`138ccf0`) | R2 | 10-12 21:00 | **yes** |
| DHT | T | 10-13 13:32 report (`d654695`) | R2 | 10-13 21:00 | **yes** |
| INSM | T | 10-13 16:10 monitor (`4e0458c`) | R3 | 10-13 21:00 | **yes** |
| SKYD | T | 10-15 13:32 report (`3b89a9a`) | R2 | 10-15 21:01 | **yes** |

Totals: **R1 10, R2 6, R3 5.** For the 11 queue trades, the commit subject and
the `promoted` timestamp agree 11/11.

Non-trade: RVTY was promoted 09-17 13:31 (R2) and lost in the fill path with no
event. This is the known Sep 18 incident; `fill_dropped` now covers it.

---

## 3. Findings

### A1 — every route can be reconstructed today, but only from git

All 21 routes are recovered. **EROC and ITUB were R1, admitted by the
10-06 21:01 report.** That closes `initiating_source: unknown` in
`RECONCILIATION_2026-10-07_fill_cycle.md` §2 item 6.

The method is inference, not evidence. It works only because every cron run
commits `pending_signals.json`, and R1 is identified by the *absence* of queue
events. A run that does not commit (manual, failed push, a pending added and
removed within one run) leaves no trace. The design should therefore record
admissions explicitly, not because the route is unknowable today.

### A2 — 11 of 21 fills are booked at an open that preceded the admission decision

`execute_pending_signals` fills at `df.iloc[-1]['Open']` once the last bar is
today's (`tracker.py:120-130`). A pending created *during* a session is filled
at the 21:00 report at **that same session's open**, which happened before the
decision. Intended behaviour is "execute at next day's open"
(`daily_report.py:121`, `tracker.py:629`).

| Admitted at | Trades | Decision after open by |
|---|---|---|
| 13:3x report | DYN, TMO, PCVX, MANE, DHT, SKYD | ~1–2 min |
| 16:10 monitor | NEOG, BCE, GMAB, INSM | ~2 h 40 min |
| 17:30 monitor | WBD | ~4 h |

For the queue trades, booked entry against the price the decision saw:

| Trade | Decision price | Booked entry | Diff |
|---|---|---|---|
| NEOG (R3) | 13.74 | 14.22 | +3.49% |
| GMAB (R3) | 34.70 | 35.29 | +1.70% |
| MANE (R2) | 128.08 | 130.12 | +1.59% |
| SKYD (R2) | 6.99 | 7.07 | +1.14% |
| BCE (R3) | 20.38 | 20.22 | −0.79% |
| DHT (R2) | 26.03 | 26.17 | +0.54% |
| WBD (R3) | 30.80 | 30.87 | +0.23% |
| INSM (R3) | 98.20 | 98.28 | +0.08% |
| TMO (R2) | 655.46 | 654.54 | −0.14% |

The booked price was not attainable when the decision was made. In 7 of 9
cases here it is *worse* (higher) than the decision price, so it does not
flatter the sample. It is still a fill-model defect that touches **9 of the 14
`clean_v3` trades** (TMO, NEOG, BCE, GMAB, PCVX, MANE, DHT, INSM, SKYD), plus
pre-clean WBD and DYN.

At the 13:30 run the gap is about a minute. After US DST ends (1 Nov), 13:30
UTC is 08:30 ET, pre-market, and the bar-date guard defers those fills to
21:00 instead. The R3 case (16:10 / 17:30) is unaffected by DST.

**Ares is frozen: this is recorded, not fixed.** It belongs to the admission
chain (decision price -> fill price) and the design must capture both so the gap is
measured per trade rather than reconstructed.

### A3 — admission evidence lost or overwritten on the queue route

`signal_date`, `signal_price` and `strength` are overwritten or lost for every
`from_queue` trade (§1.3 items 2–3). The trade record cannot distinguish the
scan signal from the promotion decision.

### A4 — manual admission not labelled

AMP was admitted by an off-schedule 05:35 run and filled by the scheduled 13:30
run. It is `clean_v3`. Whether a manually *admitted* position belongs in the
clean sample is a policy question for the design to raise, not decide.

### A5 — smaller gaps

- Q1 vs Q2 `queued` events are indistinguishable.
- `duplicate` outcomes from `open_trade` are never logged.
- `_log_queue_event` drops write errors silently.
- R3 fires on peak-advance cycles, not only after closes.
- Unranked fallback in `promote_queue` (§1.3 item 4).

---

## 4. Consequences for the design

1. One admission record per `open_trade` call, on every route: route, run kind
   (scheduled/manual), decision time, decision price, `use_live`, free slots,
   outcome (`pending|queued|duplicate`).
2. A linkage key carried from admission -> pending -> trade, so the fill joins
   its admission without timestamp inference.
3. The fill record must carry the bar date and open used, so A2 is measured
   directly: `fill_bar_open_time < admission_time`.
4. Original scan signal (date, price, strength) preserved alongside the
   promotion re-base, not replaced by it.
5. Coverage rule: a trade without an admission record is a **gap**.
6. Registration scope grows to `engine/tracker.py` and `daily_report.py`. Both
   paths share `tracker.py`, so isolation must be verified for both entry
   points.
