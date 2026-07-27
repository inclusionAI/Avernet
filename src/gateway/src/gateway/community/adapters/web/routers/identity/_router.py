"""Identity group — ``/openapi/v1/identity`` bot identity files (definition only).

Read/write a bot's identity markdown files (RULES, SOUL, …), addressed by bot.
Handlers are stubs; every route requires an authenticated user principal.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from gateway.community.adapters.web import require_identities
from gateway.community.adapters.web.contracts import Envelope, requires_user_principal
from gateway.community.spi.authn import Identities

from ._schemas import (
    IdentityFile,
    IdentityFileList,
    IdentityFileRef,
    IdentityFileType,
    IdentityFileWrite,
)

router = APIRouter(prefix="/openapi/v1/identity", tags=["identity"])

_SEC = requires_user_principal()
IdentitiesDep = Annotated[Identities, Depends(require_identities)]


@router.get(
    "/bot/{bot_id}", response_model=Envelope[IdentityFileList], openapi_extra=_SEC
)
async def list_bot_identity_files(
    bot_id: str, identities: IdentitiesDep
) -> Envelope[IdentityFileList]:
    """List a bot's identity files and whether each exists."""
    raise NotImplementedError


@router.get(
    "/bot/{bot_id}/{file_type}",
    response_model=Envelope[IdentityFile],
    openapi_extra=_SEC,
)
async def get_bot_identity_file(
    bot_id: str, file_type: IdentityFileType, identities: IdentitiesDep
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
    identities: IdentitiesDep,
) -> Envelope[IdentityFileRef]:
    """Overwrite one identity file of a bot."""
    raise NotImplementedError
