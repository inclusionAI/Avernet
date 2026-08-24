"""Health diagnosis orchestration tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.harness.errors import (
    HealthDiagnosisConflictError,
    HealthDiagnosisNotFoundError,
    HealthDiagnosisUnavailableError,
)
from agentclaw.community.core.harness.models import FindingsReport
from agentclaw.community.core.harness.services.health_diagnosis_service import (
    HealthDiagnosisService,
)
from agentclaw.community.utils.env_utils import get_current_env


@pytest.mark.asyncio
async def test_start_persists_and_completes_existing_scanner_report():
    scanner = AsyncMock()
    scanner.scan.return_value = FindingsReport(
        bot_id="bot-1",
        entity_id="owner",
        health_score=92,
        score_grade="excellent",
    )
    repo = Mock()
    repo.has_active_scan.return_value = False
    repo.create.return_value = 7
    service = HealthDiagnosisService(scanner, repo)

    result = await service.start(bot_id="bot-1", owner_id="owner", operator_id="member")
    await asyncio.gather(*tuple(service._tasks))

    assert result == {"scan_id": 7, "bot_id": "bot-1", "status": "scanning"}
    repo.create.assert_called_once()
    scanner.scan.assert_awaited_once_with(
        entity_type="staff",
        entity_id="owner",
        bot_id="bot-1",
        operator_id="member",
    )
    completed = repo.complete.call_args.args[1]
    assert completed.status == "completed"
    assert completed.health_score == 92


@pytest.mark.asyncio
async def test_start_rejects_recent_active_scan():
    repo = Mock()
    repo.has_active_scan.return_value = True
    service = HealthDiagnosisService(AsyncMock(), repo)

    with pytest.raises(HealthDiagnosisConflictError):
        await service.start(bot_id="bot-1", owner_id="owner", operator_id="owner")

    repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_start_normalizes_repository_failure():
    repo = Mock()
    repo.has_active_scan.side_effect = RuntimeError("database detail")
    service = HealthDiagnosisService(AsyncMock(), repo)

    with pytest.raises(HealthDiagnosisUnavailableError):
        await service.start(bot_id="bot-1", owner_id="owner", operator_id="owner")


@pytest.mark.asyncio
async def test_failed_scan_is_persisted_without_leaking_exception_text():
    scanner = AsyncMock()
    scanner.scan.side_effect = RuntimeError("sensitive upstream detail")
    repo = Mock()
    service = HealthDiagnosisService(scanner, repo)

    await service._run(scan_id=8, bot_id="bot-1", owner_id="owner", operator_id="owner")

    repo.update_status.assert_called_once_with(8, "failed", "Health diagnosis failed")


@pytest.mark.asyncio
async def test_scan_id_is_bound_to_authorized_bot_and_owner():
    repo = Mock()
    repo.get_by_id.return_value = {
        "id": 9,
        "bot_id": "other-bot",
        "entity_id": "owner",
    }
    service = HealthDiagnosisService(AsyncMock(), repo)

    with pytest.raises(HealthDiagnosisNotFoundError):
        await service.get_by_id(scan_id=9, bot_id="bot-1", owner_id="owner")


@pytest.mark.asyncio
async def test_get_by_id_returns_record_in_authorized_environment():
    repo = Mock()
    repo.get_by_id.return_value = {
        "id": 9,
        "bot_id": "bot-1",
        "entity_id": "owner",
        "env": get_current_env(),
    }
    service = HealthDiagnosisService(AsyncMock(), repo)

    result = await service.get_by_id(scan_id=9, bot_id="bot-1", owner_id="owner")

    assert result["id"] == 9


@pytest.mark.asyncio
async def test_get_recent_uses_bot_owner_and_fixed_diagnosis_scope():
    repo = Mock()
    repo.get_recent.return_value = {"id": 10}
    service = HealthDiagnosisService(AsyncMock(), repo)

    result = await service.get_recent(bot_id="bot-1", owner_id="owner")

    assert result == {"id": 10}
    repo.get_recent.assert_called_once_with("bot-1", "owner", "full", "L1")
