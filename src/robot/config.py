"""Robot network settings (override via environment or .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class RobotConfig:
    host: str
    port: int

    @classmethod
    def from_env(cls) -> RobotConfig:
        return cls(
            host=os.getenv("ROBOT_IP", "192.168.3.11"),
            port=int(os.getenv("ROBOT_CRI_PORT", "3920")),
        )
