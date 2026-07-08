from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Binder, Injector, InstanceProvider, Module, singleton

from engine.community.api.work_item.router import router
from engine.community.plugin_api.work_item.models import WorkItem, WorkItemCreate, WorkItemRef
from engine.community.plugin_api.work_item.protocol import WorkItemService


class _FakeService:
    def __init__(self):
        self.created: list[WorkItemCreate] = []
        self.list_return: list[WorkItem] = []
        self.get_return: WorkItem | None = None

    async def list_work_items(self, space_ref, staff_id):
        return self.list_return

    async def get_work_item(self, ref, staff_id):
        if self.get_return is None:
            raise RuntimeError("not configured")
        return self.get_return

    async def create_work_item(self, req):
        self.created.append(req)
        return WorkItem(id="NEW", url="u", subject=req.subject, content=req.content, raw={})


def _app(service):
    app = FastAPI()

    class _Module(Module):
        def configure(self, binder: Binder) -> None:
            binder.bind(WorkItemService, to=InstanceProvider(service), scope=singleton)

    attach_injector(app, Injector([_Module()]))
    app.include_router(router)
    return app


def test_list_returns_neutral_items():
    svc = _FakeService()
    svc.list_return = [WorkItem(id="A", url="u", subject="s", content="c", raw={"k": 1})]
    client = TestClient(_app(svc))
    r = client.get("/api/work-items", params={"space_ref": "W1", "staff_id": "1"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"][0]["id"] == "A"
    assert body["data"][0]["raw"] == {"k": 1}


def test_detail_uses_url_query_param():
    svc = _FakeService()
    svc.get_return = WorkItem(id="X", url="u", subject="s", content="c", raw={})
    client = TestClient(_app(svc))
    r = client.get("/api/work-items/detail", params={"url": "https://x/i", "staff_id": "1"})
    assert r.status_code == 200
    assert r.json()["data"]["id"] == "X"


def test_create_translates_camel_body_and_extra_escape_hatch():
    svc = _FakeService()
    client = TestClient(_app(svc))
    r = client.post("/api/work-items", json={
        "staffId": "100000",
        "spaceRef": "W1",
        "subject": "重构",
        "content": "正文",
        "itemType": "bug",
        "priority": "P1",
        "assignee": "bob",
    })
    assert r.status_code == 200
    req = svc.created[0]
    assert req.staff_id == "100000"
    assert req.space_ref == "W1"
    assert req.item_type == "bug"
    assert req.extra == {"assignee": "bob"}


def test_not_implemented_translates_to_501():
    svc = _FakeService()

    async def boom(*a, **kw):
        raise NotImplementedError("not supported")
    svc.list_work_items = boom  # type: ignore

    client = TestClient(_app(svc))
    r = client.get("/api/work-items", params={"space_ref": "W1", "staff_id": "1"})
    assert r.status_code == 501
    assert "not supported" in r.json()["detail"]


def test_detail_not_implemented_translates_to_501():
    svc = _FakeService()

    async def boom(*a, **kw):
        raise NotImplementedError("detail unsupported")
    svc.get_work_item = boom  # type: ignore

    client = TestClient(_app(svc))
    r = client.get("/api/work-items/detail", params={"url": "https://x/i", "staff_id": "1"})
    assert r.status_code == 501
    assert r.json()["detail"] == "detail unsupported"


def test_create_not_implemented_translates_to_501():
    svc = _FakeService()

    async def boom(*a, **kw):
        raise NotImplementedError("create unsupported")
    svc.create_work_item = boom  # type: ignore

    client = TestClient(_app(svc))
    r = client.post("/api/work-items", json={"subject": "s"})
    assert r.status_code == 501
    assert r.json()["detail"] == "create unsupported"


def test_create_uses_vendor_neutral_defaults_for_missing_optional_fields():
    svc = _FakeService()
    client = TestClient(_app(svc))
    r = client.post("/api/work-items", json={"subject": "只填标题"})
    assert r.status_code == 200
    req = svc.created[0]
    assert req.staff_id == ""
    assert req.space_ref == ""
    assert req.subject == "只填标题"
    assert req.content == ""
    assert req.item_type == "task"
    assert req.priority == "P2"
    assert req.extra == {}
