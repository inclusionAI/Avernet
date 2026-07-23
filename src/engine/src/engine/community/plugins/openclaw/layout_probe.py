"""OpenClaw 插件对持久化 Skills Pool layout 的运行时核验。"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from uuid import UUID


LAYOUT_CONTRACT_VERSION = "skills-pool-p3-v1"


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


@dataclass(frozen=True)
class _OpenClawLayout:
    legacy_root: Path
    legacy_local: Path
    legacy_repo: Path
    pool_root: Path
    pool_local: Path
    pool_repo: Path
    marker: Path

    @classmethod
    def for_home(cls, home: Path) -> "_OpenClawLayout":
        workspace = home / ".openclaw" / "workspace"
        legacy_root = workspace / "skills"
        pool_root = workspace / "skills-pool"
        return cls(
            legacy_root=legacy_root,
            legacy_local=legacy_root / "skills-local",
            legacy_repo=legacy_root / "skills-repo",
            pool_root=pool_root,
            pool_local=pool_root / "skills-local",
            pool_repo=pool_root / "skills-repo",
            marker=pool_root / ".pool-ready",
        )


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
    layout: _OpenClawLayout,
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
    layout: _OpenClawLayout,
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
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
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
    layout: _OpenClawLayout,
    expected_contract_version: str,
) -> bool:
    summary = marker.get("validation_summary")
    if not isinstance(summary, dict):
        return False
    bridge = summary.get("legacy_repo_bridge")
    pool_local = summary.get("pool_local")
    pool_repo = summary.get("pool_repo")
    managed = summary.get("managed_active_entries")
    return (
        marker.get("engine") == "openclaw"
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
        and pool_repo.get("readable_mount") is True
        and pool_repo.get("valid") is True
        and isinstance(bridge, dict)
        and bridge.get("path") == str(layout.legacy_repo)
        and bridge.get("target") == str(layout.pool_repo)
        and bridge.get("valid") is True
        and isinstance(managed, list)
    )


def _managed_entry_record(
    entry: Path, layout: _OpenClawLayout
) -> dict[str, Any] | None:
    try:
        entry_stat = entry.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISLNK(entry_stat.st_mode):
        return None
    target = _lexical_symlink_target(entry)
    try:
        relative = target.relative_to(layout.legacy_local)
        source = "local"
        pool_target = layout.pool_local / relative
    except ValueError:
        try:
            relative = target.relative_to(layout.legacy_repo)
            source = "repo"
            pool_target = layout.pool_repo / relative
        except ValueError:
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


def _managed_entries_valid(marker: dict[str, Any], layout: _OpenClawLayout) -> bool:
    entries = layout.legacy_root.iterdir()
    for entry in entries:
        if entry in (layout.legacy_local, layout.legacy_repo):
            continue
        record = _managed_entry_record(entry, layout)
        if record is not None:
            if record["valid"] is not True:
                return False

    declared = marker["validation_summary"]["managed_active_entries"]
    if any(
        not isinstance(record, dict)
        or record.get("source") not in {"local", "repo"}
        or not isinstance(record.get("path"), str)
        or not isinstance(record.get("legacy_target"), str)
        or not isinstance(record.get("pool_target"), str)
        or record.get("valid") is not True
        for record in declared
    ):
        return False
    return True


def inspect_runtime_layout(
    *,
    engine: str,
    expected_contract_version: str = LAYOUT_CONTRACT_VERSION,
    home: Path = Path("/home/admin"),
    repo_is_mounted: Callable[[Path], bool] = os.path.ismount,
) -> RuntimeLayoutInspection:
    """Inspect local runtime facts; this function never mutates the filesystem."""
    if engine == "teclaw":
        return _not_capable(
            engine,
            expected_contract_version,
            "engine_has_no_filesystem_pool_layout",
        )
    if engine != "openclaw":
        return _not_capable(
            engine,
            expected_contract_version,
            "engine_pool_probe_not_implemented",
        )

    layout = _OpenClawLayout.for_home(home)
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
        expected_contract_version=expected_contract_version,
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
    try:
        pool_repo_mounted = repo_is_mounted(layout.pool_repo)
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
        or not pool_repo_mounted
    ):
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="pool_repo_not_mounted",
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
    try:
        legacy_repo_stat = layout.legacy_repo.lstat()
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        legacy_repo_stat = None
    except OSError as error:
        return _transient(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="legacy_repo_bridge_temporarily_unavailable",
            error=error,
            preparation_id=preparation_id,
        )
    legacy_repo_target = None
    if legacy_repo_stat is not None and stat.S_ISLNK(legacy_repo_stat.st_mode):
        try:
            legacy_repo_target = _lexical_symlink_target(layout.legacy_repo)
        except OSError as error:
            return _transient(
                engine=engine,
                contract_version=expected_contract_version,
                layout=layout,
                reason="legacy_repo_bridge_temporarily_unavailable",
                error=error,
                preparation_id=preparation_id,
            )
    if (
        legacy_repo_stat is None
        or not stat.S_ISLNK(legacy_repo_stat.st_mode)
        or legacy_repo_target != layout.pool_repo
    ):
        return _invalid(
            engine=engine,
            contract_version=expected_contract_version,
            layout=layout,
            reason="legacy_repo_bridge_invalid",
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
        "pool_repo_mounted": True,
        "pool_repo_readable": True,
        "legacy_repo_bridge_valid": True,
        "managed_active_entries_valid": True,
    }
    return RuntimeLayoutInspection(
        status=RuntimeLayoutInspectionStatus.READY,
        engine=engine,
        layout_contract_version=expected_contract_version,
        preparation_id=preparation_id,
        evidence={
            "marker": str(layout.marker),
            "prepared_at": marker["prepared_at"],
            "checks": checks,
        },
    )


__all__ = [
    "LAYOUT_CONTRACT_VERSION",
    "RuntimeLayoutInspection",
    "RuntimeLayoutInspectionStatus",
    "inspect_runtime_layout",
]
