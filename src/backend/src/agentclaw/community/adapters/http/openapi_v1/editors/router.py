"""Public bot-first CRUD routes for Bot editors."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
    ErrorEnvelope,
    error_example,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.errors import (
    BotEditLockRequiredError,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.models import CollaboratorRecord
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

from .schemas import Editor, EditorCreate, EditorList, EditorRole, EditorUpdate
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute


router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/editors", tags=["editors"], route_class=PublicAPIRoute)
logger = get_logger()
EditorIdPath = Annotated[
    int,
    Path(ge=1, description="Editor-relation identifier returned by this collection."),
]


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


def _cleanup_removed_holder(
    locks: CollaboratorLockServiceProtocol,
    *,
    bot_id: str,
    owner_id: str,
    actor_id: str,
    lock_info: object,
    removed_user_id: str,
    remaining_editors: int,
) -> None:
    lock = getattr(lock_info, "lock", None)
    if lock is None:
        return
    if remaining_editors > 0 and lock.holder_user_id != removed_user_id:
        return
    try:
        locks.release_lock(bot_id, owner_id, actor_id, True)
    except Exception:
        # The Editor mutation already committed. Report cleanup failure without
        # turning that successful mutation into a retryable client error.
        logger.exception(
            "[editors] failed to clean stale edit lock after removing user=%s "
            "from bot=%s",
            removed_user_id,
            bot_id,
        )


def _lock_info_for_cleanup(
    locks: CollaboratorLockServiceProtocol,
    *,
    bot_id: str,
    owner_id: str,
    actor_id: str,
) -> object | None:
    try:
        return locks.get_lock_info(bot_id, owner_id, actor_id)
    except Exception:
        # Editor governance does not require a lock. An unavailable lock store
        # may prevent stale-row cleanup, but must not block the Editor mutation.
        logger.exception(
            "[editors] could not read edit lock for cleanup: bot=%s actor=%s",
            bot_id,
            actor_id,
        )
        return None


@router.get(
    "",
    response_model=Envelope[EditorList],
)
@envelope_errors
async def list_editors(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
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
    responses={
        423: {
            "model": ErrorEnvelope,
            "description": "Another collaborator currently holds the Bot edit lock.",
            **error_example(423, "Edit lock required"),
        }
    },
)
@envelope_errors
async def add_editor(
    bot_id: BotIdPath,
    body: EditorCreate,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[Editor]:
    before = locks.get_lock_info(bot_id, owner_id, actor_id)
    lock = locks.acquire_lock(bot_id, owner_id, actor_id)
    if lock is None:
        raise BotEditLockRequiredError("could not acquire the Bot edit lock")
    acquired_here = before.lock is None
    try:
        record = service.add_editor(
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=body.editor_user_id,
            operator_id=actor_id,
            user_name=body.user_name,
            role=body.role.value,
        )
    except Exception:
        if acquired_here:
            try:
                locks.release_lock(bot_id, owner_id, actor_id, False)
            except Exception:
                logger.exception(
                    "[editors] failed to release edit lock after add failed: "
                    "bot=%s actor=%s",
                    bot_id,
                    actor_id,
                )
        raise
    return created(_editor(record), request)


@router.patch(
    "/{editor_id}",
    response_model=Envelope[Editor],
)
@envelope_errors
async def update_editor(
    bot_id: BotIdPath,
    editor_id: EditorIdPath,
    body: EditorUpdate,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
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
)
@envelope_errors
async def leave_editors(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[Deleted]:
    editors = service.list_editors(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=actor_id,
        role=None,
    )
    lock_info = _lock_info_for_cleanup(
        locks,
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
    )
    service.leave_editors(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=actor_id,
    )
    _cleanup_removed_holder(
        locks,
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
        lock_info=lock_info,
        removed_user_id=actor_id,
        remaining_editors=sum(record.user_id != actor_id for record in editors),
    )
    return deleted(request)


@router.delete(
    "/{editor_id}",
    response_model=Envelope[Deleted],
)
@envelope_errors
async def remove_editor(
    bot_id: BotIdPath,
    editor_id: EditorIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[Deleted]:
    editors = service.list_editors(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=actor_id,
        role=None,
    )
    removed = next((record for record in editors if record.id == editor_id), None)
    lock_info = _lock_info_for_cleanup(
        locks,
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
    )
    service.remove_editor(
        bot_id=bot_id,
        owner_id=owner_id,
        collaborator_id=editor_id,
        operator_id=actor_id,
    )
    if removed is not None:
        _cleanup_removed_holder(
            locks,
            bot_id=bot_id,
            owner_id=owner_id,
            actor_id=actor_id,
            lock_info=lock_info,
            removed_user_id=removed.user_id,
            remaining_editors=len(editors) - 1,
        )
    return deleted(request)
