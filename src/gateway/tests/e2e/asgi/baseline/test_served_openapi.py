from __future__ import annotations

import pytest
from fastapi import FastAPI

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestServedOpenAPI:
    def test_schema_contains_upstream_domains(self, app_no_lifespan: FastAPI) -> None:
        schema = app_no_lifespan.openapi()
        paths = schema.get("paths", {})
        domain_paths = [p for p in paths if p.startswith("/openapi/v1/")]
        assert len(domain_paths) > 0, (
            "Upstream domain paths must be populated from configs/schemas/ "
            "by build_forwarding calling set_sources + refresh_all"
        )

    def test_bots_domain_served(self, app_no_lifespan: FastAPI) -> None:
        schema = app_no_lifespan.openapi()
        paths = schema.get("paths", {})
        bots = [p for p in paths if p.startswith("/openapi/v1/bots")]
        assert len(bots) > 0, "bots.openapi.json must be loaded by schema catalog"

    def test_bot_logs_are_served_under_bots_domain(
        self, app_no_lifespan: FastAPI
    ) -> None:
        schema = app_no_lifespan.openapi()
        paths = schema.get("paths", {})
        expected = {
            "/openapi/v1/bots/logs/sessions/{session_key}/traces",
            "/openapi/v1/bots/logs/tasks/{biz_scene}/{biz_task_id}/traces",
            "/openapi/v1/bots/logs/groups/{group_id}/traces",
            "/openapi/v1/bots/logs/traces",
            "/openapi/v1/bots/logs/traces/{trace_id}",
        }
        assert expected <= set(paths), (
            "bots.openapi.json must publish all Bot Logs operations"
        )
        for path in expected:
            assert paths[path]["get"]["x-avernet-security"] == {
                "user": "required",
                "app": "required",
            }
        assert not any(path.startswith("/openapi/v1/bot-chats") for path in paths)

    def test_baas_domain_served(self, app_no_lifespan: FastAPI) -> None:
        schema = app_no_lifespan.openapi()
        paths = schema.get("paths", {})
        # sessions/messages/runs filter by /openapi/v1/{domain} — no match for /openapi/v1/chat/*.
        # Add a "chat" domain to config to serve these paths.
        baas = [p for p in paths if p.startswith("/openapi/v1/sessions")]
        assert len(baas) == 0, (
            "sessions domain filter /openapi/v1/sessions does not match "
            "/openapi/v1/chat/sessions/* — add a 'chat' domain to serve these"
        )

    def test_collaboration_domain_uses_the_approved_prefix_only(
        self, app_no_lifespan: FastAPI
    ) -> None:
        paths = app_no_lifespan.openapi().get("paths", {})
        assert "/openapi/v1/collaboration/bots/mine" in paths
        for retired_prefix in (
            "/openapi/v1/bots/collaboration",
            "/openapi/v1/group-sessions",
        ):
            assert not any(path.startswith(retired_prefix) for path in paths)

    def test_schema_has_required_fields(self, app_no_lifespan: FastAPI) -> None:
        from fastapi.testclient import TestClient

        with TestClient(app_no_lifespan) as c:
            resp = c.get("/openapi.json")
            assert resp.status_code == 200
            data = resp.json()
            assert "openapi" in data

    def test_local_routes_excluded(self, app_no_lifespan: FastAPI) -> None:
        schema = app_no_lifespan.openapi()
        paths = schema.get("paths", {})
        assert "/health" not in paths, (
            "Local /health must be excluded from served schema"
        )
        assert "/api/test" not in paths, (
            "Local /api/test must be excluded from served schema"
        )

    def test_openapi_json_endpoint_serves_upstream_paths(
        self, app_no_lifespan: FastAPI
    ) -> None:
        from fastapi.testclient import TestClient

        with TestClient(app_no_lifespan) as c:
            resp = c.get("/openapi.json")
            assert resp.status_code == 200
            data = resp.json()
            paths = data.get("paths", {})
            domain_paths = [p for p in paths if p.startswith("/openapi/v1/")]
            assert len(domain_paths) > 0, (
                "/openapi.json must return schema with upstream domain paths"
            )
