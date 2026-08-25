"""Bot-scoped public access to the existing health diagnosis capability."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import (
    resolve_operable_bot,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.health_diagnosis_service import (
    HealthDiagnosisServiceProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotOperationNotAllowedError,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.di import Injected

from .schemas import (
    BotHealth,
    HealthCheckAccepted,
    HealthCheckItem,
    HealthDiagnosisStatus,
    HealthFindingDetail,
    HealthFindingGroup,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute


router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/diagnostics", tags=["diagnostics"], route_class=PublicAPIRoute)

ScanIdQuery = Annotated[
    int | None,
    Query(
        ge=1,
        description="Diagnosis identifier returned by health-check; omit for the latest completed result.",
    ),
]

_STATUSES = frozenset({"scanning", "scan_completed", "patching", "completed", "failed"})


def _status(value: Any) -> HealthDiagnosisStatus:
    candidate = str(value or "")
    if candidate == "running":
        candidate = "scanning"
    return cast(
        HealthDiagnosisStatus, candidate if candidate in _STATUSES else "failed"
    )


def _check_items(record: dict[str, Any]) -> list[HealthCheckItem]:
    status = _status(record.get("status"))
    result: list[HealthCheckItem] = []
    for raw in record.get("check_items") or []:
        if not isinstance(raw, dict):
            continue
        name = raw.get("check_item") or raw.get("rule_name") or raw.get("rule_id")
        if not name:
            continue
        result.append(
            HealthCheckItem(
                name=str(name),
                status=str(raw.get("status") or status),
                result=str(raw["result"]) if raw.get("result") is not None else None,
                score=raw.get("score"),
                duration_ms=raw.get("cost"),
            )
        )
    return result


def _findings(record: dict[str, Any]) -> list[HealthFindingGroup]:
    result: list[HealthFindingGroup] = []
    for group in record.get("findings") or []:
        if not isinstance(group, dict):
            continue
        details: list[HealthFindingDetail] = []
        for raw in group.get("finding_details") or []:
            if not isinstance(raw, dict):
                continue
            rule_id = str(raw.get("rule_id") or "")
            # Harness system findings can contain the original upstream
            # exception. Public callers need the diagnosis state, not internal
            # paths, hosts, or credentials embedded in that exception text.
            message = (
                "Diagnostic item failed"
                if rule_id in {"SYS01", "SYS02"}
                else str(raw.get("message") or "")
            )
            details.append(
                HealthFindingDetail(
                    rule_id=rule_id,
                    name=str(raw.get("name") or ""),
                    message=message,
                    risk_level=str(raw.get("risk_level") or ""),
                    result=str(raw.get("result") or ""),
                    score=int(raw.get("score") or 0),
                )
            )
        result.append(
            HealthFindingGroup(
                check_item=str(group.get("check_item") or ""),
                findings=details,
            )
        )
    return result


def _project(bot_id: str, record: dict[str, Any] | None) -> BotHealth:
    if record is None:
        return BotHealth(found=False, bot_id=bot_id, status="not_run")
    status = _status(record.get("status"))
    completed = status == "completed"
    return BotHealth(
        found=True,
        bot_id=bot_id,
        scan_id=int(record["id"]),
        status=status,
        health_score=record.get("health_score") if completed else None,
        grade=(
            str(record.get("score_grade"))
            if completed and record.get("score_grade")
            else None
        ),
        summary=record.get("findings_summary") or {},
        check_items=_check_items(record),
        findings=_findings(record) if completed else [],
        failed_reason="Health diagnosis failed" if status == "failed" else None,
        duration_ms=record.get("duration_ms") if completed else None,
        created_at=record.get("gmt_create"),
    )


async def _authorize(
    *,
    relay: EngineRuntimeRelayProtocol,
    bot_id: str,
    actor_id: str,
    owner_id: str,
) -> str:
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=actor_id,
        owner_id=owner_id,
        stage="draft",
        surface="diagnostics",
    )
    if (facts.active_engine or DEFAULT_ENGINE_TYPE) != DEFAULT_ENGINE_TYPE:
        raise BotOperationNotAllowedError(
            "health diagnosis is supported only by openclaw"
        )
    return facts.owner_id


@router.get("/health", response_model=Envelope[BotHealth])
@envelope_errors
async def get_health(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    scan_id: ScanIdQuery = None,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    service: HealthDiagnosisServiceProtocol = Injected(HealthDiagnosisServiceProtocol),
) -> Envelope[BotHealth]:
    """Return a requested diagnosis, or the latest completed diagnosis."""
    resolved_owner = await _authorize(
        relay=relay,
        bot_id=bot_id,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    if scan_id is None:
        record = await service.get_recent(bot_id=bot_id, owner_id=resolved_owner)
    else:
        record = await service.get_by_id(
            scan_id=scan_id,
            bot_id=bot_id,
            owner_id=resolved_owner,
        )
    return envelope(_project(bot_id, record), request)


@router.post(
    "/health-check",
    status_code=202,
    response_model=Envelope[HealthCheckAccepted],
)
@envelope_errors
async def start_health_check(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    service: HealthDiagnosisServiceProtocol = Injected(HealthDiagnosisServiceProtocol),
) -> Envelope[HealthCheckAccepted]:
    """Start an asynchronous health diagnosis for an OpenClaw cloud Bot."""
    resolved_owner = await _authorize(
        relay=relay,
        bot_id=bot_id,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    result = await service.start(
        bot_id=bot_id,
        owner_id=resolved_owner,
        operator_id=actor_id,
    )
    return accepted(HealthCheckAccepted.model_validate(result), request)


__all__ = ["get_health", "router", "start_health_check"]
