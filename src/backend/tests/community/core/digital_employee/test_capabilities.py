from types import SimpleNamespace
from unittest.mock import Mock

from agentclaw.community.core.digital_employee.capabilities import DigitalEmployeeCapabilityReader


def scenario():
    reader, skills, packages, identities, mcps, passport, scan, history, engines, provider = [Mock() for _ in range(10)]
    reader.active_capabilities.return_value = SimpleNamespace(skills=(), installed_mcp_server_codes=frozenset({"installed"}))
    identities.list_draft_call_types.return_value = {}
    passport.query_agent_passport.return_value = {"agent_code": "agent", "mcps": [], "clis": []}
    mcps.get_mcp_detail.side_effect = lambda code: {"accessLevel": "PUBLIC", "name": code}
    service = DigitalEmployeeCapabilityReader(reader, skills, packages, identities, mcps, passport, scan, history, engines, provider)
    bot = {"id": 17, "env": "dev", "bot_id": "bot", "owner_id": "owner", "entity_id": "owner", "active_engine": "openclaw"}
    return SimpleNamespace(**locals())


def test_approval_uses_effective_policy_and_dependency_mcps():
    s = scenario()
    s.provider.collect_bot_active_mcps.return_value = [{"server_code": code} for code in ["installed", "policy", "dependency"]]
    snapshot = s.service.read(s.bot)
    assert {item["mcpServerCode"] for item in snapshot["capabilities"]["mcps"]} == {"installed", "policy", "dependency"}


def test_restored_portable_skill_uses_frozen_content_location():
    s = scenario()
    artifact = {"schema_version": 4, "engine_type": "teclaw", "stores": {"skill-repo": {"type": "oss", "bucket": "bucket", "base": "frozen/42"}},
                "skills": [{"name": "old", "scope": "user", "store": "skill-repo", "path": "tools/old"}]}
    record = SimpleNamespace(source_bot_pk=17, env="dev", ext={"config_artifact": artifact})
    s.history.read.return_value = {"skills": [{"name": "old"}], "mcps": [], "clis": []}
    s.skills.get_by_git_path.return_value = {"id": 9, "description": "old"}
    s.scan.from_store.return_value = {"url": "https://example.test/package", "sha256": "a" * 64, "key": "immutable-package"}
    s.scan.sign.return_value = "https://example.test/refreshed"
    result = s.service.read_published(s.bot, record)
    args = s.scan.from_store.call_args.args
    assert args[2].base == "frozen/42" and args[3] == "tools/old"
    assert result["capabilities"]["skills"][0]["ossAddress"] == "https://example.test/refreshed"
    assert result["package_hashes"] == {"9": "a" * 64}
    s.reader.active_capabilities.assert_not_called()
