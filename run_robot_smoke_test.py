#!/usr/bin/env python3
"""Verify Mac → ReBeL CRI connectivity. Close iRC before running (one client only)."""

from __future__ import annotations

import logging
import socket
import sys
from time import sleep

from robot.config import RobotConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as exc:
        logger.error("TCP %s:%s unreachable: %s", host, port, exc)
        return False


def main() -> int:
    cfg = RobotConfig.from_env()
    logger.info("Target robot: %s:%s", cfg.host, cfg.port)

    if not tcp_reachable(cfg.host, cfg.port):
        logger.error(
            "Port 3920 closed. Check robot power, network, and that iRC is disconnected."
        )
        return 1

    try:
        from cri_lib import CRIController
    except ImportError:
        logger.error("cri_lib not installed. Run: pip install -r requirements-robot.txt")
        return 1

    controller = CRIController()
    if not controller.connect(cfg.host, cfg.port):
        logger.error("CRI connect failed.")
        return 1

    logger.info("Connected. Reading joint setpoints for 3s...")
    for _ in range(6):
        sleep(0.5)
        j = controller.robot_state.joints_set_point
        logger.info(
            "A1=%.2f A2=%.2f A3=%.2f A4=%.2f A5=%.2f A6=%.2f",
            j.A1,
            j.A2,
            j.A3,
            j.A4,
            j.A5,
            j.A6,
        )

    controller.close()
    logger.info("Smoke test OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
