"""多文件型引擎对持久化 Skills Pool layout 的运行时核验。"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from engine.community.config import RepoDelivery, current_repo_delivery
from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    MAPPING_CONTRACT_VERSION,
    LayoutIdentity,
    RuntimeLayoutContext,
    resolve_filesystem_skill_layout,
    resolved_filesystem_layout_evidence,
)
from engine.community.core.skills.layout_planner import (
    ResolvedFilesystemLayoutPlan as _FilesystemPoolLayout,
)


class RuntimeLayoutInspectionStatus(str, Enum):
    READY = "READY"
    NOT_CAPABLE = "NOT_CAPABLE"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    INVALID = "INVALID"


@dataclass(frozen=True)
class RuntimeLayoutInspection:
    status: RuntimeLayoutInspectionStatus
    engine: str
    layout_contract_version: str
    preparation_id: str | None
    evidence: dict[str, Any]

    def to_data(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "engine": self.engine,
            "layout_contract_version": self.layout_contract_version,
            "preparation_id": self.preparation_id,
            "evidence": self.evidence,
        }


def _ready_evidence(
    *,
    layout: _FilesystemPoolLayout,
    marker: dict[str, Any],
    checks: dict[str, bool],
    mapping_contract_version: str | None,
    activation_state: str | None = None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "marker": str(layout.marker),
        "prepared_at": marker["prepared_at"],
        "cutover_evidence_contract_version": "quarantine-v1",
        "checks": checks,
    }
    if activation_state is not None:
        evidence.update(
            {
                "active_marker": str(layout.active_marker),
                "activation_state": activation_state,
            }
        )
    if mapping_contract_version is not None:
        evidence.update(
            {
                "mapping_contract_version": mapping_contract_version,
                "resolved_layout": resolved_filesystem_layout_evidence(
                    layout,
                    local_root=layout.pool_local,
                    repo_root=layout.pool_repo,
                ),
            }
        )
    return evidence


def _not_capable(
    engine: str, contract_version: str, reason: str
) -> RuntimeLayoutInspection:
    return RuntimeLayoutInspection(
        status=RuntimeLayoutInspectionStatus.NOT_CAPABLE,
        engine=engine,
        layout_contract_version=contract_version,
        preparation_id=None,
        evidence={"reason": reason},
    )


def _invalid(
    *,
    engine: str,
    contract_version: str,
    layout: _FilesystemPoolLayout,
    reason: str,
    preparation_id: str | None = None,
    checks: dict[str, bool] | None = None,
) -> RuntimeLayoutInspection:
    evidence: dict[str, Any] = {
        "reason": reason,
        "marker": str(layout.marker),
    }
    if checks is not None:
        evidence["checks"] = checks
    return RuntimeLayoutInspection(
        status=RuntimeLayoutInspectionStatus.INVALID,
        engine=engine,
        layout_contract_version=contract_version,
        preparation_id=preparation_id,
        evidence=evidence,
    )


def _transient(
    *,
    engine: str,
    contract_version: str,
    layout: _FilesystemPoolLayout,
    reason: str,
    error: OSError,
    preparation_id: str | None = None,
) -> RuntimeLayoutInspection:
    return RuntimeLayoutInspection(
        status=RuntimeLayoutInspectionStatus.TRANSIENT_ERROR,
        engine=engine,
        layout_contract_version=contract_version,
        preparation_id=preparation_id,
        evidence={
            "reason": reason,
            "marker": str(layout.marker),
            "error_type": type(error).__name__,
            "errno": error.errno,
        },
    )


def _valid_uuid(value: object) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _lexical_symlink_target(path: Path) -> Path:
    target = path.readlink()
    if not target.is_absolute():
        target = path.parent / target
    return Path(os.path.abspath(target))


def _marker_contract_valid(
    marker: dict[str, Any],
    *,
    layout: _FilesystemPoolLayout,
    expected_engine: str,
    expected_contract_version: str,
    repo_delivery: RepoDelivery,
) -> bool:
    summary = marker.get("validation_summary")
    if not isinstance(summary, dict):
        return False
    bridge = summary.get("legacy_repo_bridge")
    pool_local = summary.get("pool_local")
    pool_repo = summary.get("pool_repo")
    managed = summary.get("managed_active_entries")
    common = (
        marker.get("engine") == expected_engine
        and marker.get("layout_contract_version") == expected_contract_version
        and _valid_uuid(marker.get("preparation_id"))
        and _valid_timestamp(marker.get("prepared_at"))
        and marker.get("pool_local_root") == str(layout.pool_local)
        and marker.get("pool_repo_root") == str(layout.pool_repo)
        and summary.get("all_valid") is True
        and isinstance(pool_local, dict)
        and pool_local.get("path") == str(layout.pool_local)
        and pool_local.get("valid") is True
        and isinstance(pool_repo, dict)
        and pool_repo.get("path") == str(layout.pool_repo)
        and pool_repo.get("valid") is True
        and isinstance(managed, list)
    )
    if not common:
        return False
    if repo_delivery is RepoDelivery.DOWNLOAD:
        delivery_source = marker.get("repo_delivery_source")
        delivery_bridge = summary.get("repo_delivery_bridge")
        delivery_valid = (
            marker.get("repo_delivery") == RepoDelivery.DOWNLOAD.value
            and isinstance(delivery_source, str)
            and bool(delivery_source)
            and pool_repo.get("readable_delivery") is True
            and pool_repo.get("source") == delivery_source
            and isinstance(delivery_bridge, dict)
            and delivery_bridge.get("path") == str(layout.pool_repo)
            and delivery_bridge.get("target") == delivery_source
            and delivery_bridge.get("valid") is True
        )
        if not delivery_valid:
            return False
        expected_bridges: list[dict[str, object]] = []
        if expected_engine != "openclaw":
            expected_bridges.append(
                {
                    "name": "stable_local_bridge",
                    "path": str(layout.local_bridge),
                    "target": str(layout.legacy_local),
                    "valid": True,
                }
            )
            expected_bridges.append(
                {
                    "name": "legacy_repo_delivery",
                    "path": str(layout.legacy_repo),
                    "target": delivery_source,
                    "valid": True,
                }
            )
        if expected_engine == "claude_code":
            expected_bridges.append(
                {
                    "name": "legacy_repo_bridge",
                    "path": str(layout.repo_bridge),
                    "target": str(layout.legacy_repo),
                    "valid": True,
                }
            )
        bridges_valid = summary.get("structural_bridges") == expected_bridges
        if expected_engine == "hermes":
            return bridges_valid and summary.get("legacy_bridge_verified") is True
        return bridges_valid
    if pool_repo.get("readable_mount") is not True:
        return False
    if expected_engine == "openclaw":
        return (
            isinstance(bridge, dict)
            and bridge.get("path") == str(layout.repo_bridge)
            and bridge.get("target") == str(layout.pool_repo)
            and bridge.get("valid") is True
        )
    bridges_valid = summary.get("structural_bridges") == [
        {
            "name": "stable_local_bridge",
            "path": str(layout.local_bridge),
            "target": str(layout.legacy_local),
            "valid": True,
        },
        {
            "name": "stable_repo_bridge",
            "path": str(layout.repo_bridge),
            "target": str(layout.pool_repo),
            "valid": True,
        },
    ]
    if expected_engine == "hermes":
        return bridges_valid and summary.get("legacy_bridge_verified") is True
    return bridges_valid


def _managed_entry_record(
    entry: Path, layout: _FilesystemPoolLayout
) -> dict[str, Any] | None:
    try:
        entry_stat = entry.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISLNK(entry_stat.st_mode):
        return None
    target = _lexical_symlink_target(entry)
    source = ""
    pool_target: Path | None = None
    for source_root, source_name, pool_root in (
        (layout.legacy_local, "local", layout.pool_local),
        (layout.local_bridge, "local", layout.pool_local),
        (layout.legacy_repo, "repo", layout.pool_repo),
        (layout.repo_bridge, "repo", layout.pool_repo),
    ):
        try:
            relative = target.relative_to(source_root)
        except ValueError:
            continue
        source = source_name
        pool_target = pool_root / relative
        break
    if pool_target is None:
        return None
    try:
        entry.stat()
        valid = True
    except (FileNotFoundError, NotADirectoryError):
        valid = False
    return {
        "path": str(entry),
        "source": source,
        "legacy_target": str(target),
        "pool_target": str(pool_target),
        # The marker is a structural capability proof, not a content checksum.
        # Skills created after preparation are copied by the final-sync task.
        "valid": valid,
    }


def _managed_entries_valid(
    marker: dict[str, Any],
    layout: _FilesystemPoolLayout,
) -> bool:
    entries = layout.active_root.iterdir()
    for entry in entries:
        if entry in (layout.local_bridge, layout.repo_bridge):
            continue
        record = _managed_entry_record(entry, layout)
        if record is not None and record["valid"] is not True:
            return False

    declared = marker["validation_summary"]["managed_active_entries"]
    return not any(
        not isinstance(record, dict)
        or record.get("source") not in {"local", "repo"}
        or not isinstance(record.get("path"), str)
        or not isinstance(record.get("legacy_target"), str)
        or not isinstance(record.get("pool_target"), str)
        or record.get("valid") is not True
        for record in declared
    )


def _active_marker_valid(
    marker: object,
    *,
    layout: _FilesystemPoolLayout,
    engine: str,
    expected_contract_version: str,
    preparation_id: str,
) -> bool:
    if not isinstance(marker, dict):
        return False
    if (
        marker.get("engine") != engine
        or marker.get("layout_contract_version") != expected_contract_version
        or marker.get("preparation_id") != preparation_id
        or marker.get("activation_state") not in {"finalizing", "active"}
        or not isinstance(marker.get("migration_generation"), str)
    ):
        return False
    if marker["activation_state"] == "active":
        return True
    mappings = marker.get("mappings")
    if not isinstance(mappings, list):
        return False
    seen_targets: set[Path] = set()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            return False
        source_value = mapping.get("source")
        target_value = mapping.get("target")
        if not isinstance(source_value, str) or not isinstance(target_value, str):
            return False
        source = Path(os.path.abspath(source_value))
        target = Path(os.path.abspath(target_value))
        if not (
            source.is_relative_to(Path(os.path.abspath(layout.pool_local)))
            or source.is_relative_to(Path(os.path.abspath(layout.pool_repo)))
        ):
            return False
        if (
            target.parent != Path(os.path.abspath(layout.active_root))
            or target in {layout.local_bridge, layout.repo_bridge}
            or target in seen_targets
        ):
            return False
        seen_targets.add(target)
    return True


def _active_entries_valid(layout: _FilesystemPoolLayout) -> bool:
    """Validate mutable managed entries without freezing an old mapping set."""

    pool_roots = tuple(
        Path(os.path.abspath(root)) for root in (layout.pool_local, layout.pool_repo)
    )
    retired_roots = tuple(
        Path(os.path.abspath(root))
        for root in (
            layout.legacy_local,
            layout.legacy_repo,
            layout.local_bridge,
            layout.repo_bridge,
        )
    )
    for entry in layout.active_root.iterdir():
        if not entry.is_symlink():
            continue
        target = _lexical_symlink_target(entry)
        if any(target.is_relative_to(root) for root in pool_roots):
            try:
                target_stat = entry.stat()
            except (FileNotFoundError, NotADirectoryError):
                return False
            if not stat.S_ISDIR(target_stat.st_mode):
                return False
            continue
        if any(target.is_relative_to(root) for root in retired_roots):
            return False
        # External active entries predate Pool and remain outside this migration.
    return True


def inspect_runtime_layout(
    *,
    engine: str,
    expected_contract_version: str = LAYOUT_CONTRACT_VERSION,
    mapping_contract_version: str | None = MAPPING_CONTRACT_VERSION,
    home: Path = Path("/home/admin"),
    repo_is_mounted: Callable[[Path], bool] = os.path.ismount,
    repo_delivery: RepoDelivery | None = None,
) -> RuntimeLayoutInspection:
    """Inspect local runtime facts; this function never mutates the filesystem."""
    if engine == "teclaw":
        return _not_capable(
            engine,
            expected_contract_version,
            "engine_has_no_filesystem_pool_layout",
        )
    if engine not in {"openclaw", "claude_code", "aicoding", "hermes"}:
        return _not_capable(
            engine,
            expected_contract_version,
            "engine_pool_probe_not_implemented",
        )

    effective_repo_delivery = repo_delivery or current_repo_delivery()
    layout = resolve_filesystem_skill_layout(
        LayoutIdentity(
            engine_type=engine,
            layout_contract_version=expected_contract_version,
        ),
        RuntimeLayoutContext(home=home),
    )
    try:
        marker_stat = layout.marker.stat()
    except (FileNotFoundError, NotADirectoryError):
        result = _not_capable(
            engine,
            expected_contract_version,
            "pool_ready_marker_absent",
        )
        return RuntimeLayoutInspection(
            status=result.status,
            engine=result.engine,
            layout_contract_version=result.layout_contract_version,
            preparation_id=None,
            evidence={**result.evidence, "marker": str(layout.marker)},
        )
    except PermissionError:
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="marker_unreadable",
        )
    except OSError as error:
        return _transient(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="marker_temporarily_unreadable",
            error=error,
        )
    if not stat.S_ISREG(marker_stat.st_mode):
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="marker_not_regular_file",
        )
    try:
        marker = json.loads(layout.marker.read_bytes())
    except PermissionError:
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="marker_unreadable",
        )
    except OSError as error:
        return _transient(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="marker_temporarily_unreadable",
            error=error,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="invalid_marker_json",
        )
    if not isinstance(marker, dict) or not _marker_contract_valid(
        marker,
        layout=layout,
        expected_engine=engine,
        expected_contract_version=expected_contract_version,
        repo_delivery=effective_repo_delivery,
    ):
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="marker_contract_mismatch",
            preparation_id=(
                str(marker.get("preparation_id"))
                if isinstance(marker, dict) and marker.get("preparation_id")
                else None
            ),
        )

    preparation_id = str(marker["preparation_id"])
    try:
        pool_local_stat = layout.pool_local.lstat()
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        pool_local_stat = None
    except OSError as error:
        return _transient(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="pool_local_temporarily_unavailable",
            error=error,
            preparation_id=preparation_id,
        )
    if (
        pool_local_stat is None
        or stat.S_ISLNK(pool_local_stat.st_mode)
        or not stat.S_ISDIR(pool_local_stat.st_mode)
    ):
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="pool_local_invalid",
            preparation_id=preparation_id,
        )
    try:
        pool_repo_stat = layout.pool_repo.stat()
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        pool_repo_stat = None
    except OSError as error:
        return _transient(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="pool_repo_temporarily_unavailable",
            error=error,
            preparation_id=preparation_id,
        )
    pool_repo_delivered = False
    if effective_repo_delivery is RepoDelivery.MOUNT:
        try:
            pool_repo_delivered = repo_is_mounted(layout.pool_repo)
        except OSError as error:
            return _transient(
                engine=engine,
                contract_version=expected_contract_version,
                layout=layout,
                reason="pool_repo_temporarily_unavailable",
                error=error,
                preparation_id=preparation_id,
            )
    else:
        try:
            delivery_source = Path(str(marker["repo_delivery_source"]))
            pool_repo_delivered = (
                layout.pool_repo.is_symlink()
                and _lexical_symlink_target(layout.pool_repo)
                == Path(os.path.abspath(delivery_source))
            )
        except OSError as error:
            return _transient(
                engine=engine,
                contract_version=expected_contract_version,
                layout=layout,
                reason="pool_repo_temporarily_unavailable",
                error=error,
                preparation_id=preparation_id,
            )
    if (
        pool_repo_stat is None
        or not stat.S_ISDIR(pool_repo_stat.st_mode)
        or not pool_repo_delivered
    ):
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason=(
                "pool_repo_not_mounted"
                if effective_repo_delivery is RepoDelivery.MOUNT
                else "pool_repo_delivery_invalid"
            ),
            preparation_id=preparation_id,
        )
    try:
        with os.scandir(layout.pool_repo):
            pass
    except PermissionError:
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="pool_repo_unreadable",
            preparation_id=preparation_id,
        )
    except OSError as error:
        return _transient(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="pool_repo_temporarily_unavailable",
            error=error,
            preparation_id=preparation_id,
        )
    active_marker: dict[str, Any] | None = None
    try:
        active_marker_stat = layout.active_marker.lstat()
    except (FileNotFoundError, NotADirectoryError):
        active_marker_stat = None
    except PermissionError:
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="active_marker_unreadable",
            preparation_id=preparation_id,
        )
    except OSError as error:
        return _transient(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="active_marker_temporarily_unavailable",
            error=error,
            preparation_id=preparation_id,
        )
    if active_marker_stat is not None:
        if not stat.S_ISREG(active_marker_stat.st_mode):
            return _invalid(
                engine=engine,
                contract_version=expected_contract_version,
                layout=layout,
                reason="active_marker_not_regular_file",
                preparation_id=preparation_id,
            )
        try:
            active_marker = json.loads(layout.active_marker.read_bytes())
        except (PermissionError, UnicodeDecodeError, json.JSONDecodeError):
            return _invalid(
                engine=engine,
                contract_version=expected_contract_version,
                layout=layout,
                reason="active_marker_invalid",
                preparation_id=preparation_id,
            )
        except OSError as error:
            return _transient(
                engine=engine,
                contract_version=expected_contract_version,
                layout=layout,
                reason="active_marker_temporarily_unavailable",
                error=error,
                preparation_id=preparation_id,
            )
        if not _active_marker_valid(
            active_marker,
            layout=layout,
            engine=engine,
            expected_contract_version=expected_contract_version,
            preparation_id=preparation_id,
        ):
            return _invalid(
                engine=engine,
                contract_version=expected_contract_version,
                layout=layout,
                reason="active_marker_contract_mismatch",
                preparation_id=preparation_id,
            )
        if active_marker["activation_state"] == "active":
            retired_bridges = [("local", layout.local_bridge)]
            if engine in {"openclaw", "claude_code"} and not (
                engine == "openclaw"
                and effective_repo_delivery is RepoDelivery.DOWNLOAD
            ):
                retired_bridges.append(("repo", layout.repo_bridge))
            for bridge_name, bridge_path in retired_bridges:
                if bridge_path.exists() or bridge_path.is_symlink():
                    return _invalid(
                        engine=engine,
                        contract_version=expected_contract_version,
                        layout=layout,
                        reason=f"retired_{bridge_name}_bridge_present",
                        preparation_id=preparation_id,
                    )
            if engine in {"aicoding", "hermes"}:
                try:
                    repo_bridge_valid = (
                        layout.repo_bridge.is_symlink()
                        and _lexical_symlink_target(layout.repo_bridge)
                        == layout.pool_repo
                    )
                except OSError as error:
                    return _transient(
                        engine=engine,
                        contract_version=expected_contract_version,
                        layout=layout,
                        reason="stable_repo_bridge_temporarily_unavailable",
                        error=error,
                        preparation_id=preparation_id,
                    )
                if not repo_bridge_valid:
                    return _invalid(
                        engine=engine,
                        contract_version=expected_contract_version,
                        layout=layout,
                        reason="stable_repo_bridge_invalid",
                        preparation_id=preparation_id,
                    )
            try:
                active_entries_valid = _active_entries_valid(layout)
            except PermissionError:
                active_entries_valid = False
            except OSError as error:
                return _transient(
                    engine=engine,
                    contract_version=expected_contract_version,
                    layout=layout,
                    reason="active_entries_temporarily_unavailable",
                    error=error,
                    preparation_id=preparation_id,
                )
            if not active_entries_valid:
                return _invalid(
                    engine=engine,
                    contract_version=expected_contract_version,
                    layout=layout,
                    reason="active_managed_entry_invalid",
                    preparation_id=preparation_id,
                )
        active_checks = {
            "marker_valid": True,
            "active_marker_valid": True,
            "pool_local_valid": True,
            (
                "pool_repo_mounted"
                if effective_repo_delivery is RepoDelivery.MOUNT
                else "pool_repo_downloaded"
            ): True,
            "pool_repo_readable": True,
            "pool_mappings_valid": True,
            "legacy_storage_entries_absent": (
                active_marker["activation_state"] == "active"
            ),
        }
        if engine in {"aicoding", "hermes"}:
            active_checks["stable_repo_bridge_valid"] = True
        return RuntimeLayoutInspection(
            status=RuntimeLayoutInspectionStatus.READY,
            engine=engine,
            layout_contract_version=expected_contract_version,
            preparation_id=preparation_id,
            evidence=_ready_evidence(
                layout=layout,
                marker=marker,
                activation_state=active_marker["activation_state"],
                mapping_contract_version=mapping_contract_version,
                checks=active_checks,
            ),
        )
    if effective_repo_delivery is RepoDelivery.DOWNLOAD:
        required_bridges = []
        if engine != "openclaw":
            required_bridges.append(
                ("stable_local", layout.local_bridge, layout.legacy_local)
            )
            required_bridges.append(
                (
                    "legacy_repo_delivery",
                    layout.legacy_repo,
                    Path(str(marker["repo_delivery_source"])),
                )
            )
        if engine == "claude_code":
            required_bridges.append(
                ("legacy_repo", layout.repo_bridge, layout.legacy_repo)
            )
    else:
        required_bridges = [("legacy_repo", layout.repo_bridge, layout.pool_repo)]
        if engine != "openclaw":
            required_bridges = [
                ("stable_local", layout.local_bridge, layout.legacy_local),
                ("stable_repo", layout.repo_bridge, layout.pool_repo),
            ]
    for bridge_name, bridge_path, expected_target in required_bridges:
        try:
            bridge_stat = bridge_path.lstat()
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            bridge_stat = None
        except OSError as error:
            return _transient(
                engine=engine,
                contract_version=expected_contract_version,
                layout=layout,
                reason=f"{bridge_name}_bridge_temporarily_unavailable",
                error=error,
                preparation_id=preparation_id,
            )
        bridge_target = None
        if bridge_stat is not None and stat.S_ISLNK(bridge_stat.st_mode):
            try:
                bridge_target = _lexical_symlink_target(bridge_path)
            except OSError as error:
                return _transient(
                    engine=engine,
                    contract_version=expected_contract_version,
                    layout=layout,
                    reason=f"{bridge_name}_bridge_temporarily_unavailable",
                    error=error,
                    preparation_id=preparation_id,
                )
        if (
            bridge_stat is None
            or not stat.S_ISLNK(bridge_stat.st_mode)
            or bridge_target != expected_target
        ):
            return _invalid(
                engine=engine,
                contract_version=expected_contract_version,
                layout=layout,
                reason=f"{bridge_name}_bridge_invalid",
                preparation_id=preparation_id,
            )
    try:
        managed_entries_valid = _managed_entries_valid(marker, layout)
    except PermissionError:
        managed_entries_valid = False
    except OSError as error:
        return _transient(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="managed_active_entries_temporarily_unavailable",
            error=error,
            preparation_id=preparation_id,
        )
    if not managed_entries_valid:
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="managed_active_entry_invalid",
            preparation_id=preparation_id,
        )

    checks = {
        "marker_valid": True,
        "pool_local_valid": True,
        (
            "pool_repo_mounted"
            if effective_repo_delivery is RepoDelivery.MOUNT
            else "pool_repo_downloaded"
        ): True,
        "pool_repo_readable": True,
        "managed_active_entries_valid": True,
    }
    if engine == "openclaw" and effective_repo_delivery is RepoDelivery.MOUNT:
        checks["legacy_repo_bridge_valid"] = True
    else:
        if engine != "openclaw":
            checks["stable_local_bridge_valid"] = True
        if effective_repo_delivery is RepoDelivery.MOUNT:
            checks["stable_repo_bridge_valid"] = True
        if engine == "hermes":
            checks["legacy_local_bridge_valid"] = True
    return RuntimeLayoutInspection(
        status=RuntimeLayoutInspectionStatus.READY,
        engine=engine,
        layout_contract_version=expected_contract_version,
        preparation_id=preparation_id,
        evidence=_ready_evidence(
            layout=layout,
            marker=marker,
            mapping_contract_version=mapping_contract_version,
            checks=checks,
        ),
    )


__all__ = [
    "LAYOUT_CONTRACT_VERSION",
    "RuntimeLayoutInspection",
    "RuntimeLayoutInspectionStatus",
    "inspect_runtime_layout",
]
