"""Bot-scoped public Channel CRUD backed by the existing Channel service.

**Where the two bars live.** A read needs member level on the addressed Bot; a
write needs admin. Both used to be enforced here — the member half by the bot
resolve inside ``_authorize``, the admin half by a ``_require_admin`` helper
each of the four writes called first. The admin half is now declared on the
route (``Check(PermissionLevel.ADMIN)`` in ``openapi_v1/authorization.py``) and
enforced by ``bot_access`` before the handler runs, so the helper and its four
call sites are gone, along with the ``CollaboratorServiceProtocol`` parameter
they were the only reason for.

``_authorize`` stays, because the handlers do not call it for the check: they
call it for the **resolved owner**, which every subsequent write is scoped by,
and for the bot-type refusal the shared engine-runtime gate performs.

``_require_edit_lock`` stays untouched, and its 423 with it. It is not a
collaborator bar — it asks who currently holds the Bot's draft, which is a
question about concurrent editing, not about permission.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Path, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
    ErrorEnvelope,
    error_example,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import (
    resolve_operable_bot,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    require_granted_addressed_bot,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.channel_service import ChannelServiceProtocol
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.channel.errors import (
    ChannelEditLockedError,
    ChannelModeViolationError,
    ChannelNotFoundError,
    ChannelSyncError,
)
from agentclaw.community.core.channel.models import ChannelRecord
from agentclaw.community.di import Injected
from agentclaw.community.di import config as cfg

from .schemas import (
    Channel,
    ChannelCreate,
    ChannelStatus,
    ChannelStatusUpdate,
    ChannelType,
    ChannelUpdate,
    DingTalkChannelConfig,
    validate_mode_matrix,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute


router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/channels",
    tags=["channels"],
    route_class=PublicAPIRoute,
)

# Keep the admission mode visible at the route boundary. ``OwnerIdDep`` also
# consumes this dependency to resolve an application grant's owner, and FastAPI
# caches that shared dependency result within the request.
_GRANT_CHECKED_ADDRESSED_BOT = [Depends(require_granted_addressed_bot)]

ChannelIdPath = Annotated[
    int,
    Path(
        ge=1,
        description="Numeric Channel id returned by the collection or create operation.",
    ),
]

CHANNEL_WRITE_RESPONSES = {
    423: {
        "model": ErrorEnvelope,
        "description": (
            "A Bot with collaborators requires the caller to hold its edit lock."
        ),
        **error_example(423, "Edit lock required"),
    },
    409: {
        "model": ErrorEnvelope,
        "description": (
            "BCS rejected the binding because it conflicts with an existing binding."
        ),
        **error_example(409, "Channel binding conflict"),
    },
}


def _status(value: str) -> ChannelStatus:
    return "active" if value == "1" else "inactive"


def _safe_config(raw: dict[str, Any]) -> DingTalkChannelConfig:
    """Project the stored config without returning credentials or private URLs."""
    raw_allowlist = raw.get("allowlist")
    allowlist = (
        [str(item) for item in raw_allowlist]
        if isinstance(raw_allowlist, list)
        else ["*"]
    )
    return DingTalkChannelConfig(
        client_id=str(raw.get("client_id") or ""),
        has_client_secret=bool(raw.get("client_secret")),
        card_template_id=raw.get("card_template_id"),
        card_template_key=raw.get("card_template_key"),
        enable_streaming_cards=bool(raw.get("enable_streaming_cards", False)),
        dm_policy=str(raw.get("dm_policy") or "open"),
        allowlist=allowlist,
        reply_to_message=bool(raw.get("reply_to_message", True)),
        robot_code=str(raw.get("robot_code") or ""),
        aix_enable=bool(raw.get("aix_enable", True)),
        include_sender_name=bool(raw.get("include_sender_name", True)),
        group_chat_scope=raw.get("group_chat_scope"),
        outbound_visibility=raw.get("outbound_visibility"),
    )


def _project(record: ChannelRecord) -> Channel:
    return Channel(
        id=record.id,
        type=cast(ChannelType, record.type),
        binding_mode=record.config.get("binding_mode", "plugin"),
        description=record.description,
        bot_id=record.bind_bot_id,
        owner_id=record.identity_id,
        config=_safe_config(record.config),
        status=_status(record.status),
        stage="draft",
        created_at=record.gmt_create,
        updated_at=record.gmt_modified,
    )


async def _authorize(
    *,
    relay: EngineRuntimeRelayProtocol,
    bot_id: str,
    user_id: str,
    owner_id: str,
) -> str:
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=user_id,
        owner_id=owner_id,
        stage="draft",
        surface="channels",
    )
    return facts.owner_id


def _require_edit_lock(
    locks: CollaboratorLockServiceProtocol,
    *,
    bot_id: str,
    owner_id: str,
    user_id: str,
) -> None:
    """Match the internal mutation policy for Bots that have collaborators."""
    info = locks.get_lock_info(
        bot_id=bot_id,
        owner_id=owner_id,
        user_id=user_id,
    )
    if not info.has_collaborators:
        return
    if info.lock is None or info.lock.holder_user_id != user_id:
        raise ChannelEditLockedError("Bot edit lock is not held by the caller")


def _owned_channel(
    service: ChannelServiceProtocol,
    channel_id: ChannelIdPath,
    *,
    bot_id: str,
    owner_id: str,
) -> ChannelRecord:
    record = service.get_channel_by_id(channel_id)
    if (
        record is None
        or record.deleted != 0
        or record.bind_bot_id != bot_id
        or record.identity_id != owner_id
        or record.type != "dingding"
        or record.stage not in (None, "", "draft")
    ):
        raise ChannelNotFoundError("channel is outside the addressed Bot scope")
    return record


async def _sync_active(service: ChannelServiceProtocol, channel_id: int) -> None:
    try:
        await service.sync_active_channel(channel_id)
    except (BotNotFoundError, FileNotFoundError, ValueError) as exc:
        raise ChannelSyncError("channel runtime synchronization failed") from exc


async def _set_status(
    service: ChannelServiceProtocol,
    channel_id: ChannelIdPath,
    status: ChannelStatus,
) -> None:
    try:
        await service.set_channel_status(
            channel_id,
            "1" if status == "active" else "0",
        )
    except (BotNotFoundError, FileNotFoundError, ValueError) as exc:
        raise ChannelSyncError("channel runtime synchronization failed") from exc


@router.get(
    "",
    response_model=Envelope[list[Channel]],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def list_channels(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
) -> Envelope[list[Channel]]:
    """List user-managed draft DingTalk Channels for the addressed Bot."""
    resolved_owner = await _authorize(
        relay=relay,
        bot_id=bot_id,
        user_id=user_id,
        owner_id=owner_id,
    )
    records = service.list_channels(
        type="dingding",
        identity_id=resolved_owner,
        bind_bot_id=bot_id,
    )
    # The legacy service also returns a shared aideskdingding default row. It is
    # useful to the internal UI but is not owned by this user and therefore is
    # not part of the public per-Bot CRUD collection.
    items = [
        _project(record)
        for record in records
        if record.type == "dingding"
        and record.identity_id == resolved_owner
        and record.deleted == 0
        and record.stage in (None, "", "draft")
    ]
    return envelope(items, request)


@router.post(
    "",
    status_code=201,
    response_model=Envelope[Channel],
    responses=CHANNEL_WRITE_RESPONSES,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def create_channel(
    bot_id: BotIdPath,
    body: ChannelCreate,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
    aix_config: cfg.AixConfig = Injected(cfg.AixConfig),
) -> Envelope[Channel]:
    """Create an inactive draft DingTalk Channel."""
    resolved_owner = await _authorize(
        relay=relay,
        bot_id=bot_id,
        user_id=user_id,
        owner_id=owner_id,
    )
    _require_edit_lock(
        locks,
        bot_id=bot_id,
        owner_id=resolved_owner,
        user_id=user_id,
    )
    config = body.config.model_dump()
    for bcn_key in ("group_chat_scope", "outbound_visibility"):
        if config.get(bcn_key) is None:
            config.pop(bcn_key, None)
    config["binding_mode"] = body.binding_mode
    if body.binding_mode == "bcn_gateway":
        config.setdefault("group_chat_scope", "per_sender")
        config.setdefault("outbound_visibility", "full_transcript")
    config["aix_preview_url"] = aix_config.preview_url
    channel_id = service.create_channel(
        type=body.type,
        description=body.description,
        identity_id=resolved_owner,
        bind_bot_id=bot_id,
        config=config,
        status="0",
        stage="draft",
    )
    record = _owned_channel(
        service,
        channel_id,
        bot_id=bot_id,
        owner_id=resolved_owner,
    )
    return created(_project(record), request)


@router.get(
    "/{channel_id}",
    response_model=Envelope[Channel],
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def get_channel(
    bot_id: BotIdPath,
    channel_id: ChannelIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
) -> Envelope[Channel]:
    """Get one draft Channel within the addressed Bot scope."""
    resolved_owner = await _authorize(
        relay=relay,
        bot_id=bot_id,
        user_id=user_id,
        owner_id=owner_id,
    )
    return envelope(
        _project(
            _owned_channel(
                service,
                channel_id,
                bot_id=bot_id,
                owner_id=resolved_owner,
            )
        ),
        request,
    )


@router.patch(
    "/{channel_id}",
    response_model=Envelope[Channel],
    responses=CHANNEL_WRITE_RESPONSES,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def update_channel(
    bot_id: BotIdPath,
    channel_id: ChannelIdPath,
    body: ChannelUpdate,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
    aix_config: cfg.AixConfig = Injected(cfg.AixConfig),
) -> Envelope[Channel]:
    """Partially update a draft Channel, preserving an omitted secret."""
    resolved_owner = await _authorize(
        relay=relay,
        bot_id=bot_id,
        user_id=user_id,
        owner_id=owner_id,
    )
    _require_edit_lock(
        locks,
        bot_id=bot_id,
        owner_id=resolved_owner,
        user_id=user_id,
    )
    record = _owned_channel(
        service,
        channel_id,
        bot_id=bot_id,
        owner_id=resolved_owner,
    )
    stored_mode = record.config.get("binding_mode", "plugin")
    if body.binding_mode is not None and body.binding_mode != stored_mode:
        raise ChannelModeViolationError(
            "binding_mode is immutable; delete and recreate the Channel to switch"
        )
    config = dict(record.config)
    patch: dict[str, Any] = {}
    if body.config is not None:
        patch = body.config.model_dump(exclude_unset=True)
        if patch.get("client_secret") is None:
            patch.pop("client_secret", None)
        config.update(patch)
    try:
        validate_mode_matrix(
            stored_mode,
            robot_code=config.get("robot_code"),
            fields_set=set(patch),
        )
    except ValueError as exc:
        raise ChannelModeViolationError(str(exc)) from exc
    config.setdefault("aix_preview_url", aix_config.preview_url)
    description = (
        body.description
        if "description" in body.model_fields_set
        else record.description
    )
    service.update_channel(
        channel_id=channel_id,
        type=record.type,
        description=description,
        identity_id=resolved_owner,
        bind_bot_id=bot_id,
        config=config,
        status=record.status,
        stage="draft",
    )
    if record.status == "1":
        await _sync_active(service, channel_id)
    updated = _owned_channel(
        service,
        channel_id,
        bot_id=bot_id,
        owner_id=resolved_owner,
    )
    return envelope(_project(updated), request)


@router.put(
    "/{channel_id}/status",
    response_model=Envelope[Channel],
    responses=CHANNEL_WRITE_RESPONSES,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def update_channel_status(
    bot_id: BotIdPath,
    channel_id: ChannelIdPath,
    body: ChannelStatusUpdate,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[Channel]:
    """Activate or deactivate a draft Channel and synchronize its runtime."""
    resolved_owner = await _authorize(
        relay=relay,
        bot_id=bot_id,
        user_id=user_id,
        owner_id=owner_id,
    )
    _require_edit_lock(
        locks,
        bot_id=bot_id,
        owner_id=resolved_owner,
        user_id=user_id,
    )
    _owned_channel(
        service,
        channel_id,
        bot_id=bot_id,
        owner_id=resolved_owner,
    )
    await _set_status(service, channel_id, body.status)
    updated = _owned_channel(
        service,
        channel_id,
        bot_id=bot_id,
        owner_id=resolved_owner,
    )
    return envelope(_project(updated), request)


@router.delete(
    "/{channel_id}",
    response_model=Envelope[Deleted],
    responses=CHANNEL_WRITE_RESPONSES,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def delete_channel(
    bot_id: BotIdPath,
    channel_id: ChannelIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
    service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    locks: CollaboratorLockServiceProtocol = Injected(CollaboratorLockServiceProtocol),
) -> Envelope[Deleted]:
    """Deactivate and remove a draft Channel."""
    resolved_owner = await _authorize(
        relay=relay,
        bot_id=bot_id,
        user_id=user_id,
        owner_id=owner_id,
    )
    _require_edit_lock(
        locks,
        bot_id=bot_id,
        owner_id=resolved_owner,
        user_id=user_id,
    )
    record = _owned_channel(
        service,
        channel_id,
        bot_id=bot_id,
        owner_id=resolved_owner,
    )
    if record.status == "1":
        await _set_status(service, channel_id, "inactive")
    service.delete(channel_id=channel_id)
    return deleted(request)
