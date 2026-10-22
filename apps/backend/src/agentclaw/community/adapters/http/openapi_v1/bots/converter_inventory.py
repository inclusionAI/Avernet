"""Convert inventory cards without querying or exposing publication internals."""

from agentclaw.community.core.bot_inventory.types import (
    BotInventoryItem as CoreItem,
    BotInventoryKind,
)

from .schemas import BotInventoryItem
from .schemas_inventory import InventoryPublication


def to_inventory_item(item: CoreItem) -> BotInventoryItem:
    """Adapt a core ``BotInventoryItem`` value object to the public schema.

    ``space`` is forwarded as a plain dict — pydantic coerces it into a
    ``BusinessSpace``. ``actions`` is a tuple of enums on the core side and a
    list of strings on the public side; ``disabled_actions`` mirrors that.
    """
    space = None
    if item.space is not None:
        space = {
            "space_id": item.space.space_id,
            "name": item.space.name,
            "kind": item.space.kind,
        }
    edit_lock = None
    if item.edit_lock is not None:
        edit_lock = {
            "locked": item.edit_lock.locked,
            "acquired": None,
            "holder_user_id": item.edit_lock.holder_user_id,
            "holder_name": item.edit_lock.holder_name,
            "has_collaborators": item.edit_lock.has_collaborators,
            "is_owner_holder": item.edit_lock.is_owner_holder,
            "need_lock": item.edit_lock.need_lock,
        }
    return BotInventoryItem(
        bot_id=item.bot_id,
        card_id=item.card_id,
        bot_name=item.bot_name,
        bot_desc=item.bot_desc,
        engine=item.engine,
        bot_type=item.bot_type,
        kind=item.kind.value,
        deploy_mode=item.deploy_mode.value,
        display_state=item.display_state.value,
        status=item.status,
        publish=(
            InventoryPublication(
                id=item.publication_id,
                status=item.internal_status or "",
                version=item.publication_version,
            )
            if item.kind is BotInventoryKind.SERVICE and item.publication_id is not None
            else None
        ),
        publication_id=item.publication_id,
        publication_version=item.publication_version,
        live_version=item.live_version,
        internal_status=item.internal_status,
        entity_id=item.entity_id,
        owner_entity_id=item.owner_entity_id,
        space=space,
        template_type=item.template_type,
        template_config=dict(item.template_config) if item.template_config else None,
        avatar_url=item.avatar_url,
        machine_id=item.machine_id,
        mount_path=item.mount_path,
        passport_id=item.passport_id,
        actions=[a.value for a in item.actions],
        disabled_actions=dict(item.disabled_actions) if item.disabled_actions else None,
        edit_lock=edit_lock,
    )
