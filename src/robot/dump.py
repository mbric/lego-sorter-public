"""Real ReBeL dump cycle: home → approach → dump → approach → home."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from cri_lib import CRIController

from .config import RobotConfig
from .cri_session import (
    log_state,
    move_joints_and_wait,
    prepare_for_motion,
    release,
)
from .poses import Pose, PoseSet, load_poses

logger = logging.getLogger(__name__)


def _goto(
    controller: CRIController,
    pose: Pose,
    *,
    label: str,
    velocity_pct: float,
    tol_deg: float,
) -> None:
    logger.info("→ %s", label)
    if not move_joints_and_wait(
        controller,
        pose,
        velocity_pct=velocity_pct,
        tol_deg=tol_deg,
    ):
        raise RuntimeError(f"move to {label} failed")


def dump_cycle(
    controller: CRIController,
    bin_id: str,
    poses: PoseSet | None = None,
    *,
    velocity_pct: float | None = None,
    hold_s: float | None = None,
    return_home: bool = True,
) -> None:
    """Execute taught dump sequence for ``bin_id`` (controller already prepared)."""
    poses = poses or load_poses()
    if poses.home is None:
        raise RuntimeError("home pose not taught — run run_robot_teach.py")
    bin_poses = poses.require_bin(bin_id)
    assert bin_poses.approach is not None and bin_poses.dump is not None

    vel = poses.defaults.joint_vel_pct if velocity_pct is None else velocity_pct
    hold = poses.defaults.dump_hold_s if hold_s is None else hold_s
    tol = poses.defaults.settle_tol_deg

    _goto(
        controller,
        poses.home,
        label="home",
        velocity_pct=vel,
        tol_deg=tol,
    )
    _goto(
        controller,
        bin_poses.approach,
        label=f"{bin_id}/approach",
        velocity_pct=vel,
        tol_deg=tol,
    )
    _goto(
        controller,
        bin_poses.dump,
        label=f"{bin_id}/dump",
        velocity_pct=vel,
        tol_deg=tol,
    )
    if hold > 0:
        time.sleep(hold)
    _goto(
        controller,
        bin_poses.approach,
        label=f"{bin_id}/approach (retreat)",
        velocity_pct=vel,
        tol_deg=tol,
    )
    if return_home:
        _goto(
            controller,
            poses.home,
            label="home",
            velocity_pct=vel,
            tol_deg=tol,
        )
    logger.info("Dump to bin=%s complete", bin_id)


def go_home(
    controller: CRIController,
    poses: PoseSet | None = None,
    *,
    velocity_pct: float | None = None,
) -> None:
    poses = poses or load_poses()
    if poses.home is None:
        raise RuntimeError("home pose not taught")
    vel = poses.defaults.joint_vel_pct if velocity_pct is None else velocity_pct
    _goto(
        controller,
        poses.home,
        label="home",
        velocity_pct=vel,
        tol_deg=poses.defaults.settle_tol_deg,
    )


def go_rest(
    controller: CRIController,
    poses: PoseSet | None = None,
    *,
    velocity_pct: float | None = None,
) -> None:
    """Move to the taught idle/park pose (clear of bins and furniture)."""
    poses = poses or load_poses()
    if poses.rest is None:
        raise RuntimeError(
            "rest pose not taught — teach a clear parked pose with "
            "`save rest` in run_robot_teach.py"
        )
    vel = poses.defaults.joint_vel_pct if velocity_pct is None else velocity_pct
    _goto(
        controller,
        poses.rest,
        label="rest (park)",
        velocity_pct=vel,
        tol_deg=poses.defaults.settle_tol_deg,
    )


def park_and_release(
    controller: CRIController,
    poses: PoseSet | None = None,
    *,
    velocity_pct: float | None = None,
) -> None:
    """Park at rest if taught, then disable drives (brakes on)."""
    poses = poses or load_poses()
    try:
        if poses.rest is not None:
            go_rest(controller, poses, velocity_pct=velocity_pct)
        elif poses.home is not None:
            logger.warning(
                "No rest pose taught — returning to home before disable. "
                "Teach `rest` so the arm parks clear of the finished space."
            )
            go_home(controller, poses, velocity_pct=velocity_pct)
        else:
            logger.warning("No rest/home pose — disabling in place")
    except RuntimeError as exc:
        logger.error("Park move failed (%s) — disabling in place", exc)
    release(controller)


def dump_to_bin(
    bin_id: str,
    *,
    poses_path: Path | None = None,
    velocity_pct: float | None = None,
    hold_s: float | None = None,
    park_after: bool = False,
) -> None:
    """Connect, dump one part into ``bin_id``, optionally park, then release.

    For offline / Gradio without the arm, keep using ``dump_stub.dump_to_bin``.
    """
    poses = load_poses(poses_path)
    cfg = RobotConfig.from_env()
    controller = CRIController()
    try:
        logger.info("Dump session → bin=%s @ %s:%s", bin_id, cfg.host, cfg.port)
        try:
            connected = controller.connect(cfg.host, cfg.port)
        except Exception as exc:
            raise RuntimeError(f"connect failed ({exc})") from exc
        if not connected:
            raise RuntimeError("connect failed")
        time.sleep(1)
        log_state(controller, "connected")
        prepare_for_motion(controller)
        if poses.defaults.override_pct > 0:
            controller.set_override(poses.defaults.override_pct)
        dump_cycle(
            controller,
            bin_id,
            poses,
            velocity_pct=velocity_pct,
            hold_s=hold_s,
            return_home=True,
        )
        if park_after:
            park_and_release(controller, poses, velocity_pct=velocity_pct)
            return
    finally:
        # If park_and_release already closed, release is a no-op best-effort.
        try:
            if getattr(controller, "connected", False):
                release(controller)
        except Exception:
            pass
