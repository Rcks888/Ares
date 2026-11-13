# Ares: current status

Last updated: 2026-10-30. This page is the entry point. History is in
`Ares_Logbook.md`, full reference in `README.md`, finished investigations in
`archive/`. Everything removed in the cleanup is kept at tag
`checkpoint-2026-10-12`.

## State: FROZEN

The bot runs as is: paper trading only, V3.1, on the VPS (`/root/ares/Ares`).
No code changes without an explicit decision. Phase 5, policy unification and AI
modelling remain prohibited.

## Plan and timeline

The main flow (agreed Oct 9), and where things stand:

```
✔ 0. Recorder repairs accepted   (v7 accepted 2026-10-12, branch 28ae5f0)
✔ 1. Conditional recorder activation   (5fd13e9 on the VPS, 2026-10-13; rollback pre-tested)
✔ 2. First monitor-cycle inspection  → CHECKPOINT   (2026-10-15: both nights, 4 cycles, PASS)
▶ 3. Admission-source capture (separately reviewed, both routes)   ← HERE: design r5 accepted 2026-10-21; implementation M1 accepted 2026-10-29, M2 accepted 2026-11-03 (recorder/admission-v1 5d274de, not merged, not active); M3 accepted 2026-11-12 (a00b185; design r6 accepted 2026-11-11); next: M4 inspection tool (week of 16 Nov)
◻ 4. Evidence-informed decision on which exit policy is authoritative
```

By week (Mon–Fri). Update the "Actual" column at the end of each week, and say
why if it differs from the plan.

| Week | Planned | Actual | Change vs plan |
|---|---|---|---|
| Aug 27 – Sep 4 | V1, then V2/V2.1 | as planned | — |
| Sep 7 – Sep 25 | V3, data cleaning, parity build | as planned | — |
| Sep 28 – Oct 2 | parity (Phase 4) live; vacation from Oct 1 | parity started around Sep 29 | — |
| Oct 5 – Oct 9 | vacation; resume | incident (Oct 7), reconciliation, recorder designed, built and reviewed | unplanned: incident work |
| Oct 12 – Oct 16 | recorder activation, Mon 16:10 UTC | HOLD from review → repair round 2 → pause → checkpoint, audit, cleanup, WBD closed → v3 update → inspection-tool rounds v4–v7 → **accepted Mon** → **activated Tue 13 Oct** (`5fd13e9`) → **inspection Thu 15 Oct: PASS, both nights** → checkpoint | one day late against the original Mon date, a week early against the replan |
| Oct 19 – Oct 23 | step 0 review → steps 1–2 → checkpoint | (steps 0–2 done the week before) route audit, step 3 design r1–r5, **r5 accepted for implementation review Wed 21 Oct**. Monitor inspections Mon–Thu: 8 cycles, all PASS. Unplanned, Thu 22 Oct: eMudhra CA response, environment only (venv certifi 2026.10.19, emSign roots removed from the gateway JRE, DigitalOcean firewall inbound 22 only), all three verified 23 Oct. SKYD closed at TP (clean sample 10, W 2/10). Finding: AMP cache regression (known issue 8) | pulled forward; step 3 design done a week early; security work unplanned, no code change |
| Oct 26 – Oct 30 | **1st: Finviz** (if still broken on 23 Oct night): identify the cause, design and review a minimal fix with the reviewer. **2nd, if time:** step 3 implementation on `recorder/admission-v1` (not activated) | **Both done.** Finviz fixed Tue 27 Oct (D-004, `2e45851`, no reviewer round, by user decision). On the VPS since the 27 Oct 13:32 report: 103 screened. Signals resumed: RRC and SGRY (27 Oct) filled 28 Oct, so 5/5 slots; ICLR, KNSA and BRZE queued (28 Oct). Step 3 split into milestones M1–M4. **M1** built Tue–Thu, committed Thu 29 Oct (`00aee55`, `d25bec3`). Implementation review v1, Thu 29 Oct: 2 blocking findings, fixed the same day (`a6dd2f1`). **v2 accepted for M1** (not merge, deploy or activation). Monitor inspections Mon–Thu nights: 8 cycles, all PASS (Fri's on Mon 2 Nov). Thu 29 Oct: INSM scaled out on a gap and closed at TP (clean sample 12, W 3/12); BRZE promoted by the monitor and filled. DST: D-005. Finding: the 13:30 scan reads a one-minute bar (known issue 11) | the reviewer round dropped for Finviz; step 3 implementation started a week early and is planned in four milestones, one a week |
| Nov 2 – Nov 6 | **Step 3 M2**: capture controls, one per outcome (all 9 fill outcomes, both queue branches, every promotion early return and candidate outcome); completion and partial outcomes; cross-recorder pairing; monitor entrypoint ordering; recorder unimportable or unwritable; data-feed call counts; end-to-end run of the real `daily_report.py` entrypoint. Also D4 (A2 three-valued, inconsistency as a `contract_violation`), the G2 naming ruling, and `repro_pack.py --only N`. **Done when** implementation review v3 accepts M2. Checkpoint also runs the D-005 check | **Tue 3 Nov: review candidate built.** `2470a50` (D4 + controls) and `00303ec` (G2 §4.1 amendment) pushed on `recorder/admission-v1` only. Full-repo run at `00303ec`, 0 uncommitted paths: all exit 0 (A2 11, report entrypoint 16, capture 44 new, monitor admission entrypoint 34 new, report end-to-end new; isolation pairs 142/20/55 unchanged). G2: only `schema_version` → `record_schema_version` is naming-only; C1 (`missing_fields` not emitted), C2 (`run_kind` null on failure), C3 (§4.5 completion flags) open, reviewer's suggestion requested. O1 recorded (BaseException in the monitor flush skips the admission block). Bundle `review_bundle_admission_impl_v3` sent. **Review v3 (Tue 3 Nov): delta acceptable, no new blocker; M2 not yet accepted.** D4 closed; schema rename accepted; C2 accepted as explicit unknown evidence (not a valid scheduled/manual classification); C3 accepted if completion is read from the flag matching `decision_path`; O1 accepted as a recorded limitation, not coverage. **C1 blocks G2:** reviewer probe showed a returned-pending admission with no timestamps, position counts or `max_positions` gets `record_complete: true`, so it means "no implemented violation fired", not "capture complete". Reviewer reproduced hashes, pairs 142/20/55, smoke and end-to-end (stubbed indicators); 4 longer suites (25/16/34/44) timed out there and remain supplied evidence. **C1 fix `5d274de`** (branch only, pushed): `REQUIRED_FIELDS` per kind and state, `missing_fields` emitted, `record_complete` = returned + nothing missing + no violation; two rules for ruling (a raised call is never complete; unconditional saves on a returned path must be `completed`). Capture 55/55 (c50–c53, k01–k04, 11 mutants), e2e 22 checks; full run at `5d274de` 31/31 exit 0. Bundle `review_bundle_admission_impl_v4` sent. **Review v4 (Tue 3 Nov): C1 accepted, G2 closed** (R1: a raised call is never complete; R2: unconditional saves must be `completed`, call completion not disk persistence; C2, C3, O1 wording accepted). Reviewer reproduced hashes, capture 55/55 and end-to-end 22/22 (stubbed indicators). **M2 milestone ruling (Tue 3 Nov): ACCEPTED** for `2470a50` + `00303ec` + `5d274de`; D4 and G2 closed, O1 kept as a limitation. Reviewer reproduced `--only 4` 25/25, `--only 6` 16/16, `--only 7` 34/34; real-indicator full run is supplied evidence. Milestone only: no merge, deploy or activation. **Target done.** **Checkpoint Fri 6 Nov:** monitor inspections Mon–Fri nights, 10 cycles, all PASS (Fri's inspected Tue 10 Nov). D-005 check done: 21:00 price and RSI final, `vol_ratio` short by 2–22% (known issue 11); cron kept. Wed 4 Nov: RRC stopped out by the monitor (−$14.34, known issue 9), TBBB promoted and filled at the session's open (known issue 7); clean sample 13, W 3/13, −$94.47. Finding: winter 13:30 report uses extended-hours IBKR prices (known issue 13) | M3 not pulled forward |
| Nov 9 – Nov 13 | **Step 3 M3**: verifier extensions (statement whitelist, call-site inventory, writer inventory and save data flow); persistence and save-chain controls, C-HANDOFF, C-ENUM. Done when the review accepts M3 | Mon 9 Nov: owner's holiday, no work. Tue 10 Nov: Fri and Mon monitor cycles inspected, 4 PASS. **Wed 11 Nov:** `39f6c81` (call-site inventory), `f13be1e` (statement whitelist), `4d750d5` (writer inventory). **Round 1: not approved**, 2 false passes (indirect `tracker.__dict__` caller; read before binding, W5 rejected); W1–W4 ruled. Fixes `1b4efb5` (call inventory 35/35, whitelist 51/51, design r6). **Round 2: approved for scope**: both findings closed, r6 accepted (`f6e0776`), ext. 3 and X1–X7 accepted; not M3 acceptance. Persistence, save-chain, C-HANDOFF and C-ENUM controls `a00b185` (61/61; full run 37/37), round 3 next with Y1–Y8; concurrent_runs, baseline_ambiguous and cross-run trade links deferred to M4. Wed 11 Nov night: 2 monitor cycles PASS; CVI and XERS dropped on drift, NWE queued (4/10). **Round 3 (Thu 12 Nov): M3 APPROVED at `a00b185`**, Y1–Y8 accepted (Y2 §7 wording corrected; Y7 order rule kept; Y8 deferrals to M4). No blocking finding. Milestone only: no merge, deploy or activation. **Target done.** Thu 12 Nov night: 2 cycles PASS; TBBB peak moved on a pre-open IBKR print (known issue 13); MANE, MBGL queued (6/10); stall 1064 ms. **Checkpoint Fri 13 Nov:** target done (M3 accepted at `a00b185`). Monitor inspections Fri 6 – Thu 12 Nov nights, 10 cycles, all PASS (Fri 13's inspected next week); parity all MATCH. No fills or closes since 4 Nov: clean sample 13, W 3/13, −$94.47; 5/5 slots. Queue: CVI and XERS dropped on drift (11 Nov). Known issue 13 moved two peaks (TMO 10 Nov, TBBB 12 Nov). Next: M4, week of 16 Nov Fri 6 Nov: first queue overflow (DT, CVI evicted); CVI's at-close vol 40% short (known issue 11). Mon 9 Nov: SGRY +13.7%, trail above entry | moves a week if an M2 round runs over |
| Nov 16 – Nov 20 | **Step 3 M4**: `tools/inspect_admission_cycles.py` (state-diff reconciliation, episodes, per-file check, trade links, A2), mutation test, final bundle. Done when the review accepts M4 | | as above |
| after M4 | activation decision (design §8, owner's go-ahead), then evidence collection → step 4 | | no date until there is enough evidence to judge |

## What runs (cron, weekdays only)

| UTC | MYT | Job | What it does |
|---|---|---|---|
| 13:00, 16:00, 17:25 | 21:00, 00:00, 01:25 | `restart_gateway.sh` | starts IB Gateway **only if no gateway process exists** |
| 13:30, 21:00 | 21:30, 05:00 | `run_ares.sh` → `daily_report.py`, `build_dashboard.py` | scan, fill pending, check exits, dashboard to Telegram, commit `logs/` |
| 16:10, 17:30 | 00:10, 01:30 | `run_monitor.sh` → `monitor_trades.py` | live-price exit check, Telegram, commit `logs/` |

**Winter time (D-005).** From Mon 2 Nov the NYSE session is 14:30–21:00 UTC, and
the UTC (and MYT) times above are kept. The 13:30 report runs before the open,
so fills move to the 21:00 report, and its scan sees the previous day's final
bar instead of a one-minute one (known issue 11). The 21:00 report runs at the
close. Checked 6 Nov: its scan sees final price and RSI, but `vol_ratio`
short by 2–22% (known issue 11). The 13:30 report's IBKR prices are
extended-hours trades (known issue 13).

Production state is in `logs/` (`virtual_trades.json` is authoritative), and is
backed up to GitHub by the bot's own commits.

## Code

- **Trading:** `daily_report.py`, `monitor_trades.py`, `build_dashboard.py`,
  `engine/{data_feed,indicators,signals,screener,sample,tracker}.py`, the four
  launchers, and `config/`.
- **Parity check (Phase 4), kept and frozen:**
  `engine/{parity_*,tracker_compat,exit_policy}.py`.
  - Report path only. Observes and never writes trading state; output goes to
    `logs/tracker_parity_v1.jsonl`.
  - Off-switch: comment out `export ARES_PARITY=1` in `run_ares.sh`.
- **Monitor-path recorder (active since `5fd13e9`):** `engine/monitor_observer.py`
  plus 46 registered observation lines in `monitor_trades.py`.
  - Observes only, and writes `logs/monitor_{observation,heartbeat,health}_v1.jsonl`.
  - Each cycle is checked with `tools/inspect_monitor_cycles.py`.
  - Rollback and procedure: `ACTIVATION_monitor_recorder_v1.md` §3.5.
- **Admission-path recorder (step 3, branch `recorder/admission-v1`, not merged,
  not active):** `engine/admission_observer.py` plus registered capture lines in
  `engine/tracker.py`, `daily_report.py` and `monitor_trades.py`.
  - Isolation: `tools/verify_recorder_isolation.py --registry …` on three
    fixture → code pairs, and `tests/run_admission_isolation_smoke.py`.
  - Merging to `main` would start writing `logs/admission_*_v1.jsonl`, so a
    merge needs the activation decision.
- **Tests:** `tests/`, `tools/`. Only needed if trading or parity code changes.
  Run `for t in tests/test_*.py; do python3 "$t" || echo "FAIL $t"; done`; all
  must exit 0 (they did on 2026-10-12).

## How deployment actually works

Merging to `main` does **not** reliably reach the VPS. Each launcher runs Python
**first** and git **afterwards**, and it pulls only if its push fails. To deploy,
outside the cron times above:

```sh
cd /root/ares/Ares && git pull --ff-only && git log -1 --oneline
```

## Known issues with live consequence (unrepaired)

1. **The bot cannot detect a delisting.** WBD was cash-acquired by SKYD and
   delisted on 2026-10-06. The bot kept holding it with no price for a week,
   until it was closed by hand on 2026-10-12 (commit `22142a4`; see the
   logbook). A future acquisition will look the same: "No live price", a
   1-row cache file and a parity failure, while the position blocks a slot.
2. **The monitor's decisions were not recorded.** The monitor-path recorder
   (activated `5fd13e9`, 2026-10-13) records them from the 16:10 UTC cycle on
   Oct 13. **Confirmed 2026-10-15**: 4 cycles inspected, PASS, including MANE's close. Known
   producer limitation: it can write non-finite values marked complete; the
   inspection (`tools/inspect_monitor_cycles.py`) rejects them.
3. **The gateway restart only acts when no gateway process exists at all.** A
   running but wedged gateway is never restarted.
4. **All Ares processes use IB `clientId=10`, and only `connect` has a
   timeout.** A stalled monitor can block the report (Oct 1).
   - **Recurred Fri 23 Oct.** The 16:10 UTC monitor hung for 80 min, still
     holding `clientId=10`.
   - The gateway rejected the 17:30 run's first connect ("clientId 10
     already in use?"), so TMO got no live price.
   - No decision was affected, and the dashboard gave no warning.
   - Where the run hung is unknown. See the logbook, 23 → 27 Oct.
5. **SSH password login is enabled on the VPS**, and outsiders are trying
   passwords. Since 2026-10-22, a DigitalOcean cloud firewall allows inbound
   TCP 22 only (port 4002 verified dropped from outside). SSH itself is
   unchanged.
6. **The Finviz screener returns nothing (since Mon 19 Oct 13:32 UTC).**
   - Finviz moved and redesigned its screener page: `/screener/stocks`, no
     HTML tables.
   - `finvizfinance` 1.5.0, the newest version, silently returns `None`.
   - Every report screens only SPY/QQQ/IWM ("3 screened"), so no new signals
     come in. The fixed-watchlist fallback never fires, and the dashboard
     gives no warning.
   - Decision 2026-10-20: **wait, no change** (FROZEN). Revisit at the weekly
     checkpoint. Details are in the logbook.
   - Checkpoint 2026-10-23: still broken after 5 weekdays (10 reports, 0
     signals). Since SKYD closed, 1 slot is idle and the queue is empty. Step
     3 needs admissions to observe.
   - **Decision:** hold, and check whether the 23 Oct 21:32 MYT dashboard
     still says 3 screened. If it does, Finviz becomes next week's first
     target: identify the cause, then a reviewed fix in a joint session with
     the reviewer.
   - 23 and 26 Oct: still 3 screened (6 weekdays, 12 reports). **This is
     week 26–30 Oct's first target.**
   - **27 Oct: fixed (D-004).** `engine/screener.py` now reads the new
     page's JSON block, using the same screens and filter codes as before.
     Tests 26/26; a live laptop run gave 329 unique candidates.
   - The user decided to skip the reviewer round, because this restores
     behaviour rather than adding it.
   - Deploy: VPS `git pull`. Then confirm at the first report that it
     screens far more than 3 and that `cron.log` has no `Screen [...] error`
     line.
   - **Verified: resolved.** Every report since 27 Oct 13:32 UTC has screened
     103. Signals resumed on 27 Oct 21:01 (RRC, SGRY) and 28 Oct 21:01
     (ICLR, KNSA, BRZE). Still open: a total screener failure shows only as
     "3 screened".
7. **Intra-session admissions fill at that session's open, which came before
   the decision** (found 2026-10-20, corrected r2, r3 and r4). 11 fills (9
   `clean_v3`) are bounded: each holds if its admitting run started at its
   cron slot. None can be established from history: even a matching cron
   launch does not show which process made the commit. They are booked at a price that wasn't available when the
   pending was created (−0.79% to +3.49% against the decision price).
   - Recorded, not fixed (FROZEN). Audit:
     `archive/AUDIT_2026-10-20_admission_routes.md`.
   - Step 3 will record the decision time and price, plus the fill bar, so
     the gap is measured on every trade.
   - From 2 Nov (D-005), the 13:30 report runs before the open, so its
     admissions are no longer exposed. Monitor promotions still are.
   - **New instance, 29 Oct: BRZE.** The 16:10 UTC monitor promoted it
     (live $45.75); the 21:00 report filled it at the 29 Oct open, $45.80
     (+0.11%). The monitor's own record times the decision, so this one is
     established, not bounded. Now 12 fills (10 `clean_v3`).
   - **New instance, 4 Nov: TBBB.** The 17:30 UTC monitor promoted it (live
     $64.51) in the cycle that closed RRC; the 21:01 report filled it at the
     4 Nov open, $63.80 (open $63.74 + slippage; −1.10%). Established. Now 13
     fills (11 `clean_v3`).
8. **The OHLCV cache accepts a history that goes backwards** (found
   2026-10-23).
   - `download_stock` (`engine/data_feed.py:72-80`) saves whatever yfinance
     returns. On 22 Oct 13:30 UTC, Yahoo returned AMP without its 21 Oct
     row, which replaced a cache that had it.
   - Both 22 Oct reports used the 20 Oct bar for AMP's daily exit check. AMP
     was far from its SL and TP, so there was probably no effect.
   - Recorded, not fixed (FROZEN). Self-check answered: yes, the 23 Oct
     re-download restored 21 and 22 Oct.
   - A milder variant on 26 Oct: Yahoo had no 26 Oct row for AMP at 13:30.
     The 21:00 report does not refresh a 7.5 h-old cache, so both 26 Oct
     reports used the 23 Oct bar.
9. **Exits are booked at the stop or TP level, not at the observed price.**
   - ITUB on 26 Oct: IBKR showed $7.28 after a gap of about −22% overnight
     (Yahoo $7.50, 8.4 M shares). The stop was $9.32, and the exit was
     booked at $9.31.
   - Booked −8.0% (−$13.92). At the observed price it would be about −26%
     to −28% (−$41 to −$44).
   - So the clean sample's −$96.38 is about $27–30 too good: roughly −$123
     to −$126.
   - The same defect books TP exits below a higher live price (SKYD, 23 Oct).
   - **INSM, 29 Oct.** The 13:30 report scaled out half at $113.89 after a
     gap. The monitor has no scale-out branch, so at 16:10 it closed the
     other half because live $113.26 ≥ TP $108.11, booked at $108.00. The
     half meant to ride was sold 2 h 40 min later at the level. At the live
     price it would book about $3.91 more.
   - Net with ITUB and INSM: the clean sample's −$80.13 (12 closed) is about
     −$103 to −$106 at observed prices.
   - **RRC, 4 Nov.** The 17:30 monitor saw $51.44, below the $51.95 stop, and
     booked $51.90 (−$14.34 after costs). At the observed price about $1.34
     more. Clean sample −$94.47 (13 closed) is about −$119 to −$122 at
     observed prices.
   - Recorded, not fixed (FROZEN). It bears on step 4, which exit policy is
     authoritative. The monitor recorder captures `observed_live` against
     `booked_exit_price`. The report path's parity record has the IBKR price.
10. **Queue validation checks price drift only** (found 2026-10-29, in the step
    3 work; the implementation review v2 bundle calls it K-9).
    - `_validate_queued` (`engine/tracker.py:410`, `:413` on `main`) reads `RSI` and
      `EMA_20`. `add_indicators` produces `rsi` and no EMA column.
    - So RSI is always 50 and EMA20 is always 0. The overbought check and the
      below-EMA20 check can never fire.
    - Confirmed in production: all 92 validation events in
      `logs/queue_events.jsonl` (72 kept, 12 promoted, 8 dropped) have
      `rsi: 50.0` and `ema20_ok: true`.
    - Present before M1, and unchanged by it. Recorded, not fixed (FROZEN).
      This matters for step 4, and for any reading of queue outcomes.
11. **The 13:30 report scans today's bar about one minute after the open**
    (found 2026-10-30).
    - Evidence: `vol_ratio` on 13:30 signals is BCE 0.01× (28 Sep), INSM
      0.02× (12 Oct), SKHY 0.26× (15 Oct), FFIV 0.05× and FTNT 0.4× (29 Oct).
      PCVX 1.57× (5 Oct) is the one exception. Every 21:00 signal is 0.61× to
      7.93×. Yahoo serves today's row within seconds of the open (SGRY's,
      38 s after it, on 28 Oct).
    - So price, RSI and `vol_ratio` come from a one-minute bar, and the
      volume trigger cannot count towards confluence.
    - 29 Oct: FTNT was queued at $129.80 (conf 2), then dropped at 21:00 for
      +7.2% drift, and re-queued at $139.10 (conf 3). FFIV keeps its conf 2
      entry although the 21:00 scan gave conf 3, because a queued symbol
      keeps its first entry.
    - Inferred from the ratios, not from a stored bar: the 21:00 run
      replaced the 29 Oct rows in the cache.
    - Stops from 2 Nov (D-005): the 13:30 report then runs before the open.
      It comes back at the March DST change. Recorded, not fixed (FROZEN).
    - **Confirmed stopped, 2 Nov 13:32.** The scan reproduced Friday 21:00's
      final-bar FFIV exactly (conf 3, $338.34, vol 2.75×). Queue live prices
      equalled Friday 21:01's.
    - **Also affects exits (found 3 Nov).** Held symbols outside the screened
      100 are re-downloaded only by the 13:30 report (20 h cache window,
      `tracker.py:840`), and the 21:00 report reuses that file. The RSI and
      divergence exits therefore judge the 13:30 row: in summer, normally a
      one-minute bar; in winter, the previous session's final bar (bar a
      session old at 21:00). Stops and TP use the live price. Evidence:
      parity bar dates, 30 Oct 13:31 and 2 Nov 21:01. Logbook, 30 Oct → 3 Nov.
      It also makes the entry-day skip (`tracker.py:865`, bar date ==
      `entry_date`) skip a day-1 trade whose file is on the 21:00 cycle (TBBB,
      5 Nov 13:31).
    - **Winter form at 21:00 (D-005 check, 6 Nov).** The 21:00 scan runs at
      16:00 ET. Against the final bars, its price is within $0.02 and RSI
      within 0.1 on all 8 winter signals, but `vol_ratio` is short on all 8
      by 2–22% (median about 12%). The summer 17:00 EDT baseline was within
      +3% on 9 of 9. A short ratio can only remove a signal: a breakout
      (`vol_ratio ≥ 1.5` gate, `signals.py:107`) with a final ratio up to
      about 1.8× can be rejected; in mean reversion it costs a confluence
      vote. Logbook, 3 → 6 Nov.
    - **CVI, 6 Nov 21:01:** vol 1.21× at the close against 1.70× final (+40%,
      the widest so far; price +$0.05), so conf 2 instead of 3. It was
      evicted from the full queue, but would have been at conf 3 too. It was
      re-queued at conf 3 by the 9 Nov pre-open scan. Logbook, 6 → 10 Nov.
12. **The trailing stop starts only at the first new high** (found 2026-11-03).
    - At fill, `trailing_stop = stop_loss` and `peak_price = entry`
      (`tracker.py:192-193`). The 10% trail is recomputed only when the price
      exceeds the peak (`tracker.py:876`, `monitor_trades.py:207`).
    - So a wide volatility stop protects the trade until the first tick
      above entry, and peak × 0.90 protects it from then on.
    - BRZE, 2 Nov 16:10: a $0.014 new high moved the stop $35.08 → $41.23
      (−23.4% → −10.0% of entry). SGRY, 28 Oct: $18.27 → $21.02.
    - Two trades with the same path below entry, one tick apart, end up with
      stops about 13 points apart. The 23 Sep dead-band table holds only when
      SL is above entry × 0.90.
    - Code behaviour, not a regression. Recorded, not fixed (FROZEN). It bears
      on step 4, which exit policy is authoritative.
13. **In winter the 13:30 report judges held positions at extended-hours IBKR
    prices** (found 2026-11-06).
    - `get_live_price` takes the last 1-minute `TRADES` bar with
      `useRTH=False` and drops its timestamp (`data_feed.py:56-59`). At 08:30
      ET no regular-session bar of that day exists.
    - Evidence, against the final daily close: the 13:31 prices on 4 and 5 Nov
      differ by −1.24% to +1.93% (TMO, AMP, SGRY, BRZE, TBBB); the 21:00
      prices are within −0.23% to +0.49%. SGRY's $23.40 (4 Nov) is above
      every regular-session high from 2 to 5 Nov.
    - Peaks and trails advance on these prices (AMP 4 and 5 Nov, SGRY 4 Nov:
      its trail $21.06 rests on that print). A stop or TP can fire on one;
      none has yet.
    - Summer had the same at 21:00 (17:00 EDT, logbook 12 Oct finding F).
      Recorded, not fixed (FROZEN). It bears on step 4.

## Open questions (not being worked on)

- the five monitor exits that cannot be recovered
- which exit policy is authoritative
- `MIN_CLOSED_SAMPLE`
- why `get_live_price` returns `None` (four possible causes)
- the monitor's missing per-trade exception guard
- the monitor's exit-date basis
- the pending-path drift guard
- PCVX fill realism (permanently unanswerable)
- the Hermes record says "halted", but Hermes is live
