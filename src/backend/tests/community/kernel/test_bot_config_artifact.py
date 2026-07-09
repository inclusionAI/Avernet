"""Unit tests for the published ``BotConfigArtifact`` contract.

Covers: serialization round-trip, and conformance of produced artifacts to
the language-neutral source of truth ``artifact.schema.json`` (the same schema
the external engine owner codes against — Task 16 also reuses this oracle).
"""
from __future__ import annotations

import json
import pathlib

import jsonschema
import pytest

from agentclaw.community.kernel.bot_config import (
    SCHEMA_VERSION,
    BotConfigArtifact,
    FileRef,
    McpManifest,
    McpServerRef,
    ResourceRef,
    SkillRef,
    StoreRef,
)

_SCHEMA_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "src"
    / "agentclaw"
    / "community"
    / "kernel"
    / "bot_config"
    / "artifact.schema.json"
)


def _sample_artifact() -> BotConfigArtifact:
    return BotConfigArtifact(
        schema_version=SCHEMA_VERSION,
        engine_type="teclaw",
        version=7,
        mcp=McpManifest(
            servers=[
                McpServerRef(
                    server_code="mcp.ant.faas.xxx",
                    endpoint="https://example/mcp",
                    transport="STREAMABLE_HTTP",
                    headers={"x-ling-auth": "ak-antchat-inlined"},
                )
            ]
        ),
        skills=[
            SkillRef(name="odps-sql-generator", scope="shared", store="skill-repo", path="team/odps"),
            SkillRef(name="my-skill", scope="user", store="user-nas", path="skills/my-skill"),
        ],
        resources=[ResourceRef(name="r1", store="user-nas", path="resources/r1")],
        identity_files=[FileRef(name="RULES.md", store="user-nas", path="identity/RULES.md")],
        stores={
            "skill-repo": StoreRef(
                type="oss",
                bucket="antsys-agentclaw-prod",
                base="aidesktop/aidesktop_prod/bolt_shared/skills-repo",
            ),
            "user-nas": StoreRef(type="nas", base="/home/admin/nfs/bot-data"),
        },
        engine_overrides={"channels": {"dingtalk": {"enabled": True}}},
        engine_ext={"memory_ref": "nas://ws/MEMORY.md", "anything": [1, 2, 3]},
    )


@pytest.mark.unit
def test_to_dict_from_dict_roundtrip_is_identity() -> None:
    artifact = _sample_artifact()
    restored = BotConfigArtifact.from_dict(artifact.to_dict())
    assert restored == artifact


@pytest.mark.unit
def test_to_dict_is_json_serializable() -> None:
    payload = json.dumps(_sample_artifact().to_dict())
    assert '"schema_version"' in payload


@pytest.mark.unit
def test_produced_artifact_conforms_to_schema() -> None:
    schema = json.loads(_SCHEMA_PATH.read_text())
    jsonschema.validate(instance=_sample_artifact().to_dict(), schema=schema)


@pytest.mark.unit
def test_minimal_artifact_conforms_to_schema() -> None:
    """A personal/draft bot: no version, empty collections."""
    minimal = BotConfigArtifact(schema_version=SCHEMA_VERSION, engine_type="openclaw")
    schema = json.loads(_SCHEMA_PATH.read_text())
    jsonschema.validate(instance=minimal.to_dict(), schema=schema)


@pytest.mark.unit
def test_secret_is_inlined_into_the_server_entry() -> None:
    """The resolved MCP credential is inlined into the entry (header or endpoint
    query); there is no by-reference ``auth_ref`` field anymore."""
    server = _sample_artifact().mcp.servers[0]
    assert not hasattr(server, "auth_ref")
    assert server.headers["x-ling-auth"] == "ak-antchat-inlined"


@pytest.mark.unit
def test_schema_version_distinct_from_content_version() -> None:
    artifact = _sample_artifact()
    assert artifact.schema_version == SCHEMA_VERSION
    assert artifact.version == 7
    assert artifact.schema_version != artifact.version


@pytest.mark.unit
def test_stores_carry_location_only_no_credentials() -> None:
    """Store coords hold bucket/base/endpoint — never credentials."""
    store = _sample_artifact().stores["skill-repo"]
    assert store.type == "oss"
    assert store.bucket == "antsys-agentclaw-prod"
    # Scan the stores block only: MCP entries now legitimately carry inlined
    # credentials, so the whole-artifact scan no longer applies — stores must
    # still be credential-free.
    serialized = json.dumps(_sample_artifact().to_dict()["stores"])
    for forbidden in ("access_key", "secret", "password", "credential", "token"):
        assert forbidden not in serialized


@pytest.mark.unit
def test_refs_store_keys_resolve_against_stores() -> None:
    """Every file ref's 'store' names a store id present in 'stores', and each
    carries a relative 'path' within that store."""
    artifact = _sample_artifact()
    refs = [*artifact.skills, *artifact.resources, *artifact.identity_files]
    for ref in refs:
        assert ref.store in artifact.stores
        assert ref.path  # relative path within the store


@pytest.mark.unit
def test_example_artifact_conforms_and_roundtrips() -> None:
    """The in-code EXAMPLE_ARTIFACT (living documentation) is valid against the
    schema and survives a serialize round-trip."""
    from agentclaw.community.kernel.bot_config.artifact import EXAMPLE_ARTIFACT

    schema = json.loads(_SCHEMA_PATH.read_text())
    jsonschema.validate(instance=EXAMPLE_ARTIFACT.to_dict(), schema=schema)
    assert BotConfigArtifact.from_dict(EXAMPLE_ARTIFACT.to_dict()) == EXAMPLE_ARTIFACT
    assert EXAMPLE_ARTIFACT.schema_version == SCHEMA_VERSION
