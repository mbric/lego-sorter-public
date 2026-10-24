// Vertical bin stacks (3 levels × 4 columns) in an arc around the arm.
include <../config.scad>

// [col][level] colors
stack_colors = [
  ["#c62828", "#6a1b9a", "#78909c"], // col 0: red / set A / unknown
  ["#1565c0", "#00838f", "#5d4037"], // col 1: blue / set B / misc
  ["#f9a825", "#ef6c00", "#455a64"], // col 2: yellow / plates / overflow
  ["#2e7d32", "#546e7a", "#37474f"], // col 3: green / technic / reject
];

module bin_cell(col) {
  color(col)
    translate([0, 0, bin_h / 2])
      cube([bin_w, bin_d, bin_h], center=true);
  color("#263238")
    translate([0, 0, bin_h / 2 + 4])
      cube([bin_w - 8, bin_d - 8, bin_h - 8], center=true);
  color("#ffffff")
    translate([0, bin_d / 2 + 1, bin_h - 7])
      cube([bin_w - 14, 2, 9], center=true);
  color("#455a64")
    translate([0, 0, -2])
      cube([bin_w + 8, bin_d + 10, 4], center=true);
}

module sorting_bins() {
  for (c = [0 : bin_cols - 1]) {
    t = bin_cols == 1 ? 0.5 : c / (bin_cols - 1);
    ang = bin_arc_start + t * (bin_arc_end - bin_arc_start);

    // Upright
    stack_h = bin_levels * (bin_h + bin_shelf_gap) + 20;
    color("#37474f")
      translate([
        sin(ang) * (bin_arc_r - bin_d / 2 - 15),
        cos(ang) * (bin_arc_r - bin_d / 2 - 15),
        stack_h / 2,
      ])
        cube([8, 8, stack_h], center=true);

    for (lv = [0 : bin_levels - 1]) {
      translate([sin(ang) * bin_arc_r, cos(ang) * bin_arc_r, lv * (bin_h + bin_shelf_gap)])
        rotate([0, 0, ang + 180])
          bin_cell(stack_colors[c][lv]);
    }
  }
}
