"""Taught ReBeL poses: home, rest, and per-bin approach/dump."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cri_lib import CRIController

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
POSES_PATH = _ROOT / "data" / "robot" / "poses.json"

# MVP color bins + catch-all (matches PLAN.md / routing).
MVP_BIN_IDS = ("red", "blue", "yellow", "green", "unknown")

JOINT_KEYS = ("A1", "A2", "A3", "A4", "A5", "A6")
CART_KEYS = ("X", "Y", "Z", "A", "B", "C")


@dataclass
class Joints:
    A1: float = 0.0
    A2: float = 0.0
    A3: float = 0.0
    A4: float = 0.0
    A5: float = 0.0
    A6: float = 0.0
    E1: float = 0.0
    E2: float = 0.0
    E3: float = 0.0

    def as_move_args(self) -> dict[str, float]:
        return {
            "A1": self.A1,
            "A2": self.A2,
            "A3": self.A3,
            "A4": self.A4,
            "A5": self.A5,
            "A6": self.A6,
            "E1": self.E1,
            "E2": self.E2,
            "E3": self.E3,
        }

    def max_abs_delta(self, other: Joints) -> float:
        return max(
            abs(getattr(self, k) - getattr(other, k)) for k in JOINT_KEYS
        )


@dataclass
class Cartesian:
    X: float = 0.0
    Y: float = 0.0
    Z: float = 0.0
    A: float = 0.0
    B: float = 0.0
    C: float = 0.0


@dataclass
class Pose:
    """One taught waypoint. Joints are the replay source; cartesian is a snapshot."""

    joints: Joints
    cartesian: Cartesian = field(default_factory=Cartesian)

    def to_dict(self) -> dict[str, Any]:
        return {
            "joints": asdict(self.joints),
            "cartesian": asdict(self.cartesian),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Pose | None:
        if not raw or not isinstance(raw, dict):
            return None
        j = raw.get("joints")
        if not isinstance(j, dict):
            return None
        try:
            joints = Joints(
                **{k: float(j.get(k, 0.0)) for k in (*JOINT_KEYS, "E1", "E2", "E3")}
            )
        except (TypeError, ValueError):
            return None
        c_raw = raw.get("cartesian") if isinstance(raw.get("cartesian"), dict) else {}
        cartesian = Cartesian(
            **{k: float(c_raw.get(k, 0.0)) for k in CART_KEYS}  # type: ignore[arg-type]
        )
        return cls(joints=joints, cartesian=cartesian)


@dataclass
class BinPoses:
    approach: Pose | None = None
    dump: Pose | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.approach is not None:
            out["approach"] = self.approach.to_dict()
        if self.dump is not None:
            out["dump"] = self.dump.to_dict()
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> BinPoses:
        if not raw or not isinstance(raw, dict):
            return cls()
        return cls(
            approach=Pose.from_dict(raw.get("approach")),
            dump=Pose.from_dict(raw.get("dump")),
        )

    def complete(self) -> bool:
        return self.approach is not None and self.dump is not None


@dataclass
class PoseDefaults:
    joint_vel_pct: float = 20.0
    settle_tol_deg: float = 0.5
    cart_nudge_mm: float = 10.0
    joint_nudge_deg: float = 3.0
    dump_hold_s: float = 0.4
    override_pct: float = 20.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> PoseDefaults:
        if not raw or not isinstance(raw, dict):
            return cls()
        kwargs: dict[str, float] = {}
        for key in (
            "joint_vel_pct",
            "settle_tol_deg",
            "cart_nudge_mm",
            "joint_nudge_deg",
            "dump_hold_s",
            "override_pct",
        ):
            if key in raw:
                try:
                    kwargs[key] = float(raw[key])
                except (TypeError, ValueError):
                    pass
        return cls(**kwargs)


@dataclass
class PoseSet:
    """All taught poses for the sorter cell.

    ``home`` — scoop level under the scan-stage trapdoor (working catch pose).
    ``rest`` — parked idle pose clear of furniture / bins (use when not sorting).
    """

    home: Pose | None = None
    rest: Pose | None = None
    bins: dict[str, BinPoses] = field(default_factory=dict)
    defaults: PoseDefaults = field(default_factory=PoseDefaults)

    def to_dict(self) -> dict[str, Any]:
        return {
            "home": None if self.home is None else self.home.to_dict(),
            "rest": None if self.rest is None else self.rest.to_dict(),
            "bins": {k: v.to_dict() for k, v in sorted(self.bins.items())},
            "defaults": self.defaults.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PoseSet:
        bins_raw = raw.get("bins") if isinstance(raw.get("bins"), dict) else {}
        bins = {
            str(name): BinPoses.from_dict(payload if isinstance(payload, dict) else None)
            for name, payload in bins_raw.items()
        }
        return cls(
            home=Pose.from_dict(raw.get("home") if isinstance(raw.get("home"), dict) else None),
            rest=Pose.from_dict(raw.get("rest") if isinstance(raw.get("rest"), dict) else None),
            bins=bins,
            defaults=PoseDefaults.from_dict(
                raw.get("defaults") if isinstance(raw.get("defaults"), dict) else None
            ),
        )

    def bin_poses(self, bin_id: str) -> BinPoses | None:
        return self.bins.get(bin_id)

    def require_bin(self, bin_id: str) -> BinPoses:
        bp = self.bin_poses(bin_id)
        if bp is None or not bp.complete():
            raise KeyError(
                f"bin '{bin_id}' missing approach/dump poses — teach them first"
            )
        assert bp.approach is not None and bp.dump is not None
        return bp

    def summary_lines(self) -> list[str]:
        lines = [
            f"home: {'set' if self.home else 'MISSING'}",
            f"rest: {'set' if self.rest else 'MISSING (park when idle)'}",
        ]
        known = set(MVP_BIN_IDS) | set(self.bins)
        for name in sorted(known):
            bp = self.bins.get(name) or BinPoses()
            a = "ok" if bp.approach else "-"
            d = "ok" if bp.dump else "-"
            lines.append(f"bin {name}: approach={a} dump={d}")
        return lines


def empty_pose_set() -> PoseSet:
    return PoseSet(bins={bid: BinPoses() for bid in MVP_BIN_IDS})


def load_poses(path: Path | None = None) -> PoseSet:
    """Load taught poses, or an empty MVP skeleton if the file is missing."""
    target = path or POSES_PATH
    if not target.is_file():
        logger.info("No poses file at %s — starting empty", target)
        return empty_pose_set()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not load poses from {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"poses file {target} must be a JSON object")
    poses = PoseSet.from_dict(raw)
    for bid in MVP_BIN_IDS:
        poses.bins.setdefault(bid, BinPoses())
    return poses


def save_poses(poses: PoseSet, path: Path | None = None) -> Path:
    target = path or POSES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(poses.to_dict(), indent=2) + "\n"
    target.write_text(payload, encoding="utf-8")
    logger.info("Saved poses → %s", target)
    return target


def capture_pose(controller: CRIController) -> Pose:
    """Snapshot current commanded joints + TCP (use set-point, not encoder lag)."""
    s = controller.robot_state
    # CRI docs: prefer joints_set_point as move targets to avoid feedback loops.
    j = s.joints_set_point
    p = s.position_robot
    return Pose(
        joints=Joints(
            A1=j.A1,
            A2=j.A2,
            A3=j.A3,
            A4=j.A4,
            A5=j.A5,
            A6=j.A6,
            E1=j.E1,
            E2=j.E2,
            E3=j.E3,
        ),
        cartesian=Cartesian(X=p.X, Y=p.Y, Z=p.Z, A=p.A, B=p.B, C=p.C),
    )


def format_pose(pose: Pose) -> str:
    j = pose.joints
    c = pose.cartesian
    return (
        f"J[{j.A1:.2f} {j.A2:.2f} {j.A3:.2f} {j.A4:.2f} {j.A5:.2f} {j.A6:.2f}] "
        f"XYZ[{c.X:.1f} {c.Y:.1f} {c.Z:.1f}]"
    )
