"""Hermes Skills Pool 文件系统契约。"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path

from engine.community.plugins.skills_pool.layout_activation import (
    MappingPublishResult,
    MappingSourceLayout,
    MappingVerificationResult,
    PoolActivationResult,
    PoolActivationStatus,
    SkillMapping,
    activate_hermes_pool,
    rollback_hermes_pool,
)
from engine.community.plugins.skills_pool.layout_activation import (
    publish_pool_mappings as _publish_pool_mappings,
)
from engine.community.plugins.skills_pool.layout_activation import (
    verify_skill_mappings as _verify_skill_mappings,
)
from engine.community.plugins.skills_pool.layout_probe import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutInspection,
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)


def inspect_hermes_runtime_layout(
    *,
    expected_contract_version: str = LAYOUT_CONTRACT_VERSION,
    mapping_contract_version: str | None = None,
    home: Path = Path("/home/admin"),
    repo_is_mounted: Callable[[Path], bool] = os.path.ismount,
) -> RuntimeLayoutInspection:
    return inspect_runtime_layout(
        engine="hermes",
        expected_contract_version=expected_contract_version,
        mapping_contract_version=mapping_contract_version,
        home=home,
        repo_is_mounted=repo_is_mounted,
    )


def publish_hermes_pool_mappings(
    *,
    mappings: list[SkillMapping],
    retired_mappings: Sequence[SkillMapping] = (),
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
    home: str | Path = "/home/admin",
) -> MappingPublishResult:
    return _publish_pool_mappings(
        mappings=mappings,
        retired_mappings=retired_mappings,
        home=home,
        engine="hermes",
        source_layout=source_layout,
    )


def verify_hermes_pool_mappings(
    *,
    mappings: list[SkillMapping],
    retired_mappings: Sequence[SkillMapping] = (),
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
    home: str | Path = "/home/admin",
) -> MappingVerificationResult:
    return _verify_skill_mappings(
        mappings=mappings,
        retired_mappings=retired_mappings,
        home=home,
        engine="hermes",
        source_layout=source_layout,
    )


__all__ = [
    "LAYOUT_CONTRACT_VERSION",
    "MappingPublishResult",
    "MappingSourceLayout",
    "MappingVerificationResult",
    "PoolActivationResult",
    "PoolActivationStatus",
    "RuntimeLayoutInspection",
    "RuntimeLayoutInspectionStatus",
    "SkillMapping",
    "activate_hermes_pool",
    "inspect_hermes_runtime_layout",
    "publish_hermes_pool_mappings",
    "rollback_hermes_pool",
    "verify_hermes_pool_mappings",
]
