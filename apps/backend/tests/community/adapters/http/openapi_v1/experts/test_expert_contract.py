"""Public expert discovery uses real persisted scope-filtered content."""

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.openapi_v1.experts import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
    resolve_avernet_tenant,
)
from agentclaw.community.api.expert_catalog_service import ExpertCatalogServiceProtocol
from agentclaw.community.core.expert_market.models import ExpertScope
from agentclaw.community.core.expert_market.repository import ExpertCatalogRepository
from agentclaw.community.core.expert_market.scanner import ExpertScanner
from agentclaw.community.core.expert_market.service import (
    ExpertCatalogConfig,
    ExpertCatalogService,
)
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests
from tests.community.core.expert_market.test_catalog import write_expert


def test_real_catalog_http_dto_scope_filters_and_limits(tmp_path):
    reset_for_tests()
    db = SqliteDB()
    asyncio.run(db.bootstrap())
    catalog = ExpertCatalogRepository(db)
    write_expert(tmp_path)
    ExpertScanner(catalog).scan(
        tmp_path, source_commit="a" * 40, scope=ExpertScope("a", "dev", "aiworkbench")
    )
    service = ExpertCatalogService(catalog, ExpertCatalogConfig())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: object()
    app.dependency_overrides[resolve_avernet_tenant] = lambda: "a"
    injector = Injector()
    injector.binder.bind(ExpertCatalogServiceProtocol, to=service)
    attach_injector(app, injector)
    with TestClient(app) as client:
        response = client.get("/openapi/v1/experts", params={"keyword": "PRD"})
        assert response.status_code == 200
        assert response.json()["data"]["total"] == 1
        assert response.json()["data"]["items"][0]["creation_count"] == 0
        assert "manifest_template" not in response.text
        assert client.get("/openapi/v1/experts/categories").json()["data"] == ["内容"]
        detail = client.get("/openapi/v1/experts/writer")
        assert detail.json()["data"]["identity"][0]["content"] == "原文正文"
        assert "source_commit" not in detail.text
        assert (
            client.get("/openapi/v1/experts", params={"page_size": 101}).status_code
            == 422
        )
        assert (
            client.get("/openapi/v1/experts", params={"sort": "rating"}).status_code
            == 422
        )
        app.dependency_overrides[resolve_avernet_tenant] = lambda: "other"
        assert client.get("/openapi/v1/experts").json()["data"] == {
            "total": 0,
            "items": [],
        }
        assert client.get("/openapi/v1/experts/writer").status_code == 404
    reset_for_tests()
