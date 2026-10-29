# Decision log

Durable record of settled decisions and of factual corrections that were
established after an incorrect claim had been made.

**Why this file exists.** Several substantial decisions — the entry-ranking model
sequence, the rejection of agent and reinforcement-learning architectures, the
objections that produced the entry-only constraint — were reached in discussion and
never written down. They were later partially recovered from memory, and in the
interim incorrect statements were made about what had been decided and what
evidence existed. A decision that lives only in a conversation is not a decision
the project can rely on.

**Format.** `D-nnn` settled decisions, `C-nnn` corrections of record. Each entry
cites the authoritative file and line where the underlying evidence lives.

---

## D-001 — `n >= 100` is a strategy-validity threshold only

**Settled 2026-09-30.**

`n >= 100` closed clean trades remains the **preregistered** threshold for a
**strategy-validity claim**. It was registered `ROADMAP.md`, section *"PRE-REGISTERED
— how the `clean_v3` live sample may be read"*, written **2026-09-24, before the
first `clean_v3` trade closed**. Derived from Run A′'s 145 trades: mean per-trade
−0.27%, SD 10.82%, giving a 95% CI half-width of ±3.16% at n=45 against the +3.0%
per trade needed to match SPY.

**It does not automatically become the Phase 5 cutover threshold.**
`minimum_closed_sample` remains `UNEVALUATED` by design. The two answer different
questions — strategy performance versus migration confidence — and conflating them
would let a threshold registered for one purpose silently satisfy a gate written for
another.

If `n >= 100` is ever adopted as the cutover threshold, it must be **registered as
such deliberately**, with its own statement of population, cleanliness exclusions,
runtime epoch, and whether exits require parity coverage. It may not be inherited.

Binding sub-rules, carried from the preregistration:

- Report the confidence interval whenever the mean is reported. A mean without its
  interval is not a result.
- A positive mean whose CI includes zero is **not** evidence of edge and must not be
  recorded as one.

---

## D-002 — the historical harnesses exist and are maintained

**Settled 2026-09-30.** Supersedes any statement that they are missing, absent, or
in need of reconstruction.

| Harness | Location | Establishes |
|---|---|---|
| `run_tracker_equivalence.py` | Athena | 20 boundary fixtures, 250 replayable historical Run A′ paths, 4,730 bars, zero decision-changing mismatches, 8,616 stored-value differences all reproduced by re-deriving tracker's rounding discipline |
| `run_exit_replay.py` | Athena | Block A stage 1; Policy A reproduced 293/293, before and after the scale-out fix |

Both are referenced from `TRACKER_MIGRATION_PLAN.md:116` and `:591`, and
`ROADMAP.md:2090`. They are **cross-repo**: the harnesses live in Athena while the
policy under test lives in Ares. A search confined to the Ares repository will not
find them, and their absence from an Ares-only search is not evidence of absence.

**Disposition: maintain, do not reconstruct.**

---

## D-003 — pretrained and agent architectures

**Settled 2026-09-30.**

**Deferred to optional feature extraction only** — FinGPT, FinBERT, Chronos,
PatchTST, TimesFM. They may not control entries or exits unless checkpoint identity,
pretraining cutoff, corpus provenance and incremental value over the preregistered
feature set are independently established. A checkpoint whose training data extends
beyond the historical decision date creates **potential** temporal leakage unless
cutoff and provenance are known; the objection is conditional, not categorical.

**Rejected for the current architecture** — TradingAgents and reinforcement-learning
agents. Reasons: simulator sensitivity, unstable reward definitions, poor
auditability, irreproducible decision paths.

**Model sequence, in order:** deterministic ranking control → single-factor baseline
→ six-feature linear model → LightGBM or CatBoost challenger. The tree must clearly
beat the linear model under **every** preregistered condition, is tested **once**,
and a failed condition is neither relaxed nor rerun on the same data.

**Standing constraints:** the model ranks **new entries only**; a declining rank
never sells a holding; exit authority stays with the deterministic policy; Ares must
retain the ability to hold cash.

---

## D-004 — Finviz screener restored by a minimal parser fix, without a reviewer round

**Settled 2026-10-27 (user decision). An exception to FROZEN, limited to `engine/screener.py`.**

**Problem.** From Mon 19 Oct 13:32 UTC, every report screened only SPY/QQQ/IWM:
6 weekdays, 12 reports, 0 signals (STATUS known issue 6; logbook 2026-10-20 and
23 → 27 Oct).
- Finviz moved its screener to `/screener/stocks`. Results are now an embedded
  JSON block (`data-ssr-props="ssr-island-1"`, `data.rows[].text.ticker`,
  `data.totalCount`, 20 rows per page). The page has no `<table>`.
- `finvizfinance` 1.5.0 is still the newest on PyPI (checked 27 Oct). It finds no
  table and returns `None`. `_run_screen` printed that as "0 stocks", with no
  error.

**Change** (branch `fix/finviz-screener`, `engine/screener.py` only):
- `_run_screen` reads the JSON block with `requests`, with a 20 s timeout.
- The screens are unchanged: same five screens, same filter dictionaries.
  - URL codes are copied from finvizfinance 1.5.0 `constants.filter_dict`.
  - Same `v=111`, `o=ticker`, `r` paging and 1 s between pages, as in 1.5.0
    `screener_view`.
  - Pages are fetched until `totalCount`.
- An unreadable page now prints `Screen [...] error:` and returns `[]`; before,
  it showed "0 stocks". Cases covered: block missing, bad JSON, invalid
  `totalCount`, a row without a ticker, an HTTP error.
- An unmapped filter pair raises, because Finviz silently ignores unknown codes
  (verified: a bogus code returned the unfiltered 1673).
- `scan_market`, `get_screened_symbols`, `MAX_CANDIDATES` and `daily_report.py`
  are unchanged.

**Evidence.**
- `tests/test_screener.py`: 26/26. 14 deliberate faults injected; all caught.
- Live from the laptop, 27 Oct 02:15 UTC: 67 / 91 / 3 / 146 / 94 per screen,
  329 unique, capped to 100.
  - The same counts as direct URL checks of the codes. Each technical filter
    narrowed the base 1673 and its rows satisfied the filter (e.g. `ta_change_u3`
    page 1 change +3.0…+8.0%).
  - Before the break: 102 and 455 unique, 16 Oct.
- The other tests are unchanged and pass. `test_monitor_observer.py` needs
  `pytest`, which is not installed on the laptop.

**Why no reviewer round.** This restores behaviour, not new behaviour: the same
screens and universe definition, parsed from the new page format. It touches no
trading, exit, tracker or recorder logic.
- This replaces the 23 Oct checkpoint wording "reviewed fix in a joint session
  with the reviewer".

**Effects to expect.**
- New candidates and signals resume, so the clean sample can grow again. Its
  definition is unchanged.
- The `engine/screener.py` hash changes in the recorder's `decision_manifest`.
- Still open (not part of this fix): if every screen fails, the dashboard still
  shows only "3 screened". The fixed-watchlist fallback still never fires.

**Verification after deploy.** At the first report, check:
- the dashboard shows far more than 3 screened;
- `cron.log` has five `Screen [...]: N stocks` lines and no `error` line.

**Rollback.** `git revert` the fix commit on the laptop, then `git pull --ff-only` on
the VPS. The worst case is back to "3 screened".

---

## D-005 — winter time: the cron stays in UTC; the behaviour change is recorded

**Settled 2026-10-29 (user decision). No code or config change.**

**Problem.** US clocks go back on Sun 1 Nov. From Mon 2 Nov the NYSE regular
session is 14:30–21:00 UTC, not 13:30–20:00. Every cron time is UTC (STATUS,
*What runs*), so each job moves one hour earlier against the market. MYT has no
DST, so the MYT times do not change.

| Job (UTC, unchanged) | Summer (EDT) | From 2 Nov (EST) |
|---|---|---|
| 13:30 report | 09:30 ET, at the open | **08:30 ET, 1 h before the open** |
| 16:10 / 17:30 monitor | 12:10 / 13:30 ET | 11:10 / 12:30 ET |
| 21:00 report | 17:00 ET, 1 h after the close | **16:00 ET, at the close** |

**Effects, read from the code:**
- **Fills move to the 21:00 report.**
  - A fill pulls fresh data (`load_stock(..., max_age_hours=0)`,
    `engine/tracker.py:105` on `main`). At 08:30 ET there is no bar for the day yet, so
    every pending gets `fill_retry` ("last bar … != today",
    `engine/tracker.py:122`) and is kept.
  - The 21:00 report then fills it at that session's open.
  - Precedent: RRC on 28 Oct, retried at 13:30:38 UTC and filled at 21:01 at
    $56.59.
  - The extra 7.5 h is far inside `pending_max_age_days: 4`.
- **The 13:30 report's admissions come before the open.** Its scan sees only
  the previous session's complete bars. So its fills are not exposed to known
  issue 7. Monitor promotions (11:10 / 12:30 ET) still are.
- **The 21:00 scan runs about at the close.** The screened symbols are
  refreshed at about 16:00–16:01 ET. Yahoo's bar for the day may not be final
  yet: the close may be before the closing auction, and volume may be short.
  That changes the signal inputs (close, RSI on ohlc4, volume ratio). It is
  **not verified**; see the check below.
- **Daily exit checks at 21:00** use the IBKR live price when there is one, as
  now. A held symbol that is not screened uses the 13:30 cache (20 h window):
  - that cache is now always the previous session's bar;
  - in summer it held that day's bar, partial, or nothing (AMP, 26 Oct).
- **Unaffected:**
  - `_fill_window_ok` assumes EDT. Its gap estimate is now 1 h short, but no
    cron slot is near the 48 h limit.
  - The parity schedule contract (`PARITY_CYCLE_SCHEDULE_UTC`,
    `vps_phase3_verify.sh` §4b), the gateway restarts, and the PSI windows
    (7.5 h / 16.5 h / 64.5 h).
- **Watch, not verified: the IBKR daily kill** (README, 15:45 UTC in summer).
  - If it is set in ET, it moves to 16:45 UTC. That is after the 16:00 restart
    and the 16:10 monitor, and before the 17:25 restart, which still recovers
    the gateway for the 17:30 monitor.
  - On 2 Nov, check the gateway log for the time of the kill.

**Why not shift every job +1 h now.** That keeps the market-relative timing,
but it is more than a crontab edit:
- `PARITY_CYCLE_SCHEDULE_UTC` (`tools/pre_parity_snapshot.py:351`) must change;
- so must its tests (`tests/test_parity_freshness.py`) and the schedule
  contract in `vps_phase3_verify.sh`.

That is a code change under FROZEN, in three days.

**Check at the 6 Nov checkpoint.** For each signal of the 21:00 reports of
2–5 Nov:
- compare the recorded `price` with that date's `Close` in the VPS
  `data/ohlcv/<SYM>.csv`, re-downloaded by later runs. The recorded price is in
  `logs/pending_signals.json` (`price`) or `logs/signal_queue.json`
  (`price_at_signal`) at the report's commit;
- compare the recorded `vol_ratio` with that date's `Volume` divided by the
  20-day mean including that day (as `engine/indicators.py:21-22`),
  recomputed from that file;
- as a summer baseline, do the same for the 21:00 signals of 27 and 28 Oct
  (RRC, SGRY, ICLR, KNSA, BRZE), taken at 17:00 ET.

If the closes differ beyond rounding, or the volume is short, a reviewed
schedule change is designed then; it is not rushed in. The same question returns
in reverse when US DST starts on 14 Mar 2027.

---

## C-001 — heartbeat lineage is deployed but not yet verified

**Recorded 2026-09-30T02:43Z.** An earlier summary listed this as completed. It is
not.

The fix (heartbeat lineage reading `production_commit` rather than a key no producer
emits) shipped in `50a42a7`, **after** the last cycle ran. The two most recent
heartbeats are pre-fix and correctly carry `production_commit: null`:

```
2026-09-29T13:31:35Z   production_commit: null
2026-09-29T21:00:55Z   production_commit: null
```

**These must never be backfilled.** The item is complete only when a cycle running
the fixed code emits a non-null commit with `lineage_complete: true`.

Marking a fix verified on the strength of absent evidence is the same failure mode as
the Phase 5 false green, and is rejected for the same reason.

---

## C-002 — the 43 are untested entries, not excluded paths

**Recorded 2026-09-30.**

Correct statement: historical decision coverage is **250 of 293**;
excluded-path classification is **43 of 43**, all classified
`UNTESTED_INSUFFICIENT_WARMUP` — **not passed, not failed.**

Two distinct causes:

1. **Snapshot-start truncation (majority).** A harness limitation. Athena's frozen
   snapshot begins 2021-09-22, so early entries lack sufficient pre-entry bars
   within the snapshot. Run A′ admitted them because `prepare()` computes indicators
   once on the full frame and slices, leaving early rows `NaN` rather than raising.
2. **Genuine post-IPO short history.** `IOT@2022-01-20` (24 pre-entry bars) and
   `BIRK@2024-01-18` (67).

Describing these as "43 excluded historical paths" overstates the exclusion and
understates the coverage. See `TRACKER_MIGRATION_PLAN.md:252`.

---

## C-003 — the live-price path is not proven by Phase 0

**Recorded 2026-09-30.**

Phase 0 established equivalence on the **daily-price path only**. Its harness forced
`get_live_price` to `None`, while production takes the live branch during scheduled
cycles. See the scope correction at `TRACKER_MIGRATION_PLAN.md:106`.

The live-price path is being **evaluated by Phase 4 observation** — that is why
Phase 0.5 exact-input capture exists. Any wording implying the live path was
separately tested and proven is incorrect.

---

## Standing rules to prevent recurrence

1. **Decisions are recorded here on the day they are settled**, not at the next
   documentation pass. A decision recoverable only from conversation does not exist.
2. **"Does not exist" requires a search of both repositories.** Ares and Athena are
   separate checkouts; evidence, harnesses and vendored copies cross between them.
3. **Cite `file:line`** when asserting what a document establishes. Paraphrase from
   memory is how the retracted `PF 2.41 over 1060 trades` figure propagated.
4. **A threshold registered for one claim does not transfer to another gate.**
   Transfer requires its own registration.
5. **Never mark a fix verified while its evidence is absent.** Absent evidence is
   `UNEVALUATED`, never a pass.
6. **Record rejected alternatives and the reasons.** Without them, a future reader
   sees only the surviving plan and re-litigates settled questions — or worse,
   reinstates a rejected one.
