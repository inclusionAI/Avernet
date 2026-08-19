"""Public bot-first CRUD routes for Bot editors."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.core.bot_collaborator.models import CollaboratorRecord
from agentclaw.community.di import Injected

from .schemas import Editor, EditorCreate, EditorList, EditorRole, EditorUpdate


router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/editors", tags=["editors"])
_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]
EditorIdPath = Annotated[
    int,
    Path(ge=1, description="Editor-relation identifier returned by this collection."),
]


async def resolve_editor_owner_id(
    actor_id: UserIdDep,
    owner_id: Annotated[
        str | None,
        Query(
            min_length=1,
            description="Owner of the Bot whose editor set is addressed. "
            "Defaults to the current user; collaborators name the Bot Owner.",
        ),
    ] = None,
) -> str:
    """Resolve a human-only addressed owner without application grant logic."""
    return owner_id if owner_id is not None else actor_id


EditorOwnerIdDep = Annotated[str, Depends(resolve_editor_owner_id)]


def _editor(record: CollaboratorRecord) -> Editor:
    if record.id is None:
        raise RuntimeError("persisted collaborator is missing its primary key")
    return Editor(
        id=record.id,
        user_id=record.user_id,
        user_name=record.user_name,
        role=EditorRole(record.role),
        created_at=record.gmt_create,
        updated_at=record.gmt_modified,
    )


@router.get(
    "",
    response_model=Envelope[EditorList],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def list_editors(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: EditorOwnerIdDep,
    role: Annotated[
        EditorRole | None, Query(description="Optional editor-role filter.")
    ] = None,
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> Envelope[EditorList]:
    records = service.list_editors(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=actor_id,
        role=role.value if role is not None else None,
    )
    items = [_editor(record) for record in records]
    return envelope(EditorList(total=len(items), items=items), request)


@router.post(
    "",
    status_code=201,
    response_model=Envelope[Editor],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def add_editor(
    bot_id: BotIdPath,
    body: EditorCreate,
    request: Request,
    actor_id: UserIdDep,
    owner_id: EditorOwnerIdDep,
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> Envelope[Editor]:
    record = service.add_editor(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=body.editor_user_id,
        operator_id=actor_id,
        user_name=body.user_name,
        role=body.role.value,
    )
    return created(_editor(record), request)


@router.patch(
    "/{editor_id}",
    response_model=Envelope[Editor],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def update_editor(
    bot_id: BotIdPath,
    editor_id: EditorIdPath,
    body: EditorUpdate,
    request: Request,
    actor_id: UserIdDep,
    owner_id: EditorOwnerIdDep,
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> Envelope[Editor]:
    record = service.update_editor(
        bot_id=bot_id,
        owner_id=owner_id,
        collaborator_id=editor_id,
        operator_id=actor_id,
        role=body.role.value,
    )
    return envelope(_editor(record), request)


@router.delete(
    "/me",
    response_model=Envelope[Deleted],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def leave_editors(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: EditorOwnerIdDep,
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> Envelope[Deleted]:
    service.leave_editors(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=actor_id,
    )
    return deleted(request)


@router.delete(
    "/{editor_id}",
    response_model=Envelope[Deleted],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def remove_editor(
    bot_id: BotIdPath,
    editor_id: EditorIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: EditorOwnerIdDep,
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
) -> Envelope[Deleted]:
    service.remove_editor(
        bot_id=bot_id,
        owner_id=owner_id,
        collaborator_id=editor_id,
        operator_id=actor_id,
    )
    return deleted(request)
