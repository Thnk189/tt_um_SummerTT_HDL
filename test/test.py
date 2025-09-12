# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles


# Your design steps when (timer >= UPDATE_INTERVAL) AND (vsync == 1)
# With CLOCK_FREQ=24_000_000 and UPDATE_INTERVAL = CLOCK_FREQ/10:
UPDATE_INTERVAL = 2_400_000  # clocks (~0.1 s @ 24 MHz)


def fsm_name(val: int) -> str:
    return {
        0: "IDLE",
        1: "UPDATE",
        2: "COPY",
        3: "INIT"
    }.get(val, f"UNKNOWN({val})")


def up(dut):
    """
    Tiny Tapeout CI often wraps the user design under 'user_project'.
    Locally, your top may be the design itself. This helper returns the
    correct handle either way.
    """
    return getattr(dut, "user_project", dut)


async def monitor_fsm(clk, action_sig, cycles: int):
    """Track FSM state for N cycles and return counts by name."""
    counts = {"IDLE": 0, "UPDATE": 0, "COPY": 0, "INIT": 0}
    for _ in range(cycles):
        await RisingEdge(clk)
        state = fsm_name(int(action_sig.value))
        if state in counts:
            counts[state] += 1
    return counts


@cocotb.test()
async def test_reset_init_idle(dut):
    """1) Reset → INIT → IDLE"""

    clock = Clock(dut.clk, 41.7, units="ns")  # ~24 MHz
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.ui_in.value = 0         # running=1 (since ~ui_in[0]), randomize=0
    dut.uio_in.value = 0
    dut.rst_n.value = 0

    dut._log.info("Applying reset...")
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    dut._log.info("Released reset.")

    # Immediately after reset, FSM should be INIT
    await RisingEdge(dut.clk)
    assert int(up(dut).action.value) == 3, "FSM not in INIT right after reset"

    # Track FSM for a while; it should pass through INIT then settle to IDLE
    counts = await monitor_fsm(dut.clk, up(dut).action, 300)
    dut._log.info(f"Summary: {counts}")
    assert counts["INIT"] > 0, "Expected some INIT activity"
    assert counts["IDLE"] > 0, "Expected to reach IDLE after INIT"


@cocotb.test()
async def test_pause_running(dut):
    """2) ui_in[0]=1 (pause) holds FSM in IDLE"""

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0         # running=1
    dut.uio_in.value = 0

    # Let it settle into IDLE after power-up INIT
    await ClockCycles(dut.clk, 300)
    assert int(up(dut).action.value) == 0, "Expected IDLE before pausing"

    # Pause (ui_in[0]=1 -> running=0), hold for 200 cycles
    dut.ui_in.value = 0b0000_0001
    dut._log.info("Pause asserted.")
    counts = await monitor_fsm(dut.clk, up(dut).action, 200)
    dut._log.info(f"Summary while paused: {counts}")
    assert counts["IDLE"] == 200, "FSM left IDLE while paused"

    # Clear pause
    dut.ui_in.value = 0


@cocotb.test()
async def test_randomize_triggers_init(dut):
    """
    3) ui_in[1]=1 triggers INIT at the next tick:
       Preload timer to UPDATE_INTERVAL and wait for the *real* VSYNC.
    """

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0         # running=1, randomize=0
    dut.uio_in.value = 0

    # Ensure we are in IDLE after power-up
    await ClockCycles(dut.clk, 300)

    # Arm randomize and preload timer so the next real VSYNC triggers INIT
    dut.ui_in.value = 0b0000_0010   # randomize=1, running=1
    up(dut).timer.value = UPDATE_INTERVAL

    # WAIT for the natural VSYNC edge from hvsync_generator
    await RisingEdge(up(dut).vsync)
    # One extra clock to latch the state transition
    await RisingEdge(dut.clk)

    counts = await monitor_fsm(dut.clk, up(dut).action, 300)
    dut._log.info(f"Summary with randomize (aligned to vsync): {counts}")
    assert counts["INIT"] > 0, "Expected INIT activity when randomize is asserted at tick"

    # Clear randomize
    dut.ui_in.value = 0


@cocotb.test()
async def test_randomize_short_pulse_ignored(dut):
    """5) Short ui_in[1] pulse far from the tick is ignored (stays in IDLE)."""

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0         # running=1, randomize=0
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 300)

    # Brief randomize pulse (1 cycle) while timer is far from threshold
    dut.ui_in.value = 0b0000_0010
    await ClockCycles(dut.clk, 1)
    dut.ui_in.value = 0

    counts = await monitor_fsm(dut.clk, up(dut).action, 200)
    dut._log.info(f"Summary after short pulse: {counts}")
    assert counts["IDLE"] == 200, "Short randomize pulse between ticks should be ignored"


@cocotb.test()
async def test_reset_mid_operation(dut):
    """7) Reset asserted mid-UPDATE restarts INIT."""

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0         # running=1, randomize=0
    dut.uio_in.value = 0

    # Settle to IDLE after power-up INIT
    await ClockCycles(dut.clk, 300)

    # Force an immediate UPDATE on the next real VSYNC
    up(dut).timer.value = UPDATE_INTERVAL
    await RisingEdge(up(dut).vsync)

    # Wait until FSM actually enters UPDATE
    entered_update = False
    for _ in range(2000):
        await RisingEdge(dut.clk)
        if int(up(dut).action.value) == 1:
            entered_update = True
            break

    assert entered_update, "Expected FSM to enter UPDATE after vsync tick"

    # Assert reset in the middle of UPDATE
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    # After reset, INIT should occur again
    counts = await monitor_fsm(dut.clk, up(dut).action, 300)
    dut._log.info(f"Summary after reset mid-operation: {counts}")
    assert counts["INIT"] > 0, "Expected INIT after reset asserted during UPDATE"
