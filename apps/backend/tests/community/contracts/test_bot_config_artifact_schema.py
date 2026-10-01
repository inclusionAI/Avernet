"""Contract: every *produced* ``BotConfigArtifact`` conforms to ``artifact.schema.json``.

``tests/kernel/test_bot_config_artifact.py`` already validates *hand-built* sample
artifacts against the schema. This suite closes the remaining gap: it runs the
**real production paths** — the ``ConfigComposer`` (DB-state → artifact) and the
``ExternalComposeProducer`` freeze (compose → inject engine_ext → canonical JSON)
— and validates *their* output against the schema. The schema is the oracle the
external engine owner codes against, so a drift between what the backend emits and
what the schema permits is a contract break, caught here regardless of which
producer changed.

Distinct from the kernel suite: there the artifact is constructed by hand; here it
is whatever the composer/producer actually assemble from inputs (resolved
``{store, path}`` refs, embedded stores, inlined MCP secrets, frozen engine_ext).
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import asdict
from typing import Any

import jsonschema
import pytest

from agentclaw.community.core.config_compose.models import (
    CollectedCliTool,
    CollectedFile,
    CollectedSkill,
    ComposeRequest,
    McpComposeInput,
    StdioLaunch,
)
from agentclaw.community.core.config_compose.services.config_composer import ConfigComposer
from agentclaw.community.core.config_compose.services.mcporter_composer import (
    STDIO_TRANSPORT,
    McporterComposer,
)
from agentclaw.community.core.service_bot.services.deploy.external_compose_producer import (
    ExternalComposeProducer,
)
from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ArtifactBuildRequest,
)
from agentclaw.community.kernel.bot_config import SCHEMA_VERSION, BotConfigArtifact, StoreRef


_SCHEMA_PATH = (
    pathlib.Path(__file__).resolve().parents[2].parent
    / "src"
    / "agentclaw"
    / "community"
    / "kernel"
    / "bot_config"
    / "artifact.schema.json"
)
_SKILLS_BASE = "/home/admin/.openclaw/workspace/skills"
_BOLT_DATA = "/aidesktop/aidesktop_prod/bolt_data"
_SECRET = "secret-token-inlined-xyz"


def _schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text())


def _validate(artifact: BotConfigArtifact) -> None:
    """Schema-as-oracle: a produced artifact must validate against the schema."""
    jsonschema.validate(instance=artifact.to_dict(), schema=_schema())


class _FakeCollector:
    """In-memory ``ComposeInputCollector`` — drives the real composer from fixtures."""

    def __init__(
        self,
        *,
        skills: list[CollectedSkill] | None = None,
        mcps: list[McpComposeInput] | None = None,
        resources: list[CollectedFile] | None = None,
        identity_files: list[CollectedFile] | None = None,
        cli_tools: list[CollectedCliTool] | None = None,
        engine_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._skills = skills or []
        self._mcps = mcps or []
        self._resources = resources or []
        self._identity = identity_files or []
        self._cli_tools = cli_tools or []
        self._overrides = engine_overrides or {}

    def skills(self, req): return self._skills
    def mcps(self, req): return self._mcps
    def resources(self, req): return self._resources
    def identity_files(self, req): return self._identity
    def cli_tools(self, req): return self._cli_tools
    def engine_overrides(self, req): return self._overrides


def _composer(collector: _FakeCollector) -> ConfigComposer:
    return ConfigComposer(
        mcporter_composer=McporterComposer(),
        collector=collector,
        stores={
            "skill-repo": StoreRef(
                type="oss", bucket="antsys-agentclaw-prod", base="skills-repo"
            ),
            "bot-data": StoreRef(
                type="oss", bucket="antsys-agentclaw-prod", base="teclaw/prod/bolt_data"
            ),
        },
    )


def _req(**kw) -> ComposeRequest:
    base = dict(entity_id="staff_u1", bot_id="bot7", user_id="u1", engine_type="teclaw")
    base.update(kw)
    return ComposeRequest(**base)


def _full_collector() -> _FakeCollector:
    return _FakeCollector(
        skills=[
            CollectedSkill("weather", "shared", store="skill-repo", path="team/weather"),
        ],
        mcps=[
            McpComposeInput(
                mcp_data={
                    "server_code": "github",
                    "run_mode": "REMOTE",
                    "endpoints": [
                        {
                            "networkType": "INTERNET", "env": "PROD",
                            "transportProtocol": "STREAMABLE_HTTP",
                            "url": "https://mcp/github",
                        }
                    ],
                },
                api_key=f"x-ling-auth={_SECRET}",
                endpoint_env="PROD",
            )
        ],
        resources=[
            CollectedFile("sales.csv", store="bot-data", path="staff_u1/bot7/openclaw/workspace/data/sales.csv"),
        ],
        identity_files=[
            CollectedFile("RULES.md", store="bot-data", path="staff_u1/default/openclaw/workspace/RULES.md"),
        ],
        engine_overrides={"temperature": 0.2},
    )


# ── composer output conforms across the bot-state matrix ─────────────────────


def _mixed_mcp_collector() -> _FakeCollector:
    """A bot carrying both MCP forms — the shape a teclaw bot with ``hitl`` has."""
    return _FakeCollector(
        mcps=[
            McpComposeInput(
                mcp_data={
                    "server_code": "github",
                    "run_mode": "REMOTE",
                    "endpoints": [
                        {
                            "networkType": "INTERNET", "env": "PROD",
                            "transportProtocol": "STREAMABLE_HTTP",
                            "url": "https://mcp/github",
                        }
                    ],
                },
                api_key=f"x-ling-auth={_SECRET}",
                endpoint_env="PROD",
            ),
            McpComposeInput(
                mcp_data={"server_code": "hitl", "run_mode": "LOCAL"},
                stdio=StdioLaunch(
                    command="python3",
                    args=["/home/admin/hitl/hitl_mcp_server.py"],
                    env={"MCP_TRANSPORT": "stdio"},
                ),
            ),
        ],
    )


@pytest.mark.parametrize(
    "name,collector,version",
    [
        ("empty_live_bot", _FakeCollector(), None),
        ("full_published_bot", _full_collector(), 7),
        ("mixed_remote_and_stdio_mcp", _mixed_mcp_collector(), 7),
    ],
)
def test_composed_artifact_conforms_to_schema(name, collector, version) -> None:
    """The artifact the real ``ConfigComposer`` assembles from inputs validates
    against the schema oracle — for a bare live bot, a fully-populated published
    snapshot (skills + mcp-by-reference + resources + url + identity + embedded
    stores + overrides), and a bot mixing remote and stdio MCP servers."""
    artifact = _composer(collector).compose(_req(version=version))
    _validate(artifact)


def test_composed_stdio_mcp_carries_launch_and_no_credential() -> None:
    """Beyond raw schema shape: the stdio entry is the *local* form end-to-end.

    The schema permits both forms on every entry, so it cannot express that a
    stdio server must carry a launch instruction and must not carry endpoint or
    credentials. Assert that here, against the real composer's output.
    """
    artifact = _composer(_mixed_mcp_collector()).compose(_req(version=7))
    by_code = {s.server_code: s for s in artifact.mcp.servers}

    assert set(by_code) == {"github", "hitl"}

    local = by_code["hitl"]
    assert local.transport == STDIO_TRANSPORT
    assert local.command == "python3"
    assert local.args == ["/home/admin/hitl/hitl_mcp_server.py"]
    assert local.endpoint is None and local.headers == {}

    # The remote entry is unaffected: it still inlines its secret.
    remote = by_code["github"]
    assert remote.command is None
    assert _SECRET in json.dumps(asdict(remote), ensure_ascii=False)


def test_composed_artifact_carries_channels_engine_override() -> None:
    """A teclaw bot whose collector reports DingTalk channels surfaces them
    verbatim under ``artifact.engine_overrides['channels']['dingding']``, and the
    schema version is unchanged (channels ride the free-form overrides field, no
    SCHEMA_VERSION bump)."""
    channels = {
        "channels": {
            "dingding": {
                "enabled": True,
                "accounts": [
                    {
                        "client_id": "cid-1", "client_secret": "sec-1",
                        "robot_code": "cid-1", "dm_policy": "open",
                        "group_policy": "open", "message_type": "markdown",
                        "enable_streaming_cards": False,
                    }
                ],
            }
        }
    }
    collector = _FakeCollector(engine_overrides=channels)
    artifact = _composer(collector).compose(_req())

    assert artifact.engine_overrides == channels
    assert artifact.engine_overrides["channels"]["dingding"]["accounts"][0]["client_id"] == "cid-1"
    assert artifact.schema_version == SCHEMA_VERSION
    _validate(artifact)


def test_composed_artifact_refs_resolve_and_secret_inlined() -> None:
    """Beyond raw schema shape: every produced ref names an embedded store, and the
    MCP secret is inlined into the entry (the invariants the schema can't express)."""
    artifact = _composer(_full_collector()).compose(_req(version=7))
    refs = [*artifact.skills, *artifact.resources, *artifact.identity_files]
    for ref in refs:
        # URL passthrough yields a "https" store that is intentionally not embedded;
        # every *real* store ref must resolve against the embedded stores.
        if ref.store in ("https", "http"):
            continue
        assert ref.store in artifact.stores, f"{ref.name}: store {ref.store!r} not embedded"
    # secret inlined as a header (the fixture uses x-ling-auth=<secret>)
    assert not hasattr(artifact.mcp.servers[0], "auth_ref")
    assert artifact.mcp.servers[0].headers.get("x-ling-auth") == _SECRET


# ── producer-frozen output (compose → engine_ext → canonical JSON) conforms ───


class _StubComposer:
    """Returns a schema-valid composed artifact, isolating the producer's
    engine_ext-inject + freeze (the build path the ConfigComposer feeds)."""

    def __init__(self, artifact: BotConfigArtifact) -> None:
        self._artifact = artifact

    def compose(self, req) -> BotConfigArtifact:
        return self._artifact


def test_frozen_artifact_bytes_conform_to_schema() -> None:
    """The pinned ``config_artifact`` (with an opaque engine_ext injected) is
    itself schema-valid — the dict actually delivered to baas, not just the
    in-memory dataclass."""
    composed = _composer(_full_collector()).compose(_req(version=7))

    # Base ExternalComposeProducer injects engine_ext={} (the teclaw subclass would
    # source a real opaque payload via EngineExtClient — covered in its own suite).
    producer = ExternalComposeProducer(composer=_StubComposer(composed))
    result = producer.produce_artifact(
        ArtifactBuildRequest.create(
            bot={"bot_id": "bot7", "entity_id": "staff_u1"}, version=7
        )
    )

    assert result.success is True
    pinned = result.ext["config_artifact"]
    jsonschema.validate(instance=pinned, schema=_schema())
    assert pinned["schema_version"] == SCHEMA_VERSION
    # secret is inlined into the delivered artifact (no by-reference indirection).
    assert _SECRET in json.dumps(pinned)


def test_frozen_artifact_with_opaque_engine_ext_still_conforms() -> None:
    """An arbitrary opaque engine_ext (free-form, engine-owned) must not break
    schema conformance — the schema permits an open ``engine_ext`` object. The
    producer also injects the backend identity + draft-stage keys alongside the
    opaque payload; both ride the same open object."""
    composed = _composer(_FakeCollector()).compose(_req())

    class _ExtProducer(ExternalComposeProducer):
        def _fetch_engine_ext(self, bot):
            return {"memory_ref": "oss://ws/MEMORY.md", "nested": {"x": [1, 2]}}

    producer = _ExtProducer(composer=_StubComposer(composed))
    result = producer.produce_artifact(
        ArtifactBuildRequest.create(
            bot={"bot_id": "bot7", "entity_id": "staff_u1"}, version=1
        )
    )

    pinned = result.ext["config_artifact"]
    jsonschema.validate(instance=pinned, schema=_schema())
    # opaque engine payload carried verbatim, plus the backend identity/stage keys
    # (owner_id / bot_name default to "" — the bot row here carries neither).
    assert pinned["engine_ext"] == {
        "memory_ref": "oss://ws/MEMORY.md",
        "nested": {"x": [1, 2]},
        "bot_id": "bot7",
        "owner_id": "",
        "bot_name": "",
        "stage": "draft",
    }
