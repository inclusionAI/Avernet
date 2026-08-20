"""Tests for the strict SKILL.md parser contract."""

from pathlib import Path

import pytest

from agentclaw.community.core.skill_center.services.skill_parser import (
    SkillManifestError,
    SkillParser,
)


def _manifest(name: str, description: str = "A test skill", **extra: object) -> str:
    lines = ["---", f"name: {name}", f"description: {description}"]
    lines.extend(f"{key}: {value}" for key, value in extra.items())
    return "\n".join([*lines, "---", "", f"# {name}", ""])


class TestSkillParserFindFile:
    def test_find_skill_md(self, tmp_path: Path):
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# Test Skill\n", encoding="utf-8")
        assert SkillParser.find_skill_file(tmp_path) == skill_md

    def test_readme_is_not_a_manifest(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Test Skill\n", encoding="utf-8")
        assert SkillParser.find_skill_file(tmp_path) is None

    def test_find_no_file(self, tmp_path: Path):
        assert SkillParser.find_skill_file(tmp_path) is None

    def test_skill_md_takes_priority(self, tmp_path: Path):
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# From SKILL.md\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# From README.md\n", encoding="utf-8")
        assert SkillParser.find_skill_file(tmp_path) == skill_md


class TestSkillParserHasSkillFile:
    def test_has_skill_md(self, tmp_path: Path):
        (tmp_path / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        assert SkillParser.has_skill_file(tmp_path) is True

    def test_readme_only_returns_false(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Readme\n", encoding="utf-8")
        assert SkillParser.has_skill_file(tmp_path) is False

    def test_empty_dir_returns_false(self, tmp_path: Path):
        assert SkillParser.has_skill_file(tmp_path) is False


class TestSkillParserParse:
    def test_parse_yaml_frontmatter(self, tmp_path: Path):
        skill_path = tmp_path / "my-skill"
        skill_path.mkdir()
        content = """\
---
name: my-skill
description: A test skill
version: "2.0.0"
category: Productivity
author: tester
tags:
  - test
  - demo
---

# My Skill

## Setup

Setup instructions.

## Usage

Usage instructions.
"""
        (skill_path / "SKILL.md").write_text(content, encoding="utf-8")

        result = SkillParser.parse(skill_path)

        assert result is not None
        assert result["name"] == "my-skill"
        assert result["description"] == "A test skill"
        assert result["version"] == "2.0.0"
        assert result["category"] == "general"
        assert result["author"] == "tester"
        assert result["tags"] == ["test", "demo"]
        assert [item["name"] for item in result["capabilities"]] == ["Setup", "Usage"]
        assert "created_at" in result
        assert "updated_at" in result

    def test_parse_nonexistent_dir(self, tmp_path: Path):
        assert SkillParser.parse(tmp_path / "does-not-exist") is None

    def test_parse_gbk_encoded_skill_md(self, tmp_path: Path):
        skill_path = tmp_path / "security-check"
        skill_path.mkdir()
        content = _manifest(
            "security-check",
            "检查应用安全合规性，通过GuardrailsConfirmServiceSPI接口验证",
        )
        (skill_path / "SKILL.md").write_bytes(content.encode("gbk"))

        result = SkillParser.parse(skill_path)

        assert result is not None
        assert result["name"] == "security-check"
        assert "GuardrailsConfirmServiceSPI" in result["description"]

    def test_parse_reports_file_read_errors(self, tmp_path: Path, monkeypatch):
        skill_path = tmp_path / "broken-skill"
        skill_path.mkdir()
        skill_file = skill_path / "SKILL.md"
        skill_file.write_text(_manifest("broken-skill"), encoding="utf-8")

        original_read_bytes = Path.read_bytes

        def fail_read(path):
            if path == skill_file:
                raise OSError("read failed")
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", fail_read)

        with pytest.raises(SkillManifestError) as exc_info:
            SkillParser.parse(skill_path)

        assert exc_info.value.code == "SKILL_FILE_READ_ERROR"

    def test_parse_rejects_directory_name_mismatch(self, tmp_path: Path):
        skill_path = tmp_path / "folder-name"
        skill_path.mkdir()
        (skill_path / "SKILL.md").write_text(
            _manifest("manifest-name"), encoding="utf-8"
        )

        with pytest.raises(SkillManifestError) as exc_info:
            SkillParser.parse(skill_path)

        assert exc_info.value.code == "NAME_DIRECTORY_MISMATCH"
        assert exc_info.value.field == "name"


class TestSkillParserDecodeContent:
    def test_decode_utf8_and_gbk(self):
        assert "中文" in SkillParser.decode_content("中文".encode())
        assert "中文" in SkillParser.decode_content("中文".encode("gbk"))

    def test_decode_rejects_bytes_invalid_in_utf8_and_gbk(self):
        with pytest.raises(SkillManifestError) as exc_info:
            SkillParser.decode_content(b"\x81\x30")

        assert exc_info.value.code == "INVALID_ENCODING"


class TestSkillParserParseContent:
    def test_parse_content_with_frontmatter(self):
        content = """\
---
name: remote-skill
description: A remotely fetched skill
version: "3.0.0"
category: Tools
author: remote_author
tags: alpha, beta
---

# Remote Skill
"""
        result = SkillParser.parse_content(content)

        assert result is not None
        assert result["name"] == "remote-skill"
        assert result["description"] == "A remotely fetched skill"
        assert result["version"] == "3.0.0"
        assert result["category"] == "general"
        assert result["author"] == "remote_author"
        assert result["tags"] == ["alpha", "beta"]

    def test_parse_content_accepts_utf8_bom(self):
        result = SkillParser.parse_content("\ufeff" + _manifest("bom-skill"))
        assert result is not None
        assert result["name"] == "bom-skill"

    def test_parse_content_empty_string(self):
        assert SkillParser.parse_content("") is None

    @pytest.mark.parametrize(
        ("content", "code", "field"),
        [
            ("name: no-frontmatter\n", "MISSING_FRONTMATTER", None),
            ("---\nname: unclosed\n", "INVALID_FRONTMATTER", None),
            ("---\nname: [invalid\n---\n", "INVALID_FRONTMATTER", None),
            ("---\n- item\n---\n", "INVALID_FRONTMATTER", None),
            ("---\ndescription: value\n---\n", "MISSING_NAME", "name"),
            ("---\nname: valid\n---\n", "MISSING_DESCRIPTION", "description"),
            ("---\nname: 123\ndescription: value\n---\n", "INVALID_NAME_TYPE", "name"),
            (
                "---\nname: valid\ndescription: []\n---\n",
                "INVALID_DESCRIPTION_TYPE",
                "description",
            ),
            ("---\nname: '   '\ndescription: value\n---\n", "EMPTY_NAME", "name"),
            (
                "---\nname: valid\ndescription: '   '\n---\n",
                "EMPTY_DESCRIPTION",
                "description",
            ),
        ],
    )
    def test_parse_content_reports_manifest_errors(self, content, code, field):
        with pytest.raises(SkillManifestError) as exc_info:
            SkillParser.parse_content(content)

        assert exc_info.value.code == code
        assert exc_info.value.field == field

    @pytest.mark.parametrize(
        "name",
        ["My Skill", "Remote_Skill", "安全检查", "测试技能"],
    )
    def test_parse_content_keeps_legacy_skill_names(self, name):
        result = SkillParser.parse_content(_manifest(name))

        assert result is not None
        assert result["name"] == name
