"""Thread-safe ReBeL teach session shared by CLI and Gradio UI."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .config import RobotConfig
from .cri_session import (
    log_state,
    move_base_relative_and_wait,
    move_joints_and_wait,
    move_joints_relative_and_wait,
    prepare_for_motion,
    release,
)
from .dump import dump_cycle, go_home, go_rest, park_and_release
from .poses import (
    MVP_BIN_IDS,
    BinPoses,
    PoseSet,
    capture_pose,
    format_pose,
    load_poses,
    save_poses,
)

logger = logging.getLogger(__name__)


@dataclass
class TeachStatus:
    connected: bool
    message: str
    live: str
    summary: str
    pose_list: str


class TeachSession:
    """One CRI connection + in-memory pose set for interactive teaching."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._controller = None
        self.poses: PoseSet = load_poses()
        self.velocity_pct: float = self.poses.defaults.joint_vel_pct
        self._last_message = "Disconnected — click Connect"

    @property
    def connected(self) -> bool:
        c = self._controller
        return bool(c is not None and getattr(c, "connected", False))

    def status(self, message: str | None = None) -> TeachStatus:
        if message is not None:
            self._last_message = message
        live = "(not connected)"
        if self.connected:
            assert self._controller is not None
            try:
                pose = capture_pose(self._controller)
                live = format_pose(pose)
                s = self._controller.robot_state
                live += (
                    f"\nkin={s.kinematics_state.name} "
                    f"ref={s.referencing_state.global_state.name} "
                    f"override={s.override:.0f}%"
                )
            except Exception as exc:
                live = f"(status error: {exc})"
        summary = "\n".join(self.poses.summary_lines())
        pose_list = ", ".join(sorted(self.poses.bins)) or "(none)"
        return TeachStatus(
            connected=self.connected,
            message=self._last_message,
            live=live,
            summary=summary,
            pose_list=pose_list,
        )

    def connect(self) -> TeachStatus:
        with self._lock:
            if self.connected:
                return self.status("Already connected")
            try:
                from cri_lib import CRIController
            except ImportError:
                return self.status(
                    "cri_lib missing — pip install -r requirements-robot.txt"
                )
            cfg = RobotConfig.from_env()
            controller = CRIController()
            try:
                try:
                    ok = controller.connect(cfg.host, cfg.port)
                except Exception as exc:
                    raise RuntimeError(str(exc)) from exc
                if not ok:
                    raise RuntimeError("connect returned False")
                time.sleep(1)
                prepare_for_motion(controller)
                controller.set_override(self.poses.defaults.override_pct)
                self.velocity_pct = self.poses.defaults.joint_vel_pct
                self._controller = controller
                log_state(controller, "teach connected")
                return self.status(
                    f"Connected to {cfg.host}:{cfg.port} "
                    f"(override {self.poses.defaults.override_pct:.0f}%)"
                )
            except Exception as exc:
                try:
                    controller.close()
                except Exception:
                    pass
                self._controller = None
                return self.status(f"Connect failed: {exc}")

    def _require(self):
        if not self.connected or self._controller is None:
            raise RuntimeError("Not connected — click Connect first")
        return self._controller

    def stop(self) -> TeachStatus:
        with self._lock:
            if not self.connected:
                return self.status("Not connected")
            c = self._require()
            try:
                c.stop_move()
                c.stop_jog()
            except Exception as exc:
                return self.status(f"Stop error: {exc}")
            return self.status("Stopped")

    def set_override(self, pct: float) -> TeachStatus:
        with self._lock:
            c = self._require()
            pct = float(pct)
            c.set_override(pct)
            self.poses.defaults.override_pct = pct
            save_poses(self.poses)
            return self.status(f"Override set to {pct:.0f}%")

    def set_velocity(self, pct: float) -> TeachStatus:
        with self._lock:
            self.velocity_pct = float(pct)
            self.poses.defaults.joint_vel_pct = self.velocity_pct
            save_poses(self.poses)
            return self.status(f"Goto/test velocity set to {self.velocity_pct:.0f}%")

    def set_nudge_steps(self, cart_mm: float, joint_deg: float) -> TeachStatus:
        with self._lock:
            self.poses.defaults.cart_nudge_mm = float(cart_mm)
            self.poses.defaults.joint_nudge_deg = float(joint_deg)
            save_poses(self.poses)
            return self.status(
                f"Nudge steps: {cart_mm:.1f} mm / {joint_deg:.1f}°"
            )

    def nudge_cart(self, axis: str, sign: float, amount: float | None = None) -> TeachStatus:
        with self._lock:
            c = self._require()
            axis = axis.lower()
            step = (
                self.poses.defaults.cart_nudge_mm if amount is None else float(amount)
            )
            delta = {"x": 0.0, "y": 0.0, "z": 0.0}
            if axis not in delta:
                return self.status(f"Unknown cart axis {axis}")
            delta[axis] = sign * step
            ok = move_base_relative_and_wait(
                c,
                delta["x"],
                delta["y"],
                delta["z"],
                vel_mm_s=max(5.0, step),
            )
            return self.status(
                f"Nudge {axis} {sign * step:+.1f} mm → {'ok' if ok else 'FAIL'}"
            )

    def nudge_joint(self, axis: str, sign: float, amount: float | None = None) -> TeachStatus:
        with self._lock:
            c = self._require()
            axis = axis.lower()
            joint_map = {"a1": 0, "a2": 1, "a3": 2, "a4": 3, "a5": 4, "a6": 5}
            if axis not in joint_map:
                return self.status(f"Unknown joint {axis}")
            step = (
                self.poses.defaults.joint_nudge_deg
                if amount is None
                else float(amount)
            )
            d = [0.0] * 6
            d[joint_map[axis]] = sign * step
            ok = move_joints_relative_and_wait(
                c,
                dA1=d[0],
                dA2=d[1],
                dA3=d[2],
                dA4=d[3],
                dA5=d[4],
                dA6=d[5],
                velocity_pct=self.velocity_pct,
                tol_deg=self.poses.defaults.settle_tol_deg,
            )
            return self.status(
                f"Nudge {axis} {sign * step:+.1f}° → {'ok' if ok else 'FAIL'}"
            )

    def save_named(self, name: str) -> TeachStatus:
        with self._lock:
            c = self._require()
            pose = capture_pose(c)
            name = name.lower().strip()
            if name == "home":
                self.poses.home = pose
            elif name == "rest":
                self.poses.rest = pose
            else:
                return self.status("Save name must be home or rest")
            save_poses(self.poses)
            return self.status(f"Saved {name} ← {format_pose(pose)}")

    def save_bin(self, bin_id: str, kind: str) -> TeachStatus:
        with self._lock:
            c = self._require()
            pose = capture_pose(c)
            bin_id = bin_id.strip()
            kind = kind.lower().strip()
            if kind not in ("approach", "dump"):
                return self.status("Kind must be approach or dump")
            if not bin_id:
                return self.status("Pick a bin id")
            if bin_id not in self.poses.bins:
                self.poses.bins[bin_id] = BinPoses()
            bp = self.poses.bins[bin_id]
            if kind == "approach":
                bp.approach = pose
            else:
                bp.dump = pose
            save_poses(self.poses)
            note = ""
            if bin_id not in MVP_BIN_IDS:
                note = f" (custom bin outside MVP {MVP_BIN_IDS})"
            return self.status(
                f"Saved bin {bin_id}/{kind} ← {format_pose(pose)}{note}"
            )

    def goto_named(self, name: str) -> TeachStatus:
        with self._lock:
            c = self._require()
            name = name.lower().strip()
            try:
                if name == "home":
                    go_home(c, self.poses, velocity_pct=self.velocity_pct)
                elif name == "rest":
                    go_rest(c, self.poses, velocity_pct=self.velocity_pct)
                else:
                    return self.status("Goto name must be home or rest")
            except RuntimeError as exc:
                return self.status(str(exc))
            return self.status(f"At {name}")

    def goto_bin(self, bin_id: str, kind: str) -> TeachStatus:
        with self._lock:
            c = self._require()
            kind = kind.lower().strip()
            bp = self.poses.bin_poses(bin_id)
            target = None if bp is None else getattr(bp, kind, None)
            if target is None:
                return self.status(f"Missing pose bin {bin_id}/{kind}")
            ok = move_joints_and_wait(
                c,
                target,
                velocity_pct=self.velocity_pct,
                tol_deg=self.poses.defaults.settle_tol_deg,
            )
            if not ok:
                return self.status(f"Goto bin {bin_id}/{kind} failed")
            return self.status(f"At bin {bin_id}/{kind}")

    def test_dump(self, bin_id: str) -> TeachStatus:
        with self._lock:
            c = self._require()
            try:
                dump_cycle(
                    c,
                    bin_id,
                    self.poses,
                    velocity_pct=self.velocity_pct,
                    hold_s=self.poses.defaults.dump_hold_s,
                    return_home=True,
                )
            except (RuntimeError, KeyError) as exc:
                return self.status(f"Test dump failed: {exc}")
            return self.status(f"Test dump {bin_id} ok")

    def park(self) -> TeachStatus:
        with self._lock:
            if not self.connected:
                return self.status("Not connected")
            c = self._require()
            try:
                park_and_release(c, self.poses, velocity_pct=self.velocity_pct)
            except Exception as exc:
                try:
                    release(c)
                except Exception:
                    pass
                self._controller = None
                return self.status(f"Park error (released): {exc}")
            self._controller = None
            return self.status("Parked and drives disabled")

    def disconnect(self, *, park: bool = True) -> TeachStatus:
        with self._lock:
            if not self.connected:
                self._controller = None
                return self.status("Disconnected")
            if park:
                return self.park()
            c = self._require()
            try:
                release(c)
            except Exception as exc:
                self._controller = None
                return self.status(f"Disconnect error: {exc}")
            self._controller = None
            return self.status("Disconnected (drives disabled, no park move)")

    def reload_poses(self) -> TeachStatus:
        with self._lock:
            self.poses = load_poses()
            self.velocity_pct = self.poses.defaults.joint_vel_pct
            return self.status("Reloaded poses.json from disk")
