"""Read-only evidence for publishing from a stable OpenClaw Legacy layout."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

from engine.community.core.skills.layout_planner import (
    MAPPING_CONTRACT_VERSION,
    MAPPING_V3_CONTRACT_VERSION,
    LayoutIdentity,
    RuntimeLayoutContext,
    resolve_filesystem_skill_layout,
    resolved_filesystem_layout_evidence,
)
from engine.community.plugins.skills_pool.center_mount import (
    CenterMountStatus,
    inspect_center_mount,
)
from engine.community.plugins.skills_pool.inspection_types import (
    RuntimeLayoutInspection,
    RuntimeLayoutInspectionStatus,
)


def inspect_legacy_service_layout(
    *,
    context: RuntimeLayoutContext,
    contract_version: str,
    is_mounted: Callable[[Path], bool],
) -> RuntimeLayoutInspection | None:
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity("openclaw", contract_version), context
    )
    try:
        for marker in (layout.marker, layout.active_marker):
            try:
                marker.lstat()
            except FileNotFoundError:
                continue
            return None
        # Fresh Legacy drafts may have no local packages yet.
        if not stat.S_ISDIR(layout.active_root.lstat().st_mode):
            return None
        # Legacy may link the Repo to a shared directory.
        if not stat.S_ISDIR(layout.legacy_repo.stat().st_mode):
            return None
        with os.scandir(layout.legacy_repo):
            pass
    except OSError:
        return None

    center_mount = inspect_center_mount(layout.pool_center, is_mounted=is_mounted)
    versions = [MAPPING_CONTRACT_VERSION]
    if center_mount.status is CenterMountStatus.READY:
        versions.append(MAPPING_V3_CONTRACT_VERSION)
    return RuntimeLayoutInspection(
        status=RuntimeLayoutInspectionStatus.READY,
        engine=layout.engine_type,
        layout_contract_version=contract_version,
        preparation_id=None,
        evidence={
            "source_layout": "legacy",
            "mapping_contract_version": MAPPING_CONTRACT_VERSION,
            "supported_mapping_contract_versions": versions,
            "center_mount": center_mount.to_evidence(),
            "resolved_layout": resolved_filesystem_layout_evidence(
                layout, local_root=layout.legacy_local, repo_root=layout.legacy_repo
            ),
        },
    )
