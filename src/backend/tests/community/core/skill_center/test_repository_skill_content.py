"""Repo Skill content is the exact global SKILL.md, never a Bot-local fallback."""

from __future__ import annotations

from agentclaw.community.core.skill_center.services.skill_service import SkillService


class _Skills:
    def get_by_id(self, skill_id: str):
        if skill_id == "7":
            return {"git_path": "git://ops/report"}
        return None


def _service(repo_root):
    service = object.__new__(SkillService)
    service._skill_repo = _Skills()
    service._get_market_repo_dir = lambda: repo_root
    return service


def test_repo_content_reads_only_exact_global_skill_md(tmp_path) -> None:
    skill_dir = tmp_path / "ops" / "report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Canonical Skill", encoding="utf-8")
    (skill_dir / "README.md").write_text("# Legacy fallback", encoding="utf-8")

    assert _service(tmp_path).get_repository_skill_content("7") == "# Canonical Skill"


def test_repo_content_refuses_missing_manifest_and_path_escape(tmp_path) -> None:
    service = _service(tmp_path)
    assert service.get_repository_skill_content("7") is None
