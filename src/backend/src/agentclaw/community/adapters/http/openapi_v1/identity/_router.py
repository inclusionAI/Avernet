"""Identity group — ``/openapi/v1/identity`` bot identity files (definition only).

Read/write a bot's identity markdown files (RULES, SOUL, …), addressed by bot.
Handlers are stubs; every route requires an authenticated user principal.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.openapi_v1._deps import require_principal
from agentclaw.community.adapters.http.openapi_v1._contracts import (
    Envelope,
    requires_user_principal,
)
from agentclaw.community.adapters.http.openapi_v1._deps import Principal

from ._schemas import (
    IdentityFile,
    IdentityFileList,
    IdentityFileRef,
    IdentityFileType,
    IdentityFileWrite,
)

router = APIRouter(prefix="/openapi/v1/bots/identity", tags=["identity"])

_SEC = requires_user_principal()
PrincipalDep = Annotated[Principal, Depends(require_principal)]


@router.get(
    "/bot/{bot_id}", response_model=Envelope[IdentityFileList], openapi_extra=_SEC
)
async def list_bot_identity_files(
    bot_id: str, principal: PrincipalDep
) -> Envelope[IdentityFileList]:
    """List a bot's identity files and whether each exists."""
    raise NotImplementedError


@router.get(
    "/bot/{bot_id}/{file_type}",
    response_model=Envelope[IdentityFile],
    openapi_extra=_SEC,
)
async def get_bot_identity_file(
    bot_id: str, file_type: IdentityFileType, principal: PrincipalDep
) -> Envelope[IdentityFile]:
    """Read one identity file of a bot."""
    raise NotImplementedError


@router.put(
    "/bot/{bot_id}/{file_type}",
    response_model=Envelope[IdentityFileRef],
    openapi_extra=_SEC,
)
async def update_bot_identity_file(
    bot_id: str,
    file_type: IdentityFileType,
    body: IdentityFileWrite,
    principal: PrincipalDep,
) -> Envelope[IdentityFileRef]:
    """Overwrite one identity file of a bot."""
    raise NotImplementedError
