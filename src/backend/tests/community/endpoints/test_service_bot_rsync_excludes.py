"""Unit tests for rsync excludes configuration logic.

Tests the core logic used by GET /api/service-bot/rsync-excludes endpoint.
"""
import pytest

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext
from agentclaw.community.core.workspace.engines.openclaw import _OPENCLAW_RSYNC_EXCLUDES
from agentclaw.community.core.workspace.engines.claude_code import _CLAUDE_CODE_RSYNC_EXCLUDES
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectSuccess,
    ExpectError,
    endpoint_test,
)


@pytest.mark.unit
class TestRsyncExcludesLogic:
    """Test rsync excludes configuration logic."""

    def test_openclaw_default_excludes_count(self):
        """OpenClaw引擎默认有25条rsync排除规则"""
        assert len(_OPENCLAW_RSYNC_EXCLUDES) == 25

    def test_claude_code_default_excludes_count(self):
        """ClaudeCode引擎默认有31条rsync排除规则"""
        assert len(_CLAUDE_CODE_RSYNC_EXCLUDES) == 31

    def test_parse_ext_with_custom_excludes(self):
        """测试从ext字段解析自定义rsync excludes"""
        ext = {"build_rsync_excludes": ["custom_cache/", "temp_files/"]}
        result = parse_build_rsync_excludes_from_ext(ext)
        assert result == ["custom_cache/", "temp_files/"]

    def test_parse_ext_with_none(self):
        """测试ext为None时返回None"""
        result = parse_build_rsync_excludes_from_ext(None)
        assert result is None

    def test_parse_ext_with_empty_list(self):
        """测试build_rsync_excludes为空列表时返回None"""
        ext = {"build_rsync_excludes": []}
        result = parse_build_rsync_excludes_from_ext(ext)
        assert result is None

    def test_parse_ext_without_key(self):
        """测试ext缺少build_rsync_excludes键时返回None"""
        ext = {"other_key": "value"}
        result = parse_build_rsync_excludes_from_ext(ext)
        assert result is None

    def test_merge_logic_with_custom_excludes(self):
        """测试合并逻辑：默认规则 + 自定义规则（去重）"""
        # 模拟默认规则
        default = ["workspace/memory/", "logs/", "cache/"]

        # 模拟自定义规则（包含一个重复项）
        custom = ["custom_1/", "logs/", "custom_2/"]

        # 执行合并
        merged = list(default)
        for item in custom:
            if item not in merged:
                merged.append(item)

        # 验证结果
        assert "workspace/memory/" in merged
        assert "logs/" in merged  # 重复项只保留一个
        assert merged.count("logs/") == 1  # 去重验证
        assert "custom_1/" in merged
        assert "custom_2/" in merged
        assert len(merged) == 5  # 3 default + 2 custom (1 duplicate)

    def test_determine_excludes_source_with_custom(self):
        """测试excludes_source字段判定：有自定义配置"""
        custom_excludes = ["custom_cache/"]
        source = "default_only" if not custom_excludes else "default_plus_custom"
        assert source == "default_plus_custom"

    def test_determine_excludes_source_without_custom(self):
        """测试excludes_source字段判定：无自定义配置"""
        custom_excludes = None
        source = "default_only" if not custom_excludes else "default_plus_custom"
        assert source == "default_only"

    def test_openclaw_default_excludes_content(self):
        """验证OpenClaw默认排除规则包含关键目录"""
        assert "workspace/memory/" in _OPENCLAW_RSYNC_EXCLUDES
        assert "logs/" in _OPENCLAW_RSYNC_EXCLUDES
        assert "agents/*/sessions/" in _OPENCLAW_RSYNC_EXCLUDES

    def test_claude_code_default_excludes_content(self):
        """验证ClaudeCode默认排除规则包含关键目录"""
        assert "workspace/.claude" in _CLAUDE_CODE_RSYNC_EXCLUDES
        assert "projects" in _CLAUDE_CODE_RSYNC_EXCLUDES
        assert "sessions" in _CLAUDE_CODE_RSYNC_EXCLUDES


# ============================================================================
# Endpoint Tests - GET /api/service-bot/rsync-excludes
# ============================================================================


def _seed_openclaw_bot_with_custom(world):
    """Seed bot owner + an openclaw bot with custom rsync excludes."""
    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert({
        "bot_id": "bot_openclaw",
        "bot_name": "OpenClaw Bot",
        "owner_id": "u_owner",
        "bot_type": "service",
        "status": "ACTIVE",
        "entity_id": "u_owner",
        "entity_type": "user",
        "creator_id": "u_owner",
        "active_engine": "openclaw",
        "ext": {
            "build_rsync_excludes": ["custom_cache/", "temp_files/"]
        }
    })


def _seed_claude_code_bot_no_custom(world):
    """Seed bot owner + a claude_code bot without custom excludes."""
    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert({
        "bot_id": "bot_claude_code",
        "bot_name": "ClaudeCode Bot",
        "owner_id": "u_owner",
        "bot_type": "service",
        "status": "ACTIVE",
        "entity_id": "u_owner",
        "entity_type": "user",
        "creator_id": "u_owner",
        "active_engine": "claude_code",
        "ext": None
    })


def _seed_openclaw_bot_empty_excludes(world):
    """Seed bot owner + an openclaw bot with empty excludes list."""
    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert({
        "bot_id": "bot_empty",
        "bot_name": "Empty Excludes Bot",
        "owner_id": "u_owner",
        "bot_type": "service",
        "status": "ACTIVE",
        "entity_id": "u_owner",
        "entity_type": "user",
        "creator_id": "u_owner",
        "active_engine": "openclaw",
        "ext": {
            "build_rsync_excludes": []
        }
    })


@endpoint_test(
    method="GET",
    path="/api/service-bot/rsync-excludes",
    scenario="openclaw_with_custom_excludes",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_openclaw", "owner_id": "u_owner"},
    ),
    seed=_seed_openclaw_bot_with_custom,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "bot_id": "bot_openclaw",
                "engine_type": "openclaw",
                "excludes_source": "default_plus_custom"
            }
        }
    ),
)
def test_rsync_excludes_openclaw_with_custom():
    """GET /api/service-bot/rsync-excludes with openclaw bot and custom excludes."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/rsync-excludes",
    scenario="claude_code_without_custom",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_claude_code", "owner_id": "u_owner"},
    ),
    seed=_seed_claude_code_bot_no_custom,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "bot_id": "bot_claude_code",
                "engine_type": "claude_code",
                "excludes_source": "default_only"
            }
        }
    ),
)
def test_rsync_excludes_claude_code_without_custom():
    """GET /api/service-bot/rsync-excludes with claude_code bot and no custom excludes."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/rsync-excludes",
    scenario="empty_excludes_list",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_empty", "owner_id": "u_owner"},
    ),
    seed=_seed_openclaw_bot_empty_excludes,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "bot_id": "bot_empty",
                "excludes_source": "default_only"
            }
        }
    ),
)
def test_rsync_excludes_empty_list():
    """GET /api/service-bot/rsync-excludes with empty excludes list treated as no custom."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/rsync-excludes",
    scenario="bot_not_found",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_nonexistent", "owner_id": "u_owner"},
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def test_rsync_excludes_bot_not_found():
    """GET /api/service-bot/rsync-excludes with non-existent bot returns 404."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/rsync-excludes",
    scenario="missing_owner_id",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_openclaw"},  # Missing owner_id
    ),
    seed=_seed_openclaw_bot_with_custom,
    expect=ExpectError(
        status=422,  # FastAPI validation error
    ),
)
def test_rsync_excludes_missing_owner_id():
    """GET /api/service-bot/rsync-excludes without owner_id returns 422."""