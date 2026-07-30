"""Rule #1 — Bot 管理 (/api/bots) 契约测试。

验证接口可达性、HTTP 状态码、响应顶层结构（success/data/message）。
字段细节通过 Mock BotService DI override 验证。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory

from tests.community.contracts.gateway.conftest import (
    assert_response_schema, assert_success, assert_has_fields,
    assert_response_data_contract, bind_mock_service,
)


MOCK_BOT_ITEM = {
    "id": 1, "bot_id": "bot_test_001", "bot_name": "test-bot",
    "bot_desc": "测试Bot", "entity_id": "448524", "entity_type": "staff",
    "creator_id": "448524", "owner_id": "448524", "owner_name": "TestUser",
    "engine_types": ["openclaw"], "active_engine": "openclaw",
    "status": "ACTIVE", "binding_id": 100, "device_id": "device_001",
    "gmt_create": "2026-01-01 00:00:00", "gmt_modified": "2026-01-01 00:00:00",
    "modifier_id": "448524", "share_policy": {}, "is_delete": False,
    "public": "private",
    "ext": {
        "passport": {"status": "AUTHORIZED", "is_first_bot": False},
        "avatar_url": "", "start_status": "running", "start_message": "",
        "friend_approval": "auto",
        "public_approval": {"puid": "", "public": "private", "status": "",
            "applicant": "", "approval_url": "", "processed_at": "",
            "friend_approval": "auto", "permission_owner": "caller"},
        "permission_owner": "caller", "env": "pre", "bot_type": "personal",
        "can_edit_bot": True, "engine_paths": {}, "bot_work_dir": "",
    },
}


def _bind_bot(app):
    """Bind mock BotService and return it."""
    svc = MagicMock(spec=BotService)
    svc.list_bots_by_owner.return_value = {
        "total": 1, "items": [MOCK_BOT_ITEM],
        "default_bot": {"bot_id": "bot_test_001"},
    }
    # Handler 调用 get_engine_paths 为每个 bot 计算路径
    svc.get_engine_paths.return_value = {"openclaw": "/tmp/test/openclaw"}
    svc.get_bot.return_value = MOCK_BOT_ITEM
    svc.create_bot.return_value = {
        "bot": MOCK_BOT_ITEM,
        "passport": {"agent_code": "ac_001", "status": "AUTHORIZED"},
    }
    svc.update_bot.return_value = {**MOCK_BOT_ITEM, "bot_name": "updated-bot"}
    svc.delete_bot.return_value = True
    svc.check_bot_name_exists.return_value = False
    svc.restart_bot.return_value = {"bot_id": "bot_test_001", "status": "RESTARTING"}
    # create_flow asks the service whether this is the owner's first bot, and the
    # response contract types passport.is_first_bot as a boolean.
    svc.is_first_bot.return_value = True
    bind_mock_service(BotService, svc, app)
    return svc


def _bind_bot_repo(app):
    """Bind mock BotRepository (handler 调用 generate_bot_id 使用)."""
    mock_repo = MagicMock(spec=BotRepository)
    mock_repo.exists_by_owner_and_bot_id.return_value = False
    mock_repo.get_by_id_and_owner.return_value = None
    bind_mock_service(BotRepository, mock_repo, app)
    return mock_repo


def _bind_create_bot_deps(app):
    """Bind all dependencies for POST /api/bots (create_bot handler).

    create_bot 注入: BotService, BotRepository, PassportPlugin,
    AuthRelationshipPlugin, SkillSetServiceFactory.
    """
    svc = _bind_bot(app)
    _bind_bot_repo(app)

    # PassportPlugin: apply_first_agent_passport / apply_agent_passport
    mock_passport = MagicMock(spec=PassportPlugin)
    mock_passport.apply_first_agent_passport.return_value = {
        "token": "test-token",
        "agent_code": "ac_001",
        "iframe_url": None,
        "redirect_url": None,
    }
    mock_passport.apply_agent_passport.return_value = {
        "token": "test-token",
        "agent_code": "ac_001",
        "iframe_url": None,
        "redirect_url": None,
    }
    bind_mock_service(PassportPlugin, mock_passport, app)

    # AuthRelationshipPlugin: create_relationship
    mock_auth_rel = MagicMock(spec=AuthRelationshipPlugin)
    mock_auth_rel.create_relationship.return_value = None
    bind_mock_service(AuthRelationshipPlugin, mock_auth_rel, app)

    # SkillSetServiceFactory: _get_bot_mcp_codes 使用
    mock_skillset_factory = MagicMock(spec=SkillSetServiceFactory)
    mock_skillset_svc = MagicMock()
    mock_skillset_svc.get_bot_mcp_codes.return_value = []
    mock_skillset_factory.create.return_value = mock_skillset_svc
    bind_mock_service(SkillSetServiceFactory, mock_skillset_factory, app)

    return svc


class TestListBotsByOwner:
    def test_by_owner_response_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_bot(app_with_testing_modules)
        resp = gw_client.get("/api/bots/by-owner")
        body = resp.json()
        assert_success(body, "GET /api/bots/by-owner")
        assert_response_data_contract(body, "rule01_GET_api_bots_by_owner", update=contract_snapshot_update)
        assert_response_schema(body, required_top={"success": bool, "data": (dict, list, type(None))},
                               data_fields={"total": int, "items": list}, label="GET /api/bots/by-owner")
        items = body["data"]["items"]
        assert isinstance(items, list)
        assert_has_fields(items[0],
            {"bot_id": str, "bot_name": str, "owner_id": str, "status": str, "engine_types": list,
             "active_engine": str, "binding_id": int, "entity_id": str, "entity_type": str},
            label="GET /api/bots/by-owner items[0]")


class TestGetBot:
    def test_get_bot_response_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_bot(app_with_testing_modules)
        resp = gw_client.get("/api/bots/bot_test_001")
        body = resp.json()
        assert_success(body, "GET /api/bots/{id}")
        assert_response_data_contract(body, "rule01_GET_api_bots_id", update=contract_snapshot_update)
        assert_has_fields(body["data"],
            {"bot_id": str, "bot_name": str, "owner_id": str, "status": str,
             "engine_types": list, "active_engine": str, "binding_id": int},
            label="GET /api/bots/{id} data")


class TestCreateBot:
    def test_create_bot_response_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_create_bot_deps(app_with_testing_modules)
        resp = gw_client.post("/api/bots", json={
            "bot_name": "new-bot", "entity_id": "448524",
            "entity_type": "staff", "engine_type": "openclaw",
        })
        body = resp.json()
        assert_success(body, "POST /api/bots")
        assert_response_data_contract(body, "rule01_POST_api_bots", update=contract_snapshot_update)
        data = body.get("data", {})
        assert isinstance(data, dict), f"POST /api/bots data should be dict, got {type(data).__name__}"
        # Handler 返回 data = {"bot": <create_bot_result>, "passport": {...}}
        # create_bot 返回 {"bot": MOCK_BOT_ITEM, ...}，所以 data.bot.bot 才是 bot 对象
        assert "bot" in data, f"POST /api/bots data should contain 'bot', got keys: {sorted(data.keys())}"
        bot_data = data["bot"]
        # bot_data 可能直接是 bot 对象（含 bot_id），也可能是嵌套 {"bot": {...}, "passport": {...}}
        if "bot_id" in bot_data:
            assert_has_fields(bot_data, {"bot_id": str, "bot_name": str},
                             label="POST /api/bots data.bot")
        elif "bot" in bot_data:
            inner = bot_data["bot"]
            assert_has_fields(inner, {"bot_id": str, "bot_name": str},
                             label="POST /api/bots data.bot.bot")


class TestUpdateBot:
    def test_update_bot_response_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_bot(app_with_testing_modules)
        resp = gw_client.put("/api/bots/bot_test_001", json={"bot_name": "updated-bot"})
        body = resp.json()
        assert_success(body, "PUT /api/bots/{id}")
        assert_response_data_contract(body, "rule01_PUT_api_bots_id", update=contract_snapshot_update)
        assert_has_fields(body["data"], {"bot_id": str, "bot_name": str}, label="PUT /api/bots/{id} data")


class TestDeleteBot:
    def test_delete_bot_response_schema(self, gw_client, app_with_testing_modules):
        _bind_bot(app_with_testing_modules)
        resp = gw_client.delete("/api/bots/bot_test_001")
        body = resp.json()
        assert_response_schema(body, required_top={"success": bool, "message": str, "error_code": int},
                               label="DELETE /api/bots/{id}")


class TestCheckBotName:
    def test_check_name_response_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_bot(app_with_testing_modules)
        resp = gw_client.get("/api/bots/check/name", params={"bot_name": "my-bot"})
        body = resp.json()
        assert_success(body, "GET /api/bots/check/name")
        assert_response_data_contract(body, "rule01_GET_api_bots_check_name", update=contract_snapshot_update)
        assert_has_fields(body["data"], {"exists": bool, "bot_name": str}, label="GET /api/bots/check/name data")


class TestRestartBot:
    def test_restart_response_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_bot(app_with_testing_modules)
        resp = gw_client.post("/api/bots/bot_test_001/restart")
        body = resp.json()
        assert_success(body, "POST /api/bots/{id}/restart")
        assert_response_data_contract(body, "rule01_POST_api_bots_id_restart", update=contract_snapshot_update)
        data = body.get("data", {})
        assert isinstance(data, dict), f"POST /api/bots/{{id}}/restart data should be dict, got {type(data).__name__}"
        assert_has_fields(data, {"bot_id": str, "status": str},
                         label="POST /api/bots/{id}/restart data")


MOCK_PASSPORT = {
    "agent_id": "bot_test_001",
    "agent_code": "ac_test_001",
    "credential_id": "cred_001",
    "expire_at": "2026-12-31T23:59:59Z",
    "access_mode": "RESTRICTED",
    "mcps": [
        {"mcp_code": "mcp_search", "mcp_name": "Search", "mcp_desc": "Search tool"},
    ],
    "certificate_url": "https://example.com/cert/bot_test_001",
}


def _bind_passport(app):
    """Bind mock BotService + PassportPlugin and return them."""
    svc = _bind_bot(app)
    mock_passport = MagicMock(spec=PassportPlugin)
    mock_passport.query_agent_passport.return_value = MOCK_PASSPORT
    bind_mock_service(PassportPlugin, mock_passport, app)
    return svc, mock_passport


class TestGetBotPassport:
    """GET /api/bots/{bot_id}/passport — 查询 Bot Passport 信息。"""

    def test_passport_response_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_passport(app_with_testing_modules)
        resp = gw_client.get("/api/bots/bot_test_001/passport")
        body = resp.json()

        assert_success(body, "GET /api/bots/{id}/passport")
        assert_response_data_contract(body, "rule01_GET_api_bots_id_passport", update=contract_snapshot_update)
        assert_has_fields(body["data"],
            {"agent_id": str, "agent_code": str, "access_mode": str, "mcps": list},
            label="GET /api/bots/{id}/passport data")

    def test_passport_not_found(self, gw_client, app_with_testing_modules):
        """Bot 存在但 Passport 未找到时返回 success=False, error_code=404。"""
        svc = _bind_bot(app_with_testing_modules)
        mock_passport = MagicMock(spec=PassportPlugin)
        mock_passport.query_agent_passport.return_value = None
        bind_mock_service(PassportPlugin, mock_passport, app_with_testing_modules)

        resp = gw_client.get("/api/bots/bot_test_001/passport")
        body = resp.json()

        # 接口返回 success=False, error_code=404
        assert body.get("success") is False or body.get("error_code") == 404, (
            f"Expected passport-not-found response, got: {body}"
        )

    def test_passport_bot_not_found(self, gw_client, app_with_testing_modules):
        """Bot 不存在时返回 success=False。"""
        svc = MagicMock(spec=BotService)
        svc.get_bot.return_value = None
        bind_mock_service(BotService, svc, app_with_testing_modules)

        resp = gw_client.get("/api/bots/nonexistent_bot/passport")
        body = resp.json()

        assert body.get("success") is False, (
            f"Expected bot-not-found response, got: {body}"
        )
