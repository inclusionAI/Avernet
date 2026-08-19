"""Persisted asynchronous orchestration around the existing content scanner."""

from __future__ import annotations

import asyncio
from typing import Any

from injector import inject

from agentclaw.community.api.content_scanner_service import ContentScannerProtocol
from agentclaw.community.api.health_diagnosis_service import (
    HealthDiagnosisServiceProtocol,
)
from agentclaw.community.core.harness.errors import (
    HealthDiagnosisConflictError,
    HealthDiagnosisNotFoundError,
    HealthDiagnosisUnavailableError,
)
from agentclaw.community.core.harness.models import FindingsReport, Layer
from agentclaw.community.core.repository.protocols.harness import (
    HarnessScanRecordRepository,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class HealthDiagnosisService(HealthDiagnosisServiceProtocol):
    """Run the existing health scanner and persist its lifecycle."""

    @inject
    def __init__(
        self,
        scanner: ContentScannerProtocol,
        scan_repo: HarnessScanRecordRepository,
    ) -> None:
        self._scanner = scanner
        self._scan_repo = scan_repo
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(
        self,
        *,
        bot_id: str,
        owner_id: str,
        operator_id: str,
    ) -> dict[str, Any]:
        """Create a scan record and launch the existing scanner asynchronously."""
        try:
            active = await asyncio.to_thread(
                self._scan_repo.has_active_scan,
                bot_id,
                owner_id,
                5,
            )
            if active:
                raise HealthDiagnosisConflictError(
                    "a health diagnosis is already running"
                )

            initial = FindingsReport(
                bot_id=bot_id,
                entity_id=owner_id,
                scan_type="full",
                layer=Layer.L1,
                trigger_source="openapi",
                status="scanning",
            )
            scan_id = await asyncio.to_thread(self._scan_repo.create, initial)
        except HealthDiagnosisConflictError:
            raise
        except Exception as exc:
            raise HealthDiagnosisUnavailableError(
                "health diagnosis storage is unavailable"
            ) from exc

        task = asyncio.create_task(
            self._run(
                scan_id=scan_id,
                bot_id=bot_id,
                owner_id=owner_id,
                operator_id=operator_id,
            ),
            name=f"health-diagnosis-{scan_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"scan_id": scan_id, "bot_id": bot_id, "status": "scanning"}

    async def _run(
        self,
        *,
        scan_id: int,
        bot_id: str,
        owner_id: str,
        operator_id: str,
    ) -> None:
        try:
            report = await self._scanner.scan(
                entity_type="staff",
                entity_id=owner_id,
                bot_id=bot_id,
                operator_id=operator_id,
            )
            report.status = "completed"
            await asyncio.to_thread(self._scan_repo.complete, scan_id, report)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "health diagnosis failed for bot=%s scan_id=%s", bot_id, scan_id
            )
            try:
                await asyncio.to_thread(
                    self._scan_repo.update_status,
                    scan_id,
                    "failed",
                    "Health diagnosis failed",
                )
            except Exception:
                logger.exception(
                    "failed to persist health diagnosis failure for scan_id=%s",
                    scan_id,
                )

    async def get_recent(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> dict[str, Any] | None:
        try:
            return await asyncio.to_thread(
                self._scan_repo.get_recent,
                bot_id,
                owner_id,
                "full",
                Layer.L1.value,
            )
        except Exception as exc:
            raise HealthDiagnosisUnavailableError(
                "health diagnosis storage is unavailable"
            ) from exc

    async def get_by_id(
        self,
        *,
        scan_id: int,
        bot_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        try:
            record = await asyncio.to_thread(self._scan_repo.get_by_id, scan_id)
        except Exception as exc:
            raise HealthDiagnosisUnavailableError(
                "health diagnosis storage is unavailable"
            ) from exc

        # COSEC: scan ids are globally enumerable. Bind the record to the Bot and
        # resolved owner that already passed authorization before returning it.
        if (
            record is None
            or str(record.get("bot_id") or "") != bot_id
            or str(record.get("entity_id") or "") != owner_id
            or str(record.get("env") or "") != get_current_env()
        ):
            raise HealthDiagnosisNotFoundError("health diagnosis not found")
        return record


__all__ = ["HealthDiagnosisService"]
