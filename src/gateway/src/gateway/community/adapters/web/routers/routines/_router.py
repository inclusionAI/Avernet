"""Routines group — ``/openapi/v1/routines`` (definition only).

Scheduled/triggered agent tasks (the former "cron"), with a stable
gateway-owned schema and a nested trigger. Handlers are stubs; every route
requires an authenticated user principal.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from gateway.community.adapters.web import require_identities
from gateway.community.adapters.web.contracts import (
    Deleted,
    Envelope,
    Page,
    PageParamsDep,
    requires_user_principal,
)
from gateway.community.spi.authn import Identities

from ._schemas import Routine, RoutineCreate, RoutineRun, RoutineUpdate

router = APIRouter(prefix="/openapi/v1/routines", tags=["routines"])

_SEC = requires_user_principal()
IdentitiesDep = Annotated[Identities, Depends(require_identities)]


@router.get("", response_model=Envelope[Page[Routine]], openapi_extra=_SEC)
async def list_routines(
    page: PageParamsDep,
    identities: IdentitiesDep,
    bot_id: str | None = None,
    status: str | None = None,
) -> Envelope[Page[Routine]]:
    """List routines (filter + paginate)."""
    raise NotImplementedError


@router.post("", status_code=201, response_model=Envelope[Routine], openapi_extra=_SEC)
async def create_routine(
    body: RoutineCreate, identities: IdentitiesDep
) -> Envelope[Routine]:
    """Create a routine."""
    raise NotImplementedError


@router.get("/{routine_id}", response_model=Envelope[Routine], openapi_extra=_SEC)
async def get_routine(routine_id: str, identities: IdentitiesDep) -> Envelope[Routine]:
    """Get a routine."""
    raise NotImplementedError


@router.patch("/{routine_id}", response_model=Envelope[Routine], openapi_extra=_SEC)
async def update_routine(
    routine_id: str, body: RoutineUpdate, identities: IdentitiesDep
) -> Envelope[Routine]:
    """Update a routine (partial)."""
    raise NotImplementedError


@router.delete("/{routine_id}", response_model=Envelope[Deleted], openapi_extra=_SEC)
async def delete_routine(
    routine_id: str, identities: IdentitiesDep
) -> Envelope[Deleted]:
    """Delete a routine."""
    raise NotImplementedError


@router.post(
    "/{routine_id}/run", response_model=Envelope[RoutineRun], openapi_extra=_SEC
)
async def run_routine(
    routine_id: str, identities: IdentitiesDep
) -> Envelope[RoutineRun]:
    """Run a routine now."""
    raise NotImplementedError


@router.get(
    "/{routine_id}/runs",
    response_model=Envelope[Page[RoutineRun]],
    openapi_extra=_SEC,
)
async def list_routine_runs(
    routine_id: str, page: PageParamsDep, identities: IdentitiesDep
) -> Envelope[Page[RoutineRun]]:
    """List a routine's execution history."""
    raise NotImplementedError
