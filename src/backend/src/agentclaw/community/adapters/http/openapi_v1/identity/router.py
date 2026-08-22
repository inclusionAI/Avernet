"""Identity group — ``/openapi/v1/bots/{bot_id}/identity`` bot identity files.

Read/write a bot's identity markdown files (RULES, SOUL, …), addressed by bot.
``{bot_id}`` comes first, ahead of the component, as everywhere on this
surface: an operation that acts on one bot starts with that bot's address.
The component name follows it, and there is no ``/bot/`` segment anywhere —
the base already says ``bots``.
Every route requires an authenticated user principal. ``entity_type`` is
hardcoded to ``staff`` for the personal-bot surface — ``proj``/``team``
identity files (both valid entity types) are intentionally out of scope for
openapi_v1 and not reachable through this API.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    StageQuery,
    WriteStageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
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
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/identity", tags=["identity"], route_class=PublicAPIRoute)

#: The path parameter naming which identity file an operation addresses.
FileTypePath = Annotated[
    IdentityFileType,
    Path(
        description="Which identity file to address — one of the whitelisted "
        "types (see the enum's per-value documentation for what each file is "
        "for)."
    ),
]


@router.get("", response_model=Envelope[IdentityFileList])
@envelope_errors
async def list_bot_identity_files(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    identity_service: IdentityService = Injected(IdentityService),
) -> Envelope[IdentityFileList]:
    """List every identity file type a bot can carry and whether each exists.

    Every entry comes from the one runtime the stage parameter names, so a
    file's presence is always reported for the runtime asked about.

    A file reports exists false both when it is absent and when it exists
    with empty content. Entry order is not guaranteed — key off type.
    """
    # I2: entity_type/entity_id/operator_id come from the authenticated
    # request's user_id parameter (personal bot owner = the named user).
    entity_type = "staff"  # personal bot owner is a staff entity
    entity_id = owner_id
    presence = await identity_service.list_bot_files(
        entity_type,
        entity_id,
        bot_id,
        owner_id,
        stage=stage.value,
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
    "/{file_type}",
    response_model=Envelope[IdentityFile],
)
@envelope_errors
async def get_bot_identity_file(
    bot_id: BotIdPath,
    file_type: FileTypePath,
    owner_id: UserIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    identity_service: IdentityService = Injected(IdentityService),
) -> Envelope[IdentityFile]:
    """Read one identity file of a bot.

    Reads the runtime named by the stage parameter — the bot's own workspace
    unless a published one is asked for.

    A file that has never been written reads as an empty content string, not
    an error.
    """
    # I2: entity params come from the authenticated principal via UserIdDep
    # (personal bot owner = the named user). I3: publish_id is not exposed —
    # the runtime is named by ``stage`` instead. The service's
    # validate_file_type requires the physical <type>.md form
    # (VALID_IDENTITY_FILES carries the suffix), so the enum value is
    # re-suffixed before forwarding.
    entity_type = "staff"  # personal bot owner is a staff entity
    entity_id = owner_id
    file_type_md = f"{file_type.value}.md"
    resp = await identity_service.get_bot_file(
        entity_type,
        entity_id,
        bot_id,
        file_type_md,
        owner_id,
        stage=stage.value,
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
    "/{file_type}",
    response_model=Envelope[IdentityFileRef],
)
@envelope_errors
async def update_bot_identity_file(
    bot_id: BotIdPath,
    file_type: FileTypePath,
    body: IdentityFileWrite,
    owner_id: UserIdDep,
    request: Request,
    stage: WriteStageQuery = RuntimeStage.DRAFT,
    identity_service: IdentityService = Injected(IdentityService),
) -> Envelope[IdentityFileRef]:
    """Create or overwrite one identity file of a bot.

    A full replacement — the body's content becomes the whole file. The
    response is a reference only; the content is not echoed back.

    Writes the bot's own workspace. A published runtime is what a release
    produced and is replaced by publishing again, never edited, so naming one is
    refused and nothing is written.
    """
    # I2: entity params come from the authenticated principal via UserIdDep
    # as above; validate_file_type requires the <type>.md form.
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
        stage=stage.value,
    )
    return envelope(
        IdentityFileRef(
            type=file_type,
            bot_id=bot_id,
            file_path=getattr(resp, "file_path", f"{IDENTITY_NS}/{file_type_md}"),
        ),
        request,
    )
