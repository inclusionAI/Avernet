"""Identity group — ``/openapi/v1/bots/identity/{bot_id}`` bot identity files.

Read/write a bot's identity markdown files (RULES, SOUL, …), addressed by bot.
``{bot_id}`` is the first segment after the component, as everywhere on this
surface — there is no ``/bot/`` segment before it, because the base already
says ``bots`` and saying it twice told a reader nothing the base did not.
Every route requires an authenticated user principal. ``entity_type`` is
hardcoded to ``staff`` for the personal-bot surface — ``proj``/``team``
identity files (both valid entity types) are intentionally out of scope for
openapi_v1 and not reachable through this API.
"""

from __future__ import annotations


from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.core.config_compose.teclaw_paths import IDENTITY_NS

# Waiver (Rule 5 vs Rule 19): openapi_v1 injects the concrete IdentityService
# class, not a Protocol — legacy identity router already does the same. A
# Protocol would be speculative abstraction today (Rule 19: abstract after two
# examples; only one IdentityService impl exists).
from agentclaw.community.core.services.identity import IdentityService
from agentclaw.community.di import Injected

from .schemas import (
    IdentityFile,
    IdentityFileInfo,
    IdentityFileList,
    IdentityFileRef,
    IdentityFileType,
    IdentityFileWrite,
)

router = APIRouter(prefix="/openapi/v1/bots/identity", tags=["identity"])


@router.get("/{bot_id}", response_model=Envelope[IdentityFileList])
@envelope_errors
async def list_bot_identity_files(
    bot_id: str,
    owner_id: UserIdDep,
    request: Request,
    identity_service: IdentityService = Injected(IdentityService),
) -> Envelope[IdentityFileList]:
    """List a bot's identity files and whether each exists.

    Every possible file is listed, including the ones the bot does not have yet
    — read `exists` to tell them apart.
    """
    entity_type = "staff"  # personal bot owner is a staff entity
    entity_id = owner_id
    presence = await identity_service.list_bot_files(
        entity_type,
        entity_id,
        bot_id,
        owner_id,
    )
    files = [
        IdentityFileInfo(
            type=IdentityFileType(ft.removesuffix(".md")),
            exists=exists,
            file_path=f"{IDENTITY_NS}/{ft}",
        )
        for ft, exists in presence
    ]
    return envelope(IdentityFileList(bot_id=bot_id, files=files), request)


@router.get(
    "/{bot_id}/{file_type}",
    response_model=Envelope[IdentityFile],
)
@envelope_errors
async def get_bot_identity_file(
    bot_id: str,
    file_type: IdentityFileType,
    owner_id: UserIdDep,
    request: Request,
    identity_service: IdentityService = Injected(IdentityService),
) -> Envelope[IdentityFile]:
    """Read one identity file of a bot.

    Name the file by its type — `RULES`, not `RULES.md`. A file the bot does not
    have yet reads as empty content rather than a 404.
    """
    entity_type = "staff"  # personal bot owner is a staff entity
    entity_id = owner_id
    file_type_md = f"{file_type.value}.md"
    resp = await identity_service.get_bot_file(
        entity_type,
        entity_id,
        bot_id,
        file_type_md,
        owner_id,
    )
    # BotIdentityFileResponse → openapi IdentityFile. content/file_path are
    # guaranteed by the legacy response model; getattr is a defensive belt.
    return envelope(
        IdentityFile(
            type=file_type,
            bot_id=bot_id,
            content=getattr(resp, "content", "") or "",
            file_path=getattr(resp, "file_path", f"{IDENTITY_NS}/{file_type_md}"),
        ),
        request,
    )


@router.put(
    "/{bot_id}/{file_type}",
    response_model=Envelope[IdentityFileRef],
)
@envelope_errors
async def update_bot_identity_file(
    bot_id: str,
    file_type: IdentityFileType,
    body: IdentityFileWrite,
    owner_id: UserIdDep,
    request: Request,
    identity_service: IdentityService = Injected(IdentityService),
) -> Envelope[IdentityFileRef]:
    """Overwrite one identity file of a bot.

    The body replaces the file's whole content; there is no append. Name the
    file by its type — `RULES`, not `RULES.md`. Writing a file the bot does not
    have creates it. The content is not echoed back.
    """
    entity_type = "staff"  # personal bot owner is a staff entity
    entity_id = owner_id
    file_type_md = f"{file_type.value}.md"
    resp = await identity_service.update_bot_file(
        entity_type,
        entity_id,
        bot_id,
        file_type_md,
        body.content,
        owner_id,
    )
    return envelope(
        IdentityFileRef(
            type=file_type,
            bot_id=bot_id,
            file_path=getattr(resp, "file_path", f"{IDENTITY_NS}/{file_type_md}"),
        ),
        request,
    )
