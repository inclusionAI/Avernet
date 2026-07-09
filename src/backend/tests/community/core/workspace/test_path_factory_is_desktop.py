"""Test path_factory.get_bot_skills_{local,repo}_dir routing by is_desktop."""
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.plugins.local.skill_repo_sync import LocalSkillRepoSyncPlugin


def _factory() -> WorkspacePathFactory:
    return WorkspacePathFactory(skill_repo_sync=LocalSkillRepoSyncPlugin())


def test_local_dir_desktop_returns_engine_view():
    factory = _factory()
    p = factory.get_bot_skills_local_dir(
        "user_001", "bot_x", "openclaw", "staff", is_desktop=True
    )
    assert str(p) == "/home/admin/.openclaw/workspace/skills/skills-local"


def test_repo_dir_desktop_returns_engine_view():
    factory = _factory()
    p = factory.get_bot_skills_repo_dir(
        "user_001", "bot_x", "openclaw", "staff", is_desktop=True
    )
    assert str(p) == "/home/admin/.openclaw/workspace/skills/skills-repo"


def test_local_dir_non_desktop_falls_to_cloud_or_local():
    factory = _factory()
    p = factory.get_bot_skills_local_dir(
        "user_001", "bot_x", "openclaw", "staff", is_desktop=False
    )
    # Must NOT be the BAAS engine-view root — that path is desktop-only
    assert "skills-local" in str(p)
    assert not str(p).startswith("/home/admin/.openclaw/workspace/skills/skills-local")


def test_local_dir_default_is_non_desktop():
    """Omitting is_desktop must NOT silently activate the desktop branch."""
    factory = _factory()
    p_default = factory.get_bot_skills_local_dir("u", "b", "openclaw", "staff")
    p_explicit_false = factory.get_bot_skills_local_dir(
        "u", "b", "openclaw", "staff", is_desktop=False
    )
    assert str(p_default) == str(p_explicit_false)


def test_local_dir_teclaw_returns_minimal_logical():
    """teclaw bots get the minimal logical 'skills-local' (engine owns the files)."""
    factory = _factory()
    p = factory.get_bot_skills_local_dir(
        "user_001", "bot_x", "teclaw", "staff", is_teclaw=True
    )
    assert str(p) == "skills-local"


def test_local_dir_teclaw_takes_precedence_over_desktop():
    factory = _factory()
    p = factory.get_bot_skills_local_dir(
        "user_001", "bot_x", "teclaw", "staff", is_desktop=True, is_teclaw=True
    )
    assert str(p) == "skills-local"


def test_local_dir_default_is_non_teclaw():
    """Omitting is_teclaw must NOT silently activate the teclaw branch."""
    factory = _factory()
    p_default = factory.get_bot_skills_local_dir("u", "b", "openclaw", "staff")
    assert str(p_default) != "skills-local"
