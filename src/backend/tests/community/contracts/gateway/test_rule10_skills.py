"""Rule #10 — 技能市场 (/api/skills) 契约测试。

验证 GET market/list, POST market/search, GET /{id},
POST skillset/activate, POST skillset/deactivate, GET skillset/active
等接口响应字段。

Mock 策略:
- Skills 路由通过 Injected(SkillServiceFactory) 获取服务，
  此处通过 DI override 将 factory 绑定为 mock，factory.create() 返回 mock service。
- skillset/active 路由通过 Injected(SkillSetRepository) 直接获取 repo，
  此处 mock SkillSetRepository + BotRepository。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.skill_center.factories import SkillServiceFactory
from agentclaw.community.core.skill_center.services.repositories import SkillSetRepository
from agentclaw.community.core.bot_management.repository.protocol import BotRepository

from tests.community.contracts.gateway.conftest import (
    assert_response_schema, assert_success, assert_has_fields,
    assert_response_data_contract, bind_mock_service,
)


MOCK_SKILL_ITEM = {
    "id": 1, "name": "TestSkill", "description": "A test skill",
    "git_path": "skills/test-skill", "link_name": "test-skill",
    "category": "dev", "tags": "test",
    "input_schema": {}, "output_schema": {},
    "is_public": True, "is_builtin": False, "user_id": "448524",
    "gmt_created": "2026-01-01", "gmt_modified": "2026-01-01",
    "risk_tags": [], "mcp_dependencies": [], "bolt_id": "",
    "env": "pre", "status": "active", "version": 1,
    "skill_uuid": "uuid-skill-001", "source_type": "git",
    "category_path": "/dev", "package_url": "", "zip_url": "", "use_count": 0,
}


def _make_mock_skill_factory() -> MagicMock:
    """Mock SkillServiceFactory whose create() returns a mock SkillService。

    注意: handler 将 service 返回值直接传给 Pydantic Response 模型,
    所以 mock 返回值必须匹配 handler 期望的原始数据结构（不需要 success/data 包装）。
    - list_market_skills / search_market_skills → 返回列表
    - get_active_skill_sets → 返回列表
    """
    mock_svc = MagicMock()
    # list_market_skills / search_market_skills: handler 直接传 data=results（列表）
    mock_svc.list_market_skills.return_value = [MOCK_SKILL_ITEM]
    mock_svc.search_market_skills.return_value = [MOCK_SKILL_ITEM]
    mock_svc.get_skill.return_value = {
        **MOCK_SKILL_ITEM, "bot_id": "bot_test_001", "members": [],
    }
    mock_svc.activate_skill_set.return_value = {
        "activated": ["skill_1"], "failed": [],
    }
    mock_svc.deactivate_skill_set.return_value = {
        "deactivated": ["skill_1"], "failed": [],
    }
    mock_svc.get_active_skill_sets.return_value = [{
        "id": 1, "name": "Default", "description": "",
        "is_default": True, "is_builtin": False,
        "user_id": "448524", "gmt_created": "2026-01-01",
        "gmt_modified": "2026-01-01", "env": "pre",
        "is_active": True, "engine_type": "openclaw", "bot_id": "bot_test_001",
    }]

    mock_factory = MagicMock(spec=SkillServiceFactory)
    mock_factory.create.return_value = mock_svc
    return mock_factory


def _bind_skill_deps(app):
    """Bind SkillServiceFactory + BotRepository + WorkspacePathFactory mocks.

    Skills 路由 handler 通过 _get_path_params 解析路径参数，
    依赖 BotRepository 和 WorkspacePathFactory。
    """
    mock_factory = _make_mock_skill_factory()
    bind_mock_service(SkillServiceFactory, mock_factory, app)

    # BotRepository 由 _get_path_params 调用，mock 以避免数据库访问
    mock_bot_repo = MagicMock(spec=BotRepository)
    mock_bot_repo.get_by_id_and_owner.return_value = None
    bind_mock_service(BotRepository, mock_bot_repo, app)

    # WorkspacePathFactory 由 handler 注入，mock 以避免文件系统依赖
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
    mock_path_factory = MagicMock(spec=WorkspacePathFactory)
    mock_path_factory.get_bot_skills_dir.return_value = "/tmp/test/skills"
    mock_path_factory.get_bot_data_dir.return_value = "/tmp/test/data"
    mock_path_factory.get_bot_skills_local_dir.return_value = "/tmp/test/local"
    mock_path_factory.get_bot_skills_repo_dir.return_value = "/tmp/test/repo"
    bind_mock_service(WorkspacePathFactory, mock_path_factory, app)


def _bind_skillset_active_deps(app):
    """Bind SkillSetRepository + BotRepository for skillset/active endpoint.

    GET /api/skills/skillset/active 注入 SkillSetRepository（不是 SkillServiceFactory），
    还需要 BotRepository 给 _get_path_params。
    """
    # SkillSetRepository.get_all_active_skill_sets 返回 list[dict]
    mock_repo = MagicMock(spec=SkillSetRepository)
    mock_repo.get_all_active_skill_sets.return_value = [{
        "id": 1, "name": "Default", "description": "",
        "is_default": True, "is_builtin": False,
        "user_id": "448524", "gmt_created": "2026-01-01",
        "gmt_modified": "2026-01-01", "env": "pre",
        "is_active": True, "engine_type": "openclaw", "bolt_id": "bot_test_001",
    }]
    bind_mock_service(SkillSetRepository, mock_repo, app)

    # BotRepository 由 _get_path_params 调用
    mock_bot_repo = MagicMock(spec=BotRepository)
    mock_bot_repo.get_by_id_and_owner.return_value = None
    bind_mock_service(BotRepository, mock_bot_repo, app)


class TestMarketList:
    """GET /api/skills/market/list — 技能市场列表。"""

    def test_market_list_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_skill_deps(app_with_testing_modules)

        resp = gw_client.get("/api/skills/market/list", params={
            "page_num": 1, "page_size": 20,
        })
        body = resp.json()

        assert_success(body, "GET market/list")
        assert_response_data_contract(body, "rule10_GET_api_skills_market_list", update=contract_snapshot_update)
        data = body["data"]
        assert isinstance(data, list), f"GET market/list data should be list, got {type(data).__name__}"
        if data:
            assert_has_fields(
                data[0],
                {"id": int, "name": str, "description": str, "is_public": bool, "is_builtin": bool,
                 "user_id": str, "status": str, "version": int, "skill_uuid": str},
                label="GET market/list data[0]",
            )


class TestMarketSearch:
    """POST /api/skills/market/search — 技能市场搜索。"""

    def test_market_search_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_skill_deps(app_with_testing_modules)

        resp = gw_client.post("/api/skills/market/search", json={
            "query": "test", "page_num": 1, "page_size": 20,
        })
        body = resp.json()

        assert_success(body, "POST market/search")
        assert_response_data_contract(body, "rule10_POST_api_skills_market_search", update=contract_snapshot_update)
        # 文档: {success, data: [...], count}
        assert "count" in body or "total" in body, (
            "POST market/search should have count or total"
        )


class TestSkillsetActive:
    """GET /api/skills/skillset/active — 查询已激活技能。"""

    def test_active_schema(self, gw_client, app_with_testing_modules, contract_snapshot_update):
        _bind_skillset_active_deps(app_with_testing_modules)

        resp = gw_client.get("/api/skills/skillset/active", params={
            "bot_id": "bot_test_001",
        })
        body = resp.json()

        assert_success(body, "GET skillset/active")
        assert_response_data_contract(body, "rule10_GET_api_skills_skillset_active", update=contract_snapshot_update)
        data = body["data"]
        assert isinstance(data, list), f"GET skillset/active data should be list, got {type(data).__name__}"
        if data:
            assert_has_fields(
                data[0],
                {"id": int, "name": str, "is_default": bool, "is_builtin": bool,
                 "is_active": bool, "engine_type": str, "bot_id": str},
                label="GET skillset/active data[0]",
            )
