"""Unit tests verifying engine_type is threaded through SkillSetService to repository calls."""
import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
class TestSkillSetServiceEngineTypeThreading:
    """Verify engine_type is passed correctly from SkillSetService to repository calls."""

    @pytest.fixture
    def mock_skill_set_repo(self):
        return MagicMock()

    @pytest.fixture
    def mock_skill_repo(self):
        return MagicMock()

    @pytest.fixture
    def skill_set_service(self, mock_skill_set_repo, mock_skill_repo, tmp_path):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        service = SkillSetService(
            skill_repo=mock_skill_repo,
            skill_set_repo=mock_skill_set_repo,
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            skills_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            engine_type="claude-code",
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
            reader=MagicMock(**{"active_mcp_server_codes.return_value": frozenset()}),
        )
        return service

    def test_get_default_skill_set_passes_engine_type(self, skill_set_service, mock_skill_set_repo):
        """get_default_skill_set should pass engine_type to repo.get_default."""
        mock_skill_set_repo.get_default.return_value = {"id": "1", "name": "test"}
        skill_set_service.get_default_skill_set()
        mock_skill_set_repo.get_default.assert_called_once()
        call_kwargs = mock_skill_set_repo.get_default.call_args.kwargs
        assert "engine_type" in call_kwargs
        assert call_kwargs["engine_type"] == "claude-code"

    def test_ensure_default_skill_set_passes_engine_type(self, skill_set_service, mock_skill_set_repo):
        """ensure_default_skill_set should pass engine_type to repo.get_default."""
        mock_skill_set_repo.get_default.return_value = None
        mock_skill_set_repo.create.return_value = {"id": "1"}
        skill_set_service.ensure_default_skill_set()
        mock_skill_set_repo.get_default.assert_called_once()
        call_kwargs = mock_skill_set_repo.get_default.call_args.kwargs
        assert call_kwargs.get("engine_type") == "claude-code"

    def test_list_skill_sets_passes_engine_type(self, skill_set_service, mock_skill_set_repo):
        """list_skill_sets should pass engine_type to repo.list_all."""
        mock_skill_set_repo.list_all.return_value = []
        skill_set_service.list_skill_sets()
        mock_skill_set_repo.list_all.assert_called_once()
        call_kwargs = mock_skill_set_repo.list_all.call_args.kwargs
        assert call_kwargs.get("engine_type") == "claude-code"

    def test_get_user_default_enabled_passes_engine_type(self, skill_set_service, mock_skill_set_repo):
        """get_user_default_enabled should pass engine_type to repo._get_user_default_enabled."""
        mock_skill_set_repo._get_user_default_enabled.return_value = False
        skill_set_service.get_user_default_enabled(user_id="u1", bolt_id="b1")
        mock_skill_set_repo._get_user_default_enabled.assert_called_once()
        call_kwargs = mock_skill_set_repo._get_user_default_enabled.call_args.kwargs
        assert call_kwargs.get("engine_type") == "claude-code"

    def test_collect_bot_active_mcps_passes_engine_type(self, skill_set_service, mock_skill_set_repo):
        """collect_bot_active_mcps should pass engine_type to repo.get_all_active_skill_sets."""
        mock_skill_set_repo.get_all_active_skill_sets.return_value = []
        skill_set_service.collect_bot_active_mcps(entity_id="u1", bot_id="b1", user_id="u1")
        mock_skill_set_repo.get_all_active_skill_sets.assert_called_once()
        call_kwargs = mock_skill_set_repo.get_all_active_skill_sets.call_args.kwargs
        assert call_kwargs.get("engine_type") == "claude-code"

    def test_get_active_skill_sets_mcp_summary_passes_engine_type(self, skill_set_service, mock_skill_set_repo):
        """get_active_skill_sets_mcp_summary should pass engine_type to repo.get_all_active_skill_sets."""
        mock_skill_set_repo.get_all_active_skill_sets.return_value = []
        skill_set_service.get_active_skill_sets_mcp_summary(entity_id="u1", bot_id="b1", user_id="u1")
        mock_skill_set_repo.get_all_active_skill_sets.assert_called_once()
        call_kwargs = mock_skill_set_repo.get_all_active_skill_sets.call_args.kwargs
        assert call_kwargs.get("engine_type") == "claude-code"

    def test_get_symlink_mappings_reads_through_the_capability_reader(
        self, skill_set_service
    ):
        """get_symlink_mappings answers from the flush-then-read reader.

        Engine scoping happens inside the reader's flush (the projector test
        pins the layout-engine precedence); this seam only addresses the Bot.
        """
        reader = MagicMock()
        reader.active_skill_assets.return_value = ()
        skill_set_service._reader = reader
        skill_set_service.get_symlink_mappings(user_id="u1", bolt_id="b1")
        reader.active_skill_assets.assert_called_once_with(
            bot_id="b1", owner_id="u1"
        )

    async def test_get_symlink_mappings_keeps_known_legacy_moltis_paths(
        self, skill_set_service
    ):
        skill_set_service.engine_type = "moltis"
        skill_set_service.runtime_engine_type = "moltis"
        skill_set_service.is_desktop = True
        skill_set_service.get_active_skills = MagicMock(
            return_value=[
                {"name": "reviewer", "git_path": "git://business/reviewer"}
            ]
        )

        mappings = skill_set_service.get_symlink_mappings()

        assert [mapping.to_dict() for mapping in mappings] == [
            {
                "source": (
                    "/home/admin/.moltis/skills/"
                    "skills-repo/business/reviewer"
                ),
                "target": "/home/admin/.moltis/skills/reviewer",
                "skill_uuid": None,
                "version": None,
            }
        ]


    async def test_get_symlink_mappings_unknown_engine_falls_back_to_default(
        self, skill_set_service
    ):
        """未知引擎不报错，回落到 openclaw 默认技能目录（保留原 default 兜底语义）。

        曾误加 ``not in ... raise`` check 拦住了 teclaw 等未在 ENGINE_SKILLS_DIR_MAP
        里的引擎(它们不走容器软链，本就不该被此处拦下)。现恢复 ``.get(engine, 默认)``
        兜底，未知引擎产出指向默认目录的软链配置、不抛错。
        """
        skill_set_service.runtime_engine_type = "unknown_engine"
        skill_set_service.get_active_skills = MagicMock(
            return_value=[
                {"name": "reviewer", "git_path": "git://business/reviewer"}
            ]
        )

        mappings = skill_set_service.get_symlink_mappings()

        assert len(mappings) == 1
        m = mappings[0].to_dict()
        # git:// business/reviewer → source skills-repo/business/reviewer, target reviewer
        assert m["target"].endswith("/skills/reviewer")
        assert m["source"].endswith("/skills/skills-repo/business/reviewer")

    def test_get_all_skill_sets_with_skills_passes_engine_type(self, skill_set_service, mock_skill_set_repo):
        """get_all_skill_sets_with_skills should pass engine_type to repo.list_all."""
        mock_skill_set_repo.list_all.return_value = []
        skill_set_service.get_all_skill_sets_with_skills()
        mock_skill_set_repo.list_all.assert_called_once()
        call_kwargs = mock_skill_set_repo.list_all.call_args.kwargs
        assert call_kwargs.get("engine_type") == "claude-code"

    def test_service_stores_engine_type(self, mock_skill_set_repo, mock_skill_repo, tmp_path):
        """Service should store the engine_type passed at construction."""
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        service = SkillSetService(
            skill_repo=mock_skill_repo,
            skill_set_repo=mock_skill_set_repo,
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            skills_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            engine_type="moltis",
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        assert service.engine_type == "moltis"

    def test_service_defaults_to_openclaw(self, mock_skill_set_repo, mock_skill_repo, tmp_path):
        """Service should default to openclaw when no engine_type is provided."""
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        service = SkillSetService(
            skill_repo=mock_skill_repo,
            skill_set_repo=mock_skill_set_repo,
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            skills_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        assert service.engine_type == "openclaw"

    async def test_remove_skill_from_set_passes_engine_type(self, skill_set_service, mock_skill_set_repo):
        """remove_skill_from_set should pass engine_type to repo.get_all_active_skill_sets."""
        mock_skill_set_repo.get_by_id.return_value = {"id": "1", "is_default": False, "bolt_id": "b1"}
        mock_skill_set_repo.remove_skill_from_set.return_value = True
        mock_skill_set_repo.get_all_active_skill_sets.return_value = []
        await skill_set_service.remove_skill_from_set("1", "s1")
        mock_skill_set_repo.get_all_active_skill_sets.assert_called_once()
        call_kwargs = mock_skill_set_repo.get_all_active_skill_sets.call_args.kwargs
        assert call_kwargs.get("engine_type") == "claude-code"


def _make_skill_set_service_for_default_selection(
    tmp_path, *, engine_type, runtime_engine_type, default_skill_set_selection_policy=None
):
    from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService

    return SkillSetService(
        skill_repo=MagicMock(),
        skill_set_repo=MagicMock(),
        mcp_center=MagicMock(),
        mcp_config_service=MagicMock(),
        skill_service=MagicMock(),
        skills_dir=tmp_path / "skills",
        repo_dir=tmp_path / "skills-repo",
        local_dir=tmp_path / "skills-local",
        engine_type=engine_type,
        runtime_engine_type=runtime_engine_type,
        bot_repo=MagicMock(),
        default_skill_set_selection_policy=default_skill_set_selection_policy,
        path_factory=MagicMock(),
    )


def test_default_skill_set_query_kwargs_keeps_openclaw_query_shape(tmp_path):
    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="openclaw",
        runtime_engine_type="openclaw",
    )

    assert service._default_skill_set_query_kwargs() == {}


def test_default_skill_set_query_kwargs_keeps_normal_claude_code_query_shape(tmp_path):
    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="claude_code",
        runtime_engine_type="claude_code",
    )

    assert service._default_skill_set_query_kwargs() == {}


def test_default_skill_set_query_kwargs_routes_claude_code_null_default_to_aicoding(tmp_path):
    from agentclaw.community.core.bot_management.engines.registry import (
        get_default_skill_set_selection_policy,
    )

    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="claude_code",
        runtime_engine_type="aicoding",
        default_skill_set_selection_policy=get_default_skill_set_selection_policy(),
    )

    assert service._default_skill_set_query_kwargs() == {
        "default_skill_set_bolt_id": None,
        "default_skill_set_engine_type": "aicoding",
    }


def test_active_skill_sets_falls_back_to_aicoding_legacy_null_then_claude_code_default(tmp_path):
    from agentclaw.community.core.bot_management.engines.registry import (
        get_default_skill_set_selection_policy,
    )

    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="claude_code",
        runtime_engine_type="aicoding",
        default_skill_set_selection_policy=get_default_skill_set_selection_policy(),
    )
    repo = service.skill_set_repo
    repo.get_all_active_skill_sets.side_effect = [
        [{"id": "custom-1", "is_default": False}],
        [{"id": "custom-1", "is_default": False}],
        [{"id": "global-claude", "is_default": True}],
    ]

    result = service.list_active_skill_sets(user_id="owner", bolt_id="bot")

    assert result == [{"id": "global-claude", "is_default": True}]
    assert repo.get_all_active_skill_sets.call_count == 3
    first_kwargs = repo.get_all_active_skill_sets.call_args_list[0].kwargs
    second_kwargs = repo.get_all_active_skill_sets.call_args_list[1].kwargs
    third_kwargs = repo.get_all_active_skill_sets.call_args_list[2].kwargs
    assert first_kwargs["default_skill_set_bolt_id"] == "bot"
    assert first_kwargs["default_skill_set_engine_type"] == "claude_code"
    assert second_kwargs["default_skill_set_bolt_id"] is None
    assert second_kwargs["default_skill_set_engine_type"] == "aicoding"
    assert "default_skill_set_bolt_id" not in third_kwargs
    assert "default_skill_set_engine_type" not in third_kwargs


def test_active_skill_sets_stops_when_bot_default_exists(tmp_path):
    from agentclaw.community.core.bot_management.engines.registry import (
        get_default_skill_set_selection_policy,
    )

    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="claude_code",
        runtime_engine_type="aicoding",
        default_skill_set_selection_policy=get_default_skill_set_selection_policy(),
    )
    repo = service.skill_set_repo
    repo.get_all_active_skill_sets.return_value = [{"id": "bot-default", "is_default": True}]

    result = service.list_active_skill_sets(user_id="owner", bolt_id="bot")

    assert result == [{"id": "bot-default", "is_default": True}]
    assert repo.get_all_active_skill_sets.call_count == 1


def test_active_skill_sets_returns_first_result_when_no_default_candidate_exists(tmp_path):
    from agentclaw.community.core.bot_management.engines.registry import (
        get_default_skill_set_selection_policy,
    )

    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="claude_code",
        runtime_engine_type="aicoding",
        default_skill_set_selection_policy=get_default_skill_set_selection_policy(),
    )
    repo = service.skill_set_repo
    first = [{"id": "custom-1", "is_default": False}]
    repo.get_all_active_skill_sets.side_effect = [
        first,
        [{"id": "custom-2", "is_default": False}],
        [{"id": "custom-3", "is_default": False}],
    ]

    result = service.list_active_skill_sets(user_id="owner", bolt_id="bot")

    assert result == first
    assert repo.get_all_active_skill_sets.call_count == 3



def test_list_skill_sets_uses_aicoding_scoped_default_candidates(tmp_path):
    from agentclaw.community.core.bot_management.engines.registry import (
        get_default_skill_set_selection_policy,
    )

    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="claude_code",
        runtime_engine_type="aicoding",
        default_skill_set_selection_policy=get_default_skill_set_selection_policy(),
    )
    repo = service.skill_set_repo
    repo.list_all.side_effect = [
        [{"id": "custom-1", "is_default": False}],
        [{"id": "global-aicoding", "is_default": True}],
    ]

    result = service.list_skill_sets(user_id="owner", bolt_id="bot")

    assert result == [{"id": "global-aicoding", "is_default": True}]
    assert repo.list_all.call_count == 2
    first_kwargs = repo.list_all.call_args_list[0].kwargs
    second_kwargs = repo.list_all.call_args_list[1].kwargs
    assert first_kwargs["bolt_id"] == "bot"
    assert first_kwargs["engine_type"] == "claude_code"
    assert first_kwargs["default_skill_set_bolt_id"] == "bot"
    assert first_kwargs["default_skill_set_engine_type"] == "claude_code"
    assert second_kwargs["bolt_id"] == "bot"
    assert second_kwargs["engine_type"] == "claude_code"
    assert second_kwargs["default_skill_set_bolt_id"] is None
    assert second_kwargs["default_skill_set_engine_type"] == "aicoding"



def test_list_skill_sets_keeps_normal_claude_code_query_shape_with_bot_id(tmp_path):
    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="claude_code",
        runtime_engine_type="claude_code",
    )
    repo = service.skill_set_repo
    repo.list_all.return_value = []

    assert service.list_skill_sets(user_id="owner", bolt_id="bot") == []

    repo.list_all.assert_called_once()
    call_args = repo.list_all.call_args.args
    call_kwargs = repo.list_all.call_args.kwargs
    assert call_args == ("owner",)
    assert call_kwargs["bolt_id"] == "bot"
    assert call_kwargs["engine_type"] == "claude_code"
    assert "default_skill_set_bolt_id" not in call_kwargs
    assert "default_skill_set_engine_type" not in call_kwargs


def test_list_skill_sets_keeps_openclaw_query_shape_with_bot_id(tmp_path):
    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="openclaw",
        runtime_engine_type="openclaw",
    )
    repo = service.skill_set_repo
    repo.list_all.return_value = []

    assert service.list_skill_sets(user_id="owner", bolt_id="bot") == []

    repo.list_all.assert_called_once()
    call_args = repo.list_all.call_args.args
    call_kwargs = repo.list_all.call_args.kwargs
    assert call_args == ("owner",)
    assert call_kwargs["bolt_id"] == "bot"
    assert call_kwargs["engine_type"] == "openclaw"
    assert "default_skill_set_bolt_id" not in call_kwargs
    assert "default_skill_set_engine_type" not in call_kwargs


def test_get_all_skill_sets_with_mcps_keeps_direct_list_all_for_routed_aicoding(tmp_path):
    from agentclaw.community.core.bot_management.engines.registry import (
        get_default_skill_set_selection_policy,
    )

    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="claude_code",
        runtime_engine_type="aicoding",
        default_skill_set_selection_policy=get_default_skill_set_selection_policy(),
    )
    service.entity_id = "owner"
    repo = service.skill_set_repo
    repo.list_all.return_value = [
        {"id": "1", "is_default": False, "gmt_created": "2026-01-01"}
    ]
    repo.get_by_id.return_value = {"id": "1", "is_default": False}
    repo.get_mcp_servers_in_set.return_value = []

    result = service.get_all_skill_sets_with_mcps(user_id="owner", bolt_id="bot")

    assert [item["id"] for item in result] == ["1"]
    repo.list_all.assert_called_once()
    call_kwargs = repo.list_all.call_args.kwargs
    assert call_kwargs["user_id"] == "owner"
    assert call_kwargs["bolt_id"] == "bot"
    assert call_kwargs["engine_type"] == "claude_code"
    assert "default_skill_set_bolt_id" not in call_kwargs
    assert "default_skill_set_engine_type" not in call_kwargs
    repo.get_mcp_servers_in_set.assert_called_once_with("1")

def test_env_active_skill_sets_fallback_stops_when_default_exists(tmp_path):
    from agentclaw.community.core.bot_management.engines.registry import (
        get_default_skill_set_selection_policy,
    )

    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="claude_code",
        runtime_engine_type="aicoding",
        default_skill_set_selection_policy=get_default_skill_set_selection_policy(),
    )
    repo = service.skill_set_repo
    repo.get_all_active_skill_sets_for_env.side_effect = [
        [{"id": "custom-1", "is_default": False}],
        [{"id": "global-aicoding", "is_default": True}],
    ]

    result = service._get_all_active_skill_sets_for_env_with_default_fallback(
        user_id="owner",
        bolt_id="bot",
        engine_type="claude_code",
        env="pre",
    )

    assert result == [{"id": "global-aicoding", "is_default": True}]
    assert repo.get_all_active_skill_sets_for_env.call_count == 2
    first_kwargs = repo.get_all_active_skill_sets_for_env.call_args_list[0].kwargs
    second_kwargs = repo.get_all_active_skill_sets_for_env.call_args_list[1].kwargs
    assert first_kwargs["default_skill_set_bolt_id"] == "bot"
    assert first_kwargs["default_skill_set_engine_type"] == "claude_code"
    assert first_kwargs["env"] == "pre"
    assert second_kwargs["default_skill_set_bolt_id"] is None
    assert second_kwargs["default_skill_set_engine_type"] == "aicoding"
    assert second_kwargs["env"] == "pre"


def test_env_active_skill_sets_returns_first_result_when_no_default_candidate_exists(tmp_path):
    from agentclaw.community.core.bot_management.engines.registry import (
        get_default_skill_set_selection_policy,
    )

    service = _make_skill_set_service_for_default_selection(
        tmp_path,
        engine_type="claude_code",
        runtime_engine_type="aicoding",
        default_skill_set_selection_policy=get_default_skill_set_selection_policy(),
    )
    repo = service.skill_set_repo
    first = [{"id": "custom-1", "is_default": False}]
    repo.get_all_active_skill_sets_for_env.side_effect = [
        first,
        [{"id": "custom-2", "is_default": False}],
        [{"id": "custom-3", "is_default": False}],
    ]

    result = service._get_all_active_skill_sets_for_env_with_default_fallback(
        user_id="owner",
        bolt_id="bot",
        engine_type="claude_code",
        env="prod",
    )

    assert result == first
    assert repo.get_all_active_skill_sets_for_env.call_count == 3
