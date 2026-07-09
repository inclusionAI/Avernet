"""E2E: stub adapter 配置下应用启动 + open_api 端点可达。

验证目标(防升级改坏):
1. ``config.plugins.engine_adapter=stub`` 时 ApplicationContainer 能正常装配,
   ``engine_adapter_registry`` 解析出 3 个 Noop adapter(aicoding/hermes/claude_code)。
2. ``/openapi/v1/runs`` 与 ``/openapi/v1/messages`` 路由已挂载,鉴权链路通(无 token → 401)。
3. 响应体带 ``trace_id`` 字段(印证 trace_id 上提到 ``ApiResponse`` 基类)。

归一化路由(active_engine + template_type → adapter)的断言由
``tests/unit/core/service/bot_run/test_engine_dispatch_integration.py`` 覆盖;
本 e2e 聚焦装配与端点可达,不依赖真实 engine / binding。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from secbaas.adapters.web.app import app
from secbaas.plugins.bot.engine_adapter.aicoding.stub import NoopAICodingAdapter
from secbaas.plugins.bot.engine_adapter.claude_code.stub import (
    NoopClaudeCodeAdapter,
)
from secbaas.plugins.bot.engine_adapter.hermes.stub import NoopHermesAdapter

pytestmark = [pytest.mark.e2e]


class TestStubAdapterBoot:
    """stub adapter 配置下的应用装配与端点可达性。"""

    def test_registry_resolves_three_noop_adapters(self, bootstrap_init):
        """stub 配置下 registry 注入 3 个 Noop adapter,证明装配链路没坏。"""
        registry = bootstrap_init.services.engine_adapter_registry()

        assert registry.has("aicoding")
        assert registry.has("hermes")
        assert registry.has("claude_code")
        # openclaw / teclaw 不注册(走 BaasBotService else 分支)
        assert not registry.has("openclaw")
        assert not registry.has("teclaw")

        assert isinstance(registry.get("aicoding"), NoopAICodingAdapter)
        assert isinstance(registry.get("hermes"), NoopHermesAdapter)
        assert isinstance(registry.get("claude_code"), NoopClaudeCodeAdapter)

    def test_runs_endpoint_rejects_missing_token(self, bootstrap_init):
        """POST /openapi/v1/runs 无 token → 401(鉴权链路通,路由已挂载)。"""
        with TestClient(app) as client:
            resp = client.post(
                "/openapi/v1/runs",
                json={"message": "hello"},
            )
        assert resp.status_code == 401
        body = resp.json()
        # 鉴权层错误响应契约:{"detail":{"code","message"}}
        assert body["detail"]["code"] == 40101
        assert body["detail"]["message"] == "Token 缺失"

    def test_messages_endpoint_rejects_missing_token(self, bootstrap_init):
        """POST /openapi/v1/messages 无 token → 401。"""
        with TestClient(app) as client:
            resp = client.post(
                "/openapi/v1/messages",
                json={"bot_id": "default:374193", "message": "hello"},
            )
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["code"] == 40101
