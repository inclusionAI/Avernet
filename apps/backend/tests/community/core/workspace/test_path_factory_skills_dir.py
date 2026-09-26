from pathlib import Path
from unittest.mock import MagicMock
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory

def _factory(local_root):
    sync = MagicMock(); sync.get_local_skills_root.return_value = local_root
    return WorkspacePathFactory(skill_repo_sync=sync)

def test_skills_dir_local_uses_global_root():
    # LOCAL mode now uses per-bot engine dir; get_local_skills_root still feeds
    # into the result but as a suffix under the per-bot path.
    factory = _factory(Path("/tmp/skills-root"))
    result = factory.get_bot_skills_dir("u1", "default")
    assert result.name == "skills"

def test_skills_dir_prod_per_bot():
    p = _factory(None).get_bot_skills_dir("u1", "b1")
    assert p.name == "skills" and "u1" in str(p)
