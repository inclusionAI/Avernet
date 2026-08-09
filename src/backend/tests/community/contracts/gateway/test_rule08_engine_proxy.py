"""Rule #8 — 引擎代理 (/api/v1/engine/{target}/*) 契约测试。

引擎代理接口由 Gateway 直接转发到 AgentClawProxy（引擎进程），
后端本身不处理这些路由。此处使用 responses 库 mock 引擎 HTTP 响应，
验证文档规定的响应字段结构。

改进: mock 数据同时通过 JSON Schema 契约验证（schema_snapshots/bcs/），
确保 mock 数据与权威契约一致，避免 mock 与断言同源导致的同义反复。

接口:
- POST /api/v1/engine/{target}/sessions — 创建会话
- GET  /api/v1/engine/{target}/sessions — 查询会话
- GET  /api/v1/engine/{target}/sessions/mine — 我的会话
- POST /api/v1/engine/{target}/sessions/{id}/update — 更新会话
- DELETE /api/v1/engine/{target}/sessions/{id}/messages — 删除会话消息
- GET  /api/v1/engine/{target}/models — 模型列表
- GET  /api/v1/engine/{target}/engine/status — 引擎状态
- POST /api/v1/engine/{target}/engine/restart — 重启引擎

不含 engine/switch：Bot 的引擎在创建时固定（inclusionAI/Avernet#914），引擎侧的
``POST /api/engine/switch`` 已下线。Gateway 仍配着这条转发规则（见
`Gateway 透明转发接口文档.md`），但上游已无对应路由，因此这里不再冻结它的响应契约。
"""
from __future__ import annotations

import requests
import responses

from tests.community.contracts.gateway.conftest import assert_has_fields
from tests.community.contracts.gateway.schema_utils import validate_mock_against_schema, load_contract_schema

PROXY_BASE = "https://agentclawproxy-pre.teamclaw.com"
TARGET = "ARCA_test%400:20003"


# ── Mock Data ↔ JSON Schema Contract Validation ──────────────────────────────


class TestEngineProxyMockConformance:
    """验证 Engine Proxy mock 数据符合 JSON Schema 契约。"""

    def test_mock_session_conforms(self):
        validate_mock_against_schema(
            MOCK_SESSION, load_contract_schema("session_data"), label="MOCK_SESSION",
        )

    def test_mock_model_conforms(self):
        validate_mock_against_schema(
            MOCK_MODEL, load_contract_schema("model_capabilities"), label="MOCK_MODEL",
        )

    def test_mock_engine_status_conforms(self):
        validate_mock_against_schema(
            MOCK_ENGINE_STATUS, load_contract_schema("engine_status_response"),
            label="MOCK_ENGINE_STATUS",
        )


# ── Mock 数据 ──────────────────────────────────────────────────────────────

MOCK_SESSION = {
    "id": "session:abc:user:default",
    "title": "Test Session",
    "user_id": "448524",
    "agent_id": "bot_test_001",
    "model": "openclaw/default",
    "permission_mode": "default",
    "cwd": "/home/admin/.openclaw/workspace",
    "gmt_create": "2026-01-01T00:00:00Z",
    "gmt_modified": "2026-01-01T00:00:00Z",
    "message_count": 0,
    "last_message": None,
}

MOCK_MODEL = {
    "id": "model_001",
    "provider_id": "openclaw",
    "provider": "openclaw",
    "name": "default",
    "display_name": "Default",
    "description": "Default model",
    "enterprise_enabled": True,
    "enterprise_default": True,
    "capabilities": {
        "context_window": 128000,
        "max_output_tokens": 8192,
        "vision": False,
        "function_calling": True,
        "reasoning": False,
        "streaming": True,
    },
}

MOCK_ENGINE_STATUS = {
    "engine": "openclaw",
    "active_connections": 1,
    "process": {
        "running": True,
        "pid": 12345,
        "exit_code": None,
        "last_error": None,
        "command_enabled": True,
        "managed_process": True,
    },
    "transition": None,
}


class TestCreateSession:
    """POST /proxypass/{target}/api/sessions — 创建会话。"""

    @responses.activate
    def test_create_session_schema(self):
        resp_data = {"success": True, "data": MOCK_SESSION}
        responses.add(
            responses.POST,
            f"{PROXY_BASE}/proxypass/{TARGET}/api/sessions",
            json=resp_data,
            status=200,
        )
        resp = requests.post(
            f"{PROXY_BASE}/proxypass/{TARGET}/api/sessions",
            json={"title": "Test Session"},
            headers={"X-PROXYPASS-TOKEN": "test-token"},
        )
        body = resp.json()
        assert_has_fields(body, {"success": bool, "data": (dict, list, type(None))}, label="POST sessions response")
        assert_has_fields(
            body["data"],
            {"id": str, "title": str, "user_id": str, "agent_id": str, "model": str},
            label="POST sessions data",
        )


class TestListSessions:
    """GET /proxypass/{target}/api/sessions — 查询会话。"""

    @responses.activate
    def test_list_sessions_schema(self):
        resp_data = {"success": True, "data": [MOCK_SESSION]}
        responses.add(
            responses.GET,
            f"{PROXY_BASE}/proxypass/{TARGET}/api/sessions",
            json=resp_data,
            status=200,
        )
        resp = requests.get(
            f"{PROXY_BASE}/proxypass/{TARGET}/api/sessions",
            headers={"X-PROXYPASS-TOKEN": "test-token"},
        )
        body = resp.json()
        data = body.get("data", [])
        assert isinstance(data, list), "GET sessions data should be list"
        assert_has_fields(
            data[0],
            {"id": str, "title": str, "user_id": str, "gmt_create": str, "message_count": int},
            label="GET sessions data[0]",
        )


class TestListModels:
    """GET /proxypass/{target}/api/models — 模型列表。"""

    @responses.activate
    def test_list_models_schema(self):
        resp_data = {"success": True, "data": {"models": [MOCK_MODEL]}}
        responses.add(
            responses.GET,
            f"{PROXY_BASE}/proxypass/{TARGET}/api/models",
            json=resp_data,
            status=200,
        )
        resp = requests.get(
            f"{PROXY_BASE}/proxypass/{TARGET}/api/models",
            headers={"X-PROXYPASS-TOKEN": "test-token"},
        )
        body = resp.json()
        assert_has_fields(body, {"success": bool, "data": (dict, list, type(None))}, label="GET models response")
        data = body["data"]
        assert_has_fields(data, {"models": list}, label="GET models data")
        models = data["models"]
        assert isinstance(models, list)
        assert_has_fields(
            models[0],
            {"id": str, "provider": str, "name": str, "capabilities": dict},
            label="GET models data.models[0]",
        )
        assert_has_fields(
            models[0]["capabilities"],
            {"context_window": int, "max_output_tokens": int, "vision": bool, "function_calling": bool, "streaming": bool},
            label="GET models data.models[0].capabilities",
        )


class TestEngineStatus:
    """GET /proxypass/{target}/api/engine/status — 引擎状态。"""

    @responses.activate
    def test_engine_status_schema(self):
        responses.add(
            responses.GET,
            f"{PROXY_BASE}/proxypass/{TARGET}/api/engine/status",
            json=MOCK_ENGINE_STATUS,
            status=200,
        )
        resp = requests.get(
            f"{PROXY_BASE}/proxypass/{TARGET}/api/engine/status",
            headers={"X-PROXYPASS-TOKEN": "test-token"},
        )
        data = resp.json()
        assert_has_fields(
            data,
            {"engine": str, "active_connections": int, "process": dict},
            label="GET engine/status response",
        )
        assert_has_fields(
            data["process"],
            {"running": bool, "pid": (int, type(None)), "command_enabled": bool, "managed_process": bool},
            label="GET engine/status process",
        )


class TestEngineRestart:
    """POST /proxypass/{target}/api/engine/restart — 重启引擎。"""

    @responses.activate
    def test_engine_restart_schema(self):
        responses.add(
            responses.POST,
            f"{PROXY_BASE}/proxypass/{TARGET}/api/engine/restart",
            json={"success": True, "data": {"status": "restarting"}},
            status=200,
        )
        resp = requests.post(
            f"{PROXY_BASE}/proxypass/{TARGET}/api/engine/restart",
            json={},
            headers={"X-PROXYPASS-TOKEN": "test-token"},
        )
        body = resp.json()
        assert_has_fields(body, {"success": bool, "data": (dict, list, type(None))}, label="POST engine/restart response")
        assert_has_fields(body["data"], {"status": str}, label="POST engine/restart data")
        assert body["data"]["status"] == "restarting"


class TestUpdateSession:
    """POST /proxypass/{target}/api/sessions/{id}/update — 更新会话。"""

    @responses.activate
    def test_update_session_schema(self):
        responses.add(
            responses.POST,
            f"{PROXY_BASE}/proxypass/{TARGET}/api/sessions/sess_001/update",
            json={"success": True, "data": {"id": "session:abc:user:default", "title": "Updated"}},
            status=200,
        )
        resp = requests.post(
            f"{PROXY_BASE}/proxypass/{TARGET}/api/sessions/sess_001/update",
            json={"title": "Updated"},
            headers={"X-PROXYPASS-TOKEN": "test-token"},
        )
        body = resp.json()
        assert_has_fields(body, {"success": bool, "data": (dict, list, type(None))}, label="POST sessions/update response")
        assert_has_fields(body["data"], {"id": str, "title": str}, label="POST sessions/update data")


class TestDeleteSessionMessages:
    """DELETE /proxypass/{target}/api/sessions/{id}/messages — 删除会话消息。"""

    @responses.activate
    def test_delete_messages_schema(self):
        responses.add(
            responses.DELETE,
            f"{PROXY_BASE}/proxypass/{TARGET}/api/sessions/sess_001/messages",
            json={"success": True, "message": "Messages deleted"},
            status=200,
        )
        resp = requests.delete(
            f"{PROXY_BASE}/proxypass/{TARGET}/api/sessions/sess_001/messages",
            headers={"X-PROXYPASS-TOKEN": "test-token"},
        )
        body = resp.json()
        assert_has_fields(body, {"success": bool, "message": str}, label="DELETE sessions/messages response")