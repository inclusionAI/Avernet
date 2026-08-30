"""Tests for the production external Space Skill source adapter."""

from pathlib import Path

from agentclaw.community.plugins.community.space_skill_source import (
    CommunitySpaceSkillSource,
)


def test_git_root_selection_excludes_nested_skill_packages(monkeypatch) -> None:
    source = CommunitySpaceSkillSource()

    def fake_run(command, *, cwd, environment):
        assert set(environment) == {
            "HOME",
            "PATH",
            "GIT_TERMINAL_PROMPT",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_ASKPASS",
        }
        if "clone" in command:
            checkout = Path(command[-1])
            checkout.mkdir()
            (checkout / "SKILL.md").write_text(
                "---\nname: root-skill\ndescription: root\n---\n"
            )
            nested = checkout / "nested"
            nested.mkdir()
            (nested / "SKILL.md").write_text(
                "---\nname: nested-skill\ndescription: nested\n---\n"
            )
            (nested / "secret.txt").write_text("not part of root package")
            return ""
        if command[-2:] == ["branch", "--show-current"]:
            return "main\n"
        return "a" * 40 + "\n"

    monkeypatch.setattr(source, "_run", fake_run)

    snapshot = source.fetch_git_snapshot(
        git_url="https://example.com/repo.git", branch=None, subdir=None
    )

    assert snapshot.source_subdir == ""
    assert [path for path, _content in snapshot.files] == ["SKILL.md"]
