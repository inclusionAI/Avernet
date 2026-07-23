"""AICoding Skills Pool 文件系统契约。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from engine.community.plugins.skills_pool.layout_activation import (
    MappingPublishResult,
    MappingVerificationResult,
    PoolActivationResult,
    PoolActivationStatus,
    SkillMapping,
    activate_aicoding_pool,
    publish_pool_mappings as _publish_pool_mappings,
    verify_skill_mappings as _verify_skill_mappings,
)
from engine.community.plugins.skills_pool.layout_probe import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutInspection,
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)


def inspect_aicoding_runtime_layout(
    *,
    expected_contract_version: str = LAYOUT_CONTRACT_VERSION,
    home: Path = Path("/home/admin"),
    repo_is_mounted: Callable[[Path], bool] = os.path.ismount,
) -> RuntimeLayoutInspection:
    return inspect_runtime_layout(
        engine="aicoding",
        expected_contract_version=expected_contract_version,
        home=home,
        repo_is_mounted=repo_is_mounted,
)


def publish_aicoding_pool_mappings(
    *,
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
) -> MappingPublishResult:
    return _publish_pool_mappings(
        mappings=mappings,
        home=home,
        engine="aicoding",
    )


def verify_aicoding_pool_mappings(
    *,
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
) -> MappingVerificationResult:
    return _verify_skill_mappings(
        mappings=mappings,
        home=home,
        engine="aicoding",
    )


__all__ = [
    "LAYOUT_CONTRACT_VERSION",
    "MappingPublishResult",
    "MappingVerificationResult",
    "PoolActivationResult",
    "PoolActivationStatus",
    "RuntimeLayoutInspection",
    "RuntimeLayoutInspectionStatus",
    "SkillMapping",
    "activate_aicoding_pool",
    "inspect_aicoding_runtime_layout",
    "publish_aicoding_pool_mappings",
    "verify_aicoding_pool_mappings",
]
