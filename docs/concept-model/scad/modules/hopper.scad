// Large cascading rotary hoppers (basically V2 scale vs 6" light box).
include <../config.scad>

module rotary_bowl(r=100, wall_h=46, cone_h=34, cutout=hopper_cutout, wall_t=hopper_wall_t, leg_h=0) {
  color("#1a1a1a")
  difference() {
    cylinder(h=wall_h, r=r);
    translate([0, 0, -0.1])
      cylinder(h=wall_h + 0.2, r=r - wall_t);
    rotate([0, 0, -cutout / 2])
      translate([0, 0, -0.1])
        linear_extrude(height=wall_h + 0.2)
          polygon([
            [0, 0],
            [r * 2, 0],
            [r * 2 * cos(cutout), r * 2 * sin(cutout)],
          ]);
  }

  if (cone_h > 0) {
    color("#f2f2f2")
      translate([0, 0, 3])
        cylinder(h=cone_h, r1=r - 5, r2=10);
  } else {
    color("#f2f2f2")
      translate([0, 0, 2])
        cylinder(h=6, r=r - 5);
  }

  cap_z = cone_h > 0 ? cone_h + 8 : wall_h * 0.65;
  color("#111111")
    translate([0, 0, cap_z])
      cylinder(h=16, r1=22, r2=10);

  color("#1a1a1a")
    translate([(r - 12) / 2 + 8, 0, cap_z - 2])
      cube([r - 14, 10, 10], center=true);

  color("#101010")
    translate([0, r + 30, 20])
      cube([42, 42, 42], center=true);

  if (leg_h > 0) {
    color("#1d1d1d") {
      translate([0, 0, -leg_h / 2])
        cube([28, 20, leg_h], center=true);
      translate([0, 0, -leg_h + 3])
        cube([80, 70, 6], center=true);
    }
  }
}

module hopper_cascade() {
  // Compact Z-stacked triangle — posts cluster, exits still overhang
  translate([40, 120, 520])
    rotate([0, 0, -90])
      rotary_bowl(r=180, wall_h=160, cone_h=0, leg_h=520);

  translate([50, -130, 415])
    rotate([0, 0, 36])
      rotary_bowl(r=150, wall_h=70, cone_h=55, leg_h=415);

  translate([230, 10, 310])
    rotary_bowl(r=130, wall_h=60, cone_h=48, leg_h=310);

  color("#2b2b2b")
    translate([330, 10, 285])
      rotate([0, -17, 0])
        cube([70, belt_w + 12, 6], center=true);

  // Shared deck under the clustered posts
  color("#2a2a2a")
    translate([80, 0, 4])
      cube([220, 320, 8], center=true);
  color("#1a1a1a")
    translate([80, 0, 9])
      cube([200, 300, 6], center=true);
}
