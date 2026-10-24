// Compact feed line; hoppers are large; bins stack vertically.
include <config.scad>
use <modules/hopper.scad>
use <modules/conveyor.scad>
use <modules/chamber.scad>
use <modules/rebel.scad>
use <modules/bins.scad>

tip_stage_mode = 0; // 0 level, 1 → scoop, -1 → unidentified

module tabletop() {
  color("#e8e4dc")
    translate([120, 280, -11])
      cube([1600, 1300, 22], center=true);
}

module assembly() {
  if (show_table) tabletop();

  if (show_hopper)
    translate([-450, 0, 0])
      hopper_cascade();

  if (show_conveyor)
    translate([60, 0, belt_stand_h])
      conveyor();

  if (show_chamber)
    translate([320, 0, 80])
      scan_chamber(tip_mode=tip_stage_mode);

  if (show_rebel)
    translate([320, 280, 0])
      rotate([0, 0, -90])
        rebel_arm();

  if (show_bins)
    translate([320, 280, 0])
      sorting_bins();
}

assembly();
