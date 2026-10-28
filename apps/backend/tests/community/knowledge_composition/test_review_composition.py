"""Reviewer regressions: composition is part of endpoint acceptance."""

import ast
from collections import Counter
from pathlib import Path

import pytest
from agentclaw.community.adapters.http.knowledge_management.assembly import (
    management_surface,
)
from agentclaw.community.di.optional_routers import OptionalRouters


def _routes(router):
    for route in router.routes:
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        else:
            yield route


ROOT = Path(__file__).resolve().parents[3]


def test_session_does_not_create_a_plugin_protocol_or_registered_placeholders():
    for relative in (
        "src/agentclaw/community/plugin_api/knowledge_session.py",
        "src/agentclaw/community/plugins/unavailable/knowledge_session.py",
    ):
        assert not (ROOT / relative).exists(), relative


def test_management_routes_are_registered_by_the_http_app_not_profiles(world):
    source = ROOT / "src/agentclaw/community/adapters/http/app.py"
    tree = ast.parse(source.read_text())
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
        and any(
            isinstance(arg, ast.Name) and arg.id == "knowledge_management_router"
            for arg in node.args
        )
        for node in ast.walk(tree)
    )
    paths = {route.path for route in _routes(management_surface)}
    for router in world.get(OptionalRouters).routers:
        assert not paths.intersection(route.path for route in _routes(router))
    from agentclaw.community.adapters.http.app import app

    counts = Counter(
        (method, route.path)
        for route in _routes(app)
        for method in getattr(route, "methods", ())
    )
    for route in _routes(management_surface):
        for method in route.methods:
            assert counts[method, route.path] == 1


def test_endpoint_seed_never_constructs_or_rebinds_business_services():
    source = ROOT / "tests/community/endpoints/test_knowledge_management.py"
    tree = ast.parse(source.read_text())
    prohibited = {
        "KnowledgeManagementService",
        "RemoteKnowledgeContentService",
        "RemoteKnowledgeCatalog",
        "EcbKnowledgeClient",
        "EcbKnowledgeContentClient",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert not isinstance(node.func, ast.Name) or node.func.id not in prohibited
            if isinstance(node.func, ast.Attribute) and node.func.attr == "bind":
                assert not any(
                    isinstance(arg, ast.Name)
                    and arg.id
                    in {
                        "KnowledgeManagementServiceProtocol",
                        "KnowledgeContentServiceProtocol",
                    }
                    for arg in node.args
                )


def test_management_and_content_providers_are_required_by_endpoint_success(
    world, app_with_testing_modules, monkeypatch
):
    from agentclaw.community.di.modules import knowledge_management_module as wiring
    from fastapi.testclient import TestClient
    from tests.community.endpoints.test_knowledge_management import _seed_enabled

    _seed_enabled(world)
    used = []

    def broken(*args, **kwargs):
        used.append(True)
        raise RuntimeError("broken knowledge composition provider")

    monkeypatch.setattr(wiring, "KnowledgeManagementService", broken)
    with TestClient(app_with_testing_modules) as client:
        with pytest.raises(RuntimeError, match="broken knowledge composition provider"):
            client.get(
                "/api/knowledge/spaces/1/knowledge-bases/kb/catalog-detail",
                headers={"x-user-id": "001234"},
            )
    assert used


def test_content_factory_is_required_by_endpoint_success(
    world, app_with_testing_modules, monkeypatch
):
    from agentclaw.community.di.modules import knowledge_management_module as wiring
    from fastapi.testclient import TestClient
    from tests.community.endpoints.test_knowledge_management import _seed_enabled

    _seed_enabled(world)
    used = []

    def broken(*args, **kwargs):
        used.append(True)
        raise RuntimeError("broken content composition provider")

    monkeypatch.setattr(wiring, "RemoteKnowledgeContentService", broken)
    with TestClient(app_with_testing_modules) as client:
        with pytest.raises(RuntimeError, match="broken content composition provider"):
            client.get(
                "/api/knowledge/spaces/1/knowledge-bases/kb/sources",
                headers={"x-user-id": "001234", "cookie": "test_session=fixture"},
            )
    assert used


def test_community_column_does_not_reintroduce_management_optional_routes():
    from agentclaw.community.di.modules.infrastructure.community.app_services import (
        CommunityAppServicesModule,
    )
    from injector import Injector

    optional = Injector([CommunityAppServicesModule()]).get(OptionalRouters)
    paths = {route.path for route in _routes(management_surface)}
    assert all(
        not paths.intersection(route.path for route in _routes(router))
        for router in optional.routers
    )
