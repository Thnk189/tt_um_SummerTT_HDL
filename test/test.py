# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles


def fsm_name(val: int) -> str:
    return {
        0: "IDLE",
        1: "UPDATE",
        2: "COPY",
        3: "INIT"
    }.get(val, f"UNKNOWN({val})")


async def monitor_fsm(dut, cycles):
    """Track FSM state for N cycles and return counts."""
    counts = {"IDLE": 0, "UPDATE": 0, "COPY": 0, "INIT": 0}
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        state = fsm_name(int(dut.user_project.action.value))
        if state in counts:
            counts[state] += 1
    return counts


@cocotb.test()
async def test_reset_init_idle(dut):
    """1. Reset -> INIT -> IDLE"""

    clock = Clock(dut.clk, 41.7, units="ns")  # ~24 MHz
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0

    dut._log.info("Applying reset...")
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    dut._log.info("Released reset.")

    # FSM should be INIT
    await RisingEdge(dut.clk)
    assert int(dut.user_project.action.value) == 3

    # Track FSM for 300 cycles
    counts = await monitor_fsm(dut, 300)

    dut._log.info(f"Summary: {counts}")
    assert counts["INIT"] > 0
    assert counts["IDLE"] > 0


@cocotb.test()
async def test_pause_running(dut):
    """2. ui_in[0]=1 (pause) holds FSM in IDLE"""

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 300)
    assert int(dut.user_project.action.value) == 0

    dut.ui_in.value = 0b0000_0001
    dut._log.info("Pause asserted.")

    counts = await monitor_fsm(dut, 200)

    dut._log.info(f"Summary while paused: {counts}")
    assert counts["IDLE"] == 200, "FSM left IDLE while paused"

    dut.ui_in.value = 0


@cocotb.test()
async def test_randomize_triggers_init(dut):
    """3. ui_in[1]=1 triggers INIT at tick (forced)"""

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 300)
    dut.ui_in.value = 0b0000_0010

    dut.timer.value = (1 << 31) - 1
    dut.vsync.value = 1
    await RisingEdge(dut.clk)
    dut.vsync.value = 0
    await RisingEdge(dut.clk)

    counts = await monitor_fsm(dut, 300)
    dut._log.info(f"Summary with randomize: {counts}")
    assert counts["INIT"] > 0

    dut.ui_in.value = 0


@cocotb.test()
async def test_randomize_short_pulse_ignored(dut):
    """5. Short ui_in[1] pulse between ticks should be ignored"""

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 300)

    dut.ui_in.value = 0b0000_0010
    await ClockCycles(dut.clk, 1)
    dut.ui_in.value = 0

    counts = await monitor_fsm(dut, 200)
    dut._log.info(f"Summary after short pulse: {counts}")
    assert counts["IDLE"] == 200


@cocotb.test()
async def test_reset_mid_operation(dut):
    """7. Reset asserted mid-UPDATE restarts INIT"""

    clock = Clock(dut.clk, 41.7, units="ns")
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0

    await ClockCycles(dut.clk, 300)

    dut.timer.value = (1 << 31) - 1
    dut.vsync.value = 1
    await RisingEdge(dut.clk)
    dut.vsync.value = 0
    await RisingEdge(dut.clk)
    assert int(dut.user_project.action.value) == 1

    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    counts = await monitor_fsm(dut, 300)
    dut._log.info(f"Summary after reset mid-operation: {counts}")
    assert counts["INIT"] > 0
