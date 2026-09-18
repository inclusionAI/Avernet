"""Read the OpenClaw Pool steady-state contract without migration history."""

from __future__ import annotations

import json
import stat
from collections.abc import Callable
from pathlib import Path

from engine.community.core.skills.layout_planner import (
    MAPPING_CONTRACT_VERSION,
    MAPPING_V3_CONTRACT_VERSION,
    ResolvedFilesystemLayoutPlan,
    resolved_filesystem_layout_evidence,
)
from engine.community.plugins.skills_pool.active_marker_validation import (
    steady_active_marker_valid,
)
from engine.community.plugins.skills_pool.center_mount import (
    inspect_center_mount,
)
from engine.community.plugins.skills_pool.inspection_types import (
    RuntimeLayoutInspection,
    RuntimeLayoutInspectionStatus,
)


def _invalid(
    *, layout: ResolvedFilesystemLayoutPlan, contract: str, reason: str
) -> RuntimeLayoutInspection:
    return RuntimeLayoutInspection(
        status=RuntimeLayoutInspectionStatus.INVALID,
        engine="openclaw",
        layout_contract_version=contract,
        preparation_id=None,
        evidence={"reason": reason, "marker": str(layout.ready_marker)},
    )


def _transient(
    *,
    layout: ResolvedFilesystemLayoutPlan,
    contract: str,
    reason: str,
    error: OSError,
) -> RuntimeLayoutInspection:
    return RuntimeLayoutInspection(
        status=RuntimeLayoutInspectionStatus.TRANSIENT_ERROR,
        engine="openclaw",
        layout_contract_version=contract,
        preparation_id=None,
        evidence={
            "reason": reason,
            "marker": str(layout.ready_marker),
            "error_type": type(error).__name__,
            "errno": error.errno,
        },
    )


def inspect_openclaw_steady_active(
    *,
    layout: ResolvedFilesystemLayoutPlan,
    expected_contract_version: str,
    repo_is_mounted: Callable[[Path], bool],
    center_is_mounted: Callable[[Path], bool] | None,
) -> RuntimeLayoutInspection | None:
    """Return ``None`` when migration preparation remains authoritative."""

    try:
        marker_stat = layout.active_marker.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except PermissionError:
        return _invalid(
            layout=layout,
            contract=expected_contract_version,
            reason="active_marker_unreadable",
        )
    except OSError as error:
        return _transient(
            layout=layout,
            contract=expected_contract_version,
            reason="active_marker_temporarily_unavailable",
            error=error,
        )
    if not stat.S_ISREG(marker_stat.st_mode):
        return _invalid(
            layout=layout,
            contract=expected_contract_version,
            reason="active_marker_not_regular_file",
        )
    try:
        marker = json.loads(layout.active_marker.read_bytes())
    except (PermissionError, UnicodeDecodeError, json.JSONDecodeError):
        return _invalid(
            layout=layout,
            contract=expected_contract_version,
            reason="active_marker_invalid",
        )
    except OSError as error:
        return _transient(
            layout=layout,
            contract=expected_contract_version,
            reason="active_marker_temporarily_unavailable",
            error=error,
        )
    if isinstance(marker, dict) and marker.get("activation_state") == "finalizing":
        return None
    if isinstance(marker, dict) and (
        "preparation_id" in marker or "migration_generation" in marker
    ):
        return None
    if not steady_active_marker_valid(
        marker,
        engine="openclaw",
        expected_contract_version=expected_contract_version,
    ):
        return _invalid(
            layout=layout,
            contract=expected_contract_version,
            reason="active_marker_contract_mismatch",
        )

    for root, reason in (
        (layout.active_root, "active_root_invalid"),
        (layout.pool_root, "pool_root_invalid"),
        (layout.pool_local, "pool_local_invalid"),
    ):
        try:
            root_stat = root.lstat()
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            root_stat = None
        except OSError as error:
            return _transient(
                layout=layout,
                contract=expected_contract_version,
                reason=f"{reason}_temporarily_unavailable",
                error=error,
            )
        if (
            root_stat is None
            or stat.S_ISLNK(root_stat.st_mode)
            or not stat.S_ISDIR(root_stat.st_mode)
        ):
            return _invalid(
                layout=layout,
                contract=expected_contract_version,
                reason=reason,
            )

    for name, path in (("local", layout.local_bridge), ("repo", layout.repo_bridge)):
        if path.exists() or path.is_symlink():
            return _invalid(
                layout=layout,
                contract=expected_contract_version,
                reason=f"retired_{name}_bridge_present",
            )

    try:
        repo_mounted = repo_is_mounted(layout.pool_repo)
    except OSError:
        repo_mounted = False
    center_mount = inspect_center_mount(
        layout.pool_center,
        is_mounted=center_is_mounted or repo_is_mounted,
    )
    return RuntimeLayoutInspection(
        status=RuntimeLayoutInspectionStatus.READY,
        engine="openclaw",
        layout_contract_version=expected_contract_version,
        preparation_id=(
            marker.get("preparation_id")
            if isinstance(marker.get("preparation_id"), str)
            else None
        ),
        evidence={
            "active_marker": str(layout.active_marker),
            "activation_state": "active",
            "mapping_contract_version": MAPPING_CONTRACT_VERSION,
            "supported_mapping_contract_versions": [
                MAPPING_CONTRACT_VERSION,
                *(
                    [MAPPING_V3_CONTRACT_VERSION]
                    if center_mount.status.value == "READY"
                    else []
                ),
            ],
            "checks": {
                "active_marker_valid": True,
                "active_root_valid": True,
                "pool_local_valid": True,
                "legacy_storage_entries_absent": True,
            },
            "mount_diagnostics": {
                "pool_repo_mounted": repo_mounted,
                "center_mount": center_mount.to_evidence(),
            },
            "center_mount": center_mount.to_evidence(),
            "resolved_layout": resolved_filesystem_layout_evidence(
                layout,
                local_root=layout.pool_local,
                repo_root=layout.pool_repo,
            ),
        },
    )


__all__ = ["inspect_openclaw_steady_active"]
