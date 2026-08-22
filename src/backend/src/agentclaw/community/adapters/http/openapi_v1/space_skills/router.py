"""Explicit contract-only HTTP handlers for Phase 2 Space Skills.

The public OpenAPI is the authority during front-end integration.  Each route
therefore has its own typed handler, but none fabricates a domain result before
the matching Phase 2 service and persistence slice is implemented.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Path, Request
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    ErrorEnvelope,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import envelope

from .schemas import (
    CreatePublicationRequest,
    DraftDetail,
    DraftLease,
    FileContent,
    FileTreeItem,
    GitImportRequest,
    OwnerTransferRequest,
    PublicationAttempt,
    PublishedVersion,
    RefreshDraftFromGitRequest,
    RetirementImpact,
    RetireSkillRequest,
    SkillGrant,
    SkillGrants,
    SpaceSkillDetail,
    UpgradeImpact,
    WriteDraftFileRequest,
)

PREFIX = "/openapi/v1/bots/spaces/{space_id}/skills"
CONTRACT_STATUS = "contract-only"
CONTRACT_ONLY_MESSAGE = "Phase 2 endpoint is contract-only"

router = APIRouter(
    prefix=PREFIX,
    tags=["phase2-space-skills"],
    dependencies=[Depends(refuse_app_only_caller)],
    route_class=PublicAPIRoute,
)

SpaceIdPath = Annotated[int, Path(ge=1, description="Space primary identifier.")]
SkillIdPath = Annotated[str, Path(min_length=1, description="Space Skill identity.")]
VersionPath = Annotated[
    int, Path(ge=1, description="Business version ordinal, not a database id.")
]
AttemptIdPath = Annotated[
    str, Path(min_length=1, description="Publication attempt identifier.")
]
FilePath = Annotated[
    str, Path(min_length=1, description="Slash-separated path relative to the Skill root.")
]
ManagerUserIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=256,
        description="Current Space member receiving or losing the Manager grant.",
    ),
]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="Required idempotency key for this command.",
    ),
]

_CONTRACT_ONLY_RESPONSE = {
    501: {
        "model": ErrorEnvelope,
        "description": "Declared for front-end contract integration; no Phase 2 domain behavior exists yet.",
        "headers": {
            "x-contract-status": {
                "description": "Always contract-only until this operation is implemented.",
                "schema": {"type": "string", "enum": [CONTRACT_STATUS]},
            }
        },
    }
}


def _contract_only(request: Request) -> JSONResponse:
    """Return the standard public envelope without pretending the command worked."""
    body = envelope(None, request, code=501000, message=CONTRACT_ONLY_MESSAGE)
    return JSONResponse(
        status_code=501,
        content=body.model_dump(),
        headers={"x-contract-status": CONTRACT_STATUS},
    )


def _operation_extra(summary: str) -> dict[str, str]:
    return {"x-contract-status": CONTRACT_STATUS, "summary": summary}


@router.post(
    "",
    status_code=201,
    response_model=Envelope[SpaceSkillDetail],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Create a Space Skill from a raw ZIP package"),
)
async def create_space_skill(
    space_id: SpaceIdPath,
    request: Request,
    user_id: UserIdDep,
    idempotency_key: IdempotencyKey,
    package: bytes = Body(..., media_type="application/zip"),
) -> JSONResponse:
    """Create identity, initial Draft, binding, and Owner from raw ZIP bytes."""
    del space_id, package, idempotency_key, user_id
    return _contract_only(request)


@router.post(
    "/import-from-git",
    status_code=201,
    response_model=Envelope[SpaceSkillDetail],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Import a Space Skill from Git"),
)
async def import_space_skill_from_git(
    space_id: SpaceIdPath,
    body: GitImportRequest,
    request: Request,
    user_id: UserIdDep,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    """Create the same initial Draft model from an explicit Git source."""
    del space_id, body, idempotency_key, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}",
    response_model=Envelope[SpaceSkillDetail],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Read Space Skill workshop detail"),
)
async def get_space_skill(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Read the Workshop projection; consumer detail remains owned elsewhere."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.post(
    "/{skill_id}/draft/upgrade",
    status_code=201,
    response_model=Envelope[DraftDetail],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Create the next Draft for a Space Skill"),
)
async def create_upgrade_draft(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    user_id: UserIdDep,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    """Create the next business-version Draft under an idempotency key."""
    del space_id, skill_id, idempotency_key, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/draft",
    response_model=Envelope[DraftDetail],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Read the current Space Skill Draft"),
)
async def get_draft(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """Read the current Draft's independent status and Git metadata."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.delete(
    "/{skill_id}/draft",
    response_model=Envelope[DraftDetail],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Abandon the current Space Skill Draft"),
)
async def abandon_draft(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """Abandon only an eligible editable Draft; published Versions remain immutable."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/draft/files",
    response_model=Envelope[list[FileTreeItem]],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("List current Draft files"),
)
async def list_draft_files(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """List file paths from the mutable Draft only."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/draft/files/{path:path}",
    response_model=Envelope[FileContent],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Read one current Draft file"),
)
async def get_draft_file(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    path: FilePath,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Read a raw relative file path from the current Draft."""
    del space_id, skill_id, path, user_id
    return _contract_only(request)


@router.put(
    "/{skill_id}/draft/files/{path:path}",
    response_model=Envelope[FileContent],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Write one current Draft file"),
)
async def put_draft_file(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    path: FilePath,
    body: WriteDraftFileRequest,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Replace a Draft file, subject to its Team lease fencing token."""
    del space_id, skill_id, path, body, user_id
    return _contract_only(request)


@router.post(
    "/{skill_id}/draft/replace",
    response_model=Envelope[DraftDetail],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Atomically replace current Draft files from raw ZIP"),
)
async def replace_draft_package(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    request: Request,
    user_id: UserIdDep,
    package: bytes = Body(..., media_type="application/zip"),
) -> JSONResponse:
    """Atomically replace the Draft package; ZIP bodies are never base64 JSON."""
    del space_id, skill_id, package, user_id
    return _contract_only(request)


@router.post(
    "/{skill_id}/draft/refresh-from-git",
    response_model=Envelope[DraftDetail],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Refresh a Git-backed Draft"),
)
async def refresh_draft_from_git(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    body: RefreshDraftFromGitRequest,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Refresh only from the stored Git source, preserving Draft on failure."""
    del space_id, skill_id, body, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/grants",
    response_model=Envelope[SkillGrants],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("List the Owner and Managers for a Space Skill"),
)
async def list_skill_grants(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """Read the unique Owner plus current Managers."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.put(
    "/{skill_id}/managers/{manager_user_id}",
    response_model=Envelope[SkillGrant],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Grant Manager role to a Space member"),
)
async def grant_manager(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    manager_user_id: ManagerUserIdPath,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Idempotently grant Manager to a current Space member."""
    del space_id, skill_id, manager_user_id, user_id
    return _contract_only(request)


@router.delete(
    "/{skill_id}/managers/{manager_user_id}",
    response_model=Envelope[SkillGrant],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Revoke Manager role from a Space member"),
)
async def revoke_manager(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    manager_user_id: ManagerUserIdPath,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Idempotently revoke a Manager grant while retaining the sole Owner."""
    del space_id, skill_id, manager_user_id, user_id
    return _contract_only(request)


@router.post(
    "/{skill_id}/owner-transfer",
    response_model=Envelope[SkillGrants],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Transfer the unique Space Skill Owner"),
)
async def transfer_owner(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    body: OwnerTransferRequest,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Atomically transfer ownership and invalidate the former Owner's lease."""
    del space_id, skill_id, body, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/draft/lease",
    response_model=Envelope[DraftLease],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Read the current Draft lease"),
)
async def get_draft_lease(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """Read Team lease state; Personal Spaces report required=false."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.put(
    "/{skill_id}/draft/lease",
    response_model=Envelope[DraftLease],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Acquire the current Draft lease"),
)
async def acquire_draft_lease(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """Acquire a Team Draft lease and its monotonic fencing token."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.delete(
    "/{skill_id}/draft/lease",
    response_model=Envelope[DraftLease],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Release the current Draft lease"),
)
async def release_draft_lease(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """Release the caller's Team Draft lease."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.post(
    "/{skill_id}/draft/lease/takeover",
    response_model=Envelope[DraftLease],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Take over the current Draft lease"),
)
async def takeover_draft_lease(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """Allow an Owner or Manager to replace a stale Team lease holder."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/versions",
    response_model=Envelope[list[PublishedVersion]],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("List immutable published Space Skill versions"),
)
async def list_versions(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """List immutable Published Versions by their business ordinals."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/versions/{version}",
    response_model=Envelope[PublishedVersion],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Read one exact published Space Skill version"),
)
async def get_version(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    version: VersionPath,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Read one exact immutable business-version projection."""
    del space_id, skill_id, version, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/versions/{version}/files",
    response_model=Envelope[list[FileTreeItem]],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("List exact published Version files"),
)
async def list_version_files(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    version: VersionPath,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """List files from an exact immutable Version."""
    del space_id, skill_id, version, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/versions/{version}/files/{path:path}",
    response_model=Envelope[FileContent],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Read one exact published Version file"),
)
async def get_version_file(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    version: VersionPath,
    path: FilePath,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Read one file from the exact immutable Version."""
    del space_id, skill_id, version, path, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/upgrade-impact",
    response_model=Envelope[UpgradeImpact],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Read the impact of publishing the Draft upgrade"),
)
async def get_upgrade_impact(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """Read Bot bindings affected by the next successful publication."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.post(
    "/{skill_id}/publications",
    status_code=202,
    response_model=Envelope[PublicationAttempt],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Create a Space Skill publication attempt"),
)
async def create_publication(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    body: CreatePublicationRequest,
    request: Request,
    user_id: UserIdDep,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    """Freeze the Draft and enqueue an idempotent publication attempt."""
    del space_id, skill_id, body, idempotency_key, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/publications",
    response_model=Envelope[list[PublicationAttempt]],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("List Space Skill publication attempts"),
)
async def list_publications(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """List publication history without exposing an Attempt cancel command."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/publications/{attempt_id}",
    response_model=Envelope[PublicationAttempt],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Read one Space Skill publication attempt"),
)
async def get_publication(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    attempt_id: AttemptIdPath,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Read one asynchronous attempt, including RESULT_UNKNOWN when present."""
    del space_id, skill_id, attempt_id, user_id
    return _contract_only(request)


@router.post(
    "/{skill_id}/versions/{version}/materialization-retry",
    status_code=202,
    response_model=Envelope[PublicationAttempt],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Retry exact published Version materialization"),
)
async def retry_materialization(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    version: VersionPath,
    request: Request,
    user_id: UserIdDep,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    """Retry materialization only; never repeat the Skill Center publish POST."""
    del space_id, skill_id, version, idempotency_key, user_id
    return _contract_only(request)


@router.get(
    "/{skill_id}/retirement-impact",
    response_model=Envelope[RetirementImpact],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Read whole-Skill retirement impact"),
)
async def get_retirement_impact(
    space_id: SpaceIdPath, skill_id: SkillIdPath, user_id: UserIdDep, request: Request
) -> JSONResponse:
    """Read blockers before retiring the complete Space Skill."""
    del space_id, skill_id, user_id
    return _contract_only(request)


@router.post(
    "/{skill_id}/retirement",
    status_code=202,
    response_model=Envelope[RetirementImpact],
    responses=_CONTRACT_ONLY_RESPONSE,
    openapi_extra=_operation_extra("Retire a complete Space Skill"),
)
async def retire_skill(
    space_id: SpaceIdPath,
    skill_id: SkillIdPath,
    body: RetireSkillRequest,
    user_id: UserIdDep,
    request: Request,
) -> JSONResponse:
    """Retire only after bindings, artifacts, and in-flight work allow it."""
    del space_id, skill_id, body, user_id
    return _contract_only(request)
