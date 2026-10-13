#!/usr/bin/env python3
"""Run the REAL deployed monitor_trades.py entry point under stubbed deps.

    python3 entrypoint_driver.py MODE WORKDIR SOURCE_COPY

Invoked as a subprocess by tests/run_entrypoint_containment_controls.py. Not a
test itself.

WHY THIS EXISTS
---------------
A previous control tested a hand-written replica of the `__main__` block and
passed an exception-swallowing mutation that the real file would have failed. So
this executes the deployed file's own bytes, via runpy, with `__name__` set to
"__main__" so the entry point actually runs.

The file is executed from a COPY whose sha256 the caller has already checked
against the deployed file. The copy is not a rewrite -- it is the same bytes at
a different path -- and relocating it is what keeps the health emitter, which
resolves its output relative to its own __file__, from writing into the
production logs directory.

Every engine dependency is stubbed in sys.modules before execution, so no
market data is requested, no gateway is contacted and no production state is
read or written.
"""

import runpy
import sys
import types
from pathlib import Path


class MonitorMarkerError(RuntimeError):
    """Unique production failure, so the propagated exception is identifiable."""


def _trade():
    return {
        "symbol": "T1", "status": "open", "entry_date": "2026-10-01",
        "entry_price": 10.0, "stop_loss": 9.0, "take_profit": 12.0,
        "shares": 5.0,
    }


def _circular():
    """A value json cannot serialise, whatever `default` is supplied."""
    d = {}
    d["self"] = d
    return d


def _install_stubs(mode, state):
    engine = types.ModuleType("engine")

    data_feed = types.ModuleType("engine.data_feed")
    data_feed.get_live_price = lambda symbol: state["live"]
    data_feed.disconnect_ib = lambda: None

    tracker = types.ModuleType("engine.tracker")
    tracker.load_trades = lambda: state["trades"]
    tracker._load_params = lambda: {"trailing_stop_pct": 0.10,
                                    "rsi_extreme_high": 90}
    tracker._holding_days = lambda entry_date: 3
    tracker.load_stock = lambda symbol: None
    def promote_queue(**kw):
        if mode == "promote_raises_after_save":
            raise MonitorMarkerError("promote_queue failed after the save")
        state["promoted"] = True

    tracker.promote_queue = promote_queue

    def save_trades(trades):
        if mode == "save_raises":
            raise MonitorMarkerError("save_trades failed")
        state["saved"] = True

    def _close_trade(trade, today, price, reason):
        if mode == "close_raises_after_mutation":
            # Mutates, THEN fails. The trade is now partially closed in memory
            # with no booked exit price -- the state the recorder must not
            # describe as a completed close.
            trade["status"] = "closed"
            trade["exit_reason"] = reason
            raise MonitorMarkerError("_close_trade failed after mutating")
        trade["exit_price"] = round(price * 0.999, 2)
        trade["status"] = "closed"
        trade["pnl_pct"] = -5.0
        trade["pnl"] = -5.0

    tracker.save_trades = save_trades
    tracker._close_trade = _close_trade

    indicators = types.ModuleType("engine.indicators")
    indicators.add_indicators = lambda df: df

    if mode == "load_raises":
        def boom():
            raise MonitorMarkerError("load_trades failed")
        tracker.load_trades = boom

    engine.data_feed = data_feed
    engine.tracker = tracker
    engine.indicators = indicators

    sys.modules["engine"] = engine
    sys.modules["engine.data_feed"] = data_feed
    sys.modules["engine.tracker"] = tracker
    sys.modules["engine.indicators"] = indicators

    # The recorder is the real module in every mode except the one that proves
    # an unimportable recorder cannot stop the monitor.
    if mode not in ("recorder_import_fails", "health_unwritable",
                    "health_unwritable_and_raises"):
        # Loaded straight from its file. Importing it as engine.monitor_observer
        # would require the engine stub to be a real package, which would also
        # put the real engine package back on the import path and defeat the
        # isolation these stubs exist to provide.
        repo = Path(__file__).resolve().parent.parent.parent
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "engine.monitor_observer", repo / "engine" / "monitor_observer.py")
        mo = importlib.util.module_from_spec(spec)
        sys.modules["engine.monitor_observer"] = mo
        spec.loader.exec_module(mo)
        engine.monitor_observer = mo
        return mo
    return None


def main(mode, workdir, source_copy):
    work = Path(workdir)
    state = {"trades": [_trade()], "live": 10.5}

    if mode == "serialization_fails":
        # A field the recorder captures verbatim, holding a value json cannot
        # serialise. The monitor itself never serialises it.
        t = _trade()
        t["entry_date"] = _circular()
        state["trades"] = [t]

    if mode in ("health_unwritable", "health_unwritable_and_raises"):
        # The health emitter resolves its output next to the source file, so a
        # FILE where the logs directory belongs makes every health write fail.
        (work / "logs").write_text("not a directory\n")

    if mode in ("closes_position", "save_raises",
                "close_raises_after_mutation", "promote_raises_after_save"):
        # Below the stop, so the close branch runs. save_raises needs a real
        # close in memory before persistence fails, which is the whole point of
        # that scenario.
        state["live"] = 8.0

    mo = _install_stubs(mode, state)

    if mo is not None:
        if mode in ("recorder_cannot_write", "raises_and_recorder_cannot_write"):
            # LOGS_DIR points at an existing FILE, so the recorder cannot create
            # its directory and every write fails.
            blocker = work / "blocked"
            blocker.write_text("not a directory\n")
            mo.LOGS_DIR = blocker / "logs"
        else:
            mo.LOGS_DIR = work / "logs"

    if mode in ("raises_and_recorder_cannot_write",
                "health_unwritable_and_raises"):
        sys.modules["engine.tracker"].load_trades = _raiser

    runpy.run_path(source_copy, run_name="__main__")
    return 0


def _raiser():
    raise MonitorMarkerError("load_trades failed")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
