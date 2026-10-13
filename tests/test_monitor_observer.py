"""pytest bridge to the standalone control runners.

pytest is absent from the VPS venv, so the stdlib runners under tests/ are the
authoritative control sets -- they are the ones that can actually run where the
code is deployed. This file exists only so that anyone who does have pytest gets
the same controls, and it deliberately RESTATES NOTHING.

An earlier version restated each control here as well. The two copies drifted
immediately: when fields were renamed during the activation review, the copies
here kept asserting removed field names and still passed, because they checked a
value and never checked that the record was valid. Restating controls in two
places is how a suite stops testing what it claims to test.

Adding a control to any runner makes it appear here automatically.
"""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

RUNNERS = (
    "run_monitor_observer_controls",
    "run_verifier_negative_controls",
    "run_entrypoint_containment_controls",
)


def _controls():
    """(runner, name, why, fn) for every control in every runner."""
    found = []
    for mod_name in RUNNERS:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "RESULTS"):
            for name, why, fn in mod.RESULTS:
                found.append((mod_name, name, why, fn))
        else:
            # The verifier suite collects plain functions rather than
            # registering them with a decorator, so discover them as it does.
            for fn in mod.CONTROLS:
                found.append((mod_name, fn.__name__,
                              (fn.__doc__ or "").strip().splitlines()[0]
                              if fn.__doc__ else "", fn))
    return found


ALL = _controls()


@pytest.mark.parametrize(
    "runner,name,why,fn", ALL,
    ids=[f"{r.replace('run_', '')}::{n}" for r, n, _, _ in ALL])
def test_control(runner, name, why, fn):
    """One control from one stdlib runner, with the failure it exists to catch."""
    fn()


def test_every_control_is_registered_exactly_once():
    """CATCHES: a control silently shadowed by a duplicate function name."""
    names = [f"{r}::{n}" for r, n, _, _ in ALL]
    assert len(names) == len(set(names)), "duplicate control names"


def test_control_count_has_not_regressed():
    """CATCHES: a runner quietly losing controls, so a shrinking suite still
    reports all-green."""
    per = {r: sum(1 for x in ALL if x[0] == r) for r in RUNNERS}
    assert per["run_monitor_observer_controls"] >= 44, per
    assert per["run_verifier_negative_controls"] >= 14, per
    assert per["run_entrypoint_containment_controls"] >= 13, per
