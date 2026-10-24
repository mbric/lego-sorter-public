#!/usr/bin/env python3
"""Launch the Gradio ReBeL pose teacher (visual nudge + save).

  source .venv/bin/activate
  pip install -r requirements.txt -r requirements-robot.txt
  export ROBOT_IP=192.168.3.11
  python3 run_robot_teach_ui.py

Close iRC first. Keep E-stop handy.
"""

from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    try:
        import gradio  # noqa: F401
    except ImportError:
        logger.error("gradio missing — pip install -r requirements.txt")
        return 1
    try:
        import cri_lib  # noqa: F401
    except ImportError:
        logger.error("cri_lib missing — pip install -r requirements-robot.txt")
        return 1

    from robot.teach_app import build_app

    demo = build_app()
    logger.info("Opening ReBeL pose teacher — close iRC first")
    demo.launch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
