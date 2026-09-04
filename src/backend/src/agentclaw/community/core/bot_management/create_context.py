"""Caller-resolved context carried through Bot creation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BotCreateDeploymentMode(StrEnum):
    """Deployment boundary relevant to Bot creation policy."""

    CLOUD = "cloud"
    LOCAL = "local"


@dataclass(frozen=True)
class BotCreateContext:
    """Caller-resolved business context required by creation policy."""

    deployment_mode: BotCreateDeploymentMode
    space_kind: str
    # Legacy callers keep BotService's established owner/device limit behavior.
    space_quota: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "deployment_mode": self.deployment_mode.value,
            "space_kind": self.space_kind,
            "space_quota": self.space_quota,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BotCreateContext":
        return cls(
            deployment_mode=BotCreateDeploymentMode(payload["deployment_mode"]),
            space_kind=payload["space_kind"],
            space_quota=bool(payload.get("space_quota", False)),
        )
