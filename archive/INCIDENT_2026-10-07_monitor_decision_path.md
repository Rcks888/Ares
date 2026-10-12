# Incident — the monitor path runs a second, unobserved exit implementation

**Opened** 2026-10-07 · **Status** open · **Class** evidence coverage + architectural
divergence · **Production impact** none caused by this investigation; no runtime
file was modified

This record is written **before** any repair so that the incident provably
predates the fix. It is documentation only. No code, configuration, crontab or
evidence file was changed in the course of the diagnosis.

---

## 1. Summary

Phase 4 parity collection was believed to observe the production exit decision.
It observes **one of two** production exit implementations.

`daily_report.py` evaluates exits through `engine.parity_hook.
observed_check_open_trades()`, which wraps `tracker.check_open_trades()` and is
instrumented. `monitor_trades.py` does **not** call `check_open_trades` at all.
It implements its own exit loop over tracker primitives
(`load_trades`, `save_trades`, `_close_trade`, `promote_queue`), using IBKR
live prices, and writes production state directly.

The two run on different schedules:

| Path | Launcher | Schedule (UTC) | Instrumented |
|------|----------|----------------|--------------|
| report | `run_ares.sh` | 13:30, 21:00 | yes (`ARES_PARITY=1`, line 12) |
| monitor | `run_monitor.sh` | 16:10, 17:30 | **no** |

Consequence: of the six position exits since parity activation
(2026-09-29T05:52:22Z), **one** was observed. The other five occurred on the
monitor path and produced no parity record.

`monitor_trades.py` was last modified in commit `03c3c50` (2026-09-17),
**before** parity activation. This is not a regression introduced during Phase 4;
it is a pre-existing architectural condition that Phase 4 did not detect because
the gates only asked whether the cycles they knew about had run.

---

## 2. Standing qualifications

These attach to every future citation of Phase 4 parity results.

1. **The 64 MATCH records remain valid for the report path only.** They do not
   establish whole-system exit equivalence. Zero non-MATCH and
   `would_change_action: 0` across 64 comparisons is a true statement about
   agreement between `exit_policy` and `tracker.check_open_trades`. It is not a
   statement about production exit behaviour as a whole.

2. **Five monitor exits are unobtainable and remain blocking.** SECZ, BCE, GMAB,
   TWST, PCVX. They must not be recreated, reconstructed into parity records, or
   automatically retired. Retirement by analogy with SDGR is explicitly refused:
   one pre-activation closure and five systematic omissions from an unregistered
   decision path are different coverage findings.

3. **"Sole production authority" is withdrawn.** The claim that
   `engine/tracker.py` is the sole production decision authority is false as
   written. It is the sole authority *on the report path*. Report and monitor
   currently implement **different exit policies**. A corollary: an unchanged
   `engine/tracker.py` md5 no longer demonstrates unchanged production exit
   behaviour, because `monitor_trades.py` is decision code and is absent from
   the integrity manifest.

4. **The differences between the two implementations are not parity defects.**
   They are an architectural divergence. A recorder must therefore capture
   monitor inputs, decisions and outcomes **without** labelling them MATCH or
   MISMATCH. Comparing the monitor against `exit_policy` would generate
   mismatches that reflect a known design difference, degrading the meaning of a
   signal whose trustworthiness is the point of the exercise.

5. **Capturing the monitor's trigger price would clarify trigger-versus-fill
   behaviour, but would not alone prove a gap or an actually executable fill.**
   It bounds the question; it does not close it.

6. **$139 is a proposed equity-relative allocation, not established "correct
   sizing."** Reserve enforcement and stop-risk sizing each require their own
   explicit contract. Neither exists yet.

---

## 3. Discovery sequence

The instruction to verify the call chain before adding an environment export was
decisive. The originally planned Step 1 — `export ARES_PARITY=1` in
`run_monitor.sh` — would have had **no effect**, because that variable gates
`observed_check_open_trades`, a function the monitor never calls. Had it been
deployed, the repair would have appeared complete, the heartbeat would have
stayed healthy, and evidence loss would have continued silently.

```
grep -rn "check_open_trades" --include=*.py .
  daily_report.py:10   from engine.parity_hook import observed_check_open_trades
  daily_report.py:59   observed_check_open_trades()
  engine/tracker.py:812  def check_open_trades():
  (no match in monitor_trades.py)
```

This is the third occurrence of the same failure mode in this workstream: **a
complete answer to an incomplete question reads as green.** The earlier two were
a heartbeat reading a key no producer emitted, and a coverage lookup whose
`all()` over an empty dict returned `True`. See `DECISION_LOG.md`, standing
rules.

---

## 4. Divergence map

Line references are `monitor_trades.py` unless stated. `tracker` refers to
`tracker.check_open_trades()` at `engine/tracker.py:812`.

| # | Behaviour | tracker | monitor | Severity |
|---|-----------|---------|---------|----------|
| 1 | take-profit | 50% scale-out, remainder rides | **full exit** (`:75`) | **critical** |
| 2 | entry-day skip | `if today == entry_date: continue` | **absent** | **critical** |
| 3 | `emotional_extreme` RSI > 90 | implemented | `rsi_extreme` bound at `:30`, **never read** | high |
| 4 | `bearish_divergence` | implemented | absent | high |
| 5 | `mean_reversion_complete` | implemented | absent | high |
| 6 | no live price available | falls back to daily close | prints and `continue` — **no stop evaluation** (`:45-51`) | high |
| 7 | `trailing_stop_pct` default | `0.10` | **`0.08`** (`:29`) | medium, latent |
| 8 | exit date | bar date `str(latest.name)[:10]` | wall clock `datetime.now()` (`:66`) | medium |
| 9 | queue promotion | prints a slot notice only | **`promote_queue(source="monitor", use_live=True)`** (`:89`) | medium |
| 10 | parity telemetry | populates `_LAST_EVAL` packets/results with cycle token | **nothing** | — |

### #1 is active, not latent

Effective parameters confirm `scale_out: True`, `scale_out_pct: 0.5`. So the
same trigger — price reaching take-profit — sells **half** the position on the
report path and **all** of it on the monitor path. Which outcome a trade
receives is determined by which cron slot the price happens to touch TP in. No
field records that this selection occurred.

No TP exit has fired yet, so the divergence is unexercised in the current
sample. It is reachable at any time.

### #2 permits a day-0 stop-out that the report path would skip

TWST was recorded `skipped_entry_day` at 13:31 on 2026-09-30. Had it traded to
167.83 at 16:10 that same day, the monitor would have closed it. Unexercised,
live.

### #7 does not currently bite

`trailing_stop_pct` is present in params at `0.10`, so both paths agree today.
Confirmed arithmetically: TWST peak ≈ 209.0, trail 188.10, ratio 0.8996.
Remove that key and the two paths silently adopt different trail widths — 10%
on the report path, 8% on the monitor path.

### #6 is an availability asymmetry

An IBKR outage during a monitor window means no stop is evaluated at all on
that path, whereas the report path degrades to the daily close.

---

## 5. Defects shared by both implementations

These are **not** divergences. Both paths do the same thing, and in both cases
it is open to challenge.

### 5.1 Exit price is the stop level, not the observed price

```
monitor_trades.py:38    live = get_live_price(symbol)
monitor_trades.py:68    if live <= effective_stop:
monitor_trades.py:70        _close_trade(trade, today, effective_stop, reason)

engine/tracker.py       exit_price = effective_stop
                        _close_trade(trade, today, exit_price, reason)
```

Both discard the observed price and book the exit at the stop level. The monitor
holds `live` at line 68 for the trigger test and discards it at line 70. A flat
0.1% haircut is then applied in `_close_trade:766`.

**The most diagnostic value in the system — the price actually observed at the
moment of the exit decision — is computed and thrown away on every monitor
exit.**

### 5.2 Slippage is a fixed proportion of the stop level

`_close_trade:766` — `exit_price_after_slippage = raw_exit * (1 - slippage_pct)`.
`slippage_pct` is **absent from params** and defaults to `0.001` in code.
Verified against all five monitor exits:

| Symbol | Stop level | Booked exit | `exit_slippage` | = stop × 0.001 |
|--------|-----------|-------------|-----------------|----------------|
| SECZ | 15.13 | 15.11 | 0.0151 | ✓ |
| BCE | 19.86 | 19.84 | 0.0199 | ✓ |
| GMAB | 34.16 | 34.13 | 0.0342 | ✓ |
| TWST | 188.10 | 187.91 | 0.1881 | ✓ |
| PCVX | 65.93 | 65.86 | 0.0659 | ✓ |

The model is `exit = stop_level × 0.999`, independent of the observed price and
of how far the market moved past the stop.

### 5.3 Two decision-relevant parameters are not in configuration

`slippage_pct` and `commission_per_trade` are both `<ABSENT>` from params and
supplied by hardcoded in-code defaults (`0.001` and `1.00`). Both directly
determine booked P&L.

**Design constraint for the code-fingerprint work:** an effective-configuration
fingerprint must cover in-code defaults, not merely the params file. A
fingerprint built from params alone would silently omit both.

### 5.4 `trade['shadow']` is not decision evidence

`_close_trade:799` writes a `shadow` block. It records **post-exit price
tracking** — `peak_after_exit`, `trough_after_exit`, `missed_upside_pct`,
`avoided_downside_pct`. It contains no decision inputs, no pre-decision state
and no evaluation context.

It therefore offers **no partial recovery** of the five lost exits. Its
`trough_after_exit` is initialised to the exit price and only updates on later
observations, so it cannot evidence a same-bar low either.

---

## 6. Preserved state at time of discovery

Recorded 2026-10-07T06:05Z on the live host, before any edit.

### Source versions

| File | md5 | Note |
|------|-----|------|
| `engine/tracker.py` | `3c23d191b4bccea221678502bd82f30a` | unchanged since baseline; behavioural boundary |
| `engine/exit_policy.py` | `d00e621da121dc19320c739bc6b81f84` | matches `exit_policy_md5` in every record |
| `engine/parity_hook.py` | `d33d341e691e8cd2991896e312030e9b` | |
| `daily_report.py` | `80a9f29ee2da5e852395e7eaa3d26404` | |
| **`monitor_trades.py`** | **`1f81383aa6acad939b5c946eeeb76fd7`** | **decision code, not in integrity manifest** |
| **`engine/data_feed.py`** | **`28216583761fdbc7ea9c0c5dae6b4721`** | **monitor's price source, not in manifest** |

`tracker_source_hash` as recorded inside every parity record:
`490baafcf03e91d13b92dabbda0e5369`.

### Evidence

| Measure | Value |
|---------|-------|
| `logs/tracker_parity_v1.jsonl` | 64 records |
| `logs/parity_heartbeat_v1.jsonl` | 12 heartbeats |
| decisions observed | `hold` 52, `state_update` 11, `close:stop_loss` 1 |
| non-MATCH | 0 |
| `would_change_action` | 0 |
| symbols seen | ABM, AMP, BCE, GMAB, PCVX, SECZ, TMO, TWST, WBD |
| heartbeat integrity | `write_failures` 0, `contract_valid` true, `shadow_system_error` null, on all 12 |

Heartbeat continuity is intact: 2 on 2026-09-29 (pre-lineage-fix, correctly
`production_commit: None`, not backfilled), then two per trading day for
2026-09-30, 10-01, 10-02, 10-05, 10-06. No cycle missing; weekend correctly
absent.

### Effective parameters

```
trailing_stop_pct        0.1
scale_out                True
scale_out_pct            0.5
slippage_pct             <ABSENT>   -> in-code default 0.001
commission_per_trade     <ABSENT>   -> in-code default 1.00
rsi_extreme_high         90
max_positions            5
```

### Post-activation exits

Parity epoch begins 2026-09-29T05:52:22Z.

| Symbol | Exit date | Reason | Booked exit | Net after costs | Days | Path | Observed |
|--------|-----------|--------|-------------|-----------------|------|------|----------|
| ABM | 2026-09-29 | stop_loss | 48.62 | −$7.96 | 21 | report 21:00 | **yes** |
| SECZ | 2026-09-30 | trailing_stop | 15.11 | +$2.41 | 6 | monitor | no |
| BCE | 2026-10-01 | stop_loss | 19.84 | −$4.80 | 1 | monitor | no |
| GMAB | 2026-10-02 | stop_loss | 34.13 | −$6.91 | 1 | monitor | no |
| TWST | 2026-10-06 | trailing_stop | 187.91 | −$2.22 | 7 | monitor | no |
| PCVX | 2026-10-06 | stop_loss | 65.86 | −$38.66 | 1 | monitor | no |

**Observed exit coverage: 1 of 6.** ABM was captured because it happened to
close on a 21:00 UTC report cycle. That was circumstance, not design, and it
concealed the gap for nine days.

---

## 7. The five unobtainable exits

Recorded as **unobtainable** and **blocking**, pending explicit disposition
review. Keyed by lifecycle identity (symbol + entry date + exit date), not
symbol alone, since a symbol may re-enter later.

| Obligation | Entry | Exit | Cause of unobtainability |
|------------|-------|------|--------------------------|
| SECZ 2026-09-25 → 2026-09-30 | 2026-09-25 | 2026-09-30 | decided on unregistered monitor path; no capture mechanism existed |
| BCE 2026-09-30 → 2026-10-01 | 2026-09-30 | 2026-10-01 | as above |
| GMAB 2026-10-01 → 2026-10-02 | 2026-10-01 | 2026-10-02 | as above |
| TWST 2026-09-30 → 2026-10-06 | 2026-09-30 | 2026-10-06 | as above |
| PCVX 2026-10-05 → 2026-10-06 | 2026-10-05 | 2026-10-06 | as above |

Projected tally once registered alongside the existing ABM and SDGR
obligations: **7 obligations, 1 satisfied, 6 unobtainable** — of which 1 (SDGR)
is retired and non-blocking, and 5 remain blocking.

`empirically_complete` stays `False`. `administratively_resolved` must become
`False` until the five receive an explicit, reviewed disposition.

Historical reconstruction may assist diagnosis. It cannot recreate
contemporaneous parity observation, and no reconstruction may be written into
`logs/tracker_parity_v1.jsonl`.

---

## 8. Corrections of record

Claims made during this investigation that were wrong or overstated, retained
because the record is more useful showing the route than the destination.

| # | Claim | Correction |
|---|-------|-----------|
| 1 | "Zero slippage, ever — all five filled exactly at the stop." | **Wrong.** 0.1% slippage is applied and persisted as `exit_slippage`. The exact figures were Telegram *display* values; `monitor_trades.py:71` prints `effective_stop`, pre-slippage. |
| 2 | "PCVX gapped; −$57.25 is a mathematical best-case bound." | **Unfounded.** Inferred from PCVX being a clinical-stage biotech — a prior, not an observation. Still unknown and unknowable from persisted data. |
| 3 | "The monitor path is uninstrumented." | **Too mild.** It runs a second, different exit implementation. The first wording implies a visibility gap over known logic. |
| 4 | "`trough_after_exit: 65.93` shows PCVX declined into the stop rather than gapping through it." | **Wrong.** That field is initialised to the exit price and only updates on later observations. It cannot evidence a same-bar low. Reached for a second time in the opposite direction before being caught. |
| 5 | "`production_commit` is useless lineage." | **Overstated.** A log commit does identify a repository tree containing the executing code. It is valid lineage, merely unsuitable for grouping unchanged code epochs. Retained. |
| 6 | "Risk-normalised sizing would have bounded the PCVX loss." | **Overstated.** Normalisation equalises *planned* stop exposure. It cannot bound realised loss, because execution may differ from the stop price. |
| 7 | "One parity bridge" treated as a prohibition on observing the monitor. | **Reframed by the operator.** It was a boundary for the known report-path architecture, not a reason to decline observing a newly discovered decision path. Option C is appropriate because no unified reference policy has been agreed — not because a second observation point is forbidden. |

Two trading reads also required softening:

- **TWST's trail was correct ex-post.** It surrendered 11.1% of peak, but
  `avoided_downside_pct: 11.14` — the stock fell to 166.97 after exit. The
  giveback was real; the alternative was worse.
- **GMAB is the actual whipsaw.** Stopped at 34.16 with peak −0.01%, then
  rallied to 38.48. `missed_upside_pct: 12.75`, `avoided_downside_pct: −0.09`.

**PCVX stands unmodified.** A stop placed 24.5% from entry on a $150
fixed-notional position. Entirely a stop-width and sizing finding; no
execution-realism inference is required to explain it.

---

## 9. What this does to the migration

`engine/exit_policy.py` was built to replace `tracker.check_open_trades()`.
Block A's "Policy A" is defined by that function. The 293/293 replay and the 64
MATCH records validate agreement with **that** implementation.

But live production exit behaviour is the **union of two implementations with
different rule sets**, and five of six post-activation exits came from the
smaller one — two exit reasons instead of five, no entry-day skip, full
liquidation at take-profit.

So the question *"is Policy A a worthwhile modelling target?"* is now preceded
by a prior question, which becomes the governing question for the workstream:

> **Which production exit policy are we trying to preserve or replace?**

Until that is answered, a label defined as "net return produced by the frozen
Ares exit policy" has no single referent. Any dataset built on the report-path
definition would be labelled by a policy that did not make the majority of the
live exits.

### Readiness claim affected

`phase4_operational_ready: true` was reported throughout this period and is
**too broad**. The required decomposition:

- *collector healthy on registered paths* — was and is true
- *all production decision paths registered and observed* — **false**

Both are required for Phase 4 readiness. The gate currently measures only the
first.

Phase 5 and AI modelling remain **prohibited**, unchanged by this incident.

---

## 10. Approved repair sequence

```
Document incident + update logbook                    <- this document
        |
Preserve source versions, evidence hashes,
exit records                                          <- section 6
        |
Design monitor recorder
  - no comparator, no MATCH labelling
  - no decision authority
        |
Register every production decision path
and its coverage
        |
Review, test and deploy observation-only changes
        |
Separately preregister policy unification,
sizing and fill realism
```

### Stage 1 — observation only

1. Instrument the monitor as a **recorder**: capture inputs (`live`,
   `get_live_price` outcome), pre-decision state, the decision taken, the
   trigger price, and the booked exit price. No comparison, no MATCH/MISMATCH
   label.
2. Register every production decision path with: launcher, Python entry point,
   decision call site, parity activation placement, expected schedule, evidence
   and heartbeat coverage. Record the monitor's **rule subset as a declared
   property**, so its narrower policy is a registered fact rather than a
   discovery.
3. Add `monitor_trades.py` and `engine/data_feed.py` to the integrity manifest.
4. Expand the schedule contract from `[(13,30), (21,0)]` to
   `[(13,30), (16,10), (17,30), (21,0)]`. Without this, instrumenting the
   monitor creates records on cycles the freshness gate cannot see — closing one
   blind spot by opening another.
5. Split `phase4_operational_ready` into the two measures in section 9.

Controls: the monitor's inline evaluation must run exactly once; the recorder
receives the monitor's captured inputs and must not re-acquire market data; no
production mutation; operator output and exception behaviour unchanged; one
controlled invocation in a dead window, verified before relying on scheduled
runs. **No trading cycle may be rerun to manufacture missing evidence.**

### Not in Stage 1

The ten divergences are **not** repaired here. Several are trading decisions
rather than engineering ones — #1 changes how much of a winner is sold. These
go to separate preregistration:

- **Policy unification** — which exit policy is authoritative, and whether the
  monitor adopts the full rule set or the report path adopts the monitor's
  intraday responsiveness.
- **Stop-risk sizing** — fixed notional with an uncapped ATR stop produced a
  13.7× spread in dollar risk per trade ($2.66 BCE to $36.33 PCVX).
- **Reserve enforcement** — currently nominal; sizing reads `starting_capital`
  rather than equity.
- **Fill realism** — whether booking exits at the stop level is acceptable, and
  what a defensible slippage model is.

---

## 11. Open questions

1. Which production exit policy is authoritative? (governing question)
2. Did PCVX gap through its stop? Unresolved; unanswerable from persisted data.
   A recorder would bound it going forward but would not establish an executable
   fill.
3. What disposition do the five unobtainable exits receive? They remain blocking
   until reviewed.
4. Does the monitor's `promote_queue(use_live=True)` price entries on a
   different basis than the report path? Not yet examined.
5. Was `monitor_trades.py` ever changed during the parity epoch? Commit
   `03c3c50` dated 2026-09-17 says no — but this was verifiable only because the
   file happens to be tracked, not because any gate was watching it.
