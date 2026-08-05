"""Regression coverage for per-Bot skill-set ownership."""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def skill_set_service(tmp_path):
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetService,
    )

    skill_repo = MagicMock()
    skill_set_repo = MagicMock()
    return SkillSetService(
        skill_repo=skill_repo,
        skill_set_repo=skill_set_repo,
        mcp_center=MagicMock(),
        mcp_config_service=MagicMock(),
        skill_service=MagicMock(),
        bot_repo=MagicMock(),
        skills_dir=tmp_path / "skills",
        repo_dir=tmp_path / "skills-repo",
        local_dir=tmp_path / "skills-local",
        bot_id="target-bot",
        entity_id="owner",
        engine_type="openclaw",
        path_factory=MagicMock(),
    )


@pytest.mark.asyncio
async def test_add_skills_to_set_rejects_skill_owned_by_another_bot(skill_set_service):
    """A local skill may only be associated with its owning Bot's skill set."""
    skill_set_service.skill_set_repo.get_by_id.return_value = {
        "id": "10",
        "bolt_id": "target-bot",
        "is_default": False,
        "is_active": False,
    }
    skill_set_service.skill_set_repo.get_skills_in_set.return_value = []
    skill_set_service.skill_set_repo.list_all.return_value = []
    skill_set_service.skill_repo.get_by_id.return_value = {
        "id": "20",
        "name": "local-skill",
        "bolt_id": "other-bot",
        "git_path": "local:///skills-local/local-skill",
    }

    with patch(
        "agentclaw.community.core.skill_center.services.skill_set_service"
        ".SkillSetMetadataWriter.write_metadata"
    ):
        result = await skill_set_service.add_skills_to_set(
            "10", ["20"], user_id="owner"
        )

    assert result["success"] == []
    assert result["failed"] == [
        {
            "skill_id": "20",
            "error": "Skill belongs to another bot",
        }
    ]
    skill_set_service.skill_set_repo.add_skill_to_set.assert_not_called()
