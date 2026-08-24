"""Canonical Repo catalog wire contracts and stable failure envelopes."""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.adapters.http.openapi_v1.repository_catalog import (
    get_repository_skill,
    list_repository_skills,
    repository_tree,
    router,
)
from agentclaw.community.api.repository_catalog_service import (
    RepositoryCatalogServiceProtocol,
)
from tests.community.adapters.http.openapi_v1.conftest import user_scoped_client


class _Catalog:
    def __init__(self, *, sync_status: str = "completed") -> None:
        self.sync_status = sync_status
        self.calls: list[tuple[str, object]] = []
        self.items = [
            {"id": "1", "name": "report", "description": "weekly", "category": "ops"},
            {
                "id": "2",
                "name": "report-two",
                "description": "monthly",
                "category": "ops",
            },
            {"id": "3", "name": "other", "description": "other", "category": "dev"},
        ]

    def list(self, *, path=None, orderby=None):
        self.calls.append(("list", (path, orderby)))
        return self.items

    def list_page(
        self, *, path=None, orderby=None, keyword="", page: int, page_size: int
    ):
        self.calls.append(("list_page", (path, orderby, keyword, page, page_size)))
        items = [item for item in self.items if keyword.lower() in item["name"].lower()]
        start = (page - 1) * page_size
        return len(items), items[start : start + page_size]

    def search(self, *, keyword: str, limit: int = 100):
        self.calls.append(("search", (keyword, limit)))
        return [item for item in self.items if keyword in item["name"]][:limit]

    def tree(self):
        self.calls.append(("tree", None))
        return [{"name": "ops", "children": []}]

    def detail(self, skill_id: str):
        self.calls.append(("detail", skill_id))
        return next((item for item in self.items if item["id"] == skill_id), None)

    def sync(self):
        self.calls.append(("sync", None))
        if self.sync_status == "completed":
            return {"status": "completed", "result": {"synced": True}}
        if self.sync_status == "in_progress":
            return {"status": "in_progress"}
        return {"status": "failed", "message": "internal detail must not leak"}


def _client(catalog: _Catalog) -> TestClient:
    class Bindings(Module):
        def configure(self, binder) -> None:
            binder.bind(RepositoryCatalogServiceProtocol, to=catalog)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "actor"}
    attach_injector(app, Injector([Bindings()]))
    return user_scoped_client(app, "actor")


def test_repository_list_has_real_filtered_pagination_and_sort_mapping() -> None:
    catalog = _Catalog()
    response = _client(catalog).get(
        "/openapi/v1/bots/skills/repository?keyword=report&sort=hottest&page=2&page_size=1"
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "total": 2,
        "items": [catalog.items[1]],
    }
    assert catalog.calls == [("list_page", (None, "hotest", "report", 2, 1))]


def test_repository_tree_and_detail_use_the_single_catalog_service() -> None:
    catalog = _Catalog()
    client = _client(catalog)

    assert client.get("/openapi/v1/bots/skills/repository/tree").json()["data"] == [
        {"name": "ops", "children": []}
    ]
    assert (
        client.get("/openapi/v1/bots/skills/repository/1").json()["data"]
        == catalog.items[0]
    )
    assert catalog.calls == [("tree", None), ("detail", "1")]


def test_repository_missing_detail_uses_stable_envelope() -> None:
    response = _client(_Catalog()).get("/openapi/v1/bots/skills/repository/999")

    assert response.status_code == 404
    assert response.json()["code"] == 404000
    assert response.json()["message"] == "Not found"
    assert response.json()["data"] is None


def test_repository_sync_conflict_and_failure_do_not_leak_http_exception_detail() -> (
    None
):
    conflict = _client(_Catalog(sync_status="in_progress")).post(
        "/openapi/v1/bots/skills/repository/sync"
    )
    failed = _client(_Catalog(sync_status="failed")).post(
        "/openapi/v1/bots/skills/repository/sync"
    )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == 409108
    assert (
        conflict.json()["message"]
        == "Repository synchronization is already in progress"
    )
    assert failed.status_code == 502
    assert failed.json()["code"] == 502103
    assert failed.json()["message"] == "Repository synchronization failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "kwargs"),
    [
        (
            list_repository_skills,
            {"page": PageParams(), "keyword": "", "path": None, "sort": "latest"},
        ),
        (repository_tree, {}),
        (get_repository_skill, {"skill_id": "1"}),
    ],
)
async def test_repository_read_operations_offload_blocking_service_work(
    handler, kwargs
) -> None:
    class BlockingCatalog(_Catalog):
        def __init__(self) -> None:
            super().__init__()
            self.thread_ids: set[int] = set()

        def list_page(self, **kwargs):
            self.thread_ids.add(threading.get_ident())
            return super().list_page(**kwargs)

        def tree(self):
            self.thread_ids.add(threading.get_ident())
            return super().tree()

        def detail(self, skill_id):
            self.thread_ids.add(threading.get_ident())
            return super().detail(skill_id)

    catalog = BlockingCatalog()
    request = Request({"type": "http", "headers": []})
    event_loop_thread = threading.get_ident()

    await handler(request=request, _actor_id="actor", service=catalog, **kwargs)

    assert catalog.thread_ids
    assert catalog.thread_ids.isdisjoint({event_loop_thread})
