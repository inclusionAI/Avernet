import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.digital_employee.history import DigitalEmployeeHistoryReader
from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError


def scenario(ext):
    bot = {"id": 17, "env": "dev", "bot_id": "bot", "owner_id": "owner", "entity_id": "owner", "active_engine": "openclaw"}
    record = SimpleNamespace(source_bot_pk=17, env="dev", ext=ext)
    engines, passport = Mock(), Mock()
    passport.query_agent_passport.return_value = {"clis": [{"cli_code": "actual-code", "cli_name": "Tool", "identity_mode": "owner"}], "mcps": []}
    return bot, record, engines, passport, DigitalEmployeeHistoryReader(engines, passport)


def test_portable_artifact_uses_frozen_membership_and_real_cli_codes():
    artifact = {"schema_version": 4, "engine_type": "teclaw", "skills": [{"name": "old", "scope": "user", "store": "local", "path": "old"}],
                "mcp": {"servers": [{"server_code": "previous-mcp"}]}, "cli_tools": [{"name": "executable-not-cli-code", "store": "local", "path": "bin/tool", "md5": "x"}]}
    bot, record, _, _, reader = scenario({"config_artifact": artifact})
    result = reader.read(bot, record)
    assert result["skills"] == [{"name": "old"}]
    assert result["mcps"][0]["mcpServerCode"] == "previous-mcp"
    assert result["clis"][0]["cliCode"] == "actual-code"


def test_file_artifact_uses_record_path_and_registered_engine_layout(tmp_path):
    (tmp_path / "activated").mkdir()
    (tmp_path / "activated" / "old-skill").mkdir()
    (tmp_path / "activated" / "old-skill" / "SKILL.md").write_text("old skill")
    (tmp_path / "servers.json").write_text(json.dumps({"mcpServers": {"old-server": {"url": "https://example.test/mcp"}, "local": {"command": "local-tool"}}}))
    bot, record, engines, _, reader = scenario({"build_target_path": str(tmp_path), "active_skill_snapshot_path": "activated"})
    engines.resolve.return_value.get_build_plan.return_value = SimpleNamespace(skill_target_relpath="activated", mcp_config_relpath="servers.json")
    result = reader.read(bot, record)
    assert result["skills"] == [{"name": "old-skill"}]
    assert result["mcps"] == [{"mcpServerCode": "old-server"}]


def test_missing_artifact_is_not_an_empty_baseline():
    bot, record, _, _, reader = scenario({})
    with pytest.raises(DigitalEmployeeError, match="产物"):
        reader.read(bot, record)


def test_rejects_another_bots_publication():
    bot, record, _, passport, reader = scenario({})
    record.source_bot_pk = 18
    with pytest.raises(DigitalEmployeeError, match="不匹配"):
        reader.read(bot, record)
    passport.query_agent_passport.assert_not_called()
