"""Claude Code Skills Pool filesystem contract.

The migration algorithm lives in the neutral filesystem Pool implementation;
this module fixes the engine identity and exposes only Claude Code's
engine-view seam to its plugin.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from engine.community.plugins.skills_pool.layout_activation import (
    MappingPublishResult,
    MappingSourceLayout,
    MappingVerificationResult,
    PoolActivationResult,
    PoolActivationStatus,
    SkillMapping,
    activate_claude_code_pool,
    rollback_claude_code_pool,
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


def inspect_claude_code_runtime_layout(
    *,
    expected_contract_version: str = LAYOUT_CONTRACT_VERSION,
    mapping_contract_version: str | None = None,
    home: Path = Path("/home/admin"),
    repo_is_mounted: Callable[[Path], bool] = os.path.ismount,
) -> RuntimeLayoutInspection:
    return inspect_runtime_layout(
        engine="claude_code",
        expected_contract_version=expected_contract_version,
        mapping_contract_version=mapping_contract_version,
        home=home,
        repo_is_mounted=repo_is_mounted,
    )


def publish_claude_code_pool_mappings(
    *,
    mappings: list[SkillMapping],
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
    home: str | Path = "/home/admin",
) -> MappingPublishResult:
    return _publish_pool_mappings(
        mappings=mappings,
        home=home,
        engine="claude_code",
        source_layout=source_layout,
    )


def verify_claude_code_pool_mappings(
    *,
    mappings: list[SkillMapping],
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
    home: str | Path = "/home/admin",
) -> MappingVerificationResult:
    return _verify_skill_mappings(
        mappings=mappings,
        home=home,
        engine="claude_code",
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
    "activate_claude_code_pool",
    "inspect_claude_code_runtime_layout",
    "publish_claude_code_pool_mappings",
    "rollback_claude_code_pool",
    "verify_claude_code_pool_mappings",
]
