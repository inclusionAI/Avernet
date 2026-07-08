"""Rule #7, #7b, #12, #12b, #14 — BCS 接口契约测试。

BCS (Bot Coordination Service) 是独立 Rust 服务，Gateway 直接转发。
这些测试使用 responses 库 mock BCS HTTP 响应，验证文档规定的
响应字段结构。虽然是 HTTP mock 而非代码级单元测试，但确保
前端与 BCS 的契约有文档级断言保护。

改进: mock 数据同时通过 JSON Schema 契约验证（schema_snapshots/bcs/），
确保 mock 数据与权威契约一致，避免 mock 与断言同源导致的同义反复。

测试覆盖:
- Rule #7:  /api/v1/engine/groups → BCS /groups
- Rule #7b: /api/v1/engine/sessions → BCS /sessions
- Rule #12: /api/v1/engine/bots → BCS /bots
- Rule #12b: /api/v1/engine/friends → BCS /friends
- Rule #14: /api/v1/admin/bots/onboard → BCS /admin/bots/onboard
"""
from __future__ import annotations

import json
import pytest
import requests
import responses

from tests.community.contracts.gateway.conftest import assert_has_fields
from tests.community.contracts.gateway.schema_utils import validate_mock_against_schema, load_contract_schema

BCS_BASE = "https://bcn-pre.teamclaw.com"


# ── Mock Data ↔ JSON Schema Contract Validation ──────────────────────────────
# 验证 mock 数据符合 schema_snapshots/bcs/ 中的权威契约，
# 防止 mock 数据与契约定义脱节。


class TestBCSMockConformance:
    """验证 BCS mock 数据符合 JSON Schema 契约。"""

    def test_mock_group_conforms(self):
        validate_mock_against_schema(
            MOCK_GROUP, load_contract_schema("group_response"), label="MOCK_GROUP",
        )

    def test_mock_session_member_conforms(self):
        validate_mock_against_schema(
            MOCK_SESSION_MEMBER_RESPONSE, load_contract_schema("session_member_response"),
            label="MOCK_SESSION_MEMBER_RESPONSE",
        )

    def test_mock_bot_detail_conforms(self):
        validate_mock_against_schema(
            MOCK_BOT_DETAIL, load_contract_schema("bot_detail_response"), label="MOCK_BOT_DETAIL",
        )

    def test_mock_friend_request_conforms(self):
        validate_mock_against_schema(
            MOCK_FRIEND_REQUEST, load_contract_schema("friend_request_response"),
            label="MOCK_FRIEND_REQUEST",
        )

    def test_mock_onboard_conforms(self):
        mock_onboard = {
            "bot_uuid": "bot_test_001:448524",
            "onboarded": True,
            "name": "TestBot",
            "capabilities": {
                "name": "TestBot",
                "summary": "A test bot",
                "hidden": False,
                "visibility": "public",
                "skills": [],
                "domains": [],
            },
            "created_by": "448524",
        }
        validate_mock_against_schema(
            mock_onboard, load_contract_schema("onboard_response"), label="onboard_response",
        )


# ── Mock 数据 ──────────────────────────────────────────────────────────────

MOCK_GROUP = {
    "id": "grp_001",
    "label": "Test Group",
    "driver_bot": "bot_test_001:448524",
    "participants": [{
        "actor_kind": "HUMAN",
        "bot_name": "",
        "bot_uuid": "human_448524",
        "mode": "present",
        "role": "Driver",
        "staff_id": "448524",
        "nickName": "TestUser",
    }],
    "group_kind": "multi",
    "group_strategy": "round_robin",
    "status": "active",
    "created_at": 1713763200,
    "updated_at": 1713763200,
    "message_count": 5,
    "latest_running_session_id": "sess_001",
    "workspace": {
        "audit_log": [],
        "decisions": [],
        "notes": [],
        "tasks": [],
    },
    "service_group_uuid": "",
    "service_mode": "",
    "service_spec": None,
    "dm_pair_key": "",
    "context": None,
    "chat_url": "",
    "created": True,
}

MOCK_SESSION_MEMBER_RESPONSE = {
    "session_id": "sess_001",
    "group_id": "grp_001",
    "participants": [{
        "actor_kind": "BOT",
        "bot_name": "TestBot",
        "bot_uuid": "bot_test_001:448524",
        "mode": "present",
        "role": "Consultant",
    }],
    "status": "active",
}

MOCK_BOT_DETAIL = {
    "actor_kind": "BOT",
    "bot_uuid": "bot_test_001:448524",
    "capabilities": {
        "name": "TestBot",
        "summary": "A test bot",
        "hidden": False,
        "visibility": "public",
        "domains": [],
        "scopes": [],
        "skills": [],
        "binding_channels": {},
    },
    "created_by": "448524",
    "dynamic_status": {"status": "active"},
    "env": "pre",
    "status": "active",
}

MOCK_FRIEND_REQUEST = {
    "success": True,
    "request_id": "req_001",
    "status": "PENDING",
}


# ── Rule #7: Groups ─────────────────────────────────────────────────────────


class TestCreateGroup:
    """POST /groups — 创建群组。"""

    @responses.activate
    def test_create_group_response_schema(self):
        responses.add(
            responses.POST,
            f"{BCS_BASE}/groups",
            json=MOCK_GROUP,
            status=200,
        )
        resp = requests.post(f"{BCS_BASE}/groups", json={
            "label": "Test Group",
            "driver_bot": "bot_test_001:448524",
            "participants": [{"id": "448524", "type": "HUMAN", "mode": "present"}],
        })
        data = resp.json()
        assert_has_fields(
            data,
            {"id": str, "driver_bot": str, "participants": list, "group_kind": str, "created": bool},
            label="POST /groups response",
        )
        assert_has_fields(
            data["participants"][0],
            {"actor_kind": str, "mode": str, "role": str},
            label="POST /groups participants[0]",
        )


class TestGetGroup:
    """GET /groups/{id} — 获取群组详情。"""

    @responses.activate
    def test_get_group_response_schema(self):
        responses.add(
            responses.GET,
            f"{BCS_BASE}/groups/grp_001",
            json=MOCK_GROUP,
            status=200,
        )
        resp = requests.get(f"{BCS_BASE}/groups/grp_001")
        data = resp.json()
        assert_has_fields(
            data,
            {"id": str, "label": str, "driver_bot": str, "participants": list, "group_kind": str,
             "status": str, "created_at": int, "updated_at": int, "workspace": dict},
            label="GET /groups/{id} response",
        )


class TestGetGroupMessages:
    """GET /groups/{id}/messages — 获取群组消息。"""

    @responses.activate
    def test_get_messages_response_schema(self):
        responses.add(
            responses.GET,
            f"{BCS_BASE}/groups/grp_001/messages",
            json=[{"id": "msg_001", "role": "user", "content": "hello"}],
            status=200,
        )
        resp = requests.get(f"{BCS_BASE}/groups/grp_001/messages")
        data = resp.json()
        assert isinstance(data, list), "GET /groups/{id}/messages should return array"


class TestGetGroupSessions:
    """GET /groups/{id}/sessions — 获取群组会话列表。"""

    @responses.activate
    def test_get_sessions_response_schema(self):
        mock_sessions = {
            "group_id": "grp_001",
            "items": [{
                "id": "sess_001",
                "session_id": "sess_001",
                "session_kind": "default",
                "session_title": "Test Session",
                "status": "active",
                "participants": [],
                "activation_count": 1,
                "created_at": 1713763200,
                "env": "pre",
                "group_version": 1,
            }],
            "total": 1,
            "limit": 20,
            "offset": 0,
        }
        responses.add(
            responses.GET,
            f"{BCS_BASE}/groups/grp_001/sessions",
            json=mock_sessions,
            status=200,
        )
        resp = requests.get(f"{BCS_BASE}/groups/grp_001/sessions")
        data = resp.json()
        assert_has_fields(
            data,
            {"group_id": str, "items": list, "total": int},
            label="GET /groups/{id}/sessions response",
        )
        assert_has_fields(
            data["items"][0],
            {"id": str, "session_id": str, "status": str, "participants": list, "created_at": int},
            label="GET /groups/{id}/sessions items[0]",
        )


class TestUpdateParticipantMode:
    """PUT /groups/{id}/participants/{actor_id}/mode — 更新参与者模式。"""

    @responses.activate
    def test_update_mode_response_schema(self):
        responses.add(
            responses.PUT,
            f"{BCS_BASE}/groups/grp_001/participants/human_448524/mode",
            json={"success": True, "data": {"group_id": "grp_001", "actor_id": "human_448524", "mode": "absent"}},
            status=200,
        )
        resp = requests.put(
            f"{BCS_BASE}/groups/grp_001/participants/human_448524/mode",
            json={"mode": "absent"},
        )
        data = resp.json()
        assert_has_fields(
            data,
            {"success": bool, "data": (dict, list, type(None))},
            label="PUT participants/mode response",
        )
        assert_has_fields(
            data["data"],
            {"group_id": str, "actor_id": str, "mode": str},
            label="PUT participants/mode data",
        )


# ── Rule #7b: Session Members ───────────────────────────────────────────────


class TestUpdateSessionMemberMode:
    """PATCH /sessions/{session_id}/members/{bot_id} — 更新会话成员模式。"""

    @responses.activate
    def test_update_session_member_schema(self):
        responses.add(
            responses.PATCH,
            f"{BCS_BASE}/sessions/sess_001/members/bot_test_001:448524",
            json=MOCK_SESSION_MEMBER_RESPONSE,
            status=200,
        )
        resp = requests.patch(
            f"{BCS_BASE}/sessions/sess_001/members/bot_test_001:448524",
            json={"mode": "auto"},
        )
        data = resp.json()
        assert_has_fields(
            data,
            {"session_id": str, "group_id": str, "participants": list, "status": str},
            label="PATCH /sessions/{id}/members/{bot_id} response",
        )


# ── Rule #12: BCN Bot Management ───────────────────────────────────────────


class TestGetBotDetail:
    """GET /bots/{uuid} — Bot 详情。"""

    @responses.activate
    def test_get_bot_detail_schema(self):
        responses.add(
            responses.GET,
            f"{BCS_BASE}/bots/bot_test_001:448524",
            json=MOCK_BOT_DETAIL,
            status=200,
        )
        resp = requests.get(f"{BCS_BASE}/bots/bot_test_001:448524")
        data = resp.json()
        assert_has_fields(
            data,
            {"actor_kind": str, "bot_uuid": str, "capabilities": dict, "status": str},
            label="GET /bots/{uuid} response",
        )
        assert_has_fields(
            data["capabilities"],
            {"name": str, "summary": str, "hidden": bool, "visibility": str, "skills": list},
            label="GET /bots/{uuid} capabilities",
        )


class TestDiscoverBots:
    """GET /bots/discover — 发现 Bot。"""

    @responses.activate
    def test_discover_schema(self):
        responses.add(
            responses.GET,
            f"{BCS_BASE}/bots/discover",
            json={"bots": [MOCK_BOT_DETAIL], "count": 1},
            status=200,
        )
        resp = requests.get(f"{BCS_BASE}/bots/discover", params={"name": "Test"})
        data = resp.json()
        assert_has_fields(
            data, {"bots": list, "count": int},
            label="GET /bots/discover response",
        )


class TestQueryBots:
    """POST /bots/query — 查询 Bot。"""

    @responses.activate
    def test_query_bots_schema(self):
        responses.add(
            responses.POST,
            f"{BCS_BASE}/bots/query",
            json=[MOCK_BOT_DETAIL],
            status=200,
        )
        resp = requests.post(f"{BCS_BASE}/bots/query", json={
            "bot_uuids": ["bot_test_001:448524"],
        })
        data = resp.json()
        assert isinstance(data, list), "POST /bots/query should return array"
        assert_has_fields(
            data[0],
            {"actor_kind": str, "bot_uuid": str, "capabilities": dict, "status": str},
            label="POST /bots/query response[0]",
        )


class TestGetBotVisibility:
    """GET /bots/{uuid}/visibility — Bot 可见性。"""

    @responses.activate
    def test_get_visibility_schema(self):
        responses.add(
            responses.GET,
            f"{BCS_BASE}/bots/bot_test_001:448524/visibility",
            json={"success": True, "data": {"bot_uuid": "bot_test_001:448524", "visibility": "public"}},
            status=200,
        )
        resp = requests.get(f"{BCS_BASE}/bots/bot_test_001:448524/visibility")
        data = resp.json()
        assert_has_fields(
            data, {"success": bool, "data": (dict, list, type(None))},
            label="GET /bots/{uuid}/visibility response",
        )


class TestPutBotVisibility:
    """PUT /bots/{uuid}/visibility — 更新 Bot 可见性。"""

    @responses.activate
    def test_put_visibility_schema(self):
        responses.add(
            responses.PUT,
            f"{BCS_BASE}/bots/bot_test_001:448524/visibility",
            json={"success": True, "bot_uuid": "bot_test_001:448524", "visibility": "private"},
            status=200,
        )
        resp = requests.put(
            f"{BCS_BASE}/bots/bot_test_001:448524/visibility",
            json={"visibility": "private"},
        )
        data = resp.json()
        assert_has_fields(
            data, {"success": bool, "bot_uuid": str, "visibility": str},
            label="PUT /bots/{uuid}/visibility response",
        )


class TestGetBotFriends:
    """GET /bots/{uuid}/friends — Bot 好友列表。"""

    @responses.activate
    def test_get_friends_schema(self):
        responses.add(
            responses.GET,
            f"{BCS_BASE}/bots/bot_test_001:448524/friends",
            json={"success": True, "data": []},
            status=200,
        )
        resp = requests.get(f"{BCS_BASE}/bots/bot_test_001:448524/friends")
        data = resp.json()
        assert_has_fields(
            data, {"success": bool, "data": (dict, list, type(None))},
            label="GET /bots/{uuid}/friends response",
        )


class TestGetBotGroups:
    """GET /bots/{uuid}/groups — Bot 所在群组列表。"""

    @responses.activate
    def test_get_groups_schema(self):
        mock_groups = {
            "bot_uuid": "bot_test_001:448524",
            "items": [{
                "group_id": "grp_001",
                "label": "Test Group",
                "group_kind": "multi",
                "group_strategy": "round_robin",
                "coordinator_bot": "bot_test_001:448524",
                "participants": [],
                "created_at": 1713763200,
                "updated_at": 1713763200,
            }],
            "total": 1,
            "limit": 20,
            "offset": 0,
        }
        responses.add(
            responses.GET,
            f"{BCS_BASE}/bots/bot_test_001:448524/groups",
            json=mock_groups,
            status=200,
        )
        resp = requests.get(f"{BCS_BASE}/bots/bot_test_001:448524/groups")
        data = resp.json()
        assert_has_fields(
            data, {"bot_uuid": str, "items": list, "total": int},
            label="GET /bots/{uuid}/groups response",
        )


# ── Rule #12b: Friends ─────────────────────────────────────────────────────


class TestSendFriendRequest:
    """POST /friends/request — 发送好友请求。"""

    @responses.activate
    def test_send_request_schema(self):
        responses.add(
            responses.POST,
            f"{BCS_BASE}/friends/request",
            json=MOCK_FRIEND_REQUEST,
            status=200,
        )
        resp = requests.post(f"{BCS_BASE}/friends/request", json={
            "from_bot_uuid": "bot_001:100",
            "to_bot_uuid": "bot_002:200",
            "message": "Hello",
        })
        data = resp.json()
        assert_has_fields(
            data, {"success": bool, "request_id": str, "status": str},
            label="POST /friends/request response",
        )


class TestListFriendRequests:
    """GET /friends/requests — 好友请求列表。"""

    @responses.activate
    def test_list_requests_schema(self):
        responses.add(
            responses.GET,
            f"{BCS_BASE}/friends/requests",
            json={"success": True, "data": [{
                "id": "req_001",
                "from_bot": "bot_001:100",
                "to_bot": "bot_002:200",
                "status": "PENDING",
                "created_at": 1713763200,
                "updated_at": 1713763200,
            }]},
            status=200,
        )
        resp = requests.get(f"{BCS_BASE}/friends/requests", params={
            "bot_uuid": "bot_001:100",
        })
        data = resp.json()
        assert_has_fields(data, {"success": bool, "data": (dict, list, type(None))}, label="GET /friends/requests response")
        items = data["data"]
        assert isinstance(items, list)
        assert_has_fields(items[0], {"id": str, "from_bot": str, "to_bot": str, "status": str}, label="GET /friends/requests data[0]")


class TestAcceptFriendRequest:
    """POST /friends/requests/{id}/accept — 接受好友请求。"""

    @responses.activate
    def test_accept_schema(self):
        responses.add(
            responses.POST,
            f"{BCS_BASE}/friends/requests/req_001/accept",
            json={"success": True, "request_id": "req_001", "status": "ACCEPTED"},
            status=200,
        )
        resp = requests.post(f"{BCS_BASE}/friends/requests/req_001/accept")
        data = resp.json()
        assert_has_fields(
            data, {"success": bool, "request_id": str, "status": str},
            label="POST /friends/requests/{id}/accept response",
        )


class TestRejectFriendRequest:
    """POST /friends/requests/{id}/reject — 拒绝好友请求。"""

    @responses.activate
    def test_reject_schema(self):
        responses.add(
            responses.POST,
            f"{BCS_BASE}/friends/requests/req_001/reject",
            json={"success": True, "request_id": "req_001", "status": "REJECTED"},
            status=200,
        )
        resp = requests.post(f"{BCS_BASE}/friends/requests/req_001/reject")
        data = resp.json()
        assert_has_fields(
            data, {"success": bool, "request_id": str, "status": str},
            label="POST /friends/requests/{id}/reject response",
        )


# ── Rule #14: Bot Onboard ──────────────────────────────────────────────────


class TestOnboardBot:
    """POST /admin/bots/onboard — Bot 加入 BCN 网络。"""

    @responses.activate
    def test_onboard_schema(self):
        responses.add(
            responses.POST,
            f"{BCS_BASE}/admin/bots/onboard",
            json={
                "bot_uuid": "bot_test_001:448524",
                "onboarded": True,
                "name": "TestBot",
                "capabilities": {
                    "name": "TestBot",
                    "summary": "A test bot",
                    "hidden": False,
                    "visibility": "public",
                    "skills": [],
                    "domains": [],
                },
                "created_by": "448524",
            },
            status=200,
        )
        resp = requests.post(f"{BCS_BASE}/admin/bots/onboard", json={
            "bot_uuid": "bot_test_001:448524",
            "name": "TestBot",
            "capabilities": [{
                "name": "TestBot",
                "summary": "A test bot",
                "hidden": False,
                "visibility": "public",
                "skills": [],
                "domains": [],
            }],
        })
        data = resp.json()
        assert_has_fields(
            data,
            {"bot_uuid": str, "onboarded": bool, "name": str, "capabilities": dict, "created_by": str},
            label="POST /admin/bots/onboard response",
        )
        assert_has_fields(
            data["capabilities"],
            {"name": str, "summary": str, "hidden": bool, "visibility": str},
            label="POST /admin/bots/onboard capabilities",
        )