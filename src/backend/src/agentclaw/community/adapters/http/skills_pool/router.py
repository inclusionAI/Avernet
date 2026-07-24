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
    SkillsPoolRollbackServiceProtocol,
)
from agentclaw.community.api.skills_pool_rollout_service import (
    SkillsPoolRolloutServiceProtocol,
)
from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.skills_pool.schemas import (
    ApiResponse,
    BatchAcceptanceRequest,
    BotIdentityRequest,
    ControlBotRequest,
    EnginePromotionRequest,
    FeatureToggleRequest,
    RepairRequest,
    RollbackRequest,
    WhitelistAddRequest,
    WhitelistRemoveRequest,
)
from agentclaw.community.core.skills_pool.operational_query import (
    SkillsPoolOperationalQueryError,
)
from agentclaw.community.core.skills_pool.operations import (
    BatchPromotionEvidence,
    RolloutOperationError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.di import Injected
from agentclaw.community.utils.env_utils import get_current_env


router = APIRouter(prefix="/api/ops/skills-pool", tags=["skills-pool-ops"])


def _response(value: object, message: str = "OK") -> ApiResponse:
    return ApiResponse(success=True, message=message, data=asdict(value))


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
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/rollout/promote", response_model=ApiResponse)
async def promote_engine(
    request: EnginePromotionRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.promote_engine(
                env=get_current_env(),
                engine=request.engine,
                operator=user.staffId,
                reason=request.reason,
                acceptance_batch_id=request.acceptance_batch_id,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/rollout/whitelist", response_model=ApiResponse)
async def add_whitelist_bot(
    request: WhitelistAddRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.add_bot(
                env=get_current_env(),
                owner_id=request.owner_id,
                bot_id=request.bot_id,
                batch_id=request.batch_id,
                acceptance_batch_id=request.acceptance_batch_id,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/rollout/whitelist/remove", response_model=ApiResponse)
async def remove_whitelist_bot(
    request: WhitelistRemoveRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.remove_bot(
                env=get_current_env(),
                owner_id=request.owner_id,
                bot_id=request.bot_id,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/rollout/batches/accept", response_model=ApiResponse)
async def accept_batch(
    request: BatchAcceptanceRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
    query: SkillsPoolOperationalQueryServiceProtocol = Injected(
        SkillsPoolOperationalQueryServiceProtocol
    ),
):
    try:
        report = query.summarize_batch(
            env=get_current_env(),
            engine=request.engine,
            batch_id=request.batch_id,
        )
        return _response(
            service.accept_batch(
                env=get_current_env(),
                acceptance=BatchPromotionEvidence(
                    engine=request.engine,
                    batch_id=request.batch_id,
                    promotion_ready=report.promotion_ready,
                    report=asdict(report),
                ),
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/rollout/controls", response_model=ApiResponse)
async def set_control_bot(
    request: ControlBotRequest,
    user: AuthenticatedUser = Depends(require_operator),
    service: SkillsPoolRolloutServiceProtocol = Injected(
        SkillsPoolRolloutServiceProtocol
    ),
):
    try:
        return _response(
            service.set_control_bot(
                env=get_current_env(),
                owner_id=request.owner_id,
                bot_id=request.bot_id,
                batch_id=request.batch_id,
                group=request.group,
                present=request.present,
                operator=user.staffId,
                reason=request.reason,
            )
        )
    except RolloutOperationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
    return _response(
        await service.rollback(
            scope=scope,
            rollback_generation=request.rollback_generation,
            lease_owner=f"operator-api:{uuid4().hex}",
            operator=user.staffId,
            note=request.note,
        )
    )
