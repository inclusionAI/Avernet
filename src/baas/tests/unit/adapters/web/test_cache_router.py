"""
Cache Router 单元测试

测试 cache_router.py 中的缓存读写路由。
重点验证：GET 读接口必须经过 cookie 鉴权（get_op_ctx），POST 写接口不鉴权。
使用 TestClient 搭配依赖 mock 进行验证。
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.dependencies import get_op_ctx
from secbaas.community.adapters.web.routers.internal.cache_router import router
from secbaas.community.api import OperationContext
from secbaas.community.bootstrap import Provide
from tests.unit.adapters.web.conftest import iter_api_routes

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _install_mock_cache(app, mock_cache):
    """Replace every Provide[...] dependency with *mock_cache*."""
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, Provide):
                app.dependency_overrides[dep.call] = lambda: mock_cache


def _build_app(mock_cache, op_ctx_override=None):
    """Build a test app with the cache router and mocked cache plugin.

    ``op_ctx_override`` 为 None 时不覆盖 get_op_ctx，用于验证未鉴权场景。
    """
    app = FastAPI()
    app.include_router(router)
    _install_mock_cache(app, mock_cache)
    if op_ctx_override is not None:
        app.dependency_overrides[get_op_ctx] = op_ctx_override
    return app


@pytest.fixture
def mock_cache():
    return MagicMock()


@pytest.fixture
def authed_client(mock_cache):
    """TestClient with get_op_ctx overridden to a fake operator."""
    app = _build_app(
        mock_cache,
        op_ctx_override=lambda: OperationContext(operator="admin", env="dev"),
    )
    with TestClient(app) as tc:
        yield tc


# ---------------------------------------------------------------------------
# POST /api/v1/cache/{key} — 不鉴权
# ---------------------------------------------------------------------------


class TestSetCacheNoAuth:
    """POST /api/v1/cache/{key} 不依赖 get_op_ctx，无需 cookie。"""

    def test_set_cache_success_without_auth(self, mock_cache):
        """写入缓存返回 200，无需任何鉴权依赖。"""
        app = _build_app(mock_cache)
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/cache/my-key",
                json={"value": "hello", "ttl_seconds": 300},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["key"] == "my-key"
        assert body["data"]["ttl_seconds"] == 300
        mock_cache.set.assert_called_once_with("my-key", "hello", ttl_seconds=300)

    def test_set_cache_has_no_op_ctx_dependency(self):
        """set_cache 路由不应声明 get_op_ctx 依赖。"""
        post_route = next(
            r
            for r in iter_api_routes(router)
            if r.path == "/api/v1/cache/{key}" and "POST" in r.methods
        )
        dep_calls = [dep.call for dep in post_route.dependant.dependencies]
        assert get_op_ctx not in dep_calls


# ---------------------------------------------------------------------------
# GET /api/v1/cache/{key} — 鉴权行为
# ---------------------------------------------------------------------------


class TestGetCacheAuth:
    """GET /api/v1/cache/{key} 必须经过 get_op_ctx 鉴权。"""

    def test_get_cache_success_with_auth(self, authed_client, mock_cache):
        """鉴权通过时正常读取缓存。"""
        mock_cache.get.return_value = "hello"
        response = authed_client.get("/api/v1/cache/my-key")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["key"] == "my-key"
        assert body["data"]["value"] == "hello"
        mock_cache.get.assert_called_once_with("my-key")

    def test_get_cache_rejected_when_auth_fails(self, mock_cache):
        """get_op_ctx 鉴权失败（如无 cookie）时返回 401，且不读缓存。"""
        app = _build_app(
            mock_cache,
            op_ctx_override=lambda: (_ for _ in ()).throw(
                HTTPException(status_code=401, detail="missing login cookie")
            ),
        )
        with TestClient(app) as client:
            response = client.get("/api/v1/cache/my-key")

        assert response.status_code == 401
        mock_cache.get.assert_not_called()

    def test_get_cache_depends_on_op_ctx(self):
        """get_cache 路由必须声明 get_op_ctx 依赖（防回归：误删鉴权参数）。"""
        get_route = next(
            r
            for r in iter_api_routes(router)
            if r.path == "/api/v1/cache/{key}" and "GET" in r.methods
        )
        dep_calls = [dep.call for dep in get_route.dependant.dependencies]
        assert get_op_ctx in dep_calls
