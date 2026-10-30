"""Regression for explicit collaboration in the default personal inventory."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    PermissionLevel,
)
from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.adapters.noop_service_lifecycle import (
    NoopServiceLifecyclePort,
)
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import (
    BotInventoryService,
)
from agentclaw.community.core.bot_inventory.services.lifecycle_view import (
    BotLifecycleView,
)
from agentclaw.community.core.bot_inventory.types import DeployMode


@pytest.mark.unit
@pytest.mark.parametrize("source_space", [None, "personal:other", "team:shared"])
def test_personal_inventory_contains_explicit_collaborator_bot(source_space):
    row = {
        "id": 701,
        "bot_id": "shared-bot",
        "bot_name": "Shared",
        "owner_id": "other",
        "bot_type": "personal",
        "active_engine": "teclaw",
        "status": "ACTIVE",
        "template_type": "applicationCoding",
        "space_id": source_space,
    }
    bot = MagicMock()

    def list_rows(**kwargs):
        selected = [row] if kwargs.get("owner_id") in (None, "other") else []
        return {"items": selected, "total": len(selected)}

    bot.list_bots_by_conditions.side_effect = list_rows
    access = MagicMock()
    access.list_user_collaborations.return_value = [
        CollaboratorRecord(
            bot_pk=701,
            bot_id="shared-bot",
            owner_id="other",
            user_id="viewer",
            role="member",
            operator_id="other",
            env="dev",
        )
    ]
    access.get_operable_permission_levels.return_value = {701: PermissionLevel.MEMBER}
    access.has_explicit_membership.return_value = frozenset({701})
    desktop = MagicMock()
    desktop.list_user_bots.return_value = []
    locks = MagicMock()
    locks.states_for_bots.return_value = {}
    templates = MagicMock()
    templates.list_template_configs_by_bot_ids.side_effect = AssertionError(
        "shared creation snapshots must not be loaded"
    )
    inventory = BotInventoryService(
        bot_service=bot,
        desktop_service=desktop,
        access_service=access,
        business_space=NoopBusinessSpaceContext(),
        lifecycle_view=BotLifecycleView(NoopServiceLifecyclePort()),
        edit_lock_view=locks,
        template_port=templates,
    )
    items, total = inventory.list_items(
        owner_id="viewer",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="viewer", header_space_id=None
        ),
        keyword=None,
        engine="teclaw",
        deploy_mode=DeployMode.CLOUD,
        page=1,
        page_size=20,
    )
    assert ([item.bot_id for item in items], total) == (["shared-bot"], 1)

    assert items[0].template_config is None
    templates.list_template_configs_by_bot_ids.assert_not_called()
