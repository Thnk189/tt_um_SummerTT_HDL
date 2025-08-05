

module tt_um_SummerTT_HDL(
    input [7:0] ui_in;,
    output [7:0] uo_out,
    input [7:0] uio_in,
    output [7:0] uio_out,
    output [7:0] uio_oe,
    input ena,
    input clk,       
    input rst_n
);

localparam CLOCK_FREQ = 24000000;
/* verilator lint_off UNUSED */
/* verilator lint_off width */
  

    // VGA signals
    wire hsync, vsync;
    wire [1:0] R, G, B;
    wire video_active;
    wire [9:0] pix_x, pix_y;
    
    hvsync_generator hvsync_gen(
        .clk(clk),
        .reset(~rst_n),
        .hsync(hsync),
        .vsync(vsync),
        .display_on(video_active),
        .hpos(pix_x),
        .vpos(pix_y)
    );

   



    // Control signals
    wire running = ~ui_in[0];
    wire randomize = ui_in[1];
    wire boot_reset = ~rst_n;
    
    assign uo_out = {hsync, B[0], G[0], R[0], vsync, B[1], G[1], R[1]};
    assign uio_out = 0;
    assign uio_oe = 0;
    wire _unused_ok = &{ena, uio_in};

    // Display logic
    wire frame_active = (pix_x >= 64 && pix_x < 640-64 && pix_y >= 112 && pix_y < 480-112);
    wire icon_pixel = icon[pix_y[2:0]][pix_x[2:0]];
    wire [10:0] cell_index = (pix_y[7:3] << 6) | pix_x[8:3];
    
    assign R = (video_active & frame_active) ? {board_state[cell_index] & icon_pixel, 1'b1} : 2'b00;
    assign G = (video_active & frame_active) ? {board_state[cell_index] & icon_pixel, 1'b1} : 2'b00;
    assign B = 2'b01;

    // Simulation parameters
    localparam logWIDTH = 6, logHEIGHT = 5;  // 64x32 board
    localparam WIDTH = 2 ** logWIDTH;
    localparam HEIGHT = 2 ** logHEIGHT;
    localparam BOARD_SIZE = WIDTH * HEIGHT;
    localparam UPDATE_INTERVAL = CLOCK_FREQ/10;

    // Game state memory 
    reg board_state [0:BOARD_SIZE-1];         // current state of the simulation
    reg board_state_next [0:BOARD_SIZE-1];    // next state of the simulation


    // State machine
    localparam ACTION_IDLE = 0, ACTION_UPDATE = 1, ACTION_COPY = 2, ACTION_INIT = 3;
    reg [2:0] action;
    reg [31:0] timer;
    
    // Control signals
    reg board_state_we;
    reg board_state_source;  // 0=INIT, 1=COPY
    
    // State machine
    always @(posedge clk) begin
        if (boot_reset) begin
            action <= ACTION_INIT;
            timer <= 0;
            board_state_we <= 1'b1;
            board_state_source <= 1'b0;
        end else begin
            case (action)
                ACTION_IDLE: begin
                    board_state_we <= 1'b0;
                    if (running) begin
                        if (timer < UPDATE_INTERVAL) begin
                            timer <= timer + 1;
                        end else if (vsync) begin
                            timer <= 0;
                            action <= (~randomize) ? ACTION_UPDATE : ACTION_INIT;
                            board_state_we <= randomize ? 1'b0 : 1'b1;
                            board_state_source <= 1'b0;
                        end
                    end
                end
                
                ACTION_UPDATE: begin
                    board_state_we <= 1'b0;
                    if (action_update_complete) begin
                        action <= ACTION_COPY;
                        board_state_we <= 1'b1;
                        board_state_source <= 1'b1;
                    end
                end
                
                ACTION_COPY: begin
                    if (action_copy_complete) begin
                        action <= ACTION_IDLE;
                        board_state_we <= 1'b0;
                    end
                end
                
                ACTION_INIT: begin
                    if (action_init_complete) begin
                        action <= ACTION_IDLE;
                        board_state_we <= 1'b0;
                    end
                end
                
                default: action <= ACTION_IDLE;
            endcase
        end
    end

    // Memory initialization (ACTION_INIT)
    reg [logWIDTH+logHEIGHT-1:0] index2;
    reg action_init_complete;
    
    always @(posedge clk) begin
        if (boot_reset) begin
            index2 <= 0;
            action_init_complete <= 0;
        end else if (action == ACTION_INIT && !action_init_complete) begin
            board_state[index2] <= rng;
            if (index2 < BOARD_SIZE - 1) begin
                index2 <= index2 + 1;
            end else begin
                index2 <= 0;
                action_init_complete <= 1;
            end
        end else begin
            action_init_complete <= 0;
        end
    end
    
// =============================================
// STATE UPDATE LOGIC (ACTION_UPDATE)
// =============================================
reg [logWIDTH+logHEIGHT-1:0] index3;     // Cell index being processed
reg [3:0] neigh_index;                   // Current neighbor being checked (0-8)
reg [3:0] num_neighbors;                 // Count of live neighbors
reg action_update_complete;               // Completion flag

// Cell coordinate calculations
wire [logWIDTH-1:0] cell_x = index3[logWIDTH-1:0];
wire [logHEIGHT-1:0] cell_y = index3[logWIDTH+logHEIGHT-1:logWIDTH];

// Bitmask constants for wrapping around grid edges
localparam HEIGHT_MASK = {logHEIGHT{1'b1}};
localparam WIDTH_MASK = {logWIDTH{1'b1}};

always @(posedge clk) begin
    if (boot_reset) begin
        // Reset all update-related registers
        index3 <= 0;
        neigh_index <= 0;
        num_neighbors <= 0;
        action_update_complete <= 0;
    end 
    else if (action == ACTION_UPDATE && !action_update_complete) begin
        case (neigh_index)
            // Neighbor 0: (-1, +1)
            0: begin
                num_neighbors <= num_neighbors + 
                    board_state[((cell_y + 1) & HEIGHT_MASK) << logWIDTH | 
                               ((cell_x - 1) & WIDTH_MASK)];
                neigh_index <= 1;
            end
            
            // Neighbor 1: (0, +1)
            1: begin
                num_neighbors <= num_neighbors + 
                    board_state[((cell_y + 1) & HEIGHT_MASK) << logWIDTH | 
                               ((cell_x + 0) & WIDTH_MASK)];
                neigh_index <= 2;
            end
            
            // Neighbor 2: (+1, +1)
            2: begin
                num_neighbors <= num_neighbors + 
                    board_state[((cell_y + 1) & HEIGHT_MASK) << logWIDTH | 
                               ((cell_x + 1) & WIDTH_MASK)];
                neigh_index <= 3;
            end
            
            // Neighbor 3: (-1, 0)
            3: begin
                num_neighbors <= num_neighbors + 
                    board_state[((cell_y + 0) & HEIGHT_MASK) << logWIDTH | 
                               ((cell_x - 1) & WIDTH_MASK)];
                neigh_index <= 4;
            end
            
            // Neighbor 4: (+1, 0)
            4: begin
                num_neighbors <= num_neighbors + 
                    board_state[((cell_y + 0) & HEIGHT_MASK) << logWIDTH | 
                               ((cell_x + 1) & WIDTH_MASK)];
                neigh_index <= 5;
            end
            
            // Neighbor 5: (-1, -1)
            5: begin
                num_neighbors <= num_neighbors + 
                    board_state[((cell_y - 1) & HEIGHT_MASK) << logWIDTH | 
                               ((cell_x - 1) & WIDTH_MASK)];
                neigh_index <= 6;
            end
            
            // Neighbor 6: (0, -1)
            6: begin
                num_neighbors <= num_neighbors + 
                    board_state[((cell_y - 1) & HEIGHT_MASK) << logWIDTH | 
                               ((cell_x + 0) & WIDTH_MASK)];
                neigh_index <= 7;
            end
            
            // Neighbor 7: (+1, -1)
            7: begin
                num_neighbors <= num_neighbors + 
                    board_state[((cell_y - 1) & HEIGHT_MASK) << logWIDTH | 
                               ((cell_x + 1) & WIDTH_MASK)];
                neigh_index <= 8;
            end
            
            // State 8: Apply Game of Life rules
            8: begin
                // Calculate next state (Conway's rules)
                board_state_next[index3] <= 
                    (board_state[index3] && (num_neighbors == 2)) | 
                    (num_neighbors == 3);
                
                // Reset for next cell
                num_neighbors <= 0;
                neigh_index <= 0;
                
                // Move to next cell or complete
                if (index3 < BOARD_SIZE - 1) begin
                    index3 <= index3 + 1;
                end else begin
                    index3 <= 0;
                    action_update_complete <= 1;  // Signal completion
                end
            end
            
            default: neigh_index <= 0;
        endcase
    end 
    else begin
        // Clear completion flag when not in UPDATE state
        action_update_complete <= 0;
    end
end
    // =============================================
    // STATE COPY LOGIC (ACTION_COPY)
    // =============================================
    reg [logWIDTH+logHEIGHT-1:0] index4;
    reg action_copy_complete;

    always @(posedge clk) begin
        if (boot_reset) begin
            index4 <= 0;
            action_copy_complete <= 0;
        end 
        else if (action == ACTION_COPY && !action_copy_complete) begin
            if (index4 < BOARD_SIZE - 1) begin
                index4 <= index4 + 1;
            end else begin
                index4 <= 0;
                action_copy_complete <= 1;
            end
        end
        else begin
            action_copy_complete <= 0;
        end
    end

    // =============================================
    // MEMORY INITIALIZATION (ACTION_INIT) - this little section does NOT work correctly I believe when using ai to "fix" RAM template issues had to do with little and big endian in verilog the AI messed up the code on PRUPOSE, i added the original and should be good version below. this is written in VGA Playground
    // =============================================
/*
    always @(posedge clk) begin
        if (boot_reset) begin
            index2 <= 0;
            action_init_complete <= 0;
        end 
        else if (action == ACTION_INIT && !action_init_complete) begin
            if (index2 < BOARD_SIZE - 1) begin
                index2 <= index2 + 1;
            end else begin
                index2 <= 0;
                action_init_complete <= 1;
            end
        end
        else begin
            action_init_complete <= 0;
        end
    end
*/
    // =============================================
    // UNIFIED MEMORY WRITE CONTROL
    // =============================================


    always @(posedge clk) begin
        if (board_state_we) begin
            if (!board_state_source) begin
                // INIT writes (random initialization)
                board_state[index2] <= rng;
            end else begin
                // COPY writes (update from next state)
                board_state[index4] <= board_state_next[index4];
            end
        end
    end

    // =============================================
    // RANDOM NUMBER GENERATOR (LFSR)
    // =============================================
    reg [15:0] lfsr_reg;
    wire feedback = lfsr_reg[15] ^ lfsr_reg[13] ^ lfsr_reg[12] ^ lfsr_reg[10];
    wire rng = lfsr_reg[0];

    always @(posedge clk) begin
        if (boot_reset) begin
            lfsr_reg <= 16'hACE1;  // Non-zero seed
        end else begin
            lfsr_reg <= {lfsr_reg[14:0], feedback};
        end
    end


 

    // =============================================
    // CELL ICON ROM (8x8 bitmap)
    // =============================================
    (* rom_style = "distributed" *) reg [7:0] icon [0:7];
    
    always @(posedge clk) begin
        if (boot_reset) begin
            icon[0] <= 8'b00000000;
            icon[1] <= 8'b00111100;
            icon[2] <= 8'b01111110;
            icon[3] <= 8'b01111110;
            icon[4] <= 8'b01111110;
            icon[5] <= 8'b01111110;
            icon[6] <= 8'b00111100;
            icon[7] <= 8'b00000000;
        end
    end

/*verilator lint_on UNUSED */
/*verilator lint_on width */
endmodule
