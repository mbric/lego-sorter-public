"""Robot dump stub — swap for real CRI dump later."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

DEFAULT_STUB_DELAY_S = 0.3


def dump_to_bin(bin_id: str, *, delay_s: float = DEFAULT_STUB_DELAY_S) -> None:
    """Placeholder for ReBeL home → bin → dump → home.

    Use when the arm is offline (e.g. Gradio feed-loop demos).
    On hardware, prefer ``robot.dump.dump_to_bin`` / ``dump_cycle`` after
    teaching poses with ``run_robot_teach.py``.
    """
    logger.info("dump_stub: would dump to bin=%s", bin_id)
    if delay_s > 0:
        time.sleep(delay_s)
