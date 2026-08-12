"""Bot inventory public routes."""
from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors, page as page_envelope
from agentclaw.community.api.bot_inventory_service import BotInventoryServiceProtocol
from agentclaw.community.core.bot_inventory.protocols import BusinessSpaceContextProtocol
from agentclaw.community.core.bot_inventory.types import (
    BotInventoryItem as CoreItem,
    DeployMode,
)
from agentclaw.community.di import Injected

from .schemas import BotActions, BotInventoryItem

router = APIRouter(prefix="/openapi/v1/bots/inventory", tags=["bot-inventory"])


def _to_item(item: CoreItem) -> BotInventoryItem:
    space = None
    if item.space is not None:
        space = {
            "space_id": item.space.space_id,
            "name": item.space.name,
            "kind": item.space.kind,
        }
    return BotInventoryItem(
        bot_id=item.bot_id,
        bot_name=item.bot_name,
        bot_desc=item.bot_desc,
        engine=item.engine,
        bot_type=item.bot_type,
        kind=item.kind.value,
        deploy_mode=item.deploy_mode.value,
        display_state=item.display_state.value,
        status=item.status,
        owner_entity_id=item.owner_entity_id,
        space=space,
        avatar_url=item.avatar_url,
        machine_id=item.machine_id,
        mount_path=item.mount_path,
        passport_id=item.passport_id,
        actions=[a.value for a in item.actions],
        disabled_actions=dict(item.disabled_actions) if item.disabled_actions else None,
    )


@router.get("", response_model=Envelope[Page[BotInventoryItem]])
@envelope_errors
async def list_inventory(
    page: PageParamsDep,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: str | None = Header(default=None, alias="X-Space-Id"),
    keyword: str | None = Query(default=None),
    engine: str | None = Query(default=None),
    deploy_mode: DeployMode | None = Query(default=None),
    service: BotInventoryServiceProtocol = Injected(BotInventoryServiceProtocol),
    space_context: BusinessSpaceContextProtocol = Injected(BusinessSpaceContextProtocol),
) -> Envelope[Page[BotInventoryItem]]:
    """List personal cloud and local Bots visible in the current business space."""
    current_space = space_context.resolve_current(
        owner_id=owner_id,
        header_space_id=x_space_id,
    )
    items, total = service.list_items(
        owner_id=owner_id,
        space=current_space,
        keyword=keyword,
        engine=engine,
        deploy_mode=deploy_mode,
        page=page.page,
        page_size=page.page_size,
    )
    return page_envelope(total, [_to_item(item) for item in items], request)


@router.get("/{bot_id}", response_model=Envelope[BotInventoryItem])
@envelope_errors
async def get_inventory_item(
    bot_id: str,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: str | None = Header(default=None, alias="X-Space-Id"),
    service: BotInventoryServiceProtocol = Injected(BotInventoryServiceProtocol),
    space_context: BusinessSpaceContextProtocol = Injected(BusinessSpaceContextProtocol),
) -> Envelope[BotInventoryItem]:
    """Get one Bot inventory card by Bot id."""
    current_space = space_context.resolve_current(
        owner_id=owner_id,
        header_space_id=x_space_id,
    )
    return envelope(
        _to_item(service.get_item(owner_id=owner_id, bot_id=bot_id, space=current_space)),
        request,
    )


@router.get("/{bot_id}/actions", response_model=Envelope[BotActions])
@envelope_errors
async def get_inventory_actions(
    bot_id: str,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: str | None = Header(default=None, alias="X-Space-Id"),
    service: BotInventoryServiceProtocol = Injected(BotInventoryServiceProtocol),
    space_context: BusinessSpaceContextProtocol = Injected(BusinessSpaceContextProtocol),
) -> Envelope[BotActions]:
    """Get action affordances for one Bot in the current business space."""
    current_space = space_context.resolve_current(
        owner_id=owner_id,
        header_space_id=x_space_id,
    )
    item = service.actions(owner_id=owner_id, bot_id=bot_id, space=current_space)
    return envelope(
        BotActions(
            bot_id=item.bot_id,
            display_state=item.display_state.value,
            actions=[a.value for a in item.actions],
            disabled_actions=dict(item.disabled_actions) if item.disabled_actions else None,
        ),
        request,
    )
