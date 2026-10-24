"""Shared CRI connect / enable / reference helpers for ReBeL."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from cri_lib import CRIController
from cri_lib.robot_state import KinematicsState, ReferencingAxisState

if TYPE_CHECKING:
    from .poses import Joints, Pose

logger = logging.getLogger(__name__)


def log_state(controller: CRIController, label: str) -> None:
    s = controller.robot_state
    j = s.joints_current
    p = s.position_robot
    logger.info(
        "%s | op=%s kin=%s ref=%s err=%s Z=%.1f "
        "J[%.1f %.1f %.1f %.1f %.1f %.1f] XYZ[%.0f %.0f %.0f]",
        label,
        s.operation_mode.name,
        s.kinematics_state.name,
        s.referencing_state.global_state.name,
        s.combined_axes_error,
        p.Z,
        j.A1,
        j.A2,
        j.A3,
        j.A4,
        j.A5,
        j.A6,
        p.X,
        p.Y,
        p.Z,
    )


def is_referenced(controller: CRIController) -> bool:
    return (
        controller.robot_state.referencing_state.global_state
        == ReferencingAxisState.REFERENCED
    )


def is_motion_ok(controller: CRIController) -> bool:
    s = controller.robot_state
    return (
        is_referenced(controller)
        and s.kinematics_state == KinematicsState.NO_ERROR
    )


def reference_rebel(controller: CRIController) -> bool:
    """Enable drives and refresh absolute-encoder referencing."""
    if not controller.enable():
        return False
    time.sleep(2)
    if not controller.get_referencing_info():
        return False
    time.sleep(1)
    return is_referenced(controller)


def prepare_for_motion(
    controller: CRIController,
    *,
    force_reference: bool = False,
) -> None:
    """Connect-level setup: active control, enable, reference if needed.

    The enable step releases brakes (clicky sound) — required each session
    because our scripts disable on exit. Referencing is only needed when the
    robot reports NOT_REFERENCED (typically once after power-on).
    """
    if not controller.set_active_control(True):
        raise RuntimeError("failed to acquire active control")

    controller.reset()
    time.sleep(1)

    if force_reference or not is_referenced(controller):
        logger.info("Referencing (GetReferencingInfo)...")
        if not reference_rebel(controller):
            raise RuntimeError("referencing failed")
    else:
        logger.info("Already referenced — skipping GetReferencingInfo.")
        if not controller.enable():
            raise RuntimeError("enable failed")
        time.sleep(2)

    if not is_motion_ok(controller):
        log_state(controller, "not ready")
        raise RuntimeError("robot not ready for motion")

    log_state(controller, "ready")


def move_and_wait_z(
    controller: CRIController,
    dz: float,
    vel: float,
    timeout_s: float = 45.0,
) -> bool:
    """Relative base-Z move; poll until settled (ReBeL may not send EXECEND)."""
    return move_base_relative_and_wait(
        controller,
        0.0,
        0.0,
        dz,
        vel_mm_s=vel,
        timeout_s=timeout_s,
        pos_tol_mm=0.5,
    )


def _current_joints(controller: CRIController) -> Joints:
    from .poses import Joints

    j = controller.robot_state.joints_current
    return Joints(
        A1=j.A1,
        A2=j.A2,
        A3=j.A3,
        A4=j.A4,
        A5=j.A5,
        A6=j.A6,
        E1=j.E1,
        E2=j.E2,
        E3=j.E3,
    )


def wait_joints_settled(
    controller: CRIController,
    target: Joints,
    *,
    tol_deg: float = 0.5,
    timeout_s: float = 60.0,
    speed_eps_mm_s: float = 0.5,
) -> bool:
    """Poll until joints are near target and cart speed is near zero."""
    start = time.time()
    while time.time() - start < timeout_s:
        current = _current_joints(controller)
        speed = controller.robot_state.cart_speed_mm_per_s
        if current.max_abs_delta(target) < tol_deg and speed < speed_eps_mm_s:
            return True
        if not is_motion_ok(controller):
            log_state(controller, "motion fault while waiting")
            controller.stop_move()
            return False
        time.sleep(0.15)

    controller.stop_move()
    final = _current_joints(controller)
    ok = final.max_abs_delta(target) < max(tol_deg * 2, 1.0)
    if not ok:
        logger.warning(
            "joint settle timeout (delta=%.2f°)",
            final.max_abs_delta(target),
        )
    return ok


def move_joints_and_wait(
    controller: CRIController,
    pose: Pose,
    *,
    velocity_pct: float = 20.0,
    tol_deg: float = 0.5,
    timeout_s: float = 60.0,
) -> bool:
    """Absolute joint move; poll settle (do not rely on EXECEND alone)."""
    args = pose.joints.as_move_args()
    if not controller.move_joints(
        **args,
        velocity=velocity_pct,
        wait_move_finished=False,
    ):
        return False
    return wait_joints_settled(
        controller,
        pose.joints,
        tol_deg=tol_deg,
        timeout_s=timeout_s,
    )


def move_base_relative_and_wait(
    controller: CRIController,
    dx: float,
    dy: float,
    dz: float,
    *,
    da: float = 0.0,
    db: float = 0.0,
    dc: float = 0.0,
    vel_mm_s: float = 15.0,
    timeout_s: float = 45.0,
    pos_tol_mm: float = 1.0,
) -> bool:
    """Relative Cartesian nudge; poll XYZ settle."""
    p0 = controller.robot_state.position_robot
    tx, ty, tz = p0.X + dx, p0.Y + dy, p0.Z + dz
    if not controller.move_base_relative(
        dx, dy, dz, da, db, dc, 0, 0, 0, vel_mm_s, wait_move_finished=False
    ):
        return False

    start = time.time()
    while time.time() - start < timeout_s:
        p = controller.robot_state.position_robot
        speed = controller.robot_state.cart_speed_mm_per_s
        if (
            abs(p.X - tx) < pos_tol_mm
            and abs(p.Y - ty) < pos_tol_mm
            and abs(p.Z - tz) < pos_tol_mm
            and speed < 0.5
        ):
            return True
        time.sleep(0.15)

    controller.stop_move()
    p = controller.robot_state.position_robot
    return (
        abs(p.X - tx) < pos_tol_mm * 2
        and abs(p.Y - ty) < pos_tol_mm * 2
        and abs(p.Z - tz) < pos_tol_mm * 2
    )


def move_joints_relative_and_wait(
    controller: CRIController,
    *,
    dA1: float = 0.0,
    dA2: float = 0.0,
    dA3: float = 0.0,
    dA4: float = 0.0,
    dA5: float = 0.0,
    dA6: float = 0.0,
    velocity_pct: float = 15.0,
    tol_deg: float = 0.5,
    timeout_s: float = 45.0,
) -> bool:
    """Relative joint nudge; poll settle."""
    from .poses import Joints

    before = _current_joints(controller)
    target = Joints(
        A1=before.A1 + dA1,
        A2=before.A2 + dA2,
        A3=before.A3 + dA3,
        A4=before.A4 + dA4,
        A5=before.A5 + dA5,
        A6=before.A6 + dA6,
        E1=before.E1,
        E2=before.E2,
        E3=before.E3,
    )
    if not controller.move_joints_relative(
        dA1,
        dA2,
        dA3,
        dA4,
        dA5,
        dA6,
        0.0,
        0.0,
        0.0,
        velocity_pct,
        wait_move_finished=False,
    ):
        return False
    return wait_joints_settled(
        controller, target, tol_deg=tol_deg, timeout_s=timeout_s
    )


def release(controller: CRIController) -> None:
    """Disable drives (brakes engage) and drop CRI control — preferred idle state."""
    try:
        controller.stop_move()
    except Exception:
        pass
    try:
        controller.stop_jog()
    except Exception:
        pass
    controller.disable()
    controller.set_active_control(False)
    controller.close()
    logger.info("Released control (drives disabled / brakes on).")
