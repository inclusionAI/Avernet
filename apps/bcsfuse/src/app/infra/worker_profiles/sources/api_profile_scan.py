"""Convert active Profile records using the existing API source rules."""

from __future__ import annotations

from logging import Logger
from typing import TYPE_CHECKING

from app.application.ports.worker_profile_content_store import (
    WorkerScopedProfileContentStore,
)
from app.domain.models.worker_profile import (
    WorkerProfileScanResult,
    WorkerProfileWarning,
)

if TYPE_CHECKING:
    from .api_profile_source import APIProfileSource


def scan_api_profiles(
    source: APIProfileSource, worker_ids: list[str] | None, logger: Logger,
) -> WorkerProfileScanResult:
    # Phase G6H Fix: Direct DB query every time, no caching
    # This matches root_original ApiWorkerProfileSource behavior
    store = source._get_content_store()
    profiles = []
    scan_warnings = []  # 使用正确的字段名

    try:
        # 获取所有活跃的 Profile
        all_records = (
            store.get_active_by_workers(worker_ids)
            if worker_ids is not None and isinstance(store, WorkerScopedProfileContentStore)
            else store.get_all_active()
        )

        for record in all_records:
            try:
                # Phase C3 Fix: Normalize record to WorkerProfileContent
                # Extract worker_id and profile_id from record
                worker_id = None
                profile_id = None
                if isinstance(record, dict):
                    worker_id = record.get("worker_id")
                    profile_id = record.get("profile_id")
                elif hasattr(record, 'worker_id'):
                    worker_id = record.worker_id
                    profile_id = record.profile_id

                if not worker_id:
                    logger.warning(f"[APIProfileSource] Skipping record without worker_id: {type(record).__name__}")
                    continue

                if worker_ids is not None and worker_id not in worker_ids:
                    continue

                content = source._normalize_profile_record(record, worker_id, profile_id)
                profile = source._convert_to_worker_profile(content)
                if profile:
                    profiles.append(profile)
                    logger.debug(
                        f"[APIProfileSource] Converted profile: {profile.profile_key}, "
                        f"fragments={len(profile.context_fragments or [])}, "
                        f"skills={len(profile.active_skills or [])}"
                    )
            except ValueError as e:
                # Normalization error
                worker_id_str = "unknown"
                profile_id_str = "unknown"
                if isinstance(record, dict):
                    worker_id_str = record.get("worker_id", "unknown")
                    profile_id_str = record.get("profile_id", "unknown")
                elif hasattr(record, 'worker_id'):
                    worker_id_str = record.worker_id
                    profile_id_str = record.profile_id

                scan_warnings.append(WorkerProfileWarning(
                    code="PROFILE_NORMALIZATION_ERROR",
                    message=f"Failed to normalize profile {worker_id_str}:{profile_id_str}: {e}",
                    source_path=None,
                    suggestion="Check profile content format - must have non-empty content field"
                ))
                logger.warning(
                    f"[APIProfileSource] Failed to normalize profile {worker_id_str}:{profile_id_str}: {e}"
                )
            except Exception as e:  # noqa: BLE001 - Preserve scan's per-record warnings.
                worker_id_str = "unknown"
                profile_id_str = "unknown"
                if isinstance(record, dict):
                    worker_id_str = record.get("worker_id", "unknown")
                    profile_id_str = record.get("profile_id", "unknown")
                elif hasattr(record, 'worker_id'):
                    worker_id_str = record.worker_id
                    profile_id_str = record.profile_id

                scan_warnings.append(WorkerProfileWarning(
                    code="PROFILE_CONVERSION_ERROR",
                    message=f"Failed to convert profile {worker_id_str}:{profile_id_str}: {e}",
                    source_path=None,
                    suggestion="Check profile content format"
                ))
                logger.warning(
                    f"[APIProfileSource] Failed to convert profile {worker_id_str}:{profile_id_str}: {e}"
                )

        # Phase G6H Fix: Do NOT cache the result, return directly
        result = WorkerProfileScanResult(
            profiles=profiles,
            scan_warnings=scan_warnings  # 使用正确的字段名
        )

        logger.info(
            f"[APIProfileSource] Scan complete: {len(profiles)} profiles, {len(scan_warnings)} warnings"
        )

        return result

    except Exception as e:  # noqa: BLE001 - Preserve scan's error-result contract.
        logger.error(f"[APIProfileSource] Scan failed: {e}")
        # Phase G6H Fix: Do NOT cache the error result, return directly
        return WorkerProfileScanResult(
            profiles=[],
            scan_warnings=[WorkerProfileWarning(  # 使用正确的字段名
                code="SCAN_ERROR",
                message=str(e),
                source_path=None,
                suggestion="Check database connection"
            )]
        )
