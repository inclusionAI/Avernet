"""Public health diagnosis endpoint contract tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.diagnostics.router import (
    get_health,
    start_health_check,
)
from agentclaw.community.core.engine_runtime.models import BotFacts


def request(method: str = "GET") -> Request:
    req = Request({"type": "http", "method": method, "path": "/"})
    req.state.trace_id = "trace-health"
    return req


def relay(engine: str = "openclaw") -> AsyncMock:
    value = AsyncMock()
    value.resolve_bot_off_loop.return_value = BotFacts(
        bot_id="bot-1",
        bot_type="service",
        active_engine=engine,
        owner_id="owner",
    )
    return value


@pytest.mark.asyncio
async def test_get_latest_health_projects_completed_result():
    service = AsyncMock()
    service.get_recent.return_value = {
        "id": 17,
        "status": "completed",
        "health_score": 86,
        "score_grade": "good",
        "findings_summary": {"warning": 1},
        "check_items": [
            {"rule_id": "D-1", "rule_name": "Agent role", "severity": "warning"}
        ],
        "findings": [
            {
                "check_item": "AGENTS.md",
                "finding_details": [
                    {
                        "rule_id": "D-1",
                        "name": "Agent role",
                        "message": "Role is incomplete",
                        "risk_level": "warning",
                        "result": "warning",
                        "score": 86,
                    }
                ],
            }
        ],
        "duration_ms": 1200,
        "gmt_create": "2026-08-18T10:00:00",
    }

    response = await get_health(
        "bot-1", request(), "member", "owner", None, relay(), service
    )

    assert response.request_id == "trace-health"
    assert response.data.health_score == 86
    assert response.data.grade == "good"
    assert response.data.check_items[0].name == "Agent role"
    assert response.data.findings[0].findings[0].rule_id == "D-1"
    service.get_recent.assert_awaited_once_with(bot_id="bot-1", owner_id="owner")


@pytest.mark.asyncio
async def test_get_health_by_scan_id_supports_polling():
    service = AsyncMock()
    service.get_by_id.return_value = {
        "id": 18,
        "status": "scanning",
        "findings_summary": {},
        "check_items": [],
        "findings": [],
    }

    response = await get_health(
        "bot-1", request(), "member", "owner", 18, relay(), service
    )

    assert response.data.status == "scanning"
    assert response.data.health_score is None
    service.get_by_id.assert_awaited_once_with(
        scan_id=18, bot_id="bot-1", owner_id="owner"
    )


@pytest.mark.asyncio
async def test_failed_health_check_does_not_publish_internal_failure_detail():
    service = AsyncMock()
    service.get_by_id.return_value = {
        "id": 18,
        "status": "failed",
        "failed_reason": "database host and secret detail",
    }

    response = await get_health(
        "bot-1", request(), "owner", "owner", 18, relay(), service
    )

    assert response.data.failed_reason == "Health diagnosis failed"


@pytest.mark.asyncio
async def test_system_finding_does_not_publish_upstream_exception_detail():
    service = AsyncMock()
    service.get_by_id.return_value = {
        "id": 18,
        "status": "completed",
        "findings": [
            {
                "check_item": "_unknown",
                "finding_details": [
                    {
                        "rule_id": "SYS02",
                        "name": "Diagnostic failed",
                        "message": "connection failed with secret=internal-value",
                        "risk_level": "critical",
                        "result": "fail",
                        "score": 0,
                    }
                ],
            }
        ],
    }

    response = await get_health(
        "bot-1", request(), "owner", "owner", 18, relay(), service
    )

    assert response.data.findings[0].findings[0].message == "Diagnostic item failed"


@pytest.mark.asyncio
async def test_get_health_returns_not_run_when_no_completed_result_exists():
    service = AsyncMock()
    service.get_recent.return_value = None

    response = await get_health(
        "bot-1", request(), "owner", "owner", None, relay(), service
    )

    assert response.data.model_dump() == {
        "found": False,
        "bot_id": "bot-1",
        "scan_id": None,
        "status": "not_run",
        "health_score": None,
        "grade": None,
        "summary": {},
        "check_items": [],
        "findings": [],
        "failed_reason": None,
        "duration_ms": None,
        "created_at": None,
    }


@pytest.mark.asyncio
async def test_start_health_check_returns_accepted_scan():
    service = AsyncMock()
    service.start.return_value = {
        "bot_id": "bot-1",
        "scan_id": 19,
        "status": "scanning",
    }

    response = await start_health_check(
        "bot-1", request("POST"), "member", "owner", relay(), service
    )

    assert response.code == 202000
    assert response.data.scan_id == 19
    service.start.assert_awaited_once_with(
        bot_id="bot-1", owner_id="owner", operator_id="member"
    )


@pytest.mark.asyncio
async def test_non_openclaw_bot_is_refused_before_diagnosis_access():
    service = AsyncMock()

    response = await get_health(
        "bot-1", request(), "owner", "owner", None, relay("hermes"), service
    )

    assert response.status_code == 409
    service.get_recent.assert_not_awaited()
