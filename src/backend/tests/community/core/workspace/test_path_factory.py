# tests/core/workspace/test_path_factory.py
from pathlib import Path
from unittest.mock import patch
import pytest

from agentclaw.community.plugins.local.skill_repo_sync import LocalSkillRepoSyncPlugin


def _factory():
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
    return WorkspacePathFactory(skill_repo_sync=LocalSkillRepoSyncPlugin())


def test_get_bolt_base_dir_structure():
    from agentclaw.community.core.workspace.path_factory import get_bolt_base_dir
    with patch("agentclaw.community.core.workspace.path_factory._get_aidesktop_root", return_value=Path("/aidesktop")):
        with patch("agentclaw.community.core.workspace.path_factory._get_aidesktop_env", return_value="dev"):
            result = get_bolt_base_dir()
            assert result == Path("/aidesktop/aidesktop_dev/bolt_data")


def test_get_bot_dir_structure():
    from agentclaw.community.core.workspace.path_factory import get_bot_dir
    with patch("agentclaw.community.core.workspace.path_factory.get_bolt_base_dir", return_value=Path("/base")):
        result = get_bot_dir("user123", "bot456", "staff")
        assert result == Path("/base/staff_user123/bot456")


def test_get_bot_engine_dir_structure():
    from agentclaw.community.core.workspace.path_factory import get_bot_engine_dir
    with patch("agentclaw.community.core.workspace.path_factory.get_bot_dir", return_value=Path("/base/staff_u/bot")):
        result = get_bot_engine_dir("u", "bot", "openclaw", "staff")
        assert result == Path("/base/staff_u/bot/openclaw")


def test_get_bot_engine_config_dir_structure():
    from agentclaw.community.core.workspace.path_factory import get_bot_engine_config_dir
    with patch("agentclaw.community.core.workspace.path_factory.get_bot_dir", return_value=Path("/base/staff_u/bot")):
        result = get_bot_engine_config_dir("u", "bot", "openclaw", "staff")
        assert result == Path("/base/staff_u/bot/openclaw_conf")


def test_get_global_skills_repo_dir():
    from agentclaw.community.core.workspace.path_factory import get_global_skills_repo_dir
    with patch("agentclaw.community.core.workspace.path_factory.get_bolt_shared_dir", return_value=Path("/shared")):
        result = get_global_skills_repo_dir()
        assert result == Path("/shared/skills-repo")


def test_path_factory_get_bot_skills_dir():
    # LOCAL mode: factory now uses per-bot engine dir (singlebox multi-bot refactor).
    with patch("agentclaw.community.core.workspace.path_factory.get_bot_engine_dir", return_value=Path("/bot/openclaw")):
        factory = _factory()
        result = factory.get_bot_skills_dir("u", "bot", "openclaw", "staff")
        assert result == Path("/bot/openclaw") / "workspace" / "skills"


def test_entity_identity_dir_staff_openclaw():
    from agentclaw.community.core.workspace.path_factory import get_entity_identity_dir
    with patch("agentclaw.community.core.workspace.path_factory.get_bolt_base_dir", return_value=Path("/base")):
        result = get_entity_identity_dir("user123", "staff", "openclaw")
        assert result == Path("/base/staff_user123/default/openclaw/workspace")


def test_entity_identity_dir_staff_moltis():
    from agentclaw.community.core.workspace.path_factory import get_entity_identity_dir
    with patch("agentclaw.community.core.workspace.path_factory.get_bolt_base_dir", return_value=Path("/base")):
        result = get_entity_identity_dir("user123", "staff", "moltis")
        assert result == Path("/base/staff_user123/default/moltis")


def test_entity_identity_dir_proj():
    from agentclaw.community.core.workspace.path_factory import get_entity_identity_dir
    with patch("agentclaw.community.core.workspace.path_factory.get_bolt_base_dir", return_value=Path("/base")):
        result = get_entity_identity_dir("proj123", "proj", "openclaw")
        assert result == Path("/base/proj_proj123/data")
