"""Rule #15 — 能力集管理 (/api/skillsets) 契约测试。

验证 GET/POST /api/skillsets, GET/PUT/DELETE /api/skillsets/{id},
POST/DELETE /api/skillsets/{id}/skills, GET /api/skillsets/with-mcps,
POST/DELETE /api/skillsets/{id}/mcps 等接口响应字段。
"""
from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock

from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.plugin_api.passport import PassportPlugin

from tests.community.contracts.gateway.conftest import (
    assert_response_schema, assert_success, assert_has_fields,
    assert_response_data_contract, bind_mock_service,
)


# Handler 期望 service 返回原始 dict 列表（不是 {"success": True, "data": [...]} 包装）
# 每个 dict 需要 bolt_id（handler 内部映射为 bot_id）、is_default、gmt_created 等字段。
MOCK_SKILLSET_ROW = {
    "id": "1", "name": "Default", "description": "默认能力集",
    "is_default": True, "is_builtin": False,
    "user_id": "448524", "bolt_id": "bot_test_001",
    "engine_type": "openclaw",
    "gmt_created": "2026-01-01", "gmt_modified": "2026-01-01",
    "is_active": True, "type": "custom",
}

MOCK_SKILL_ROW = {
    "id": 10, "name": "TestSkill", "description": "", "git_path": "git://skills/test",
}

MOCK_MCP_ROW = {
    "id": "mcp_001", "server_code": "test-mcp",
    "name": "Test MCP", "description": "", "icon": "",
}


def _make_mock_skillset_service() -> MagicMock:
    """Mock SkillSetService（由 factory.create() 返回）。"""
    svc = MagicMock()
    # list_skill_sets 返回原始 dict 列表
    svc.list_skill_sets.return_value = [MOCK_SKILLSET_ROW]
    # create_skill_set 返回单个 dict
    svc.create_skill_set.return_value = {**MOCK_SKILLSET_ROW, "name": "NewSet"}
    # get_skill_set 返回单个 dict
    svc.get_skill_set.return_value = MOCK_SKILLSET_ROW
    # update_skill_set 返回单个 dict
    svc.update_skill_set.return_value = {**MOCK_SKILLSET_ROW, "name": "Updated"}
    # delete_skill_set 返回 bool（True=成功）
    svc.delete_skill_set.return_value = True
    # get_set_skills 返回 dict 列表
    svc.get_set_skills.return_value = [MOCK_SKILL_ROW]
    # add_skills_to_set 是 async 方法
    svc.add_skills_to_set = AsyncMock(return_value={
        "success": ["skill_1"], "failed": [],
    })
    # remove_skill_from_set 是 async 方法
    svc.remove_skill_from_set = AsyncMock(return_value=True)
    # get_set_mcp_servers 返回 dict 列表
    svc.get_set_mcp_servers.return_value = [MOCK_MCP_ROW]
    svc.get_bot_mcp_codes.return_value = ["test-mcp"]
    # add_mcp_to_skill_set 是 async 方法
    svc.add_mcp_to_skill_set = AsyncMock(return_value={
        "success": True, "server_code": "test-mcp",
        "requires_api_key": False, "requires_permission": False,
    })
    # remove_mcp_from_skill_set 是 async 方法
    svc.remove_mcp_from_skill_set = AsyncMock(return_value={
        "success": True,
    })
    return svc


def _make_mock_skillset_factory() -> MagicMock:
    """Mock SkillSetServiceFactory whose create() returns mock SkillSetService."""
    mock_svc = _make_mock_skillset_service()
    mock_factory = MagicMock(spec=SkillSetServiceFactory)
    mock_factory.create.return_value = mock_svc
    return mock_factory


def _bind_skillset_deps(app):
    """Bind SkillSetServiceFactory + BotRepository mocks."""
    mock_factory = _make_mock_skillset_factory()
    bind_mock_service(SkillSetServiceFactory, mock_factory, app)

    # BotRepository 由 _get_path_params 调用，mock 以避免数据库访问
    mock_bot_repo = MagicMock(spec=BotRepository)
    mock_bot_repo.get_by_id_and_owner.return_value = None
    bind_mock_service(BotRepository, mock_bot_repo, app)

    mock_passport = MagicMock(spec=PassportPlugin)
    mock_passport.query_passport_clis.return_value = [
        {"cli_code": "cli.keep", "cli_name": "Keep CLI", "cli_desc": "kept"},
        {"cli_code": "cli.delete", "cli_name": "Delete CLI", "cli_desc": "removed"},
    ]
    bind_mock_service(PassportPlugin, mock_passport, app)
    return mock_factory, mock_passport


class TestListSkillsets:
    """GET /api/skillsets — 能力集列表。"""

    def test_list_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_skillset_deps(app_with_testing_modules)

        resp = gw_client.get("/api/skillsets", params={
            "user_id": "448524", "bot_id": "bot_test_001",
        })
        body = resp.json()

        assert_success(body, "GET /api/skillsets")
        assert_response_data_contract(body, "rule15_GET_api_skillsets", update=contract_snapshot_update)
        data = body["data"]
        assert isinstance(data, list), f"GET /api/skillsets data should be list, got {type(data).__name__}"
        assert_has_fields(
            data[0],
            {"id": str, "name": str, "is_default": bool, "is_builtin": bool, "user_id": str, "bot_id": str, "is_active": bool},
            label="GET /api/skillsets data[0]",
        )


class TestDeleteSkillset:
    """DELETE /api/skillsets/{id} — 删除能力集。"""

    def test_delete_schema(self, gw_client, app_with_testing_modules):
        _bind_skillset_deps(app_with_testing_modules)

        resp = gw_client.delete("/api/skillsets/1")
        body = resp.json()

        assert_response_schema(
            body, required_top={"success": bool, "message": str},
            label="DELETE /api/skillsets/{id}",
        )


class TestSkillsetsWithMcps:
    """GET /api/skillsets/with-mcps — 含 MCP 的能力集列表。"""

    def test_with_mcps_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_skillset_deps(app_with_testing_modules)

        resp = gw_client.get("/api/skillsets/with-mcps", params={
            "user_id": "448524", "bot_id": "bot_test_001",
        })
        body = resp.json()

        assert_success(body, "GET /api/skillsets/with-mcps")
        assert_response_data_contract(body, "rule15_GET_api_skillsets_with_mcps", update=contract_snapshot_update)
        data = body["data"]
        assert isinstance(data, list), f"GET /api/skillsets/with-mcps data should be list, got {type(data).__name__}"
        assert_has_fields(
            data[0], {"id": str, "name": str, "mcps": list},
            label="GET /api/skillsets/with-mcps data[0]",
        )


class TestSkillsetResources:
    """GET /api/skillsets/resources — 能力集资源聚合视图。"""

    def test_resources_schema_includes_default_set_clis(self, gw_client, app_with_testing_modules):
        _bind_skillset_deps(app_with_testing_modules)

        resp = gw_client.get("/api/skillsets/resources", params={
            "user_id": "448524", "bot_id": "bot_test_001",
        })
        body = resp.json()

        assert_success(body, "GET /api/skillsets/resources")
        data = body["data"]
        assert isinstance(data, list)
        assert_has_fields(
            data[0], {"id": str, "name": str, "mcps": list, "clis": list},
            label="GET /api/skillsets/resources data[0]",
        )
        assert data[0]["clis"] == [
            {"cli_code": "cli.keep", "cli_name": "Keep CLI", "cli_desc": "kept"},
            {"cli_code": "cli.delete", "cli_name": "Delete CLI", "cli_desc": "removed"},
        ]


class TestDeleteSkillsetCli:
    """DELETE /api/skillsets/{id}/clis/{resource_code} — 删除默认能力集 CLI。"""

    def test_delete_cli_updates_passport_with_remaining_latest_clis(self, gw_client, app_with_testing_modules):
        _mock_factory, mock_passport = _bind_skillset_deps(app_with_testing_modules)

        resp = gw_client.delete("/api/skillsets/1/clis/cli.delete", params={
            "entity_id": "448524",
            "entity_type": "staff",
            "bot_id": "bot_test_001",
        })
        body = resp.json()

        assert_success(body, "DELETE /api/skillsets/{id}/clis/{resource_code}")
        mock_passport.query_passport_clis.assert_called_with("bot_test_001", "448524")
        mock_passport.update_passport.assert_called_once_with(
            bot_id="bot_test_001",
            user_id="448524",
            resource_scope={
                "mcp_codes": ["test-mcp"],
                "cli_items": [{"cli_code": "cli.keep", "cli_name": "Keep CLI", "cli_desc": "kept"}],
            },
        )
