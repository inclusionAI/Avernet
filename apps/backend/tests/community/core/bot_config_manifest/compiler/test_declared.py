"""``declared_entries``: not declared vs declared empty, for sections and categories (#2862)."""

from __future__ import annotations

from agentclaw.community.core.apply_engine.constructs import ManifestCategory, ManifestSection
from agentclaw.community.core.bot_config_manifest.compiler import declared_entries


def test_script_null_is_declared_empty_and_absent_is_not_declared() -> None:
    assert declared_entries({"schema_version": 1}, ManifestSection.SCRIPT) is None
    assert declared_entries({"schema_version": 1, "script": None}, ManifestSection.SCRIPT) == []
    assert declared_entries({"script": {"body": "echo hi"}}, ManifestSection.SCRIPT) == [{"body": "echo hi"}]


def test_categories_distinguish_absent_empty_and_declared() -> None:
    parsed = {"manifest": {"mcp": [{"server_code": "gh"}], "skills": [], "resources": None}}
    assert declared_entries(parsed, ManifestCategory.MCP) == [{"server_code": "gh"}]
    assert declared_entries(parsed, ManifestCategory.SKILLS) == []
    assert declared_entries(parsed, ManifestCategory.RESOURCES) == []
    assert declared_entries(parsed, ManifestCategory.IDENTITY) is None
    assert declared_entries({}, ManifestCategory.MCP) is None
