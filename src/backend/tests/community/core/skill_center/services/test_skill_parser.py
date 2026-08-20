"""Tests for SkillParser extracted to core/services/skill_parser.py"""

from pathlib import Path


from agentclaw.community.core.skill_center.services.skill_parser import SkillParser


# ============================================================================
# TestSkillParserFindFile
# ============================================================================


class TestSkillParserFindFile:
    """Tests for SkillParser.find_skill_file"""

    def test_find_skill_md(self, tmp_path: Path):
        """目录有 SKILL.md 时返回它"""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# Test Skill\n", encoding="utf-8")
        result = SkillParser.find_skill_file(tmp_path)
        assert result == skill_md

    def test_find_readme_fallback(self, tmp_path: Path):
        """只有 README.md 时返回它"""
        readme = tmp_path / "README.md"
        readme.write_text("# Test Skill\n", encoding="utf-8")
        result = SkillParser.find_skill_file(tmp_path)
        assert result == readme

    def test_find_no_file(self, tmp_path: Path):
        """空目录返回 None"""
        result = SkillParser.find_skill_file(tmp_path)
        assert result is None

    def test_skill_md_takes_priority(self, tmp_path: Path):
        """两个文件都存在时优先 SKILL.md"""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# From SKILL.md\n", encoding="utf-8")
        readme = tmp_path / "README.md"
        readme.write_text("# From README.md\n", encoding="utf-8")
        result = SkillParser.find_skill_file(tmp_path)
        assert result == skill_md


# ============================================================================
# TestSkillParserHasSkillFile
# ============================================================================


class TestSkillParserHasSkillFile:
    """Tests for SkillParser.has_skill_file"""

    def test_has_skill_md(self, tmp_path: Path):
        """有 SKILL.md 返回 True"""
        (tmp_path / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        assert SkillParser.has_skill_file(tmp_path) is True

    def test_readme_only_returns_false(self, tmp_path: Path):
        """只有 README.md 返回 False"""
        (tmp_path / "README.md").write_text("# Readme\n", encoding="utf-8")
        assert SkillParser.has_skill_file(tmp_path) is False

    def test_empty_dir_returns_false(self, tmp_path: Path):
        """空目录返回 False"""
        assert SkillParser.has_skill_file(tmp_path) is False


# ============================================================================
# TestSkillParserParse
# ============================================================================


class TestSkillParserParse:
    """Tests for SkillParser.parse"""

    def test_parse_yaml_frontmatter(self, tmp_path: Path):
        """解析包含 YAML frontmatter 的 SKILL.md"""
        content = """\
---
name: My Skill
description: A test skill
version: "2.0.0"
category: Productivity
author: tester
tags:
  - test
  - demo
---

# My Skill

Some body text.

## Setup

Setup instructions.

## Usage

Usage instructions.
"""
        (tmp_path / "SKILL.md").write_text(content, encoding="utf-8")
        result = SkillParser.parse(tmp_path)

        assert result is not None
        assert result["name"] == "My Skill"
        assert result["description"] == "A test skill"
        assert result["version"] == "2.0.0"
        assert result["category"] == "general"  # category 不再从 SKILL.md 解析，使用默认值
        assert result["author"] == "tester"
        assert result["tags"] == ["test", "demo"]
        assert len(result["capabilities"]) == 2
        assert result["capabilities"][0]["name"] == "Setup"
        assert result["capabilities"][1]["name"] == "Usage"
        assert "created_at" in result
        assert "updated_at" in result

    def test_parse_nonexistent_dir(self, tmp_path: Path):
        """不存在的目录返回 None"""
        nonexistent = tmp_path / "does_not_exist"
        result = SkillParser.parse(nonexistent)
        assert result is None

    def test_parse_gbk_encoded_skill_md(self, tmp_path: Path):
        """GBK 编码的 SKILL.md 应正确解析 frontmatter 字段"""
        content = """\
---
name: 安全检查
description: 检查应用安全合规性，通过GuardrailsConfirmServiceSPI接口验证
version: "1.0.0"
author: test
---

# 安全检查技能
"""
        (tmp_path / "SKILL.md").write_bytes(content.encode("gbk"))
        result = SkillParser.parse(tmp_path)

        assert result is not None
        assert result["name"] == "安全检查"
        assert "GuardrailsConfirmServiceSPI" in result["description"]

    def test_parse_gbk_encoded_no_crash(self, tmp_path: Path):
        """GBK 编码文件不应抛异常"""
        content = "---\nname: 测试技能\ndescription: 这是GBK编码\n---\n"
        (tmp_path / "SKILL.md").write_bytes(content.encode("gbk"))
        result = SkillParser.parse(tmp_path)
        assert result is not None
        assert isinstance(result["description"], str)


# ============================================================================
# TestSkillParserParseContent
# ============================================================================


class TestSkillParserParseContent:
    """Tests for SkillParser.parse_content"""

    def test_parse_content_with_frontmatter(self):
        """从字符串解析 YAML frontmatter"""
        content = """\
---
name: Remote Skill
description: A remotely fetched skill
version: "3.0.0"
category: Tools
author: remote_author
tags: [alpha, beta]
---

# Remote Skill

Body text here.
"""
        result = SkillParser.parse_content(content)

        assert result is not None
        assert result["name"] == "Remote Skill"
        assert result["description"] == "A remotely fetched skill"
        assert result["version"] == "3.0.0"
        assert result["category"] == "general"  # category 不再从 SKILL.md 解析，使用默认值
        assert result["author"] == "remote_author"
        assert result["tags"] == ["alpha", "beta"]

    def test_parse_content_empty_string(self):
        """空字符串返回 None"""
        result = SkillParser.parse_content("")
        assert result is None
