"""Internal Space endpoints used by trusted service integrations."""

from fastapi import APIRouter, HTTPException, Request, status

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope
from agentclaw.community.adapters.http.spaces_internal.schemas import (
    PersonalSpaceBatchQueryItem,
    PersonalSpaceBatchQueryRequest,
    PersonalSpaceBatchQueryResult,
    SpaceScTeamRepairResultResponse,
)
from agentclaw.community.api.space_service import SpaceServiceProtocol
from agentclaw.community.core.spaces.errors import (
    SpaceNotFoundError,
    SpaceScTeamBindingNotFoundError,
    SpaceScTeamRepairConflictError,
    SpaceScTeamRepairNotApplicableError,
)
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.skill_center_client import SkillCenterTeamQueryError


router = APIRouter(prefix="/api/internal/spaces", tags=["spaces-internal"])


@router.post(
    "/personal/batch-query",
    response_model=Envelope[PersonalSpaceBatchQueryResult],
)
async def batch_query_personal_spaces(
    body: PersonalSpaceBatchQueryRequest,
    request: Request,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[PersonalSpaceBatchQueryResult]:
    records = service.batch_query_personal(user_ids=body.user_id)
    return envelope(
        PersonalSpaceBatchQueryResult(
            list=[
                PersonalSpaceBatchQueryItem(
                    user_id=record.user_id,
                    space_id=record.space_id,
                    found=record.found,
                )
                for record in records
            ]
        ),
        request,
    )


@router.post(
    "/{space_id}/sc-team-binding/repair",
    response_model=Envelope[SpaceScTeamRepairResultResponse],
)
async def repair_space_sc_team_binding(
    space_id: int,
    request: Request,
    service: SpaceServiceProtocol = Injected(SpaceServiceProtocol),
) -> Envelope[SpaceScTeamRepairResultResponse]:
    """Repair a historical TEAM Space binding without creating an SC Team.

    This trusted maintenance endpoint is idempotent: an existing binding is
    returned as ``ALREADY_BOUND``. A missing SC lookup is a visible 404 rather
    than a trigger for creation, which prevents accidental duplicate SC Teams.
    """
    try:
        result = service.repair_sc_team_binding(space_id=space_id)
    except SpaceScTeamRepairNotApplicableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SC_TEAM_REPAIR_NOT_APPLICABLE", "message": str(exc)},
        ) from exc
    except (SpaceNotFoundError, SpaceScTeamBindingNotFoundError) as exc:
        code = (
            "SPACE_NOT_FOUND"
            if isinstance(exc, SpaceNotFoundError)
            else "SC_TEAM_BINDING_NOT_FOUND"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": code, "message": str(exc)},
        ) from exc
    except SpaceScTeamRepairConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SC_TEAM_REPAIR_CONFLICT", "message": str(exc)},
        ) from exc
    except SkillCenterTeamQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "SKILL_CENTER_QUERY_FAILED", "message": str(exc)},
        ) from exc

    return envelope(
        SpaceScTeamRepairResultResponse(
            space_id=result.space_id,
            status=result.status,
            sc_team_id=result.sc_team_id,
        ),
        request,
    )
