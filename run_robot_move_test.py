#!/usr/bin/env python3
"""Move the ReBeL up/down in Z — skips referencing if already homed.

Pass --reference to force encoder referencing first (use after power-on).

Each run still enables drives (you may hear clicks) because we disable on exit.
Override size/speed: ROBOT_MOVE_DZ_MM=30 ROBOT_MOVE_VEL_MM_S=15

Close iRC first (one CRI client only).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from robot.config import RobotConfig
from robot.cri_session import (
    log_state,
    move_and_wait_z,
    prepare_for_motion,
    release,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DZ_MM = float(os.getenv("ROBOT_MOVE_DZ_MM", "25"))
VEL_MM_S = float(os.getenv("ROBOT_MOVE_VEL_MM_S", "15"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        action="store_true",
        help="Force encoder referencing first (use after power-on)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        from cri_lib import CRIController
    except ImportError:
        logger.error("cri_lib not installed. Run: pip install -r requirements-robot.txt")
        return 1

    cfg = RobotConfig.from_env()
    c = CRIController()
    exit_code = 1

    try:
        logger.info("Target: %s:%s  dz=%.0f mm  vel=%.0f mm/s", cfg.host, cfg.port, DZ_MM, VEL_MM_S)
        if not c.connect(cfg.host, cfg.port):
            raise RuntimeError("connect failed")
        time.sleep(1)
        log_state(c, "connected")

        prepare_for_motion(c, force_reference=args.reference)

        z0 = c.robot_state.position_robot.Z
        logger.info("Moving +%.1f mm Z...", DZ_MM)
        if not move_and_wait_z(c, DZ_MM, VEL_MM_S):
            raise RuntimeError("+Z move failed")

        logger.info("Moving -%.1f mm Z...", DZ_MM)
        if not move_and_wait_z(c, -DZ_MM, VEL_MM_S):
            raise RuntimeError("-Z move failed")

        z1 = c.robot_state.position_robot.Z
        logger.info("Done. Z start=%.1f end=%.1f delta=%.1f", z0, z1, z1 - z0)
        exit_code = 0
    except RuntimeError as exc:
        logger.error("%s", exc)
    finally:
        try:
            release(c)
        except Exception:
            pass

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
