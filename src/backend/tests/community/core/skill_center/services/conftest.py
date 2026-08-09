"""Shared fixtures for core/services tests."""
import subprocess
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.repository.protocols.skill_center import SkillSetRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository


@pytest.fixture(autouse=True)
def _isolate_global_skills_repo(monkeypatch, tmp_path):
    """Force ``_get_default_global_repo_dir`` to a non-existent path so
    ``SkillService._get_market_repo_dir`` falls back to the test's
    ``repo_dir`` instead of the developer's real ``bolt_shared`` repo.

    Without this, the tree/sync tests scan ``~/.aidesktop/.../skills-repo``
    on machines where it happens to exist (commit 75e928077 hardcoded the
    global path for prod cloud mode).
    """
    fake_global = tmp_path / "nonexistent-global-skills-repo"
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.skill_service._get_default_global_repo_dir",
        lambda: fake_global,
    )


@pytest.fixture
def mock_skill_repo_sync():
    """A MagicMock for SkillRepoSyncPlugin."""
    return MagicMock()


@pytest.fixture
def mock_skill_set_repo():
    """A MagicMock conforming to the SkillSetRepository protocol."""
    repo = MagicMock(spec=SkillSetRepository)
    repo.list_all.return_value = []
    return repo


@pytest.fixture
def mock_mcp_center():
    return MagicMock()


@pytest.fixture
def mock_mcp_config_service():
    return MagicMock()


@pytest.fixture
def mock_mcp_auth_service():
    return MagicMock()


@pytest.fixture
def mock_skill_service():
    return MagicMock()


@pytest.fixture
def mock_skill_repo():
    """A MagicMock conforming to the SkillRepository protocol."""
    repo = MagicMock(spec=SkillRepository)
    repo.list_skills.return_value = []
    repo.get_by_id.return_value = None
    repo.get_by_git_path.return_value = None
    repo.get_by_link_name.return_value = None
    repo.create.return_value = {"id": "1", "name": "test-skill"}
    repo.update.return_value = {"id": "1", "name": "test-skill"}
    repo.delete.return_value = True
    return repo


@pytest.fixture
def local_skills_bare_repo(tmp_path):
    """A local bare git repo seeded with a demo skill subtree.

    Lets integration tests clone/fetch a real `master` branch offline,
    avoiding the prod `aiworkbench.git` and the developer's ~/aiworkbench dir.
    """
    work = tmp_path / "work"
    work.mkdir()

    def git(*a):
        subprocess.run(["git", *a], cwd=work, check=True, capture_output=True)

    git("init", "-q", "-b", "master")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    d = work / "skills" / "business" / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# demo skill")
    git("add", "-A")
    git("commit", "-q", "-m", "seed skills")
    bare = tmp_path / "aiworkbench.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(work), str(bare)],
        check=True,
        capture_output=True,
    )
    return bare


@pytest.fixture
def skill_dirs(tmp_path):
    """Create temporary skill directories (active, repo, local)."""
    active_dir = tmp_path / "skills"
    repo_dir = tmp_path / "skills-repo"
    local_dir = tmp_path / "skills-local"
    active_dir.mkdir()
    repo_dir.mkdir()
    local_dir.mkdir()
    return {
        "active_dir": active_dir,
        "repo_dir": repo_dir,
        "local_dir": local_dir,
    }
