from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_public_risk.input_builder import (
    RiskInputBuilder,
    McpProjectionUnavailable,
)


BOT = {
    "id": 42,
    "bot_id": "default",
    "entity_id": "entity",
    "env": "dev",
    "owner_id": "owner",
    "creator_id": "creator",
    "bot_name": "Bot",
    "bot_desc": "description",
    "active_engine": "openclaw",
    "ext": '{"passport":{"agent_code":"agent1"}}',
}


class Reader:
    def active_capabilities(self, **kwargs):
        return SimpleNamespace(owner_id="entity", skills=(), installed_mcp_server_codes=frozenset())


class Mcps:
    def collect_bot_active_mcps(self, **kwargs):
        assert kwargs["strict_policy_context"] is True
        return [{"server_code": "default", "name": "Default"}]


class Identities:
    def list_draft_call_types(self, *args):
        return {"default": "caller"}


class Projector:
    def project(self, *, bot, entry, call_type):
        assert call_type == "CALLER"
        return {"identityMode": call_type}


def test_effective_mcp_and_bot_identity_are_built_from_current_metadata():
    builder = RiskInputBuilder(Reader(), Mcps(), Identities(), None, None, Projector())
    data, digest = builder.build(
        bot=BOT,
        target={"visibility": "public", "public_scope": "user", "view_depts": []},
    )
    agent = data["agent_info"]
    assert agent["platformAgentCode"] == "default:entity:dev"
    assert agent["version"] == "V1.0" and agent["visibility"] == "PUBLIC"
    assert agent["agentDesc"] == "description"
    assert "agentDescription" not in agent
    assert agent["resourceManifest"][0]["resourceCode"] == "default"
    assert agent["resourceManifest"][0]["resourceConfig"] == {"identityMode": "CALLER"}
    assert len(digest) == 64


def test_unavailable_mcp_projection_fails_instead_of_silently_omitting():
    builder = RiskInputBuilder(Reader(), Mcps(), Identities(), None, None, None)
    with pytest.raises(McpProjectionUnavailable):
        builder.build(bot=BOT, target={"visibility": "public", "public_scope": "user"})


def test_active_skills_use_existing_local_and_external_center_identity():
    from agentclaw.community.core.skill_center.public_center_identity import (
        PublicCenterSkillIdentity,
    )

    identity = PublicCenterSkillIdentity.derive(
        tenant="teamclaw", env="dev", skill_code="external"
    )

    class Skills:
        def active_capabilities(self, **kwargs):
            skills = (
                SimpleNamespace(skill_id=1, name="Local", git_path="local://one"),
                SimpleNamespace(skill_id=2, name="Missing", git_path="local://two"),
                SimpleNamespace(
                    skill_id=3,
                    name="Center",
                    git_path=identity.locator,
                    skill_uuid=identity.skill_uuid,
                    sc_version_number="2.3",
                ),
                SimpleNamespace(skill_id=4, name="Git", git_path="git://repo"),
            )
            return SimpleNamespace(owner_id="entity", skills=skills, installed_mcp_server_codes=frozenset())

    class Observations:
        def get_observed_incarnation_id(self, **scope):
            assert scope["owner_id"] == "entity"
            return "inc" if scope["skill_id"] == 1 else None

        def get_identity_for_asset(self, **scope):
            return SimpleNamespace(
                provider_skill_id="tc:123e4567-e89b-12d3-a456-426614174000"
            )

    builder = RiskInputBuilder(
        Skills(), Mcps(), Identities(), Observations(), None, Projector()
    )
    data, _ = builder.build(
        bot={**BOT, "bot_type": "service"},
        target={"visibility": "protected", "view_depts": []},
    )
    skills = [
        v
        for v in data["agent_info"]["resourceManifest"]
        if v["resourceType"] == "SKILL"
    ]
    assert [(v["resourceCode"], v["resourceVersion"]) for v in skills] == [
        ("external", "2.3"),
        ("tc:123e4567-e89b-12d3-a456-426614174000", "1.0"),
    ]
    assert data["missing_local_skill_ids"] == [2]
    assert data["agent_info"]["visibility"] == "AUTHORIZED"


def test_passport_fallback_and_read_failure_are_not_empty_success():
    class Passport:
        def query_agent_passport(self, bot_id, owner_workno, *, entity_id):
            assert (bot_id, owner_workno, entity_id) == ("default", "owner", "entity")
            return {"agent_code": "from-passport"}

    class NoMcps:
        def collect_bot_active_mcps(self, **kwargs):
            return []

    builder = RiskInputBuilder(Reader(), NoMcps(), Identities(), None, Passport(), Projector())
    data, _ = builder.build(bot={**BOT, "ext": None}, target={"visibility": "public"})
    assert data["agent_info"]["agentCode"] == "from-passport"

    class Broken:
        def active_capabilities(self, **kwargs):
            raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        RiskInputBuilder(
            Broken(), NoMcps(), Identities(), None, Passport(), Projector()
        ).build(bot={**BOT, "ext": None}, target={"visibility": "public"})


def test_input_digest_uses_stable_department_ids_and_owner():
    builder = RiskInputBuilder(Reader(), Mcps(), Identities(), None, None, Projector())
    target = {
        "visibility": "protected",
        "view_depts": [
            {"deptNo": "2", "deptName": "Two"},
            {"deptNo": "1", "deptName": "One"},
        ],
    }
    _, first = builder.build(bot=BOT, target=target)
    _, reordered = builder.build(
        bot=BOT,
        target={
            **target,
            "view_depts": [
                {"deptNo": "1", "deptName": "changed"},
                {"deptNo": "2", "deptName": "Two"},
            ],
        },
    )
    _, changed = builder.build(bot={**BOT, "owner_id": "other"}, target=target)
    assert first == reordered and first != changed


def test_sensitive_projection_and_missing_required_config_fail_closed():
    class Sensitive:
        def project(self, **kwargs):
            return {"headers": {"Authorization": "secret"}}

    class Empty:
        def project(self, **kwargs):
            return {}

    for projector in (Sensitive(), Empty()):
        with pytest.raises(McpProjectionUnavailable):
            RiskInputBuilder(
                Reader(), Mcps(), Identities(), None, None, projector
            ).build(bot=BOT, target={"visibility": "public"})


def test_mcp_uses_one_stable_owner_capability_snapshot():
    snapshot = SimpleNamespace(bot_id="default", owner_id="entity", skills=(), installed_mcp_server_codes=frozenset({"active"}))

    class SnapshotReader:
        def active_capabilities(self, **kwargs):
            assert kwargs["owner_id"] == "owner"
            assert kwargs["bot"] == BOT
            return snapshot

    class ScopedMcps:
        def collect_bot_active_mcps(self, **kwargs):
            assert kwargs["user_id"] == "entity"
            assert kwargs["capability_snapshot"] is snapshot
            return []

    RiskInputBuilder(SnapshotReader(), ScopedMcps(), Identities(), None, None, Projector()).build(
        bot=BOT, target={"visibility": "public"}
    )


def test_capability_read_failure_is_not_an_empty_mcp_list():
    class BrokenReader:
        def active_capabilities(self, **kwargs):
            raise RuntimeError("capability read failed")

    with pytest.raises(RuntimeError, match="capability read failed"):
        RiskInputBuilder(BrokenReader(), Mcps(), Identities(), None, None, Projector()).build(
            bot=BOT, target={"visibility": "public"}
        )


def test_real_collector_exclusions_are_scoped_to_resource_owner(monkeypatch):
    from unittest.mock import MagicMock
    from agentclaw.community.core.skill_center.services import skill_set_mcps as module

    collector = module.SkillSetMcpCollection()
    collector._reader = MagicMock()
    collector.engine_type = "openclaw"
    collector.skill_set_repo = MagicMock()
    collector.skill_set_repo.get_all_excluded_mcps.side_effect = (
        lambda user, bot: ["default"] if user == "entity" else []
    )
    collector._run_effective_mcp_stage = lambda *, operation, **kwargs: operation()
    collector._get_default_capabilities_ext_info = lambda *a, **k: None
    collector._get_default_capabilities_template_type = lambda *a, **k: None
    collector._get_all_active_skill_sets_with_default_fallback = lambda **k: []
    collector._default_set_mcp_rows = lambda *a, **k: []
    collector._non_default_effective_mcp_entries = lambda **k: []
    monkeypatch.setattr(module, "get_default_mcp_servers", lambda *a, **k: [{"server_code": "default"}])
    snapshot = SimpleNamespace(bot_id="default", owner_id="entity", skills=(), installed_mcp_server_codes=frozenset())

    class SnapshotReader:
        def active_capabilities(self, **kwargs):
            return snapshot

    data, _ = RiskInputBuilder(SnapshotReader(), collector, Identities(), None, None, Projector()).build(
        bot=BOT, target={"visibility": "public"}
    )
    assert data["agent_info"]["resourceManifest"] == []
    collector.skill_set_repo.get_all_excluded_mcps.assert_called_once_with("entity", "default")


def test_real_collector_does_not_swallow_snapshot_read_failure():
    from unittest.mock import MagicMock
    from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
    from agentclaw.community.core.skill_center.services.skill_set_mcps import SkillSetMcpCollection

    collector = SkillSetMcpCollection()
    collector._reader = MagicMock()
    collector._bot_repo = MagicMock()
    collector._bot_repo.get_by_id_and_entity.return_value = BOT
    collector._reader.active_skill_assets.side_effect = LocalSkillNotFoundError("read failed")
    # Demonstrates the legacy collector's fallback; risk must avoid it.
    assert collector._active_skill_assets(entity_id="entity", bot_id="default", user_id="owner") == ()
    collector._reader.active_capabilities.side_effect = LocalSkillNotFoundError("read failed")
    with pytest.raises(LocalSkillNotFoundError, match="read failed"):
        RiskInputBuilder(collector._reader, collector, Identities(), None, None, Projector()).build(
            bot=BOT, target={"visibility": "public"}
        )
