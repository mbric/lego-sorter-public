"""Unit tests for robot helpers that do not need hardware."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import run_robot_move_test as move_test

from robot.cri_session import move_and_wait_z


def test_move_and_wait_z_delegates_to_base_relative():
    controller = MagicMock()
    controller.robot_state.position_robot = SimpleNamespace(X=0.0, Y=0.0, Z=100.0)
    controller.robot_state.cart_speed_mm_per_s = 0.0
    controller.move_base_relative.return_value = True

    with patch("robot.cri_session.move_base_relative_and_wait") as mocked:
        mocked.return_value = True
        ok = move_and_wait_z(controller, dz=25.0, vel=15.0, timeout_s=10.0)

    assert ok is True
    mocked.assert_called_once_with(
        controller,
        0.0,
        0.0,
        25.0,
        vel_mm_s=15.0,
        timeout_s=10.0,
        pos_tol_mm=0.5,
    )


def test_move_test_parse_args_reference_flag():
    assert move_test.parse_args([]).reference is False
    assert move_test.parse_args(["--reference"]).reference is True
