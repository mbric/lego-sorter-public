"""PoseSet serialization and load/save helpers."""

from __future__ import annotations

import pytest

from robot.poses import (
    MVP_BIN_IDS,
    BinPoses,
    Cartesian,
    Joints,
    Pose,
    PoseDefaults,
    PoseSet,
    load_poses,
    save_poses,
)


def _sample_pose(a1: float = 1.0) -> Pose:
    return Pose(
        joints=Joints(A1=a1, A2=2.0, A3=3.0, A4=4.0, A5=5.0, A6=6.0),
        cartesian=Cartesian(X=10.0, Y=20.0, Z=30.0, A=0.0, B=0.0, C=0.0),
    )


def test_poseset_to_dict_from_dict_roundtrip():
    poses = PoseSet(
        home=_sample_pose(1.0),
        rest=_sample_pose(2.0),
        bins={
            "red": BinPoses(approach=_sample_pose(3.0), dump=_sample_pose(4.0)),
            "blue": BinPoses(approach=_sample_pose(5.0)),  # partial
        },
        defaults=PoseDefaults(joint_vel_pct=15.0, cart_nudge_mm=5.0),
    )
    raw = poses.to_dict()
    restored = PoseSet.from_dict(raw)

    assert restored.home is not None
    assert restored.home.joints.A1 == 1.0
    assert restored.rest is not None
    assert restored.rest.joints.A1 == 2.0
    assert restored.bins["red"].complete()
    assert restored.bins["red"].approach.joints.A1 == 3.0
    assert restored.bins["blue"].approach is not None
    assert restored.bins["blue"].dump is None
    assert restored.defaults.joint_vel_pct == 15.0
    assert restored.defaults.cart_nudge_mm == 5.0
    # Untouched defaults preserved.
    assert restored.defaults.settle_tol_deg == PoseDefaults().settle_tol_deg


def test_load_poses_missing_returns_mvp_skeleton(tmp_path):
    missing = tmp_path / "nope.json"
    poses = load_poses(missing)
    for bid in MVP_BIN_IDS:
        assert bid in poses.bins
        assert poses.bins[bid].approach is None
        assert poses.bins[bid].dump is None
    assert poses.home is None
    assert poses.rest is None


def test_save_load_poses_roundtrip(tmp_path):
    path = tmp_path / "poses.json"
    poses = PoseSet(
        home=_sample_pose(7.0),
        bins={"red": BinPoses(approach=_sample_pose(8.0), dump=_sample_pose(9.0))},
        defaults=PoseDefaults(override_pct=25.0),
    )
    save_poses(poses, path)
    loaded = load_poses(path)
    assert loaded.home.joints.A1 == 7.0
    assert loaded.bins["red"].dump.joints.A1 == 9.0
    assert loaded.defaults.override_pct == 25.0
    for bid in MVP_BIN_IDS:
        assert bid in loaded.bins


def test_require_bin_raises_on_incomplete():
    poses = PoseSet(bins={"red": BinPoses(approach=_sample_pose())})
    with pytest.raises(KeyError, match="red"):
        poses.require_bin("red")
    with pytest.raises(KeyError, match="missing"):
        poses.require_bin("nope")


def test_malformed_json_raises_runtime_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="could not load poses"):
        load_poses(path)

    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="must be a JSON object"):
        load_poses(path)
