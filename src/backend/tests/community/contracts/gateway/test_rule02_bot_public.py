"""Rule #2 — 公开 Bot 互动 (/api/v1/bot-public) 契约测试。

验证 GET my-friend-bots, GET friend-record, GET search,
POST friend-request-approval 等接口响应字段。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.bot_public.services.bot_public_service import BotPublicService

from tests.community.contracts.gateway.conftest import (
    assert_response_schema, assert_success, assert_has_fields,
    assert_response_data_contract, bind_mock_service,
)


MOCK_FRIEND_BOT = {
    "id": 1,
    "requester_entity_id": "448524",
    "requester_name": "TestUser",
    "target_entity_id": "100001",
    "target_name": "OtherUser",
    "target_bot_id": "bot_friend_001",
    "target_owner_name": "OtherOwner",
    "status": "ACCEPTED",
    "gmt_create": "2026-01-01 00:00:00",
    "gmt_modified": "2026-01-01 00:00:00",
    "ext": None,
    "env": "pre",
    "bot": {
        "id": 10,
        "bot_id": "bot_friend_001",
        "bot_name": "FriendBot",
        "bot_desc": "",
        "entity_id": "100001",
        "entity_type": "staff",
        "owner_id": "100001",
        "owner_name": "OtherOwner",
        "engine_types": ["openclaw"],
        "active_engine": "openclaw",
        "status": "ACTIVE",
        "binding_id": 200,
    },
}


def _make_mock_bot_public_service() -> MagicMock:
    svc = MagicMock(spec=BotPublicService)
    svc.list_my_bot_friends.return_value = {
        "total": 1,
        "items": [MOCK_FRIEND_BOT],
    }
    svc.get_friend_record.return_value = {
        "id": 1,
        "status": "ACCEPTED",
        "requester_entity_id": "448524",
        "target_entity_id": "100001",
        "target_bot_id": "bot_friend_001",
    }
    svc.search_public_bots_by_keyword.return_value = {
        "total": 1,
        "items": [{
            "id": 10, "bot_id": "bot_pub_001", "bot_name": "PublicBot",
            "bot_desc": "公开Bot", "entity_id": "100001", "entity_type": "staff",
            "owner_id": "100001", "owner_name": "Owner",
            "engine_types": ["openclaw"], "active_engine": "openclaw",
            "status": "ACTIVE", "binding_id": 300, "device_id": "device_pub",
            "ext": {}, "env": "pre", "bot_type": "personal",
            "friend_record_approval": "auto",
        }],
    }
    svc.create_friend_request_approval.return_value = {
        "approval_id": 1, "status": "PENDING",
    }
    return svc


class TestMyFriendBots:
    """GET /api/v1/bot-public/my-friend-bots — 我的好友 Bot 列表。"""

    def test_my_friend_bots_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        mock_svc = _make_mock_bot_public_service()
        bind_mock_service(BotPublicService, mock_svc, app_with_testing_modules)

        resp = gw_client.get("/api/v1/bot-public/my-friend-bots")
        body = resp.json()

        assert_success(body, "GET my-friend-bots")
        assert_response_data_contract(body, "rule02_GET_api_v1_bot_public_my_friend_bots", update=contract_snapshot_update)
        assert_response_schema(
            body, required_top={"success": bool, "data": (dict, list, type(None))},
            data_fields={"total": int, "items": list},
            label="GET my-friend-bots",
        )
        items = body["data"]["items"]
        assert isinstance(items, list)
        assert_has_fields(
            items[0],
            {"id": int, "requester_entity_id": str, "target_entity_id": str,
             "target_bot_id": str, "status": str},
            label="GET my-friend-bots items[0]",
        )


class TestFriendRecord:
    """GET /api/v1/bot-public/friend-record — 好友记录。"""

    def test_friend_record_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        mock_svc = _make_mock_bot_public_service()
        bind_mock_service(BotPublicService, mock_svc, app_with_testing_modules)

        resp = gw_client.get("/api/v1/bot-public/friend-record", params={
            "target_entity_id": "100001",
            "target_bot_id": "bot_friend_001",
        })
        body = resp.json()

        assert_success(body, "GET friend-record")
        assert_response_data_contract(body, "rule02_GET_api_v1_bot_public_friend_record", update=contract_snapshot_update)
        assert_has_fields(body["data"], {"id": int, "status": str}, label="GET friend-record data")


class TestSearchPublicBots:
    """GET /api/v1/bot-public/search — 搜索公开 Bot。"""

    def test_search_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        mock_svc = _make_mock_bot_public_service()
        bind_mock_service(BotPublicService, mock_svc, app_with_testing_modules)

        resp = gw_client.get("/api/v1/bot-public/search", params={"search": "PublicBot"})
        body = resp.json()

        assert_success(body, "GET search")
        assert_response_data_contract(body, "rule02_GET_api_v1_bot_public_search", update=contract_snapshot_update)
        assert_response_schema(
            body, required_top={"success": bool, "data": (dict, list, type(None))},
            data_fields={"total": int, "items": list},
            label="GET search",
        )
        items = body["data"]["items"]
        assert isinstance(items, list)
        assert_has_fields(
            items[0],
            {"bot_id": str, "bot_name": str, "entity_id": str, "owner_id": str, "engine_types": list, "status": str},
            label="GET search items[0]",
        )


class TestFriendRequestApproval:
    """POST /api/v1/bot-public/friend-request-approval — 好友请求审批。"""

    def test_friend_request_approval_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        mock_svc = _make_mock_bot_public_service()
        bind_mock_service(BotPublicService, mock_svc, app_with_testing_modules)

        resp = gw_client.post("/api/v1/bot-public/friend-request-approval", params={
            "bot_id": "bot_friend_001",
            "owner_id": "100001",
        })
        body = resp.json()

        assert_response_data_contract(body, "rule02_POST_api_v1_bot_public_friend_request_approval", update=contract_snapshot_update)
        assert_response_schema(
            body, required_top={"success": bool},
            label="POST friend-request-approval",
        )
