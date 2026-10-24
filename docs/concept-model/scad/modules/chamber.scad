// Compact ~6" light box with dual-direction tilt plate (schematic).
// tip_mode: 0 = level, 1 = tip toward scoop (+Y in SCAD / +Z in Three),
//          -1 = tip toward unidentified bin.
include <../config.scad>

module scan_chamber(tip_mode=0) {
  s = chamber_size;
  w = chamber_wall;
  h = chamber_h;
  half = s / 2;
  tip = tip_mode * 32;
  exit_h = 48;
  upper_h = h - exit_h;
  slot_w = 56;
  notch_w = belt_w + 16;
  side_strip = (s - notch_w) / 2;

  // +X full wall
  color("#f7f7f7")
    translate([half - w / 2, 0, h / 2])
      cube([w, s, h], center=true);

  // −X side pillars + low sill (feed notch open, no mid ledge)
  color("#f7f7f7") {
    translate([-half + w / 2, -(notch_w / 2 + side_strip / 2), h / 2])
      cube([w, side_strip, h], center=true);
    translate([-half + w / 2, notch_w / 2 + side_strip / 2, h / 2])
      cube([w, side_strip, h], center=true);
    translate([-half + w / 2, 0, 9])
      cube([w, notch_w, 18], center=true);
  }

  // ±Y faces full width so corners meet the ±X walls
  color("#cfd8dc", 0.35)
    translate([0, half - w / 2, exit_h + upper_h / 2])
      cube([s, w, upper_h], center=true);
  color("#f7f7f7")
    translate([0, -half + w / 2, exit_h + upper_h / 2])
      cube([s, w, upper_h], center=true);

  // Lid with open feed slot on −X
  lid_main = s - slot_w;
  color("#f7f7f7") {
    translate([half - lid_main / 2, 0, h - w / 2])
      cube([lid_main, s, w], center=true);
    for (ys = [-1, 1])
      translate([-half + slot_w / 2, ys * (notch_w / 2 + side_strip / 2), h - w / 2])
        cube([slot_w, side_strip, w], center=true);
  }

  // Open feed chute (floor + side guides)
  color("#90a4ae")
    translate([-half - 10, 0, h + 18])
      rotate([0, -32, 0])
        cube([78, notch_w - 8, 3], center=true);
  color("#78909c")
    for (ys = [-1, 1])
      translate([-half - 10, ys * ((notch_w - 8) / 2 + 1.5), h + 28])
        rotate([0, -32, 0])
          cube([78, 3, 18], center=true);

  floor_w = s - 2 * w - 12;
  floor_d = s - 2 * w - 8;

  color("#d0d0d0")
    translate([0, 0, exit_h - 6])
      rotate([tip, 0, 0]) {
        translate([0, 0, 2])
          cube([floor_w, floor_d, 4], center=true);
        translate([0, -floor_d / 2 + 2, 7])
          cube([floor_w, 3, 8], center=true);
        translate([0, floor_d / 2 - 2, 7])
          cube([floor_w, 3, 8], center=true);
        color("#607d8b")
          rotate([0, 90, 0])
            cylinder(h=floor_w + 8, r=4, center=true);
      }

  color("#b0bec5")
    translate([0, half + 12, exit_h - 14])
      rotate([23, 0, 0])
        cube([floor_w - 8, 30, 3], center=true);
  color("#90a4ae")
    translate([0, -half - 12, exit_h - 12])
      rotate([-16, 0, 0])
        cube([floor_w - 8, 32, 3], center=true);

  color("#455a64")
    translate([0, -half - 70, -18])
      cube([110, 90, 4], center=true);
  color("#546e7a") {
    translate([-53, -half - 70, 8]) cube([4, 90, 55], center=true);
    translate([53, -half - 70, 8]) cube([4, 90, 55], center=true);
    translate([0, -half - 113, 8]) cube([110, 4, 55], center=true);
  }
  color("#ff8f00")
    translate([0, -half - 27, 32])
      cube([110, 4, 12], center=true);

  color("#212121") {
    translate([18, 0, h - 28]) cube([22, 18, 14], center=true);
    translate([25, half - 28, h - 50])
      rotate([-45, 0, 0])
        cube([22, 18, 14], center=true);
  }

  color("#fff8e1")
    for (y = [-half + 18, half - 18])
      translate([8, y, h - 22])
        cube([s - 40, 6, 5], center=true);

  color("#455a64")
    translate([0, -8, -4])
      cube([s + 20, s + 40, 8], center=true);
  color("#37474f")
    for (x = [-half + 10, half - 10])
      for (y = [-half + 10, half - 10])
        translate([x, y, -44])
          cube([18, 18, 80], center=true);

  color("#1565c0")
    translate([0, 0, exit_h + 8])
      cube([12, 9, 7], center=true);
}
