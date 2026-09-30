from __future__ import annotations

from engine.community.plugin_api.work_item.models import WorkItem, WorkItemCreate, WorkItemRef


def test_work_item_ref_holds_url():
    ref = WorkItemRef(url="https://example.com/space/W1/item?openWorkItemId=X")
    assert ref.url.startswith("https://")


def test_work_item_create_defaults():
    req = WorkItemCreate(staff_id="100000", space_ref="W1", subject="重构")
    assert req.content == ""
    assert req.item_type == "task"
    assert req.priority == "P2"
    assert req.extra == {}


def test_work_item_create_extra_escape_hatch():
    req = WorkItemCreate(
        staff_id="1", space_ref="W1", subject="s",
        extra={"assignee": "bob", "parentWorkitemId": "P1"},
    )
    assert req.extra["assignee"] == "bob"


def test_work_item_is_frozen():
    item = WorkItem(id="X", url="u", subject="s", content="c", raw={"k": 1})
    assert item.raw == {"k": 1}
    # frozen dataclass
    import dataclasses
    assert dataclasses.is_dataclass(item) and item.__dataclass_params__.frozen


def test_work_item_service_protocol_methods():
    from engine.community.plugin_api.work_item.protocol import WorkItemService

    # Protocol defines the three contract methods
    method_names = {m for m in dir(WorkItemService) if not m.startswith("_")}
    assert {"list_work_items", "get_work_item", "create_work_item"} <= method_names
