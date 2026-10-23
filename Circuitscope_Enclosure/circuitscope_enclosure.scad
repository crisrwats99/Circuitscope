// Circuitscope enclosure - three slide-in board shelves, LCD + terminals on the lid.
// Units: mm.  x = left/right, y = front (0) -> back, z = up.
// Print: base upright (no supports), lid top-face-down, back panel flat.
// MEASURE your LCD and veroboard and adjust the parameters marked (CHECK).

part = "all";        // "base", "lid", "panel", or "all" (assembled preview)

// ---------------------------------------------------------------- boards
board_w     = 100.0;  // veroboard width (CHECK)
board_t     = 1.6;    // veroboard thickness (CHECK)
grip        = 1.5;    // how far each board edge sits inside its groove
groove_h    = 2.6;    // groove height (board_t + clearance)
groove_d    = 2.0;    // groove depth into the wall
front_gap   = 12;     // wiring space in front of the boards (boards stop here)
back_gap    = 14;     // wiring space behind the longest board
longest     = 112;    // voltage board length
levels      = [6, 27, 47];  // board underside heights: main, voltage, current (bottom -> top)

// ---------------------------------------------------------------- box
wall        = 4.0;    // side walls (thick enough for the grooves)
fwall       = 3.0;    // front wall
floor_t     = 3.0;
inner_h     = 82;     // floor top -> lid underside
in_half     = board_w / 2 - grip;           // inner face x of side walls
inner_d     = front_gap + longest + back_gap;
panel_t     = 2.0;    // back panel thickness
panel_y     = inner_d + 1;                  // back panel slot position
outer_w     = 2 * (in_half + wall);
outer_d     = fwall + inner_d + 1 + panel_t + 2;
boss        = 8;      // lid-screw boss size
screw_d     = 2.6;    // pilot hole for M3 self-tapping screws

// ---------------------------------------------------------------- USB (main board, strip-1 edge = left wall)
usb_y       = fwall + front_gap + 38.7;     // Nucleo USB position along the board
usb_z       = floor_t + levels[0] + 13.5;   // receptacle centre height (female headers ~8.5 mm)
usb_w       = 14;  usb_h = 10;

// ---------------------------------------------------------------- lid
lid_t       = 3.0;
lcd_win     = [72.0, 25.0];  // bezel cut-out (16x2 bezel ~71.2 x 24.2) (CHECK)
lcd_holes   = [75.0, 31.0];  // PCB mounting-hole spacing (CHECK)
lcd_stand   = 4.0;           // bezel height (7) - lid thickness (3)
lcd_peg_d   = 2.3;           // fits the 2.5-3 mm PCB holes
lcd_y       = 98;            // LCD centre from the front edge of the lid
term_y      = 26;            // terminal row from the front edge of the lid
term_hole   = 1.4;           // square 0.64 mm pin passes, plastic collar does not
// terminal x positions (relative to centre), all on the 2.54 mm grid
names = ["P1","P2","P3","P4","P5","COM","I1+","I1-","I2+","I2-"];
xs    = [0, 7.62, 15.24, 22.86, 30.48, 43.18, 55.88, 63.5, 76.2, 83.82];
term_off = -41.91;
tb_screw_x = 36.83;          // terminal-board screw bosses (x = +/-), behind the pin row
tb_screw_dy = 10.16;         // screw bosses this far behind the pins
tb_stand   = 2.5;            // header plastic collar height

$fn = 40;

// ================================================================= base
module base() {
    difference() {
        union() {
            // outer shell
            difference() {
                translate([-outer_w/2, 0, 0]) cube([outer_w, outer_d, floor_t + inner_h]);
                translate([-in_half, fwall, floor_t]) cube([2*in_half, outer_d, inner_h + 1]);
            }
            // lid-screw bosses in the top corners, with 45-degree supports underneath
            for (sx = [-1, 1], yy = [fwall, panel_y - 1 - boss])
                hull() {
                    translate([sx > 0 ? in_half - boss : -in_half, yy, floor_t + inner_h - 10])
                        cube([boss, boss, 10]);
                    translate([sx > 0 ? in_half - 0.1 : -in_half, yy, floor_t + inner_h - 10 - boss])
                        cube([0.1, boss, 0.1]);
                }
            // back stops for the back panel slot at the floor
        }
        // board grooves in both side walls, from the board stop to the open back
        for (z = levels, sx = [-1, 1])
            translate([sx > 0 ? in_half - 0.01 : -in_half - groove_d,
                       fwall + front_gap, floor_t + z - (groove_h - board_t)/2])
                cube([groove_d + 0.01, outer_d, groove_h]);
        // back panel slots (vertical)
        for (sx = [-1, 1])
            translate([sx > 0 ? in_half - 0.01 : -in_half - groove_d, fwall + panel_y - 0.2, floor_t])
                cube([groove_d + 0.01, panel_t + 0.4, inner_h + 1]);
        // open back behind the panel slot
        translate([-in_half, fwall + panel_y + panel_t + 0.2, floor_t])
            cube([2*in_half, 10, inner_h + 1]);
        // USB cut-out, left wall
        translate([-outer_w/2 - 1, usb_y - usb_w/2, usb_z - usb_h/2])
            cube([wall + 2, usb_w, usb_h]);
        // lid screw holes
        for (sx = [-1, 1], yy = [fwall, panel_y - 1 - boss])
            translate([sx * (in_half - boss/2), yy + boss/2, floor_t + inner_h - 12])
                cylinder(d = screw_d, h = 13);
        // front-face label
        translate([0, 0.6, floor_t + inner_h/2]) rotate([90, 0, 0])
            linear_extrude(1) text("CIRCUITSCOPE", size = 7, halign = "center", valign = "center");
    }
}

// ================================================================= back panel (slides down into the slots)
module panel() {
    pw = 2 * (in_half + groove_d) - 0.6;
    difference() {
        translate([-pw/2, 0, 0]) cube([pw, panel_t, inner_h - 0.5]);
        // wire / cable notches at the bottom corners and centre
        translate([-20, -1, -1]) cube([40, panel_t + 2, 8]);
        // ventilation / label
        translate([0, -0.5, inner_h/2]) rotate([90, 0, 0]) mirror([1, 0, 0])
            linear_extrude(1, center = true) text("BACK", size = 6, halign = "center", valign = "center");
    }
}

// ================================================================= lid
module lid() {
    difference() {
        union() {
            translate([-outer_w/2, 0, 0]) cube([outer_w, outer_d, lid_t]);
            // LCD standoffs with pegs (pegs go through the LCD PCB holes; melt or glue the tips)
            for (sx = [-1, 1], sy = [-1, 1])
                translate([sx * lcd_holes[0]/2, lcd_y + sy * lcd_holes[1]/2, -lcd_stand]) {
                    cylinder(d = 3.6, h = lcd_stand);
                    translate([0, 0, -2.5]) cylinder(d = lcd_peg_d, h = 2.5);
                }
            // terminal board screw bosses
            for (sx = [-1, 1])
                translate([sx * tb_screw_x, term_y + tb_screw_dy, -tb_stand]) cylinder(d = 6, h = tb_stand);
            // locating lip that drops just inside the front and side walls
            difference() {
                translate([-in_half + 0.3, fwall + 0.3, -3]) cube([2*in_half - 0.6, panel_y - 0.6, 3]);
                translate([-in_half + 2.3, fwall + 2.3, -4]) cube([2*in_half - 4.6, panel_y - 4.6, 5]);
                // clear the corner screw bosses
                for (sx = [-1, 1], yy = [fwall, panel_y - 1 - boss])
                    translate([sx > 0 ? in_half - boss - 0.5 : -in_half - 0.5, yy - 0.5, -4])
                        cube([boss + 1, boss + 1, 5]);
            }
        }
        // LCD window
        translate([-lcd_win[0]/2, lcd_y - lcd_win[1]/2, -lcd_stand - 1]) cube([lcd_win[0], lcd_win[1], lcd_t_all()]);
        // terminal pin holes + labels (engraved in the top face)
        for (i = [0 : len(xs) - 1]) {
            translate([term_off + xs[i], term_y, -5]) cylinder(d = term_hole, h = 20);
            translate([term_off + xs[i], term_y + 4.5, lid_t - 0.6])
                linear_extrude(1) text(names[i], size = 3.0, halign = "center", valign = "bottom");
        }
        // group outlines (engraved)
        for (g = [[-1.27 - 2.5, 30.48 + 2.5, "VOLTAGE"], [43.18 - 3, 43.18 + 3, ""], [55.88 - 3, 83.82 + 3, "CURRENT"]])
            translate([term_off + g[0], term_y - 3.5, lid_t - 0.6]) {
                difference() {
                    cube([g[1] - g[0], 12.5, 1]);
                    translate([0.6, 0.6, -1]) cube([g[1] - g[0] - 1.2, 11.3, 3]);
                }
                translate([(g[1] - g[0])/2, -4, 0]) linear_extrude(1)
                    text(g[2], size = 2.6, halign = "center", valign = "bottom");
            }
        // terminal board screw holes
        for (sx = [-1, 1]) translate([sx * tb_screw_x, term_y + tb_screw_dy, -5]) cylinder(d = screw_d, h = 20);
        // lid screw clearance holes (M3) with countersink
        for (sx = [-1, 1], yy = [fwall, panel_y - 1 - boss])
            translate([sx * (in_half - boss/2), yy + boss/2, -5]) {
                cylinder(d = 3.4, h = 20);
                translate([0, 0, 5 + lid_t - 1.7]) cylinder(d1 = 3.4, d2 = 6.6, h = 1.71);
            }
        // title
        translate([0, lcd_y + 23, lid_t - 0.6]) linear_extrude(1)
            text("CIRCUITSCOPE", size = 5, halign = "center", valign = "center");
    }
}
function lcd_t_all() = lcd_stand + lid_t + 2;

// ================================================================= dummy boards for the preview
module boards() {
    lens = [76, 112, 91];
    cols = ["SeaGreen", "Peru", "SteelBlue"];
    for (i = [0 : 2])
        color(cols[i], 0.8) translate([-board_w/2, fwall + front_gap, floor_t + levels[i]])
            cube([board_w, lens[i], board_t]);
}

if (part == "base")  base();
if (part == "lid")   rotate([180, 0, 0]) lid();                       // print top-face down
if (part == "panel") rotate([-90, 0, 0]) panel();
if (part == "all") {
    color("Gainsboro") base();
    boards();
    color("DimGray") translate([0, fwall + panel_y, floor_t]) panel();
    color("LightGray", 0.9) translate([0, 0, floor_t + inner_h + 8]) lid();   // lifted for viewing
}
