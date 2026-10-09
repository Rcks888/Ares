"""Negative controls for the monitor-path recorder.

Each test states the failure it exists to catch. A control that cannot fail is
not a control, so every one of these was checked against a deliberately broken
variant before being kept.

Scope: observation isolation, path-scoped coverage, evidence completeness, and
the masked default divergence. These tests do NOT assert anything about whether
a monitor decision is correct — that is the governing question, still open.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import monitor_observer as mo


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect BOTH outputs through one seam.

    Giving the heartbeat an independent default is how parity fixtures once kept
    appending to the real production log while appearing isolated.
    """
    monkeypatch.setattr(mo, "LOGS_DIR", tmp_path)
    return tmp_path


def _store(**kw):
    base = {
        "invocation_token": 1,
        "cycle_id": "test0001",
        "cycle_started_at": "2026-10-09T00:00:00+00:00",
        "params_raw": {"trailing_stop_pct": 0.10, "rsi_extreme_high": 90},
        "params_resolved": None,
        "observations": [],
        "attempted": 0,
        "open_positions_seen": 0,
        "early_return_reason": None,
        "loop_exception": None,
        "monitor_completed": True,
        "flushed": False,
    }
    base.update(kw)
    base["params_resolved"] = mo.effective_config(base["params_raw"])
    return base


def _lines(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# --- observation isolation -------------------------------------------------

def test_flush_never_raises_on_unserialisable_observation(isolated):
    """CATCHES: a recorder exception escaping into the monitor.

    An object that cannot be JSON-encoded must degrade the record, not the cycle.
    """
    class Hostile:
        def __repr__(self):
            raise RuntimeError("boom")

    s = _store(observations=[{"symbol": "X", "observed_live": Hostile()}],
               attempted=1)
    summary = mo.flush(s)
    assert summary["flush_error"] is None or isinstance(summary["flush_error"], str)
    assert summary["write_failures"] >= 0


def test_flush_never_raises_on_unwritable_directory(isolated, monkeypatch):
    """CATCHES: a disk or permission failure becoming a trading failure."""
    monkeypatch.setattr(mo, "LOGS_DIR", Path("/proc/nonexistent/forbidden"))
    s = _store(observations=[{"symbol": "X", "observed_live": 10.0}], attempted=1)
    summary = mo.flush(s)
    assert summary["written"] == 0
    assert summary["write_failures"] == 1
    assert summary["heartbeat_written"] is False


def test_flush_is_idempotent(isolated):
    """CATCHES: the finally block duplicating every record written by the
    normal end-of-monitor call."""
    s = _store(observations=[{"symbol": "X", "observed_live": 10.0}], attempted=1)
    first = mo.flush(s)
    second = mo.flush(s)
    assert first["written"] == 1
    assert second["skipped_already_flushed"] is True
    assert len(_lines(mo.observation_path())) == 1


# --- coverage and freshness are PATH-SCOPED --------------------------------

def test_heartbeat_is_path_scoped(isolated):
    """CATCHES: a report-path heartbeat satisfying a monitor freshness check.

    Four wrong diagnoses during parity activation came from one combined notion
    of 'latest heartbeat'.
    """
    mo.flush(_store())
    beat = _lines(mo.heartbeat_path())[-1]
    assert beat["decision_path"] == "monitor"
    assert mo.heartbeat_path().name != "parity_heartbeat_v1.jsonl"
    assert mo.observation_path().name != "tracker_parity_v1.jsonl"


def test_empty_cycle_is_not_silence(isolated):
    """CATCHES: zero records reading as 'no positions' when the recorder in fact
    never ran."""
    mo.flush(_store(open_positions_seen=0,
                    early_return_reason="no_open_positions"))
    beat = _lines(mo.heartbeat_path())[-1]
    assert beat["open_positions_seen"] == 0
    assert beat["early_return_reason"] == "no_open_positions"
    assert beat["attempted"] == 0


def test_aborted_loop_is_visible(isolated):
    """CATCHES: the monitor's missing per-trade try/except hiding unevaluated
    positions. Two of three open trades captured, loop raised, nothing saved."""
    s = _store(observations=[{"symbol": "A", "observed_live": 1.0,
                              "decision": "hold"},
                             {"symbol": "B", "observed_live": 2.0}],
               attempted=3, open_positions_seen=3, monitor_completed=False,
               loop_exception="AttributeError: 'NoneType' object has no attribute")
    summary = mo.flush(s)
    beat = _lines(mo.heartbeat_path())[-1]
    assert beat["monitor_completed"] is False
    assert beat["loop_exception"].startswith("AttributeError")
    assert beat["attempted"] == 3
    assert summary["written"] == 2          # attempted != written, visibly


def test_partial_record_is_incomplete_not_absent(isolated):
    """CATCHES: a trade that failed mid-evaluation vanishing from evidence."""
    mo.flush(_store(observations=[{"symbol": "B", "observed_live": 2.0}],
                    attempted=1))
    rec = _lines(mo.observation_path())[-1]
    assert rec["record_complete"] is False
    assert "effective_stop_unrounded" in rec["missing_fields"]
    assert rec["decision"] == "incomplete"


# --- no comparator ---------------------------------------------------------

def test_skip_is_complete_evidence_not_missing_evidence(isolated):
    """CATCHES: a legitimately skipped symbol reporting as incomplete, making
    every WBD cycle look like a loop that died mid-trade.

    Completeness is judged against what the outcome can produce. A skip has no
    stop evaluation, so those fields have no producer for this record.
    """
    mo.flush(_store(observations=[{
        "symbol": "WBD", "entry_date": "2026-09-22", "entry_price": 30.87,
        "stop_loss": 29.31, "trailing_stop_before": 29.31,
        "peak_price_before": 30.87, "take_profit": 36.42,
        "shares_at_decision": 4.8, "ratcheted": False,
        "holding_basis": "entry_date_string", "observed_live": None,
        "skip_reason": "live_price_unavailable_cause_unknown",
        "decision": "skipped"}], attempted=1))
    rec = _lines(mo.observation_path())[-1]
    assert rec["decision"] == "skipped"
    assert rec["record_complete"] is True
    assert rec["missing_fields"] == []
    assert rec["effective_stop_unrounded"] is None


def test_derive_is_not_applied_twice(isolated):
    """REGRESSION. CATCHES: an envelope pass that fills absent keys with None
    before completeness is judged, so a partial record reports complete.

    This defect was present in the first implementation and was caught by
    test_partial_record_is_incomplete_not_absent.
    """
    s = _store(observations=[{"symbol": "B", "observed_live": 2.0}], attempted=1)
    mo.flush(s)
    rec = _lines(mo.observation_path())[-1]
    assert rec["record_complete"] is False
    assert len(rec["missing_fields"]) > 5
    # Every registered key is still PRESENT in the serialised record, so no
    # consumer has to handle a key that simply is not there.
    assert set(rec) == set(mo.REGISTERED_OBSERVATION_FIELDS)


def test_schema_contains_no_comparison_fields(isolated):
    """CATCHES: a MATCH label implying an agreed reference policy exists."""
    mo.flush(_store(observations=[{"symbol": "X", "observed_live": 10.0,
                                   "decision": "hold"}], attempted=1))
    rec = _lines(mo.observation_path())[-1]
    forbidden = ("difference_class", "would_change_action", "match",
                 "shadow_status", "shadow_after", "inline_status")
    for key in rec:
        assert not any(f in key.lower() for f in forbidden), key


def test_live_and_booked_fill_are_separate_fields(isolated):
    """CATCHES: collapsing the observed price into the booked price.

    The monitor books at the stop and discards `live`. PCVX's real gap is
    permanently unanswerable precisely because that value was never kept.
    """
    mo.flush(_store(observations=[{
        "symbol": "P", "observed_live": 60.10, "stop_loss": 65.86,
        "effective_stop_unrounded": 65.86, "booked_fill_price": 65.86,
        "decision": "closed", "exit_reason": "stop_loss"}], attempted=1))
    rec = _lines(mo.observation_path())[-1]
    assert rec["observed_live"] == 60.10
    assert rec["booked_fill_price"] == 65.86


# --- evidence identity -----------------------------------------------------

def test_manifest_fails_on_missing_path(monkeypatch):
    """CATCHES: a manifest reporting a clean fingerprint for an incomplete set.

    config/params.json never existed and hashed nothing while appearing covered.
    """
    monkeypatch.setattr(mo, "DECISION_MANIFEST",
                        ("monitor_trades.py", "config/params.json"))
    man = mo.manifests()["decision"]
    assert man["complete"] is False
    assert any("config/params.json" in m for m in man["missing"])
    assert man["digests"]["config/params.json"] is None


def test_real_manifests_are_complete():
    """CATCHES: a manifest entry renamed or deleted without review."""
    for name, man in mo.manifests().items():
        assert man["complete"], (name, man["missing"])


def test_effective_stop_records_both_precisions(isolated):
    """CATCHES: losing the unrounded value the decision actually used.

    A ratchet to 11.6640 persists 11.66, so pre == post and no function of
    stored state recovers it.
    """
    mo.flush(_store(observations=[{
        "symbol": "E", "observed_live": 12.96,
        "trailing_stop_after_unrounded": 11.664,
        "effective_stop_unrounded": 11.664, "decision": "state_update"}],
        attempted=1))
    rec = _lines(mo.observation_path())[-1]
    assert rec["effective_stop_unrounded"] == 11.664
    assert rec["effective_stop_persisted_as"] == 11.66
    assert rec["trailing_stop_persisted"] == 11.66


def test_peak_price_precision_basis_is_declared(isolated):
    """CATCHES: comparing a 2dp peak against an unrounded one as a difference.

    ITUB held 10.120109656333922 on 2026-10-07 because the monitor never
    ratcheted it; EROC held 13.07 because it did.
    """
    mo.flush(_store(observations=[
        {"symbol": "ITUB", "observed_live": 9.92,
         "peak_price_before": 10.120109656333922, "decision": "hold"},
        {"symbol": "EROC", "observed_live": 13.07,
         "peak_price_before": 13.07, "decision": "state_update"}], attempted=2))
    recs = {r["symbol"]: r for r in _lines(mo.observation_path())}
    assert recs["ITUB"]["peak_price_precision_basis"] == "unrounded_initialisation_value"
    assert recs["EROC"]["peak_price_precision_basis"] == "two_dp_or_equal"


def test_exit_date_basis_is_named(isolated):
    """CATCHES: silently comparing the monitor's local civil date against the
    report path's bar date."""
    mo.flush(_store(observations=[{"symbol": "X", "observed_live": 1.0,
                                   "decision": "hold"}], attempted=1))
    rec = _lines(mo.observation_path())[-1]
    assert rec["exit_date_basis"] == "process_local_naive_civil_date"


def test_unavailable_live_price_names_cause_as_unknown(isolated):
    """CATCHES: attributing a None price to a specific cause.

    data_feed.get_live_price returns None for a dead gateway, a timeout, an
    unqualified contract and genuinely no bars alike.
    """
    mo.flush(_store(observations=[{"symbol": "WBD", "observed_live": None}],
                    attempted=1))
    rec = _lines(mo.observation_path())[-1]
    assert rec["live_available"] is False
    assert rec["skip_reason"] == "live_price_unavailable_cause_unknown"
    assert rec["decision"] == "skipped"
    assert rec["effective_stop_unrounded"] is None   # no stop was evaluated


# --- the masked default divergence ----------------------------------------

def test_masked_divergence_is_recorded_while_masked():
    """The 2026-10-07 reading. The monitor ratcheted at 10%, so config was read
    and neither default fired."""
    cfg = mo.effective_config({"trailing_stop_pct": 0.10})["trailing_stop_pct"]
    assert cfg["resolved_value"] == 0.10
    assert cfg["source"] == "config"
    assert cfg["defaults_agree"] is False
    assert cfg["masked_divergence"] is True
    assert cfg["currently_active"] is False


def test_removing_the_key_exposes_the_disagreement():
    """CONTROL 27. CATCHES: a config deletion silently putting the monitor on 8%
    while the report path stays on 10%.

    This is the ONLY circumstance in which either default is read. The recorder
    must not reconcile them.
    """
    cfg = mo.effective_config({})["trailing_stop_pct"]
    assert cfg["resolved_value"] == 0.08            # monitor's default, not 0.10
    assert cfg["source"] == "code_default"
    assert cfg["default_at_other_site"] == 0.10
    assert cfg["masked_divergence"] is False        # no longer masked
    assert cfg["currently_active"] is True

    gate = mo.config_review_gate({})
    assert any(f["key"] == "trailing_stop_pct"
               and f["condition"] == "divergent_defaults_now_active"
               for f in gate)


def test_code_only_defaults_are_flagged_as_code_sourced():
    """slippage_pct and commission_per_trade are absent from config entirely and
    set every booked price."""
    cfg = mo.effective_config({})
    for key, expected in (("slippage_pct", 0.001),
                          ("commission_per_trade", 1.00)):
        assert cfg[key]["source"] == "code_default"
        assert cfg[key]["resolved_value"] == expected


def test_review_gate_is_quiet_while_config_supplies_the_value():
    """CATCHES: a gate that fires constantly and is therefore ignored."""
    assert mo.config_review_gate({"trailing_stop_pct": 0.10}) == []


# --- bridge to the standalone runner ---------------------------------------
# The stdlib runner is the authoritative control set, because it is the one that
# can run on the VPS. Rather than restating its controls here and letting the
# two drift, every control it registers is executed as a pytest case. Adding a
# control there makes it appear here automatically.

sys.path.insert(0, str(Path(__file__).parent))
import run_monitor_observer_controls as runner       # noqa: E402


@pytest.mark.parametrize("name,why,fn",
                         runner.RESULTS,
                         ids=[n for n, _, _ in runner.RESULTS])
def test_standalone_control(name, why, fn):
    """Each control from the stdlib runner, with its stated failure mode."""
    fn()


def test_runner_registers_every_control_exactly_once():
    """CATCHES: a control silently shadowed by a duplicate function name."""
    names = [n for n, _, _ in runner.RESULTS]
    assert len(names) == len(set(names)), "duplicate control names"
    assert len(names) >= 29, f"expected the full control set, got {len(names)}"
