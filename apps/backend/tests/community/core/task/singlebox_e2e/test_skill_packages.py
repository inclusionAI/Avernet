"""G3 验证:三个真实 skill 包可被 SkillParser 解析,frontmatter 与案例剧本 self-consistent。

这是 skill 包的静态契约测(不起 singlebox);真实端到端集成测在 G4 test_realcase_e2e.py(gated)。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentclaw.community.core.skill_center.services.skill_parser import SkillParser

SKILLS_DIR = Path(__file__).parent / "skills"


@pytest.fixture(params=["planning", "search", "acceptance"])
def skill_md(request):
    p = SKILLS_DIR / request.param / "SKILL.md"
    assert p.exists(), f"missing {p}"
    return request.param, p.read_text(encoding="utf-8")


class TestSkillPackageParses:
    def test_frontmatter_all_three(self, skill_md):
        name, text = skill_md
        info = SkillParser.parse_content(text)
        assert isinstance(info, dict)
        assert info["name"] == f"task-{name}", f"skill name 规范 mismatch: {info['name']}"
        assert info["version"] == "1.0.0"
        assert "task" in info["tags"]
        assert info["description"], "description 不能空"

    def test_case_knowledge_only_in_skills_not_framework(self):
        """AC-8 回归:案例节点名字面量只允许出现在 skills/(本目录),不出现于框架代码 core/task。"""
        case_nodes = ["N_overview", "N_market", "N_tech", "N_compete", "N_customer",
                      "N_practice_bbs", "N_report"]
        framework_dir = Path(__file__).parents[4] / "src" / "agentclaw" / "community" / "core" / "task"
        offenders: list[str] = []
        for py in framework_dir.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            try:
                txt = py.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for node in case_nodes:
                if node in txt:
                    offenders.append(f"{py.relative_to(framework_dir.parent.parent.parent.parent.parent)}: {node}")
        # 允许在 double/strategies 的提示/示例字符串出现? 否——框架零 case 知识红线。
        assert not offenders, f"框架代码出现案例节点字面量(违反零 case 知识): {offenders}"