"""Tests for agentclaw.community.core.services.skill_auth_service.SkillAuthService."""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skill_center.services.skill_auth_service import SkillAuthService


def _make_svc(*, skill_repo=None, skill_set_repo=None, mcp_center=None, mcp_auth_service=None):
    return SkillAuthService(
        skill_repo=skill_repo or MagicMock(),
        skill_set_repo=skill_set_repo or MagicMock(),
        mcp_center=mcp_center or MagicMock(),
        mcp_auth_service=mcp_auth_service or MagicMock(),
    )


# ── TestExtractServerCodes ──────────────────────────────────────────


class TestExtractServerCodes:
    """_extract_server_codes static method."""

    def test_empty_deps(self):
        assert SkillAuthService._extract_server_codes({}) == []

    def test_none_deps(self):
        assert SkillAuthService._extract_server_codes({"mcp_dependencies": None}) == []

    def test_single_dep(self):
        skill = {"mcp_dependencies": [{"code": "mcp-server-a"}]}
        assert SkillAuthService._extract_server_codes(skill) == ["mcp-server-a"]

    def test_dedup(self):
        skill = {"mcp_dependencies": [
            {"code": "mcp-server-a"},
            {"code": "mcp-server-a"},
            {"code": "mcp-server-b"},
        ]}
        result = SkillAuthService._extract_server_codes(skill)
        assert result == ["mcp-server-a", "mcp-server-b"]

    def test_name_fallback(self):
        skill = {"mcp_dependencies": [{"name": "fallback-server"}]}
        assert SkillAuthService._extract_server_codes(skill) == ["fallback-server"]

    def test_skip_non_dict(self):
        skill = {"mcp_dependencies": ["not-a-dict", {"code": "ok"}]}
        assert SkillAuthService._extract_server_codes(skill) == ["ok"]


# ── TestCheckSkillPermission ────────────────────────────────────────


class TestCheckSkillPermission:
    """check_skill_permission."""

    def test_skill_not_found_raises(self):
        skill_repo = MagicMock()
        skill_repo.get_by_id.return_value = None
        svc = _make_svc(skill_repo=skill_repo)
        with pytest.raises(ValueError, match="Skill not found"):
            svc.check_skill_permission(user_id="user1", skill_id="999")

    def test_no_mcp_deps_returns_authorized(self):
        skill_repo = MagicMock()
        skill_repo.get_by_id.return_value = {
            "id": "1", "name": "simple-skill", "mcp_dependencies": []
        }
        svc = _make_svc(skill_repo=skill_repo)
        result = svc.check_skill_permission(user_id="user1", skill_id="1")
        assert result["authorized"] is True
        assert result["mcp_details"] == {}

    def test_all_authorized(self):
        skill_repo = MagicMock()
        skill_repo.get_by_id.return_value = {
            "id": "1", "name": "auth-skill",
            "mcp_dependencies": [{"code": "server-a"}, {"code": "server-b"}]
        }
        mcp_auth_service = MagicMock()
        mcp_auth_service.check_mcp_permission_detail.return_value = {
            "has_permission": True, "access_level": "full"
        }
        svc = _make_svc(skill_repo=skill_repo, mcp_auth_service=mcp_auth_service)
        result = svc.check_skill_permission(user_id="user1", skill_id="1")
        assert result["authorized"] is True
        assert len(result["mcp_details"]) == 2

    def test_partial_authorized(self):
        skill_repo = MagicMock()
        skill_repo.get_by_id.return_value = {
            "id": "1", "name": "mixed-skill",
            "mcp_dependencies": [{"code": "allowed"}, {"code": "denied"}]
        }
        mcp_auth_service = MagicMock()
        mcp_auth_service.check_mcp_permission_detail.side_effect = [
            {"has_permission": True, "access_level": "full"},
            {"has_permission": False, "access_level": ""},
        ]
        svc = _make_svc(skill_repo=skill_repo, mcp_auth_service=mcp_auth_service)
        result = svc.check_skill_permission(user_id="user1", skill_id="1")
        assert result["authorized"] is False
