"""
TraceIdMiddleware 单元测试

覆盖:
- 自动生成 trace_id（无 header）
- 上游透传 trace_id（X-Trace-ID header）
- X-Request-ID 回退
- X-Trace-ID 优先级高于 X-Request-ID
- 响应 header 包含 X-Trace-ID
- trace_id 写入 response body
"""

import sys
import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.infra.trace_context import get_trace_id

# 直接加载 trace_middleware 模块，避免触发 __init__.py 中的 app 导入链
_spec = importlib.util.spec_from_file_location(
    "trace_middleware",
    Path(__file__).parent.parent.parent / "src" / "interfaces" / "api" / "trace_middleware.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["trace_middleware"] = _mod
_spec.loader.exec_module(_mod)
TraceIdMiddleware = _mod.TraceIdMiddleware


@pytest.fixture
def trace_app():
    """创建一个带 TraceIdMiddleware 的测试 FastAPI 应用"""
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.post("/test-recommend")
    async def test_recommend():
        return {"trace_id": get_trace_id()}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


@pytest.fixture
def client(trace_app):
    """提供 TestClient"""
    with TestClient(trace_app) as c:
        yield c


class TestTraceIdAutoGenerate:
    """不传 header 时自动生成 trace_id"""

    def test_auto_generate_in_header(self, client):
        """响应 header 包含 X-Trace-ID"""
        response = client.post("/test-recommend", json={})
        assert "X-Trace-ID" in response.headers
        assert response.headers["X-Trace-ID"].startswith("trace_")

    def test_auto_generate_in_body(self, client):
        """响应 body 包含 trace_id"""
        response = client.post("/test-recommend", json={})
        body = response.json()
        assert body["trace_id"].startswith("trace_")

    def test_header_body_consistent(self, client):
        """header 和 body 中的 trace_id 一致"""
        response = client.post("/test-recommend", json={})
        body_trace_id = response.json()["trace_id"]
        header_trace_id = response.headers["X-Trace-ID"]
        assert body_trace_id == header_trace_id


class TestTraceIdUpstreamPassthrough:
    """上游透传 trace_id"""

    def test_x_trace_id_passthrough(self, client):
        """传入 X-Trace-ID 时复用"""
        response = client.post(
            "/test-recommend",
            json={},
            headers={"X-Trace-ID": "upstream_abc123"},
        )
        assert response.headers["X-Trace-ID"] == "upstream_abc123"
        assert response.json()["trace_id"] == "upstream_abc123"

    def test_x_request_id_fallback(self, client):
        """无 X-Trace-ID 但有 X-Request-ID 时复用"""
        response = client.post(
            "/test-recommend",
            json={},
            headers={"X-Request-ID": "req_456"},
        )
        assert response.headers["X-Trace-ID"] == "req_456"
        assert response.json()["trace_id"] == "req_456"

    def test_trace_id_priority(self, client):
        """两个都有时 X-Trace-ID 优先"""
        response = client.post(
            "/test-recommend",
            json={},
            headers={"X-Trace-ID": "trace_1", "X-Request-ID": "req_2"},
        )
        assert response.headers["X-Trace-ID"] == "trace_1"
        assert response.json()["trace_id"] == "trace_1"


class TestTraceIdHealthEndpoint:
    """健康检查端点也应有 trace_id（全局中间件）"""

    def test_health_has_trace_id_header(self, client):
        """GET /health 也返回 X-Trace-ID header"""
        response = client.get("/health")
        assert "X-Trace-ID" in response.headers


class TestTraceIdPerRequest:
    """每个请求获得独立的 trace_id"""

    def test_unique_per_request(self, client):
        """连续两次请求生成不同的 trace_id"""
        response1 = client.post("/test-recommend", json={})
        response2 = client.post("/test-recommend", json={})
        id1 = response1.headers["X-Trace-ID"]
        id2 = response2.headers["X-Trace-ID"]
        assert id1 != id2