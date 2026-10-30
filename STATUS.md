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
▶ 3. Admission-source capture (separately reviewed, both routes)   ← HERE: design r5 accepted 2026-10-21; implementation M1 accepted 2026-10-29 (recorder/admission-v1 a6dd2f1, not merged, not active); next: M2
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
| Nov 2 – Nov 6 | **Step 3 M2**: capture controls, one per outcome (all 9 fill outcomes, both queue branches, every promotion early return and candidate outcome); completion and partial outcomes; cross-recorder pairing; monitor entrypoint ordering; recorder unimportable or unwritable; data-feed call counts; end-to-end run of the real `daily_report.py` entrypoint. Also D4 (A2 three-valued, inconsistency as a `contract_violation`), the G2 naming ruling, and `repro_pack.py --only N`. **Done when** implementation review v3 accepts M2. Checkpoint also runs the D-005 check | | |
| Nov 9 – Nov 13 | **Step 3 M3**: verifier extensions (statement whitelist, call-site inventory, writer inventory and save data flow); persistence and save-chain controls, C-HANDOFF, C-ENUM. Done when the review accepts M3 | | moves a week if an M2 round runs over |
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
close, so its scan may see a bar that is not final yet; this is checked on 6 Nov.

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
