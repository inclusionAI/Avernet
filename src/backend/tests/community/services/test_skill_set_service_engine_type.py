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

    def test_get_symlink_mappings_passes_engine_type(self, skill_set_service, mock_skill_set_repo, mock_skill_repo):
        """get_symlink_mappings should pass engine_type to repo.get_all_active_skill_sets."""
        mock_skill_set_repo.get_all_active_skill_sets.return_value = []
        skill_set_service.get_symlink_mappings(user_id="u1", bolt_id="b1")
        mock_skill_set_repo.get_all_active_skill_sets.assert_called_once()
        call_kwargs = mock_skill_set_repo.get_all_active_skill_sets.call_args.kwargs
        assert call_kwargs.get("engine_type") == "claude-code"

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
