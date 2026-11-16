"""Regression tests for the public inventory's cross-source page order."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.adapters.service_lifecycle import (
    ServiceLifecycleView,
)
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import (
    BotInventoryService,
)
from agentclaw.community.core.bot_inventory.services.lifecycle_view import (
    BotLifecycleView,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)


@pytest.mark.unit
def test_inventory_orders_bots_then_visible_publications_before_slicing():
    def bot(pk, name, kind, created):
        return dict(
            id=pk, bot_id=name, bot_name=name, bot_type=kind, gmt_create=created
        )

    cloud = Mock()
    cloud.list_bots_by_conditions.return_value = {
        "total": 3,
        "items": [
            dict(
                bot(1, "A", "service", "2026-01-01T00:00:00"),
                gmt_modified="2026-05-01T00:00:00",
            ),
            bot(2, "Y", "personal", "2026-03-01T00:00:00"),
            bot(3, "legacy", "personal", None),
        ],
    }
    desktop = Mock()
    desktop.list_user_bots.return_value = [
        bot(4, "Z", "desktop", "2026-04-01T00:00:00")
    ]
    publications = Mock()
    publications.list_by_source_bots.return_value = [
        BotPublishRecord(
            id=version,
            source_bot_pk=1,
            source_bot_id="A",
            publish_bot_id="A",
            name="A",
            owner_id="owner",
            permission_owner="owner",
            env="dev",
            version=version,
            status=status.value,
            gmt_create=created,
        )
        for version, status, created in [
            (2, PublishStatus.DRAFT, datetime(2026, 1, 15)),
            (1, PublishStatus.SUCCESS, datetime(2026, 4, 15)),
        ]
    ]
    access = Mock()
    access.get_operable_permission_levels.return_value = {}
    access.has_explicit_membership.return_value = frozenset()
    spaces = NoopBusinessSpaceContext()
    inventory = BotInventoryService(
        bot_service=cloud,
        desktop_service=desktop,
        access_service=access,
        business_space=spaces,
        lifecycle_view=BotLifecycleView(ServiceLifecycleView(publications)),
        edit_lock_view=Mock(),
        template_port=Mock(),
    )

    def page(number):
        cards, total = inventory.list_items(
            owner_id="owner",
            space=spaces.resolve_current(owner_id="owner", header_space_id=None),
            keyword=None,
            engine=None,
            deploy_mode=None,
            page=number,
            page_size=3,
        )
        assert total == 5
        return [(c.bot_id, c.publication_version) for c in cards]

    assert page(1) == [("Z", None), ("Y", None), ("A", 1)]
    assert page(2) == [("A", 2), ("legacy", None)]
