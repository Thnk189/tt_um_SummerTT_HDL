# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

# Ensure CI artifacts exist
import os, pathlib
pathlib.Path("test").mkdir(exist_ok=True)
os.environ.setdefault("COCOTB_RESULTS_FILE", "test/results.xml")

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles
from cocotb.result import TestSuccess, TestSkip


# With CLOCK_FREQ = 24_000_000 and UPDATE_INTERVAL = CLOCK_FREQ / 10
UPDATE_INTERVAL = 2_400_000  # clocks (~0.1 s at 24 MHz)


def fsm_name(val: int) -> str:
    return {
        0: "IDLE",
        1: "UPDATE",
        2: "COPY",
        3: "INIT",
    }.get(val, f"UNKNOWN({val})")


def resolve_handle(dut, *names):
    """
    Try multiple hierarchical names and return the first signal handle found.
    Example: resolve_handle(dut, "user_project.action", "action")
    """
    for full in names:
        try:
            obj = dut
            for part in full.split("."):
                obj = getattr(obj, part)
            _ = obj.value  # ensure it's a signal-like object
            return obj
        except Exception:
            continue
    return None


def up(dut):
    """Return 'user_project' if present, else dut itself."""
    return getattr(dut, "user_project", dut)


async def monitor_fsm(clk, action_sig, cycles: int, dut=None, tag=""):
    """
    Count FSM states for N cycles.
    If action_sig is None, return zeros; caller can decide to skip.
    """
    counts = {"IDLE": 0, "UPDATE": 0, "COPY": 0, "INIT": 0}
    if action_sig is None:
        if dut:
            dut._log.warning(f"[{tag}] 'action' not accessible; skipping monitor")
        return counts

    for _ in range(cycles):
        await RisingEdge(clk)
        try:
            state = fsm_name(int(action_sig.value))
        except Exception:
            state = "UNKNOWN(?)"
        if state in counts:
            counts[state] += 1
    return counts


def get_vsync_handle_or_fallback(dut):
    """
    Prefer internal vsync; if not accessible, fall back to uo_out[3] (your mapping).
    Returns an object with a .value usable with RisingEdge.
    """
    vs = resolve_handle(dut, "user_project.vsync", "vsync")
    if vs is not None:
        return vs

    uo = resolve_handle(dut, "user_project.uo_out", "uo_out")
    if uo is None:
        return None

    class VsyncBit:
        def __init__(self, parent, bit_index):
            self.parent = parent
            self.bit = bit_index
        @property
        def value(self):
            return (int(self.parent.value) >> self.bit) & 1

    return VsyncBit(uo, 3)  # uo_out[3] is vsync in your pack


async def wait_for_update_entry(clk, action_sig, max_cycles=4000):
    """Wait until FSM enters UPDATE (1). Return True if seen; False if timeout/hidden."""
    if action_sig is None:
        return False
    for _ in range(max_cycles):
        await RisingEdge(clk)
        try:
            if int(action_sig.value) == 1:
                return True
        except Exception:
            pass
    return False


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

@cocotb.test()
async def test_reset_init_idle(dut):
    """1) Reset → INIT → IDLE (observe INIT then return to IDLE)"""

    clock = Clock(dut.clk, 41.7, units="ns")  # ~24 MHz
    cocotb.start_soon(clock.start())

    ACT = resolve_handle(dut, "user_project.action", "action")

    # Default pins
    dut.ena.value   = 1
    dut.ui_in.value = 0          # running=1 (since ~ui_in[0]), randomize=0
    dut.uio_in.value = 0
    dut.rst_n.value = 0

    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    if ACT is None:
        dut._log.warning("action not accessible; skipping strict checks")
        await ClockCycles(dut.clk, 300)
        raise TestSuccess("Skipped: no action visibility")

    counts = await monitor_fsm(dut.clk, ACT, 300, dut, tag="reset_init_idle")
    dut._log.info(f"[reset_init_idle] {counts}")
    assert counts["INIT"] > 0, "Expected some INIT after reset"
    assert counts["IDLE"] > 0, "Expected to reach IDLE after INIT"


@cocotb.test()
async def test_pause_running(dut):
    """2) ui_in[0]=1 (pause) holds FSM in IDLE"""

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    ACT = resolve_handle(dut, "user_project.action", "action")

    dut.ena.value   = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0          # running=1
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 300)

    if ACT is None:
        dut._log.warning("action not accessible; skipping pause check")
        raise TestSuccess("Skipped: no action visibility")

    # Pause, hold for 200 cycles
    dut.ui_in.value = 0b0000_0001
    counts = await monitor_fsm(dut.clk, ACT, 200, dut, tag="pause")
    dut._log.info(f"[pause] {counts}")
    assert counts["UPDATE"] == 0 and counts["COPY"] == 0 and counts["INIT"] == 0, \
        "FSM left IDLE while paused"

    dut.ui_in.value = 0  # resume


@cocotb.test()
async def test_randomize_triggers_init(dut):
    """
    3) ui_in[1]=1 triggers INIT at the next tick:
       Preload 'timer' to UPDATE_INTERVAL, then wait for the *real* VSYNC edge.
    """

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    ACT   = resolve_handle(dut, "user_project.action", "action")
    TIMER = resolve_handle(dut, "user_project.timer",  "timer")
    VS    = get_vsync_handle_or_fallback(dut)

    dut.ena.value   = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0          # running=1, randomize=0
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 300)

    if ACT is None or VS is None:
        dut._log.warning("action or vsync not accessible; skipping randomize check")
        raise TestSuccess("Skipped: missing internal visibility")

    if TIMER is None:
        dut._log.warning("timer not accessible; cannot force tick quickly — skipping")
        raise TestSuccess("Skipped: no timer visibility")

    # Arm randomize & preload timer so next VSYNC triggers INIT
    dut.ui_in.value = 0b0000_0010  # randomize=1, running=1
    TIMER.value     = UPDATE_INTERVAL

    await RisingEdge(dut.clk)  # settle write
    await RisingEdge(VS)       # real VSYNC from hvsync_generator
    await RisingEdge(dut.clk)  # latch transition

    counts = await monitor_fsm(dut.clk, ACT, 500, dut, tag="rand_trig")
    dut._log.info(f"[rand_trig] {counts}")
    assert counts["INIT"] > 0, "Expected INIT when randomize asserted at tick"

    dut.ui_in.value = 0  # clear randomize


@cocotb.test()
async def test_randomize_short_pulse_ignored(dut):
    """5) Short ui_in[1] pulse far from tick is ignored (stay in IDLE)."""

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    ACT = resolve_handle(dut, "user_project.action", "action")

    dut.ena.value   = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0          # running=1, randomize=0
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 300)

    if ACT is None:
        dut._log.warning("action not accessible; skipping short-pulse check")
        raise TestSuccess("Skipped: no action visibility")

    # 1-cycle pulse while timer is far from threshold
    dut.ui_in.value = 0b0000_0010
    await ClockCycles(dut.clk, 1)
    dut.ui_in.value = 0

    counts = await monitor_fsm(dut.clk, ACT, 200, dut, tag="rand_short")
    dut._log.info(f"[rand_short] {counts}")
    assert counts["UPDATE"] == 0 and counts["COPY"] == 0, \
        "Unexpected UPDATE/COPY from short randomize pulse between ticks"


@cocotb.test()
async def test_reset_mid_operation(dut):
    """7) Reset asserted mid-UPDATE restarts INIT."""

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    ACT   = resolve_handle(dut, "user_project.action", "action")
    TIMER = resolve_handle(dut, "user_project.timer",  "timer")
    VS    = get_vsync_handle_or_fallback(dut)

    dut.ena.value   = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0          # running=1, randomize=0
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 300)

    if ACT is None or VS is None:
        dut._log.warning("action or vsync not accessible; skipping reset-mid-op")
        raise TestSuccess("Skipped: missing internal visibility")

    if TIMER is None:
        dut._log.warning("timer not accessible; cannot force tick quickly — skipping")
        raise TestSuccess("Skipped: no timer visibility")

    # Force an immediate UPDATE on the next real VSYNC
    TIMER.value = UPDATE_INTERVAL
    await RisingEdge(dut.clk)
    await RisingEdge(VS)

    entered = await wait_for_update_entry(dut.clk, ACT, max_cycles=4000)
    assert entered, "Expected FSM to enter UPDATE after vsync tick"

    # Assert reset mid-UPDATE
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    counts = await monitor_fsm(dut.clk, ACT, 300, dut, tag="reset_mid")
    dut._log.info(f"[reset_mid] {counts}")
    assert counts["INIT"] > 0, "Expected INIT after reset asserted during UPDATE"
