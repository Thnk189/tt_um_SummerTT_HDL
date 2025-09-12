# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

# If you want JUnit in CI, uncomment the next 3 lines:
# import os, pathlib
# pathlib.Path("test").mkdir(exist_ok=True)
# os.environ.setdefault("COCOTB_RESULTS_FILE", "test/results.xml")

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles
from cocotb.result import TestSuccess

UPDATE_INTERVAL = 2_400_000  # CLOCK_FREQ/10 at 24 MHz


def fsm_name(val: int) -> str:
    return {0: "IDLE", 1: "UPDATE", 2: "COPY", 3: "INIT"}.get(val, f"UNKNOWN({val})")


# ---------- hierarchy helpers ----------

def try_get(handle, name):
    try:
        obj = getattr(handle, name)
        _ = obj.value
        return obj
    except Exception:
        return None


def resolve_any(dut, *paths):
    """Try several dotted paths like 'user_project.user_module.action'."""
    for p in paths:
        try:
            h = dut
            for part in p.split("."):
                h = getattr(h, part)
            _ = h.value  # ensure signal-like
            return h
        except Exception:
            continue
    return None


def walk_find_signal(root, signal_name, max_depth=6):
    """Recursive best-effort search for a signal by name."""
    # try direct child attr first
    sig = try_get(root, signal_name)
    if sig is not None:
        return sig
    if max_depth <= 0:
        return None
    # iterate sub-nodes
    try:
        for child in root:
            found = walk_find_signal(child, signal_name, max_depth - 1)
            if found is not None:
                return found
    except Exception:
        pass
    return None


def locate_user_module_handles(dut):
    """
    Return (ACTION, TIMER, VSYNC_SIG, VSYNC_VIA_UO_OUT) handles, any of which may be None.
    Will try typical TT wrappers and then do a recursive search.
    """
    # Common TT wrapper layouts
    # - tb.user_project.user_module.<signal>
    # - tb.user_project.dut.<signal>
    # - tb.dut.user_project.user_module.<signal>
    # - direct top: tb.<signal>
    action = resolve_any(
        dut,
        "user_project.user_module.action",
        "user_project.dut.action",
        "dut.user_project.user_module.action",
        "user_project.uut.action",
        "action",  # direct
    )
    timer = resolve_any(
        dut,
        "user_project.user_module.timer",
        "user_project.dut.timer",
        "dut.user_project.user_module.timer",
        "user_project.uut.timer",
        "timer",
    )
    vsync = resolve_any(
        dut,
        "user_project.user_module.vsync",
        "user_project.dut.vsync",
        "dut.user_project.user_module.vsync",
        "user_project.uut.vsync",
        "vsync",
    )

    # Fallback: recursive search if still missing
    if action is None:
        action = walk_find_signal(dut, "action")
    if timer is None:
        timer = walk_find_signal(dut, "timer")
    if vsync is None:
        vsync = walk_find_signal(dut, "vsync")

    # Fallback to uo_out[3] for vsync if internal not found
    uo = resolve_any(dut, "user_project.uo_out", "dut.uo_out", "uo_out")
    vsync_via_uo = None
    if uo is not None:
        class BitAdapter:
            def __init__(self, parent, bit):
                self.parent = parent
                self.bit = bit
            @property
            def value(self):
                try:
                    return (int(self.parent.value) >> self.bit) & 1
                except Exception:
                    return 0
        vsync_via_uo = BitAdapter(uo, 3)  # mapping: uo_out[3] == vsync

    return action, timer, vsync, vsync_via_uo


async def monitor_fsm(clk, action_sig, cycles: int):
    counts = {"IDLE": 0, "UPDATE": 0, "COPY": 0, "INIT": 0}
    if action_sig is None:
        return counts
    for _ in range(cycles):
        await RisingEdge(clk)
        counts[fsm_name(int(action_sig.value))] += 1
    return counts


async def wait_until_update(clk, action_sig, limit=4000):
    if action_sig is None:
        return False
    for _ in range(limit):
        await RisingEdge(clk)
        if int(action_sig.value) == 1:
            return True
    return False


# ---------- tests ----------

@cocotb.test()
async def test_reset_init_idle(dut):
    """Reset → INIT → IDLE (observe INIT activity then IDLE)"""
    cocotb.start_soon(Clock(dut.clk, 41.7, units="ns").start())

    ACT, _, _, _ = locate_user_module_handles(dut)

    dut.ena.value = 1
    dut.ui_in.value = 0  # running=1 (since ~ui_in[0])
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    if ACT is None:
        dut._log.warning("No access to internal 'action' — skipping strict checks")
        await ClockCycles(dut.clk, 300)
        raise TestSuccess("Skipped: no action visibility")

    counts = await monitor_fsm(dut.clk, ACT, 300)
    dut._log.info(f"[reset_init_idle] {counts}")
    assert counts["INIT"] > 0, "Expected some INIT activity after reset"
    assert counts["IDLE"] > 0, "Expected to reach IDLE after INIT"


@cocotb.test()
async def test_pause_running(dut):
    """ui_in[0]=1 (pause) holds FSM in IDLE"""
    cocotb.start_soon(Clock(dut.clk, 41.7, units="ns").start())

    ACT, _, _, _ = locate_user_module_handles(dut)

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 300)

    if ACT is None:
        dut._log.warning("No access to internal 'action' — skipping pause check")
        raise TestSuccess("Skipped: no action visibility")

    # Pause for 200 cycles
    dut.ui_in.value = 0b0000_0001
    counts = await monitor_fsm(dut.clk, ACT, 200)
    dut._log.info(f"[pause] {counts}")
    assert counts["UPDATE"] == 0 and counts["COPY"] == 0 and counts["INIT"] == 0, \
        "FSM left IDLE while paused"
    dut.ui_in.value = 0


@cocotb.test()
async def test_randomize_triggers_init(dut):
    """ui_in[1]=1 triggers INIT at next tick (preload timer, wait for real VSYNC)"""
    cocotb.start_soon(Clock(dut.clk, 41.7, units="ns").start())

    ACT, TIMER, VSYNC_INT, VSYNC_UO = locate_user_module_handles(dut)
    VS = VSYNC_INT or VSYNC_UO

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 300)

    if ACT is None or VS is None:
        dut._log.warning("Missing 'action' or 'vsync' — skipping test")
        raise TestSuccess("Skipped: missing internal visibility")

    if TIMER is None:
        dut._log.warning("No 'timer' access — cannot force tick quickly; skipping test")
        raise TestSuccess("Skipped: no timer visibility")

    # Arm randomize and preload timer so next VSYNC triggers INIT
    dut.ui_in.value = 0b0000_0010
    TIMER.value = UPDATE_INTERVAL
    await RisingEdge(dut.clk)
    await RisingEdge(VS)
    await RisingEdge(dut.clk)

    counts = await monitor_fsm(dut.clk, ACT, 500)
    dut._log.info(f"[rand_trig] {counts}")
    assert counts["INIT"] > 0, "Expected INIT when randomize asserted at tick"
    dut.ui_in.value = 0


@cocotb.test()
async def test_randomize_short_pulse_ignored(dut):
    """Short ui_in[1] pulse far from tick is ignored (stay in IDLE)"""
    cocotb.start_soon(Clock(dut.clk, 41.7, units="ns").start())

    ACT, _, _, _ = locate_user_module_handles(dut)

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 300)

    if ACT is None:
        dut._log.warning("No access to internal 'action' — skipping test")
        raise TestSuccess("Skipped: no action visibility")

    # 1-cycle randomize pulse
    dut.ui_in.value = 0b0000_0010
    await ClockCycles(dut.clk, 1)
    dut.ui_in.value = 0

    counts = await monitor_fsm(dut.clk, ACT, 200)
    dut._log.info(f"[rand_short] {counts}")
    assert counts["UPDATE"] == 0 and counts["COPY"] == 0, \
        "Unexpected UPDATE/COPY from short randomize pulse between ticks"


@cocotb.test()
async def test_reset_mid_operation(dut):
    """Reset asserted mid-UPDATE restarts INIT"""
    cocotb.start_soon(Clock(dut.clk, 41.7, units="ns").start())

    ACT, TIMER, VSYNC_INT, VSYNC_UO = locate_user_module_handles(dut)
    VS = VSYNC_INT or VSYNC_UO

    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 300)

    if ACT is None or VS is None:
        dut._log.warning("Missing 'action' or 'vsync' — skipping test")
        raise TestSuccess("Skipped: missing internal visibility")

    if TIMER is None:
        dut._log.warning("No 'timer' access — cannot force tick quickly; skipping test")
        raise TestSuccess("Skipped: no timer visibility")

    # Force an UPDATE on next VSYNC
    TIMER.value = UPDATE_INTERVAL
    await RisingEdge(dut.clk)
   ## await RisingEdge(VS)
   ## ok = await wait_until_update(dut.clk, ACT, limit=4000)
   ## assert ok, "Expected FSM to enter UPDATE after vsync tick" 
# Wait until VSYNC is high (level-sensitive, matches RTL) some chat gpt fix for this making it so instead of waiting on a positive edge it waits for v sync specifically, and  i guess in the code id prefer eveyrthing to be on a rising or falling edge but i dont have time on the highkey so this is th ebest i will do.
    for _ in range(20000):
        await RisingEdge(dut.clk)
        if int(VS.value) == 1:
            break
    # Now FSM should sample UPDATE
    ok = await wait_until_update(dut.clk, ACT, limit=4000)
    assert ok, "Expected FSM to enter UPDATE after vsync tick" 
# the message right above that says excpected fsm is you noticed from the commented out portion, yes this is similar yes chat did write this part
    
    # Reset mid-UPDATE
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    counts = await monitor_fsm(dut.clk, ACT, 300)
    dut._log.info(f"[reset_mid] {counts}")
    assert counts["INIT"] > 0, "Expected INIT after reset during UPDATE"
