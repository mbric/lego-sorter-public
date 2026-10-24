#!/usr/bin/env python3
"""One-shot dump into a taught bin (or park the arm).

  python3 run_robot_dump_test.py red
  python3 run_robot_dump_test.py --park

Close iRC first. Teach poses with run_robot_teach.py before using this.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from robot.config import RobotConfig
from robot.cri_session import log_state, prepare_for_motion, release
from robot.dump import dump_cycle, park_and_release
from robot.poses import load_poses

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "bin_id",
        nargs="?",
        help="Bin to dump into (e.g. red). Omit with --park.",
    )
    parser.add_argument(
        "--park",
        action="store_true",
        help="Move to rest (or home) and disable drives; no dump.",
    )
    parser.add_argument(
        "--park-after",
        action="store_true",
        help="After a dump, park at rest before disable.",
    )
    parser.add_argument(
        "--vel",
        type=float,
        default=None,
        help="Joint velocity percent (default from poses.json).",
    )
    args = parser.parse_args(argv)

    if not args.park and not args.bin_id:
        parser.error("provide a bin_id, or use --park")

    try:
        from cri_lib import CRIController
    except ImportError:
        logger.error("cri_lib not installed. Run: pip install -r requirements-robot.txt")
        return 1

    poses = load_poses()
    cfg = RobotConfig.from_env()
    controller = CRIController()
    exit_code = 1
    released = False

    try:
        try:
            connected = controller.connect(cfg.host, cfg.port)
        except Exception as exc:
            raise RuntimeError(f"connect failed ({exc})") from exc
        if not connected:
            raise RuntimeError("connect failed")
        time.sleep(1)
        log_state(controller, "connected")
        prepare_for_motion(controller)
        controller.set_override(poses.defaults.override_pct)
        vel = poses.defaults.joint_vel_pct if args.vel is None else args.vel

        if args.park:
            park_and_release(controller, poses, velocity_pct=vel)
            released = True
            exit_code = 0
            return exit_code

        assert args.bin_id is not None
        dump_cycle(
            controller,
            args.bin_id,
            poses,
            velocity_pct=vel,
            return_home=True,
        )
        if args.park_after:
            park_and_release(controller, poses, velocity_pct=vel)
            released = True
        exit_code = 0
    except (RuntimeError, KeyError) as exc:
        logger.error("%s", exc)
    finally:
        if not released:
            try:
                if getattr(controller, "connected", False):
                    release(controller)
            except Exception:
                pass
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
