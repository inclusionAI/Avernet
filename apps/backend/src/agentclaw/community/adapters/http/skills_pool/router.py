"""Operator-only Skills Pool rollout, evidence and recovery API."""

from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from agentclaw.community.api.skills_pool_operational_query_service import (
    SkillsPoolOperationalQueryServiceProtocol,
)
from agentclaw.community.api.skills_pool_operator_commands_service import (
    SkillsPoolOperatorCommandsServiceProtocol,
)
from agentclaw.community.api.skills_pool_recovery_service import (
    SkillsPoolRecoveryServiceProtocol,
)
from agentclaw.community.api.skills_pool_rollback_service import (
    SkillsPoolRollbackOutcome,
    SkillsPoolRollbackServiceProtocol,
)
from agentclaw.community.api.skills_pool_rollout_service import (
    SkillsPoolRolloutServiceProtocol,
)
from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.skills_pool.schemas import (
    ApiResponse,
    BotPolicyRequest,
    BotIdentityRequest,
    EngineAdmissionRequest,
    FeatureToggleRequest,
    OwnerPolicyRequest,
    PolicyMutationRequest,
    RepairRequest,
    RollbackRequest,
)
from agentclaw.community.core.skills_pool.operational_query import (
    SkillsPoolOperationalQueryError,
)
from agentclaw.community.core.skills_pool.operations import RolloutOperationError
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.di import Injected
from agentclaw.community.utils.env_utils import get_current_env


router = APIRouter(prefix="/api/ops/skills-pool", tags=["skills-pool-ops"])


def _response(value: object, message: str = "OK") -> ApiResponse:
    data = asdict(value)
    data.pop("legacy_teclaw_controls", None)
    return ApiResponse(success=True, message=message, data=data)


def _resolve_scope(
    *,
    query: SkillsPoolOperationalQueryServiceProtocol,
    owner_id: str,
    bot_id: str,
) -> BotSkillLayoutScope:
    try:
        return query.get_bot(
            env=get_current_env(),
            owner_id=owner_id,
            bot_id=bot_id,
        ).scope
    except SkillsPoolOperationalQueryError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/rollout", response_model=ApiResponse)
async def get_rollout(
    _: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(service.get_snapshot(env=get_current_env()))
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/rollout/feature", response_model=ApiResponse)
async def set_rollout_feature(
    request: FeatureToggleRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_feature_enabled(
                env=get_current_env(),
                enabled=request.enabled,
                expected_revision=request.expected_revision,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put("/rollout/engines/{engine}/admission", response_model=ApiResponse)
async def set_engine_admission(
    engine: str,
    request: EngineAdmissionRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_engine_admission(
                env=get_current_env(),
                engine=engine,
                enabled=request.enabled,
                expected_revision=request.expected_revision,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put("/rollout/environments/{engine}", response_model=ApiResponse)
async def enable_environment_rollout(
    engine: str,
    request: PolicyMutationRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_environment_rollout(
                env=get_current_env(),
                enabled=True,
                engine=engine,
                expected_revision=request.expected_revision,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/rollout/environments/{engine}", response_model=ApiResponse)
async def disable_environment_rollout(
    engine: str,
    request: PolicyMutationRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_environment_rollout(
                env=get_current_env(),
                enabled=False,
                engine=engine,
                expected_revision=request.expected_revision,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put("/rollout/owners/{owner_id}", response_model=ApiResponse)
async def enable_owner_rollout(
    owner_id: str,
    request: OwnerPolicyRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_owner_rollout(
                env=get_current_env(),
                owner_id=owner_id,
                engine=request.engine,
                enabled=True,
                expected_revision=request.expected_revision,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/rollout/owners/{owner_id}", response_model=ApiResponse)
async def disable_owner_rollout(
    owner_id: str,
    request: OwnerPolicyRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_owner_rollout(
                env=get_current_env(),
                owner_id=owner_id,
                engine=request.engine,
                enabled=False,
                expected_revision=request.expected_revision,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put("/rollout/bots/{bot_id}/allow", response_model=ApiResponse)
async def add_bot_allow(
    bot_id: str,
    request: BotPolicyRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_bot_allow(
                env=get_current_env(),
                owner_id=request.owner_id,
                bot_id=bot_id,
                engine=request.engine,
                present=True,
                expected_revision=request.expected_revision,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/rollout/bots/{bot_id}/allow", response_model=ApiResponse)
async def remove_bot_allow(
    bot_id: str,
    request: BotPolicyRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_bot_allow(
                env=get_current_env(),
                owner_id=request.owner_id,
                bot_id=bot_id,
                engine=request.engine,
                present=False,
                expected_revision=request.expected_revision,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put("/rollout/bots/{bot_id}/exclude", response_model=ApiResponse)
async def add_bot_exclusion(
    bot_id: str,
    request: BotPolicyRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_bot_exclusion(
                env=get_current_env(),
                owner_id=request.owner_id,
                bot_id=bot_id,
                engine=request.engine,
                present=True,
                expected_revision=request.expected_revision,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/rollout/bots/{bot_id}/exclude", response_model=ApiResponse)
async def remove_bot_exclusion(
    bot_id: str,
    request: BotPolicyRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_bot_exclusion(
                env=get_current_env(),
                owner_id=request.owner_id,
                bot_id=bot_id,
                engine=request.engine,
                present=False,
                expected_revision=request.expected_revision,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/rollout/promote",
    response_model=ApiResponse,
    operation_id="retired_skills_pool_engine_promotion",
)
@router.post(
    "/rollout/full",
    response_model=ApiResponse,
    operation_id="retired_skills_pool_full_rollout",
)
@router.post(
    "/rollout/whitelist",
    response_model=ApiResponse,
    operation_id="retired_skills_pool_whitelist_add",
)
@router.post(
    "/rollout/whitelist/remove",
    response_model=ApiResponse,
    operation_id="retired_skills_pool_whitelist_remove",
)
@router.post(
    "/rollout/owners",
    response_model=ApiResponse,
    operation_id="retired_skills_pool_owner_rollout",
)
@router.post(
    "/rollout/batches/accept",
    response_model=ApiResponse,
    operation_id="retired_skills_pool_batch_acceptance",
)
@router.post(
    "/rollout/controls",
    response_model=ApiResponse,
    operation_id="retired_skills_pool_control_bot",
)
async def retired_batch_write(
    _: dict[str, object],
    __: AuthenticatedUser = Depends(require_operator),
):
    raise HTTPException(
        status_code=410,
        detail="ROLLOUT_BATCH_API_RETIRED",
    )


@router.get("/bots/{bot_id}", response_model=ApiResponse)
async def get_bot_evidence(
    bot_id: str,
    owner_id: str = Query(...),
    _: AuthenticatedUser = Depends(require_operator),
    query: SkillsPoolOperationalQueryServiceProtocol = Injected(
        SkillsPoolOperationalQueryServiceProtocol
    ),
):
    try:
        return _response(
            query.get_bot(
                env=get_current_env(),
                owner_id=owner_id,
                bot_id=bot_id,
            )
        )
    except SkillsPoolOperationalQueryError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/batches/{batch_id}", response_model=ApiResponse)
async def get_batch_evidence(
    batch_id: str,
    engine: str = Query(...),
    _: AuthenticatedUser = Depends(require_operator),
    query: SkillsPoolOperationalQueryServiceProtocol = Injected(
        SkillsPoolOperationalQueryServiceProtocol
    ),
):
    try:
        return _response(
            query.summarize_batch(
                env=get_current_env(),
                engine=engine,
                batch_id=batch_id,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/bots/{bot_id}/wake", response_model=ApiResponse)
async def wake_bot(
    bot_id: str,
    request: BotIdentityRequest,
    user: AuthenticatedUser = Depends(require_operator),
    commands: SkillsPoolOperatorCommandsServiceProtocol = Injected(
        SkillsPoolOperatorCommandsServiceProtocol
    ),
    query: SkillsPoolOperationalQueryServiceProtocol = Injected(
        SkillsPoolOperationalQueryServiceProtocol
    ),
):
    scope = _resolve_scope(
        query=query,
        owner_id=request.owner_id,
        bot_id=bot_id,
    )
    return _response(
        commands.wake(
            scope=scope,
            operator=user.staffId,
        )
    )


@router.post("/bots/{bot_id}/retry", response_model=ApiResponse)
async def retry_bot(
    bot_id: str,
    request: BotIdentityRequest,
    user: AuthenticatedUser = Depends(require_operator),
    commands: SkillsPoolOperatorCommandsServiceProtocol = Injected(
        SkillsPoolOperatorCommandsServiceProtocol
    ),
    query: SkillsPoolOperationalQueryServiceProtocol = Injected(
        SkillsPoolOperationalQueryServiceProtocol
    ),
):
    scope = _resolve_scope(
        query=query,
        owner_id=request.owner_id,
        bot_id=bot_id,
    )
    return _response(
        commands.wake(
            scope=scope,
            operator=user.staffId,
            retry_only=True,
        )
    )


@router.post("/bots/{bot_id}/repair", response_model=ApiResponse)
async def repair_bot(
    bot_id: str,
    request: RepairRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRecoveryServiceProtocol = Injected(
        SkillsPoolRecoveryServiceProtocol
    ),
    query: SkillsPoolOperationalQueryServiceProtocol = Injected(
        SkillsPoolOperationalQueryServiceProtocol
    ),
):
    scope = _resolve_scope(
        query=query,
        owner_id=request.owner_id,
        bot_id=bot_id,
    )
    return _response(
        service.resolve_repair_state(
            scope=scope,
            migration_generation=request.migration_generation,
            operator=user.staffId,
            note=request.note,
            resolution=request.resolution,
        )
    )


@router.post("/bots/{bot_id}/rollback", response_model=ApiResponse)
async def rollback_bot(
    bot_id: str,
    request: RollbackRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRollbackServiceProtocol = Injected(
        SkillsPoolRollbackServiceProtocol
    ),
    query: SkillsPoolOperationalQueryServiceProtocol = Injected(
        SkillsPoolOperationalQueryServiceProtocol
    ),
):
    scope = _resolve_scope(
        query=query,
        owner_id=request.owner_id,
        bot_id=bot_id,
    )
    result = await service.rollback(
        scope=scope,
        rollback_generation=request.rollback_generation,
        lease_owner=f"operator-api:{uuid4().hex}",
        operator=user.staffId,
        note=request.note,
    )
    if result.outcome is SkillsPoolRollbackOutcome.SERVICE_BOT_UNSUPPORTED:
        raise HTTPException(
            status_code=409,
            detail="Service Bot Skills Pool rollback is disabled",
        )
    return _response(result)
