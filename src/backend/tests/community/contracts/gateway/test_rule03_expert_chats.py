"""Rule #3 — 专家聊天 (/api/v1/expert-chats) 契约测试。

验证 GET/POST expert-chats, DELETE session 等接口响应字段。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.expert_chat.services.expert_chat_service import ExpertChatService

from tests.community.contracts.gateway.conftest import (
    assert_response_schema, assert_success, assert_has_fields,
    assert_response_data_contract, bind_mock_service,
)


MOCK_EXPERT_BOT = {
    "bot_id": "bot_expert_001",
    "owner_id": "100001",
    "bot_name": "ExpertBot",
    "owner_name": "ExpertOwner",
    "status": "ACTIVE",
    "binding_available": True,
    "binding_id": 200,
    "ext": {
        "passport": {"status": "AUTHORIZED"},
        "avatar_url": "",
        "start_status": "running",
        "start_message": "",
        "public_approval": {},
        "permission_owner": "caller",
        "friend_approval": "auto",
    },
}


def _make_mock_expert_chat_service() -> MagicMock:
    svc = MagicMock(spec=ExpertChatService)
    svc.list_chat_bots.return_value = [MOCK_EXPERT_BOT]
    svc.add_chat_bot.return_value = {
        "id": 1, "user_id": "448524",
        "bot_id": "bot_expert_001", "owner_id": "100001",
        "status": "ACTIVE",
    }
    svc.remove_chat_bot.return_value = True
    svc.delete_chat_session.return_value = True
    return svc


class TestListExpertChats:
    """GET /api/v1/expert-chats — 专家聊天列表。"""

    def test_list_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        mock_svc = _make_mock_expert_chat_service()
        bind_mock_service(ExpertChatService, mock_svc, app_with_testing_modules)

        resp = gw_client.get("/api/v1/expert-chats")
        body = resp.json()

        assert_success(body, "GET /api/v1/expert-chats")
        assert_response_data_contract(body, "rule03_GET_api_v1_expert_chats", update=contract_snapshot_update)
        data = body.get("data", {})
        if isinstance(data, list) and data:
            assert_has_fields(
                data[0],
                {"bot_id": str, "owner_id": str, "bot_name": str, "status": str, "binding_available": bool},
                label="GET expert-chats data[0]",
            )
        elif isinstance(data, dict) and "items" in data:
            assert_has_fields(data, {"total": int, "items": list}, label="GET expert-chats data")


class TestAddExpertChat:
    """POST /api/v1/expert-chats — 创建专家聊天。"""

    def test_add_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        mock_svc = _make_mock_expert_chat_service()
        bind_mock_service(ExpertChatService, mock_svc, app_with_testing_modules)

        resp = gw_client.post("/api/v1/expert-chats", json={
            "bot_id": "bot_expert_001",
            "owner_id": "100001",
        })
        body = resp.json()

        assert_response_data_contract(body, "rule03_POST_api_v1_expert_chats", update=contract_snapshot_update)
        assert_response_schema(
            body, required_top={"success": bool, "message": str},
            label="POST /api/v1/expert-chats",
        )


class TestDeleteExpertChatSession:
    """DELETE /api/v1/expert-chats/{bot_id}/{owner_id}/session — 删除会话。"""

    def test_delete_session_schema(self, gw_client, app_with_testing_modules):
        mock_svc = _make_mock_expert_chat_service()
        bind_mock_service(ExpertChatService, mock_svc, app_with_testing_modules)

        resp = gw_client.delete("/api/v1/expert-chats/bot_expert_001/100001/session")
        body = resp.json()

        # 文档: {success, message: "Session deleted"}
        assert_response_schema(
            body, required_top={"success": bool, "message": str},
            label="DELETE expert-chats session",
        )
