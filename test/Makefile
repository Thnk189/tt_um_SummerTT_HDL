# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src

# RTL sources (your top + dependencies)
PROJECT_SOURCES = project.v \
                  hvsync_generator.v

ifneq ($(GATES),yes)

# ---------------- RTL simulation ----------------
SIM_BUILD        = sim_build/rtl

# Add full source paths for RTL
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# ---------------- Gate-level simulation ----------------
SIM_BUILD        = sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM

# Include IHP SG13G2 PDK cells
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v

# Netlist is provided by GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

# (Optional) If you want hvsync_generator also visible in GL sim,
# you can still include it explicitly here:
VERILOG_SOURCES += $(SRC_DIR)/hvsync_generator.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS    += -I$(SRC_DIR)

# Testbench (tb.v instantiates your tt_um_* top)
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# MODULE is the basename of the Python test file (test/test_*.py)
MODULE = test

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
