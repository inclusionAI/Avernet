"""Public lifecycle routes for Bot-owned Local Skills.

This router deliberately exposes only the six ratified Local Skill operations.
Git, Center, marketplace, and install semantics remain on their separate,
non-public surfaces.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    EXAMPLE_TRACE_ID,
    BotIdPath,
    Deleted,
    Envelope,
    ErrorEnvelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
    require_granted_addressed_bot,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    page as page_envelope,
)
from agentclaw.community.api.bot_skill_asset_service import (
    BotSkillAssetServiceProtocol,
)
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.api.local_skill_query_service import (
    LocalSkillQueryServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillInvalidPackageError,
    LocalSkillNotFoundError,
)
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterPublishStatusError,
)

from .schemas import (
    Skill,
    SkillContent,
    SkillParameters,
    SkillPublishStatus,
    SkillState,
    SkillUpload,
)
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
)

publish_status_router = APIRouter(
    prefix="/openapi/v1/bots/skills",
    tags=["skills"],
    dependencies=[Depends(require_principal)],
    route_class=PublicAPIRoute,
)


@publish_status_router.get(
    "/{skill_code}/publish/status",
    response_model=Envelope[SkillPublishStatus],
)
@envelope_errors
async def get_skill_publish_status(
    request: Request,
    skill_code: str = Path(
        ...,
        min_length=1,
        max_length=200,
        description="Skill Center skill code.",
    ),
    client: SkillCenterClient = Injected(SkillCenterClient),
) -> Envelope[SkillPublishStatus]:
    """Query a Skill's publish status from Skill Center.

    This is the new Skill Workbench entry point. It deliberately does not
    consult or mutate the legacy local Skill state machine.
    """
    upstream = client.query_publish_status(skill_code)
    if not isinstance(upstream, dict) or upstream.get("success") is not True:
        raise SkillCenterPublishStatusError()
    data = upstream.get("data")
    if not isinstance(data, dict):
        raise SkillCenterPublishStatusError()
    return envelope(SkillPublishStatus.model_validate(data), request)


router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/skills", tags=["skills"], route_class=PublicAPIRoute)

#: The bot authorization for an application caller, on the two operations the
#: shared dependency can decide — the **addressed-bot** dependency, because the
#: collection operations publish ``owner_id`` and may address a shared bot.
#:
#: Declared per route rather than at ``include_router``, because this group is
#: mixed in the one way that matters to this check: the collection operations
#: name their bot's owner in the query, so the dependency can look the grant up
#: against the pair the handler will act on — while the four ``{skill_id}``
#: operations learn that owner only by reading the skill. Mounting the whole
#: group under a grant check refused an application holding a valid grant on a
#: *shared* bot, because the check fell back to the delegating user.
#: ``admission.py`` is the authority on which route is which;
#: ``test_admission_inventory.py`` fails if a declaration and a mode disagree.
_GRANT_CHECKED_ADDRESSED_BOT = [Depends(require_granted_addressed_bot)]

#: The path parameter naming the skill an operation addresses. The id alone
#: still resolves the skill's bot and owner; the address names the bot as well
#: so that every operation in the group is reached the same way, and so the
#: shared grant check can see it. :func:`_require_addressed_bot` is what keeps
#: the two from disagreeing.
SkillIdPath = Annotated[
    str,
    Path(
        description="The skill's id, as returned by the listing or upload "
        "(decimal digits, e.g. '42'). The id alone identifies the skill and "
        "its bot."
    ),
]


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(tag) for tag in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        return [str(tag) for tag in parsed] if isinstance(parsed, list) else []
    return []


def _require_addressed_bot(record: dict[str, Any], bot_id: str) -> None:
    """The skill must belong to the bot the address names.

    Without this the ``{bot_id}`` segment on the four ``{skill_id}`` operations
    would be decorative — a client could name any bot and reach a skill on
    another one, which is the precise defect this addressing change exists to
    remove. A skill id resolves its own bot, so the two can be compared, and a
    mismatch is answered as the skill not existing.

    Masked as a 404 rather than reported as a mismatch, for the same reason the
    rest of the surface masks: a distinguishable "wrong bot" answer confirms the
    skill exists somewhere, which is an enumeration oracle over other people's
    bots.

    The legacy addresses take no bot and so cannot make this comparison; they
    keep exactly the behaviour they have today.
    """
    if str(record["bolt_id"]) != bot_id:
        raise LocalSkillNotFoundError()


def _require_skills_grant(caller: ActingCaller, record: dict[str, Any]) -> None:
    """Bind the grant to the ``(bot, owner)`` this skill actually belongs to.

    Both halves come off the record. A skill can belong to another owner's bot
    and still be readable here — the user-scoped read admits a collaborator — so
    checking the grant against the *caller* rather than the skill's owner would
    authorize work on one bot with a grant for a different, same-named one.
    """
    caller.require_bot(str(record["bolt_id"]), owner_id=str(record["user_id"]))


def _to_skill(record: dict[str, Any]) -> Skill:
    return Skill(
        skill_id=str(record["id"]),
        name=str(record["name"]),
        description=record.get("description"),
        category=record.get("category"),
        tags=_tags(record.get("tags")),
        active=bool(record["active"]),
        created_at=record.get("gmt_created"),
        updated_at=record.get("gmt_modified"),
    )


def _uploaded_skill_response(
    result: dict[str, Any], request: Request, response: Response
) -> Envelope[SkillUpload]:
    operation = str(result["operation"])
    if operation == "updated":
        response.status_code = 200
    return envelope(
        SkillUpload(operation=operation, skill=_to_skill(result["skill"])),
        request,
        code=201000 if operation == "created" else 200000,
        message="Created" if operation == "created" else "OK",
    )


def _directory_relative_paths(
    raw_paths: str | None, files: list[UploadFile]
) -> list[str]:
    """Preserve the legacy multipart folder wire without trusting filenames."""
    if raw_paths is None:
        paths = [file.filename or "" for file in files]
    else:
        try:
            paths = json.loads(raw_paths)
        except json.JSONDecodeError as exc:
            raise LocalSkillInvalidPackageError() from exc
        if not isinstance(paths, list) or not all(
            isinstance(path, str) for path in paths
        ):
            raise LocalSkillInvalidPackageError()
    if len(paths) != len(files):
        raise LocalSkillInvalidPackageError()
    return paths


@router.get(
    "", response_model=Envelope[Page[Skill]], dependencies=_GRANT_CHECKED_ADDRESSED_BOT
)
@envelope_errors
async def list_skills(
    page: PageParamsDep,
    user_id: UserIdDep,
    request: Request,
    bot_id: BotIdPath,
    owner_id: OwnerIdDep,
    active: bool | None = Query(
        default=None,
        description="Filter: true for only active skills, false for only "
        "inactive ones; omit for both.",
    ),
    keyword: str | None = Query(
        default=None,
        description="Filter: case-insensitive substring match against the "
        "skill's name and description.",
    ),
    query_service: LocalSkillQueryServiceProtocol = Injected(
        LocalSkillQueryServiceProtocol
    ),
) -> Envelope[Page[Skill]]:
    """List a bot's skills from stored desired state (paginated).

    Answers even while the bot is offline: active reflects the desired
    state, not the live runtime.
    """
    total, records = query_service.list_local_skills(
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=user_id,
        page=page.page,
        page_size=page.page_size,
        active=active,
        keyword=keyword,
    )
    return page_envelope(total, [_to_skill(record) for record in records], request)


@router.get(
    "/{skill_id}",
    response_model=Envelope[Skill],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def get_skill(
    bot_id: BotIdPath,
    skill_id: SkillIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    asset_service: BotSkillAssetServiceProtocol = Injected(
        BotSkillAssetServiceProtocol
    ),
) -> Envelope[Skill]:
    """Get public metadata for one Local Skill; the Skill ID selects its Bot."""
    record = asset_service.get_skill(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    _require_addressed_bot(record, bot_id)
    # The record is already in hand, so this one checks the grant directly
    # rather than through the helper — one read, not two.
    _require_skills_grant(caller, record)
    return envelope(_to_skill(record), request)


@router.get(
    "/{skill_id}/content",
    response_model=Envelope[SkillContent],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def get_skill_content(
    bot_id: BotIdPath,
    skill_id: SkillIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    asset_service: BotSkillAssetServiceProtocol = Injected(
        BotSkillAssetServiceProtocol
    ),
) -> Envelope[SkillContent]:
    record = asset_service.get_skill(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    _require_addressed_bot(record, bot_id)
    _require_skills_grant(caller, record)
    content = await asset_service.get_content(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    return envelope(SkillContent(content=content), request)


@router.get(
    "/{skill_id}/parameters",
    response_model=Envelope[SkillParameters],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def get_skill_parameters(
    bot_id: BotIdPath,
    skill_id: SkillIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    asset_service: BotSkillAssetServiceProtocol = Injected(
        BotSkillAssetServiceProtocol
    ),
) -> Envelope[SkillParameters]:
    record = asset_service.get_skill(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    _require_addressed_bot(record, bot_id)
    _require_skills_grant(caller, record)
    parameters = await asset_service.get_parameters(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    return envelope(SkillParameters(parameters=parameters), request)


@router.put(
    "/{skill_id}/parameters",
    response_model=Envelope[SkillParameters],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def replace_skill_parameters(
    bot_id: BotIdPath,
    skill_id: SkillIdPath,
    payload: SkillParameters,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    asset_service: BotSkillAssetServiceProtocol = Injected(
        BotSkillAssetServiceProtocol
    ),
) -> Envelope[SkillParameters]:
    record = asset_service.get_skill(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    _require_addressed_bot(record, bot_id)
    _require_skills_grant(caller, record)
    parameters = await asset_service.replace_parameters(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        parameters=payload.parameters,
    )
    return envelope(SkillParameters(parameters=parameters), request)


@router.post(
    "",
    status_code=201,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
    response_model=Envelope[SkillUpload],
    responses={
        200: {
            "model": Envelope[SkillUpload],
            "description": "Existing Local Skill safely replaced.",
        },
        413: {
            "model": ErrorEnvelope,
            "description": "ZIP package exceeds an upload limit.",
            "content": {
                "application/json": {
                    "example": {
                        "code": 413101,
                        "message": "Skill package is too large",
                        "data": None,
                        "request_id": EXAMPLE_TRACE_ID,
                    }
                }
            },
        },
    },
)
@envelope_errors
async def upload_skill(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    request: Request,
    response: Response,
    owner_id: OwnerIdDep,
    package: bytes = Body(..., media_type="application/zip"),
    upload_service: LocalSkillUploadServiceProtocol = Injected(
        LocalSkillUploadServiceProtocol
    ),
) -> Envelope[SkillUpload]:
    """Upload a skill package (raw ZIP) to create or replace a bot's skill.

    The body is the ZIP's raw bytes with content type application/zip — not a
    multipart form. The archive must contain exactly one SKILL.md manifest,
    whose front matter names the skill. When the bot already has a skill of
    that name, its package is replaced in place (200, operation 'updated');
    otherwise a new, inactive skill is created (201, operation 'created').
    Limits: 10 MB compressed, 50 MB uncompressed, 500 files (413 beyond).
    """
    if (
        request.headers.get("content-type", "").split(";", 1)[0].lower()
        != "application/zip"
    ):
        raise LocalSkillInvalidPackageError()
    result = await upload_service.upload_local_skill(
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=user_id,
        package=package,
    )
    return _uploaded_skill_response(result, request, response)


@router.post(
    "/upload-folder",
    status_code=201,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
    response_model=Envelope[SkillUpload],
    responses={
        200: {
            "model": Envelope[SkillUpload],
            "description": "Existing Local Skill safely replaced.",
        },
        413: {
            "model": ErrorEnvelope,
            "description": "Directory package exceeds an upload limit.",
        },
    },
)
@envelope_errors
async def upload_skill_folder(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    request: Request,
    response: Response,
    owner_id: OwnerIdDep,
    files: list[UploadFile] = File(
        ..., description="Files selected from exactly one local Skill directory."
    ),
    file_paths: str | None = Form(
        default=None,
        description=(
            "JSON array of relative paths aligned one-to-one with files. "
            "When omitted, each uploaded filename is used as its relative path."
        ),
    ),
    upload_service: LocalSkillUploadServiceProtocol = Injected(
        LocalSkillUploadServiceProtocol
    ),
) -> Envelope[SkillUpload]:
    """Upload a browser-selected local Skill directory.

    This preserves the legacy multipart ``files`` + ``file_paths`` contract.
    The Service API converts the directory into the exact same validated
    package authority as the existing raw-ZIP endpoint.
    """
    paths = _directory_relative_paths(file_paths, files)
    uploaded = [
        (path, await file.read()) for path, file in zip(paths, files, strict=True)
    ]
    result = await upload_service.upload_local_skill_files(
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=user_id,
        files=uploaded,
    )
    return _uploaded_skill_response(result, request, response)


@router.post(
    "/{skill_id}/activate",
    response_model=Envelope[SkillState],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def activate_skill(
    bot_id: BotIdPath,
    skill_id: SkillIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    asset_service: BotSkillAssetServiceProtocol = Injected(
        BotSkillAssetServiceProtocol
    ),
) -> Envelope[SkillState]:
    """Activate a skill so its bot can use it.

    Idempotent — activating an already-active skill succeeds with changed
    false. The bot's runtime is reconciled synchronously either way.
    """
    record = asset_service.get_skill(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    _require_addressed_bot(record, bot_id)
    _require_skills_grant(caller, record)
    result = await asset_service.set_active(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        active=True,
    )
    return envelope(
        SkillState(skill=_to_skill(result), changed=bool(result["changed"])),
        request,
    )


@router.post(
    "/{skill_id}/deactivate",
    response_model=Envelope[SkillState],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def deactivate_skill(
    bot_id: BotIdPath,
    skill_id: SkillIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    asset_service: BotSkillAssetServiceProtocol = Injected(
        BotSkillAssetServiceProtocol
    ),
) -> Envelope[SkillState]:
    """Deactivate a skill so its bot stops using it.

    Idempotent — deactivating an already-inactive skill succeeds with changed
    false. The bot's runtime is reconciled synchronously either way.
    """
    record = asset_service.get_skill(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    _require_addressed_bot(record, bot_id)
    _require_skills_grant(caller, record)
    result = await asset_service.set_active(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
        active=False,
    )
    return envelope(
        SkillState(skill=_to_skill(result), changed=bool(result["changed"])),
        request,
    )


@router.delete(
    "/{skill_id}",
    response_model=Envelope[Deleted],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def delete_skill(
    bot_id: BotIdPath,
    skill_id: SkillIdPath,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    delete_service: LocalSkillDeleteServiceProtocol = Injected(
        LocalSkillDeleteServiceProtocol
    ),
    asset_service: BotSkillAssetServiceProtocol = Injected(
        BotSkillAssetServiceProtocol
    ),
) -> Envelope[Deleted]:
    """Delete a skill by id.

    Only an inactive skill can be deleted — deactivate it first; deleting an
    active one answers 409.
    """
    record = asset_service.get_skill(
        skill_id=skill_id,
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    _require_addressed_bot(record, bot_id)
    _require_skills_grant(caller, record)
    await delete_service.delete_local_skill(
        skill_id=skill_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    return envelope(Deleted(), request)
