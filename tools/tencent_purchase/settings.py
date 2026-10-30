"""Filesystem and secret references for the standalone purchase runner."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_home() -> Path:
    configured = os.getenv("TENCENT_PURCHASE_HOME", "").strip()
    if configured:
        return Path(configured)
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
        return base / "TencentPurchaseRunner"
    return Path.home() / ".local" / "share" / "tencent-purchase-runner"


@dataclass(frozen=True)
class RunnerSettings:
    home: Path
    pushplus_token: str

    @classmethod
    def from_env(cls) -> "RunnerSettings":
        return cls(
            home=default_home(),
            pushplus_token=os.getenv("PURCHASE_PUSHPLUS_TOKEN", "").strip(),
        )

    @property
    def profile_dir(self) -> Path:
        return self.home / "edge-profile"

    @property
    def state_dir(self) -> Path:
        return self.home / "state"

    @property
    def log_dir(self) -> Path:
        return self.home / "logs"

    @property
    def evidence_dir(self) -> Path:
        return self.home / "evidence"

    def ensure_directories(self) -> None:
        for path in (
            self.home,
            self.profile_dir,
            self.state_dir,
            self.log_dir,
            self.evidence_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
