// Flat chain-belt feeder (schematic).
include <../config.scad>

module conveyor() {
  rotate([0, -belt_incline, 0]) {
    color("#2a2d34")
      translate([0, 0, belt_h / 2])
        cube([belt_l + 20, belt_w + 16, belt_h], center=true);

    color("#1b1e24")
      for (s = [-1, 1])
        translate([0, s * (belt_w / 2 + 6), belt_h - 2])
          cube([belt_l + 24, 8, 14], center=true);

    color("#f5f5f5")
      for (i = [0 : floor(belt_l / 12) - 1])
        translate([-belt_l / 2 + 8 + i * 12, 0, belt_h + 2])
          cube([10, belt_w - 4, 4], center=true);

    color("#e0e0e0")
      for (x = [-belt_l / 2 + 30 : cleat_pitch : belt_l / 2 - 20])
        translate([x, 0, belt_h + 2 + cleat_h / 2])
          cube([4, belt_w - 10, cleat_h], center=true);

    color("#111111")
      translate([belt_l / 2 - 24, belt_w / 2 + 32, belt_h / 2])
        cube([42, 42, 42], center=true);
    color("#cccccc")
      translate([belt_l / 2 - 24, belt_w / 2 + 10, belt_h / 2])
        rotate([90, 0, 0])
          cylinder(h=12, r=10);

    // Support bents: paired legs, head beams, stringers and diagonal braces
    leg_y = belt_w / 2 + 10;
    bent_x = [-belt_l / 2 + 45, belt_l / 2 - 45];
    span = bent_x[1] - bent_x[0];

    for (x = bent_x) {
      for (y = [-leg_y, leg_y]) {
        color("#2a2d34")
          translate([x, y, -belt_stand_h / 2])
            cube([20, 20, belt_stand_h], center=true);
        color("#37474f")
          translate([x, y, -belt_stand_h + 4])
            cube([46, 46, 8], center=true);
      }
      color("#2a2d34")
        translate([x, 0, -10])
          cube([20, leg_y * 2 + 20, 14], center=true);
      color("#546e7a")
        translate([x, 0, -belt_stand_h * 0.55])
          cube([12, leg_y * 2, 10], center=true);
    }

    color("#546e7a")
      for (y = [-leg_y, leg_y]) {
        translate([0, y, -belt_stand_h * 0.55])
          cube([span, 12, 12], center=true);
        for (dir = [1, -1])
          translate([0, y, -belt_stand_h * 0.32])
            rotate([0, dir * atan2(belt_stand_h * 0.45, span), 0])
              cube([sqrt(span * span + pow(belt_stand_h * 0.45, 2)), 9, 9], center=true);
      }

    color("#c62828") translate([-belt_l / 2 + 80, -8, belt_h + 10]) cube([16, 10, 8], center=true);
    color("#1565c0") translate([-belt_l / 2 + 150, 0, belt_h + 10]) cube([16, 10, 8], center=true);
    color("#f9a825") translate([-belt_l / 2 + 220, 8, belt_h + 10]) cube([16, 10, 8], center=true);
  }
}
