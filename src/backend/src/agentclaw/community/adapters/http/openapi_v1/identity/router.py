"""Identity group — ``/openapi/v1/identity`` bot identity files (definition only).

Read/write a bot's identity markdown files (RULES, SOUL, …), addressed by bot.
Handlers are stubs; every route requires an authenticated user principal.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_OK,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import Principal
from agentclaw.community.core.config_compose.teclaw_paths import IDENTITY_NS
# Waiver (Rule 5 vs Rule 19): openapi_v1 injects the concrete IdentityService
# class, not a Protocol — legacy identity router already does the same. A
# Protocol would be speculative abstraction today (Rule 19: abstract after two
# examples; only one IdentityService impl exists).
from agentclaw.community.core.services.identity import IdentityService
from agentclaw.community.di import Injected
from agentclaw.community.plugins.bot_repository import BotRepository

from .schemas import (
    IdentityFile,
    IdentityFileInfo,
    IdentityFileList,
    IdentityFileRef,
    IdentityFileType,
    IdentityFileWrite,
)

router = APIRouter(prefix="/openapi/v1/bots/identity", tags=["identity"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]


def _request_id_from(request: "Request | None") -> str:
    """Read the X-Trace-Id off the incoming request.

    Returns ``""`` when called outside a request (e.g. handler unit tests
    passing ``request=None``) so the envelope field is always a string.
    """
    if request is None:
        return ""
    return request.headers.get("x-trace-id", "")


def _owner_from_bot(bot_id: str, bot_repo: Any) -> str:
    """Resolve the caller's owner_id from the bot record.

    Personal-bot scope: the bot's owner is the caller, so owner_id stands in
    for the gateway-forwarded caller identity until Direction A lands — then
    swap to ``principal.subject`` (the seam). Falls back to ``bot_id`` when
    owner_id is missing (single-box default bot).
    """
    bot = bot_repo.get_by_id(bot_id) if bot_repo is not None else None
    bot = bot or {}
    return bot.get("owner_id") or bot_id


@router.get("/bot/{bot_id}", response_model=Envelope[IdentityFileList])
async def list_bot_identity_files(
    bot_id: str,
    principal: PrincipalDep,
    identity_service: IdentityService = Injected(IdentityService),
    bot_repo: BotRepository = Injected(BotRepository),
    request: Request = None,  # type: ignore[assignment]  # FastAPI auto-injects; default exists for direct unit-test calls
) -> Envelope[IdentityFileList]:
    """List a bot's identity files and whether each exists.

    I2: entity_type/entity_id/operator_id come from the bot record (personal
    bot owner = caller) until the gateway forwards a verified principal —
    then swap to ``principal.subject`` (the seam).
    """
    owner_id = _owner_from_bot(bot_id, bot_repo)
    entity_type = "staff"  # personal bot owner is a staff entity
    entity_id = owner_id
    try:
        presence = await identity_service.list_bot_files(
            entity_type, entity_id, bot_id, owner_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    files = [
        IdentityFileInfo(
            type=IdentityFileType(ft.removesuffix(".md")),
            exists=exists,
            file_path=f"{IDENTITY_NS}/{ft}",
        )
        for ft, exists in presence
    ]
    return Envelope(
        code=CODE_OK,
        message="OK",
        data=IdentityFileList(bot_id=bot_id, files=files),
        request_id=_request_id_from(request),
    )


@router.get(
    "/bot/{bot_id}/{file_type}",
    response_model=Envelope[IdentityFile],
)
async def get_bot_identity_file(
    bot_id: str,
    file_type: IdentityFileType,
    principal: PrincipalDep,
    identity_service: IdentityService = Injected(IdentityService),
    bot_repo: BotRepository = Injected(BotRepository),
    request: Request = None,  # type: ignore[assignment]  # FastAPI auto-injects; default exists for direct unit-test calls
) -> Envelope[IdentityFile]:
    """Read one identity file of a bot.

    I2: entity params fall back to the bot owner (personal bot owner = caller)
    until the gateway forwards a verified principal. I3: ``publish_id`` is
    not exposed — only draft-device reads (``get_bot_file`` default branch).
    The service's ``validate_file_type`` requires the physical ``<type>.md``
    form (``VALID_IDENTITY_FILES`` carries the suffix), so the enum value is
    re-suffixed before forwarding.
    """
    owner_id = _owner_from_bot(bot_id, bot_repo)
    entity_type = "staff"  # personal bot owner is a staff entity
    entity_id = owner_id
    file_type_md = f"{file_type.value}.md"
    try:
        resp = await identity_service.get_bot_file(
            entity_type, entity_id, bot_id, file_type_md, owner_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # BotIdentityFileResponse → openapi IdentityFile. content/file_path are
    # guaranteed by the legacy response model; getattr is a defensive belt.
    return Envelope(
        code=CODE_OK,
        message="OK",
        data=IdentityFile(
            type=file_type,
            bot_id=bot_id,
            content=getattr(resp, "content", "") or "",
            file_path=getattr(resp, "file_path", f"{IDENTITY_NS}/{file_type_md}"),
        ),
        request_id=_request_id_from(request),
    )


@router.put(
    "/bot/{bot_id}/{file_type}",
    response_model=Envelope[IdentityFileRef],
)
async def update_bot_identity_file(
    bot_id: str,
    file_type: IdentityFileType,
    body: IdentityFileWrite,
    principal: PrincipalDep,
    identity_service: IdentityService = Injected(IdentityService),
    bot_repo: BotRepository = Injected(BotRepository),
    request: Request = None,  # type: ignore[assignment]  # FastAPI auto-injects; default exists for direct unit-test calls
) -> Envelope[IdentityFileRef]:
    """Overwrite one identity file of a bot.

    I2 entity fallback as above; ``validate_file_type`` requires the
    ``<type>.md`` form. Returns an ``IdentityFileRef`` (no content echoed).
    """
    owner_id = _owner_from_bot(bot_id, bot_repo)
    entity_type = "staff"  # personal bot owner is a staff entity
    entity_id = owner_id
    file_type_md = f"{file_type.value}.md"
    try:
        resp = await identity_service.update_bot_file(
            entity_type, entity_id, bot_id, file_type_md, body.content, owner_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Envelope(
        code=CODE_OK,
        message="OK",
        data=IdentityFileRef(
            type=file_type,
            bot_id=bot_id,
            file_path=getattr(resp, "file_path", f"{IDENTITY_NS}/{file_type_md}"),
        ),
        request_id=_request_id_from(request),
    )
