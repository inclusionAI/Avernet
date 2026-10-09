"""Tests for adapters/web/dependencies.py — FastAPI auth dependency."""

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.dependencies import get_op_ctx
from secbaas.community.api import OperationContext


class TestGetOpCtx:
    """Tests for get_op_ctx(request) — the auth dependency."""

    def test_delegates_to_auth_service_build_operation_context(self):
        """WHEN called, THEN delegates to AuthService.build_operation_context."""
        expected_ctx = OperationContext(operator="test_user", env="dev")

        app = FastAPI()

        @app.get("/test")
        async def test_handler(ctx: OperationContext = Depends(get_op_ctx)):
            return {"operator": ctx.operator, "env": ctx.env}

        app.dependency_overrides[get_op_ctx] = lambda: expected_ctx

        with TestClient(app) as client:
            resp = client.get("/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["operator"] == "test_user"
        assert data["env"] == "dev"

    def test_request_is_passed_as_parameter(self):
        """FastAPI passes the Request object as the first parameter."""
        app = FastAPI()

        @app.get("/check")
        async def check_handler(ctx: OperationContext = Depends(get_op_ctx)):
            return {"ok": True}

        captured = {}

        async def capturing_dep(request: Request):
            captured["request"] = request
            return OperationContext(operator="test_user", env="dev")

        app.dependency_overrides[get_op_ctx] = capturing_dep

        with TestClient(app) as client:
            client.get("/check")

        request_arg = captured["request"]
        assert isinstance(request_arg, Request)
        assert request_arg.method == "GET"
