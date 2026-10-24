#!/usr/bin/env python3
"""Read-only ReBeL status over CRI. Safe to run anytime (no motion)."""

from __future__ import annotations

import logging
import sys
from time import sleep

from robot.config import RobotConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def log_state(controller, label: str) -> None:
    s = controller.robot_state
    j = s.joints_current
    p = s.position_robot
    logger.info("--- %s ---", label)
    logger.info("operation_mode: %s", s.operation_mode.name)
    logger.info("kinematics_state: %s", s.kinematics_state.name)
    logger.info("combined_axes_error: %s", s.combined_axes_error)
    logger.info("referencing: %s", s.referencing_state.global_state.name)
    logger.info("ref_prog_running: %s", s.referencing_state.ref_prog_running)
    logger.info("emergency_stop_ok: %s", s.emergency_stop_ok)
    logger.info(
        "joints A1-A6: %.2f %.2f %.2f %.2f %.2f %.2f",
        j.A1,
        j.A2,
        j.A3,
        j.A4,
        j.A5,
        j.A6,
    )
    logger.info("TCP X/Y/Z: %.1f / %.1f / %.1f mm", p.X, p.Y, p.Z)


def main() -> int:
    try:
        from cri_lib import CRIController
    except ImportError:
        logger.error("cri_lib not installed. Run: pip install -r requirements-robot.txt")
        return 1

    cfg = RobotConfig.from_env()
    controller = CRIController()
    if not controller.connect(cfg.host, cfg.port):
        logger.error("CRI connect failed for %s:%s", cfg.host, cfg.port)
        return 1

    sleep(1)
    log_state(controller, "robot status")
    controller.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
