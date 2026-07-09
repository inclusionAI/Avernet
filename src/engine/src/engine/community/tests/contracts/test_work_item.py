"""Rule 25 conformance: every WorkItemService impl behaves identically through
the upper-layer consumer (the router). Exercises Noop (community) and the real
CORP impl shape with the network client doubled.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Binder, Injector, InstanceProvider, Module, singleton

from engine.community.api.work_item.router import router
from engine.community.plugin_api.work_item.models import WorkItem
from engine.community.plugin_api.work_item.protocol import WorkItemService
from engine.community.plugins.work_item import NoopWorkItemService


def _app_with(service) -> TestClient:
    app = FastAPI()

    class _Module(Module):
        def configure(self, binder: Binder) -> None:
            binder.bind(WorkItemService, to=InstanceProvider(service), scope=singleton)

    attach_injector(app, Injector([_Module()]))
    app.include_router(router)
    return TestClient(app)


class _DoublingService:
    """Doubles the CORP service shape without network."""

    async def list_work_items(self, space_ref, staff_id):
        return [WorkItem(id="A", url="u", subject="s", content="c", raw={})]

    async def get_work_item(self, ref, staff_id):
        return WorkItem(id="A", url="u", subject="s", content="c", raw={})

    async def create_work_item(self, req):
        return WorkItem(id="NEW", url="u", subject=req.subject, content="", raw={})


def test_noop_impl_yields_501_through_router():
    client = _app_with(NoopWorkItemService())
    r = client.get("/api/work-items", params={"space_ref": "W1", "staff_id": "1"})
    assert r.status_code == 501


def test_conforming_impl_returns_200_list():
    client = _app_with(_DoublingService())
    r = client.get("/api/work-items", params={"space_ref": "W1", "staff_id": "1"})
    assert r.status_code == 200
    assert r.json()["data"][0]["id"] == "A"


def test_conforming_impl_returns_200_detail():
    client = _app_with(_DoublingService())
    r = client.get("/api/work-items/detail", params={"url": "https://x/i", "staff_id": "1"})
    assert r.status_code == 200


def test_conforming_impl_returns_200_create():
    client = _app_with(_DoublingService())
    r = client.post("/api/work-items", json={
        "staffId": "1", "spaceRef": "W1", "subject": "s",
    })
    assert r.status_code == 200
    assert r.json()["data"]["id"] == "NEW"
