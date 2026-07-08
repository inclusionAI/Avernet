from __future__ import annotations

import pytest

from engine.community.di.infrastructure.work_item import CommunityWorkItemModule
from engine.community.di.infrastructure_test.work_item import TestWorkItemModule
from engine.community.plugin_api.work_item.models import WorkItemCreate, WorkItemRef
from engine.community.plugin_api.work_item.protocol import WorkItemService
from engine.community.plugins.work_item import NoopWorkItemService


@pytest.mark.asyncio
async def test_noop_work_item_service_all_methods_raise_same_clear_message():
    svc = NoopWorkItemService()
    expected = "work-item service is not supported on this engine profile"

    with pytest.raises(NotImplementedError, match=expected):
        await svc.list_work_items("space", "staff")
    with pytest.raises(NotImplementedError, match=expected):
        await svc.get_work_item(WorkItemRef(url="https://example.com/item"), "staff")
    with pytest.raises(NotImplementedError, match=expected):
        await svc.create_work_item(WorkItemCreate(staff_id="staff", space_ref="space", subject="s"))


def test_community_and_test_di_modules_bind_noop_work_item_service():
    community = CommunityWorkItemModule().work_item_service()
    test = TestWorkItemModule().work_item_service()

    assert isinstance(community, NoopWorkItemService)
    assert isinstance(test, NoopWorkItemService)
    # Structural check: the returned impl satisfies the neutral protocol surface.
    assert all(hasattr(community, name) for name in WorkItemService.__dict__ if not name.startswith("_"))
