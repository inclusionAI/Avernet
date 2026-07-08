"""Extended tests for SkillAuthService — covering bot- and skill-set-level methods."""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skill_center.services.skill_auth_service import SkillAuthService

# NOTE: apply_*_permission now uses the DI-injected self.mcp_auth_service
# (the old in-method ``from .mcp_auth import MCPAuthService`` was a prod
# ModuleNotFoundError that an autouse sys.modules-faking fixture used to
# mask). Tests set ``svc.mcp_auth_service`` directly instead.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_svc(skill_set_repo=None, mcp_auth_service=None, mcp_center=None, skill_repo=None):
    return SkillAuthService(
        skill_repo=skill_repo if skill_repo is not None else MagicMock(),
        skill_set_repo=skill_set_repo if skill_set_repo is not None else MagicMock(),
        mcp_center=mcp_center if mcp_center is not None else MagicMock(),
        mcp_auth_service=mcp_auth_service if mcp_auth_service is not None else MagicMock(),
    )


def _make_skill_set_repo(
    skill_sets=None, skills_per_set=None, mcp_servers_per_set=None
):
    repo = MagicMock()
    repo.list_all.return_value = skill_sets or []
    if skills_per_set is not None:
        repo.get_skills_in_set.side_effect = lambda ss_id: skills_per_set.get(ss_id, [])
    else:
        repo.get_skills_in_set.return_value = []
    if mcp_servers_per_set is not None:
        repo.get_mcp_servers_in_set.side_effect = lambda ss_id: mcp_servers_per_set.get(ss_id, [])
    else:
        repo.get_mcp_servers_in_set.return_value = []
    return repo


# ---------------------------------------------------------------------------
# apply_skill_permission
# ---------------------------------------------------------------------------

class TestApplySkillPermission:
    def test_no_mcp_deps_returns_authorized(self):
        svc = _make_svc()
        svc.skill_repo = MagicMock()
        svc.skill_repo.get_by_id.return_value = {
            "id": "1", "name": "simple", "mcp_dependencies": []
        }
        result = svc.apply_skill_permission("user1", "1")
        assert result["all_authorized"] is True
        assert result["apply_results"] == {}

    def test_mcp_not_found_marks_failure(self):
        svc = _make_svc()
        svc.skill_repo = MagicMock()
        svc.skill_repo.get_by_id.return_value = {
            "id": "2", "name": "sk",
            "mcp_dependencies": [{"code": "missing-server"}]
        }
        svc.mcp_center = MagicMock()
        svc.mcp_center.get_mcp_detail.return_value = None

        mock_auth_svc = MagicMock()
        svc.mcp_auth_service = mock_auth_svc
        result = svc.apply_skill_permission("user1", "2")

        assert result["all_authorized"] is False
        assert result["apply_results"]["missing-server"]["success"] is False
        assert "not found" in result["apply_results"]["missing-server"]["error"]

    def test_public_mcp_apply_succeeds_already_authorized(self):
        svc = _make_svc()
        svc.skill_repo = MagicMock()
        svc.skill_repo.get_by_id.return_value = {
            "id": "3", "name": "sk",
            "mcp_dependencies": [{"code": "pub-server"}]
        }
        svc.mcp_center = MagicMock()
        svc.mcp_center.get_mcp_detail.return_value = {
            "accessLevel": "PUBLIC",
            "tools": [{"name": "tool1"}],
        }
        mock_auth_svc = MagicMock()
        mock_auth_svc.apply_permission.return_value = {
            "success": True,
            "process_url": None,
            "error": None,
        }
        svc.mcp_auth_service = mock_auth_svc
        result = svc.apply_skill_permission("user1", "3")

        assert result["all_authorized"] is True
        r = result["apply_results"]["pub-server"]
        assert r["success"] is True
        assert r["already_authorized"] is True

    def test_apply_with_process_url_not_already_authorized(self):
        svc = _make_svc()
        svc.skill_repo = MagicMock()
        svc.skill_repo.get_by_id.return_value = {
            "id": "4", "name": "sk",
            "mcp_dependencies": [{"code": "private-server"}]
        }
        svc.mcp_center = MagicMock()
        svc.mcp_center.get_mcp_detail.return_value = {
            "accessLevel": "PRIVATE",
            "tools": [],
        }
        mock_auth_svc = MagicMock()
        mock_auth_svc.apply_permission.return_value = {
            "success": True,
            "process_url": "http://apply.url/12345",
            "error": None,
        }
        svc.mcp_auth_service = mock_auth_svc
        result = svc.apply_skill_permission("user1", "4", reason="need it")

        r = result["apply_results"]["private-server"]
        assert r["success"] is True
        assert r["already_authorized"] is False
        assert r["process_url"] == "http://apply.url/12345"

    def test_exception_during_apply_sets_error(self):
        svc = _make_svc()
        svc.skill_repo = MagicMock()
        svc.skill_repo.get_by_id.return_value = {
            "id": "5", "name": "sk",
            "mcp_dependencies": [{"code": "bad-server"}]
        }
        svc.mcp_center = MagicMock()
        svc.mcp_center.get_mcp_detail.side_effect = RuntimeError("network error")

        mock_auth_svc = MagicMock()
        svc.mcp_auth_service = mock_auth_svc
        result = svc.apply_skill_permission("user1", "5")

        assert result["all_authorized"] is False
        assert result["apply_results"]["bad-server"]["success"] is False


# ---------------------------------------------------------------------------
# check_skill_permission — exception path
# ---------------------------------------------------------------------------

class TestCheckSkillPermissionExtended:
    def test_mcp_check_exception_marks_unauthorized(self):
        svc = _make_svc()
        svc.skill_repo = MagicMock()
        svc.skill_repo.get_by_id.return_value = {
            "id": "1", "name": "sk",
            "mcp_dependencies": [{"code": "flaky-server"}]
        }
        svc.mcp_auth_service = MagicMock()
        svc.mcp_auth_service.check_mcp_permission_detail.side_effect = RuntimeError("timeout")

        result = svc.check_skill_permission("user1", "1")
        assert result["authorized"] is False
        assert "error" in result["mcp_details"]["flaky-server"]


# ---------------------------------------------------------------------------
# _collect_bot_mcp_codes
# ---------------------------------------------------------------------------

class TestCollectBotMcpCodes:
    def test_raises_when_no_skill_sets(self):
        svc = _make_svc(skill_set_repo=_make_skill_set_repo(skill_sets=[]))
        with pytest.raises(ValueError, match="No skill_set found"):
            svc._collect_bot_mcp_codes("missing-bot")

    def test_aggregates_direct_mcp_and_skill_deps(self):
        repo = _make_skill_set_repo(
            skill_sets=[
                {"id": "ss1"},
                {"id": "ss2"},
            ],
            skills_per_set={
                "ss1": [{"id": "sk1", "mcp_dependencies": [{"code": "svc-a"}]}],
                "ss2": [
                    {"id": "sk2", "mcp_dependencies": [{"code": "svc-b"}, {"code": "svc-a"}]},
                ],
            },
            mcp_servers_per_set={
                "ss1": [{"server_code": "direct-mcp"}],
                "ss2": [],
            },
        )
        svc = _make_svc(skill_set_repo=repo)
        skill_sets, codes = svc._collect_bot_mcp_codes("bot1")
        assert len(skill_sets) == 2
        # direct-mcp + svc-a (once) + svc-b = 3 unique codes
        assert set(codes) == {"direct-mcp", "svc-a", "svc-b"}
        # Order: direct-mcp, svc-a, svc-b
        assert codes.index("direct-mcp") < codes.index("svc-a")


# ---------------------------------------------------------------------------
# check_bot_permission
# ---------------------------------------------------------------------------

class TestCheckBotPermission:
    def test_no_mcp_codes_returns_authorized(self):
        repo = _make_skill_set_repo(
            skill_sets=[{"id": "ss1"}],
        )
        svc = _make_svc(skill_set_repo=repo)
        result = svc.check_bot_permission("user1", "bot1")
        assert result["authorized"] is True
        assert result["mcp_details"] == {}
        assert result["bot_id"] == "bot1"

    def test_all_authorized(self):
        repo = _make_skill_set_repo(
            skill_sets=[{"id": "ss1"}],
            skills_per_set={"ss1": [{"id": "sk1", "mcp_dependencies": [{"code": "srv-x"}]}]},
        )
        mcp_auth = MagicMock()
        mcp_auth.check_mcp_permission_detail.return_value = {"has_permission": True, "access_level": "full"}
        svc = _make_svc(skill_set_repo=repo, mcp_auth_service=mcp_auth)

        result = svc.check_bot_permission("user1", "bot1")
        assert result["authorized"] is True
        assert "srv-x" in result["mcp_details"]

    def test_partial_denial(self):
        repo = _make_skill_set_repo(
            skill_sets=[{"id": "ss1"}],
            skills_per_set={"ss1": [
                {"id": "sk1", "mcp_dependencies": [{"code": "ok-srv"}, {"code": "denied-srv"}]}
            ]},
        )
        mcp_auth = MagicMock()
        mcp_auth.check_mcp_permission_detail.side_effect = [
            {"has_permission": True},
            {"has_permission": False},
        ]
        svc = _make_svc(skill_set_repo=repo, mcp_auth_service=mcp_auth)

        result = svc.check_bot_permission("user1", "bot1")
        assert result["authorized"] is False

    def test_exception_marks_unauthorized(self):
        repo = _make_skill_set_repo(
            skill_sets=[{"id": "ss1"}],
            skills_per_set={"ss1": [{"id": "sk1", "mcp_dependencies": [{"code": "srv"}]}]},
        )
        mcp_auth = MagicMock()
        mcp_auth.check_mcp_permission_detail.side_effect = RuntimeError("err")
        svc = _make_svc(skill_set_repo=repo, mcp_auth_service=mcp_auth)

        result = svc.check_bot_permission("user1", "bot1")
        assert result["authorized"] is False
        assert "error" in result["mcp_details"]["srv"]


# ---------------------------------------------------------------------------
# apply_bot_permission
# ---------------------------------------------------------------------------

class TestApplyBotPermission:
    def test_no_mcp_codes_returns_all_authorized(self):
        repo = _make_skill_set_repo(skill_sets=[{"id": "ss1"}])
        svc = _make_svc(skill_set_repo=repo)
        result = svc.apply_bot_permission("user1", "bot1")
        assert result["all_authorized"] is True
        assert result["apply_results"] == {}

    def test_mcp_not_found(self):
        repo = _make_skill_set_repo(
            skill_sets=[{"id": "ss1"}],
            skills_per_set={"ss1": [{"id": "sk1", "mcp_dependencies": [{"code": "ghost"}]}]},
        )
        mcp_center = MagicMock()
        mcp_center.get_mcp_detail.return_value = None
        svc = _make_svc(skill_set_repo=repo, mcp_center=mcp_center)

        mock_auth_svc = MagicMock()
        svc.mcp_auth_service = mock_auth_svc
        result = svc.apply_bot_permission("user1", "bot1")

        assert result["all_authorized"] is False
        assert result["apply_results"]["ghost"]["success"] is False

    def test_successful_apply_with_process_url(self):
        repo = _make_skill_set_repo(
            skill_sets=[{"id": "ss1"}],
            skills_per_set={"ss1": [{"id": "sk1", "mcp_dependencies": [{"code": "svc-p"}]}]},
        )
        mcp_center = MagicMock()
        mcp_center.get_mcp_detail.return_value = {
            "accessLevel": "PRIVATE",
            "tools": [{"name": "t1"}],
        }
        svc = _make_svc(skill_set_repo=repo, mcp_center=mcp_center)
        mock_auth_svc = MagicMock()
        mock_auth_svc.apply_permission.return_value = {
            "success": True,
            "process_url": "http://flow/99",
            "error": None,
        }
        svc.mcp_auth_service = mock_auth_svc
        result = svc.apply_bot_permission("user1", "bot1", reason="why")

        r = result["apply_results"]["svc-p"]
        assert r["success"] is True
        assert r["already_authorized"] is False

    def test_exception_during_bot_apply(self):
        repo = _make_skill_set_repo(
            skill_sets=[{"id": "ss1"}],
            skills_per_set={"ss1": [{"id": "sk1", "mcp_dependencies": [{"code": "boom"}]}]},
        )
        mcp_center = MagicMock()
        mcp_center.get_mcp_detail.side_effect = RuntimeError("boom")
        svc = _make_svc(skill_set_repo=repo, mcp_center=mcp_center)

        mock_auth_svc = MagicMock()
        svc.mcp_auth_service = mock_auth_svc
        result = svc.apply_bot_permission("user1", "bot1")

        assert result["all_authorized"] is False
        assert result["apply_results"]["boom"]["success"] is False


# ---------------------------------------------------------------------------
# _collect_skill_set_mcp_codes
# ---------------------------------------------------------------------------

class TestCollectSkillSetMcpCodes:
    def test_raises_when_skills_is_none(self):
        repo = MagicMock()
        repo.get_skills_in_set.return_value = None
        svc = _make_svc(skill_set_repo=repo)
        with pytest.raises(ValueError, match="SkillSet not found"):
            svc._collect_skill_set_mcp_codes("missing-set")

    def test_deduplicates_across_skills(self):
        repo = MagicMock()
        repo.get_skills_in_set.return_value = [
            {"id": "sk1", "mcp_dependencies": [{"code": "a"}, {"code": "b"}]},
            {"id": "sk2", "mcp_dependencies": [{"code": "b"}, {"code": "c"}]},
        ]
        svc = _make_svc(skill_set_repo=repo)
        skills, codes, skill_to_codes = svc._collect_skill_set_mcp_codes("set1")
        assert len(codes) == 3
        assert set(codes) == {"a", "b", "c"}
        assert skill_to_codes["sk1"] == ["a", "b"]
        assert skill_to_codes["sk2"] == ["b", "c"]


# ---------------------------------------------------------------------------
# apply_skill_set_permission
# ---------------------------------------------------------------------------

class TestApplySkillSetPermission:
    def test_no_codes_returns_all_authorized_with_skills(self):
        repo = MagicMock()
        repo.get_skills_in_set.return_value = [
            {"id": "sk1", "name": "empty-skill", "mcp_dependencies": []}
        ]
        svc = _make_svc(skill_set_repo=repo)
        result = svc.apply_skill_set_permission("user1", "set1")
        assert result["all_authorized"] is True
        assert result["apply_results"] == {}
        assert "sk1" in result["skills"]

    def test_mcp_not_found_in_set(self):
        repo = MagicMock()
        repo.get_skills_in_set.return_value = [
            {"id": "sk1", "name": "sk", "mcp_dependencies": [{"code": "ghost"}]}
        ]
        mcp_center = MagicMock()
        mcp_center.get_mcp_detail.return_value = None
        svc = _make_svc(skill_set_repo=repo, mcp_center=mcp_center)

        mock_auth_svc = MagicMock()
        svc.mcp_auth_service = mock_auth_svc
        result = svc.apply_skill_set_permission("user1", "set1")

        assert result["all_authorized"] is False
        assert result["apply_results"]["ghost"]["success"] is False

    def test_skills_aggregated_correctly(self):
        repo = MagicMock()
        repo.get_skills_in_set.return_value = [
            {"id": "sk1", "name": "skill-a", "mcp_dependencies": [{"code": "svc1"}]},
            {"id": "sk2", "name": "skill-b", "mcp_dependencies": [{"code": "svc2"}]},
        ]
        mcp_center = MagicMock()
        mcp_center.get_mcp_detail.return_value = {
            "accessLevel": "PUBLIC",
            "tools": [],
        }
        mock_auth_svc = MagicMock()
        mock_auth_svc.apply_permission.return_value = {
            "success": True, "process_url": None, "error": None
        }
        svc = _make_svc(skill_set_repo=repo, mcp_center=mcp_center)

        svc.mcp_auth_service = mock_auth_svc
        result = svc.apply_skill_set_permission("user1", "set1")

        assert result["all_authorized"] is True
        assert result["skills"]["sk1"]["all_authorized"] is True
        assert result["skills"]["sk2"]["all_authorized"] is True

    def test_exception_during_set_apply(self):
        repo = MagicMock()
        repo.get_skills_in_set.return_value = [
            {"id": "sk1", "name": "sk", "mcp_dependencies": [{"code": "err-svc"}]}
        ]
        mcp_center = MagicMock()
        mcp_center.get_mcp_detail.side_effect = RuntimeError("kaboom")
        svc = _make_svc(skill_set_repo=repo, mcp_center=mcp_center)

        mock_auth_svc = MagicMock()
        svc.mcp_auth_service = mock_auth_svc
        result = svc.apply_skill_set_permission("user1", "set1")

        assert result["all_authorized"] is False
        assert result["apply_results"]["err-svc"]["success"] is False
