// igus ReBeL 6DOF schematic — datasheet scale (not exact CAD kinematics).
// Footprint Ø180 mm, max reach 664 mm, nominal 400 mm.
include <../config.scad>

rebel_base_d = 180;
rebel_base_h = 155;
rebel_upper = 255;
rebel_fore = 270;
rebel_wrist = 95;

module arm_link(len, r) {
  color("#f5f5f5")
    translate([0, 0, len / 2])
      cylinder(h=len, r=r);
  color("#ff6a00")
    translate([0, 0, len])
      cylinder(h=r * 1.3, r=r * 1.1);
}

module scoop_tool() {
  // Cantilevered off the flange along +Z (link axis), open toward −Y.
  scoop_len = 150;
  scoop_w = 100;
  wall_h = 44;
  y0 = 36;

  color("#1a1a1a")
    translate([0, 0, 7])
      cube([40, 40, 14], center=true);

  color("#ff6a00")
    translate([0, 0, 22])
      cylinder(h=22, r=11, center=true);

  color("#b0bec5")
    translate([0, 0, y0 + scoop_len / 2])
      cube([3, scoop_w, scoop_len], center=true);

  color("#90a4ae") {
    translate([0, -wall_h / 2, y0 + 2])
      cube([scoop_w, wall_h, 4], center=true);
    translate([-scoop_w / 2, -wall_h / 2, y0 + scoop_len / 2])
      cube([4, wall_h, scoop_len], center=true);
    translate([scoop_w / 2, -wall_h / 2, y0 + scoop_len / 2])
      cube([4, wall_h, scoop_len], center=true);
  }
}

module rebel_arm() {
  // Base Ø180
  color("#1a1a1a")
    translate([0, 0, rebel_base_h / 2])
      cylinder(h=rebel_base_h, r=rebel_base_d / 2);
  color("#ff6a00")
    translate([0, 0, rebel_base_h - 10])
      cylinder(h=28, r1=rebel_base_d / 2 - 8, r2=rebel_base_d / 2 - 14);

  // Nominal reach ring
  color("#ff6a00", 0.25)
    translate([0, 0, 1])
      rotate_extrude(convexity=4)
        translate([400, 0, 0])
          circle(r=1.5);

  translate([0, 0, rebel_base_h]) {
    color("#f5f5f5")
      translate([0, 0, 22])
        cylinder(h=45, r=55);
    // Compact real-ReBeL pose with scoop below the light-box exit lip.
    // Pitch rotations total -90 degrees so the scoop stays level / open-up.
    translate([0, 0, 45])
      rotate([0, -60, 0]) {
        arm_link(rebel_upper, 42);
        translate([0, 0, rebel_upper])
          rotate([0, -109, 0]) {
            arm_link(rebel_fore, 36);
            translate([0, 0, rebel_fore])
              rotate([0, 79, 0]) {
                arm_link(rebel_wrist, 28);
                translate([0, 0, rebel_wrist])
                  scoop_tool();
              }
          }
      }
  }
}
