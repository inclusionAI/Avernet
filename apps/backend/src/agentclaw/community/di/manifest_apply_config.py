"""Validated rollout configuration for existing-Bot Manifest Apply."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ManifestApplyConfig:
    defer_capability_projection: bool = False
    local_skill_parallelism: int = 1

    def __post_init__(self) -> None:
        if type(self.defer_capability_projection) is not bool:
            raise ValueError("manifest_apply.defer_capability_projection must be a boolean")
        if type(self.local_skill_parallelism) is not int or not 1 <= self.local_skill_parallelism <= 4:
            raise ValueError("manifest_apply.local_skill_parallelism must be an integer from 1 to 4")
