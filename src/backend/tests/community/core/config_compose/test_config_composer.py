"""Unit tests for ConfigComposer (aggregate DB state → BotConfigArtifact)."""
from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.config_compose.models import (
    CollectedFile,
    CollectedSkill,
    ComposeRequest,
    McpComposeInput,
)
from agentclaw.community.core.config_compose.services.config_composer import ConfigComposer
from agentclaw.community.core.config_compose.services.mcporter_composer import McporterComposer
from agentclaw.community.kernel.bot_config import SCHEMA_VERSION, StoreRef


SECRET = "secret-token-inlined-xyz"


class _FakeCollector:
    """In-memory ComposeInputCollector for composer tests."""

    def __init__(
        self,
        *,
        skills: list[CollectedSkill] | None = None,
        mcps: list[McpComposeInput] | None = None,
        resources: list[CollectedFile] | None = None,
        identity_files: list[CollectedFile] | None = None,
        engine_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._skills = skills or []
        self._mcps = mcps or []
        self._resources = resources or []
        self._identity = identity_files or []
        self._overrides = engine_overrides or {}

    def skills(self, req): return self._skills
    def mcps(self, req): return self._mcps
    def resources(self, req): return self._resources
    def bot_files(self, req): return getattr(self, "_bot_files", [])
    def identity_files(self, req): return self._identity
    def engine_overrides(self, req): return self._overrides


_STORES = {
    "skill-repo": StoreRef(type="oss", bucket="antsys-agentclaw-prod", base="bolt_shared/skills-repo"),
    "user-nas": StoreRef(type="nas", base="/home/admin/nfs/bot-data"),
    "bot-data": StoreRef(type="oss", bucket="antsys-agentclaw-prod", base="teclaw/prod/bolt_data"),
    "unused": StoreRef(type="oss", bucket="never-referenced"),
}


def _req(**kw) -> ComposeRequest:
    base = dict(entity_id="e1", bot_id="b1", user_id="u1", engine_type="teclaw")
    base.update(kw)
    return ComposeRequest(**base)


def _composer(collector: _FakeCollector) -> ConfigComposer:
    return ConfigComposer(
        mcporter_composer=McporterComposer(),
        collector=collector,
        stores=_STORES,
    )


def test_compose_assembles_full_artifact_from_store_path() -> None:
    # The collector now classifies each input straight to {store, path}; the
    # composer embeds them verbatim (no resolver round-trip).
    collector = _FakeCollector(
        skills=[
            CollectedSkill("team-skill", "shared", store="skill-repo", path="team/team-skill"),
            CollectedSkill("mine", "user", store="user-nas", path="mine"),
        ],
        resources=[CollectedFile("doc.md", store="user-nas", path="doc.md")],
        identity_files=[CollectedFile("RULES.md", store="user-nas", path="RULES.md")],
        engine_overrides={"temperature": 0.2},
    )
    artifact = _composer(collector).compose(_req(version=3))

    assert artifact.schema_version == SCHEMA_VERSION
    assert artifact.engine_type == "teclaw"
    assert artifact.version == 3

    # skills: {store, path} embedded verbatim, scope preserved
    assert artifact.skills[0].store == "skill-repo"
    assert artifact.skills[0].path == "team/team-skill"
    assert artifact.skills[0].scope == "shared"
    assert artifact.skills[1].store == "user-nas"
    assert artifact.skills[1].path == "mine"

    # resources + identity embedded verbatim
    assert artifact.resources[0].store == "user-nas"
    assert artifact.resources[0].path == "doc.md"
    assert artifact.identity_files[0].store == "user-nas"
    assert artifact.identity_files[0].path == "RULES.md"

    # referenced stores are embedded (skill-repo + user-nas, NOT "unused"); teclaw
    # additionally always carries "bot-data" for its promotion-time file refs.
    assert set(artifact.stores) == {"skill-repo", "user-nas", "bot-data"}
    assert artifact.stores["skill-repo"].bucket == "antsys-agentclaw-prod"

    # engine_overrides carried through verbatim
    assert artifact.engine_overrides == {"temperature": 0.2}


def test_compose_delegates_mcp_with_secret_inlined() -> None:
    mcp = {
        "server_code": "weather",
        "run_mode": "REMOTE",
        "endpoints": [
            {"networkType": "INTERNET", "env": "PROD", "transportProtocol": "STREAMABLE_HTTP", "url": "https://mcp/w"}
        ],
    }
    collector = _FakeCollector(
        mcps=[McpComposeInput(mcp_data=mcp, api_key=f"x-ling-auth={SECRET}", endpoint_env="PROD")]
    )
    artifact = _composer(collector).compose(_req())

    assert artifact.mcp.servers[0].server_code == "weather"
    # secret inlined as a header (device-path shape); no by-reference field
    assert not hasattr(artifact.mcp.servers[0], "auth_ref")
    assert artifact.mcp.servers[0].headers.get("x-ling-auth") == SECRET
    assert SECRET in json.dumps(artifact.to_dict(), ensure_ascii=False)


def test_key_only_change_yields_a_materially_different_artifact() -> None:
    """Freshness: with the key inlined, rotating ONLY the api_key changes the
    artifact bytes — so a key-only edit is delivered/re-applied (no more
    byte-identical artifact swallowing the change)."""
    mcp = {
        "server_code": "weather",
        "run_mode": "REMOTE",
        "endpoints": [
            {"networkType": "INTERNET", "env": "PROD", "transportProtocol": "STREAMABLE_HTTP", "url": "https://mcp/w"}
        ],
    }

    def _compose_with(api_key: str) -> str:
        collector = _FakeCollector(
            mcps=[McpComposeInput(mcp_data=mcp, api_key=api_key, endpoint_env="PROD")]
        )
        return json.dumps(_composer(collector).compose(_req()).to_dict(), ensure_ascii=False)

    before = _compose_with("x-ling-auth=old-key-1111")
    after = _compose_with("x-ling-auth=new-key-2222")
    assert before != after
    assert "new-key-2222" in after and "old-key-1111" not in after


def test_compose_leaves_engine_ext_empty_for_producer() -> None:
    artifact = _composer(_FakeCollector()).compose(_req())
    # composer never fetches/sets engine_ext — that is the producer's job
    assert artifact.engine_ext == {}


def test_compose_live_bot_has_no_version() -> None:
    artifact = _composer(_FakeCollector()).compose(_req(version=None))
    assert artifact.version is None


def test_compose_empty_bot_yields_empty_collections() -> None:
    artifact = _composer(_FakeCollector()).compose(_req())
    assert artifact.skills == []
    assert artifact.resources == []
    assert artifact.identity_files == []
    assert artifact.mcp.servers == []
    # teclaw always embeds the bot-data store (files are added to the artifact at
    # promotion, after compose), even when this compose sees no refs.
    assert set(artifact.stores) == {"bot-data"}
    assert artifact.engine_overrides == {}




# ── store_key_for: canonical teclaw object key (write==read==ref) ────────────


def _store_key_composer():
    return _composer(_FakeCollector())


def test_store_key_for_bot_data_path(monkeypatch):
    """A bolt_data host path → bot-data base + bolt_data-relative key."""
    import agentclaw.community.core.config_compose.services.collector as collector_mod
    from pathlib import Path

    base = "/aidesktop/aidesktop_prod/bolt_data"
    monkeypatch.setattr(collector_mod, "get_bolt_base_dir", lambda: Path(base))

    host = f"{base}/staff_u1/bot7/openclaw/workspace/MEMORY.md"
    assert (
        _store_key_composer().store_key_for(host)
        == "teclaw/prod/bolt_data/staff_u1/bot7/openclaw/workspace/MEMORY.md"
    )


def test_store_key_for_equals_artifact_ref_full_key(monkeypatch):
    """The regression guard: store_key_for == stores['bot-data'].base + '/' +
    bot_data_relpath(host) — the exact full key the artifact ref resolves to.
    Uses the REAL bot-data base (teclaw/prod/bolt_data), not bolt_base."""
    import agentclaw.community.core.config_compose.services.collector as collector_mod
    from agentclaw.community.core.config_compose.services.collector import bot_data_relpath
    from pathlib import Path

    base = "/aidesktop/aidesktop_prod/bolt_data"
    monkeypatch.setattr(collector_mod, "get_bolt_base_dir", lambda: Path(base))

    for host in [
        f"{base}/staff_u1/bot7/openclaw/workspace/data/sales.csv",      # resource
        f"{base}/staff_u1/bot7/openclaw/workspace/RULES.md",            # identity
        f"{base}/staff_u1/bot7/openclaw/workspace/skills/skills-local/s/SKILL.md",  # local skill
    ]:
        expected = f'{_STORES["bot-data"].base}/{bot_data_relpath(host)}'
        assert _store_key_composer().store_key_for(host) == expected


def test_store_key_for_non_bolt_data_path_raises(monkeypatch):
    """A path not under bolt_data must raise rather than be silently mis-keyed."""
    import agentclaw.community.core.config_compose.services.collector as collector_mod
    from pathlib import Path

    monkeypatch.setattr(
        collector_mod, "get_bolt_base_dir",
        lambda: Path("/aidesktop/aidesktop_prod/bolt_data"),
    )
    import pytest
    with pytest.raises(ValueError):
        _store_key_composer().store_key_for("/home/admin/.openclaw/workspace/skills/x/SKILL.md")


def test_store_key_for_no_bot_data_store_falls_back_to_lstrip():
    """A composer without a configured bot-data store keeps prior lstrip behavior."""
    composer = ConfigComposer(
        mcporter_composer=McporterComposer(),
        collector=_FakeCollector(),
        stores={},
    )
    assert composer.store_key_for("/aidesktop/x/y.md") == "aidesktop/x/y.md"
