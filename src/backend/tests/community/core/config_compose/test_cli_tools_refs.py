"""The composed artifact's ``cli_tools`` refs (W9).

Two properties carry the weight here. A bot with tools gets a ref per command,
carrying the platform's own ``md5`` — the engine's change test — read from the
table rather than recomputed. A bot with none composes an artifact that is
**byte-identical** to the one this composer produced before W9, because the key
is left off the wire entirely.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.config_compose.models import (
    CollectedCliTool,
    ComposeOccasion,
    ComposeRequest,
)
from agentclaw.community.core.config_compose.services.config_composer import (
    ConfigComposer,
)
from agentclaw.community.core.config_compose.services.mcporter_composer import (
    McporterComposer,
)
from agentclaw.community.kernel.bot_config import StoreRef

_BASE = "teclaw/prod/bolt_data"
_STORES = {
    "skill-repo": StoreRef(type="oss", bucket="b", base="skills-repo"),
    "bot-data": StoreRef(type="oss", bucket="b", base=_BASE),
}
_TOOL = CollectedCliTool(
    name="mycli",
    store="bot-data",
    path="staff_u1/b1_cli/mycli",
    md5="9f2c4a1b6d8e0f3a5c7b9d1e3a5c7b9d",
    version="1.4.2",
)


class _Collector:
    def __init__(self, *, cli_tools: list[CollectedCliTool] | None = None) -> None:
        self._cli_tools = cli_tools or []

    def skills(self, req): return []
    def mcps(self, req): return []
    def resources(self, req): return []
    def identity_files(self, req): return []
    def cli_tools(self, req): return self._cli_tools
    def engine_overrides(self, req) -> dict[str, Any]: return {}


def _composer(collector: _Collector) -> ConfigComposer:
    return ConfigComposer(
        mcporter_composer=McporterComposer(), collector=collector, stores=_STORES
    )


def _req(engine: str = "teclaw", **kw) -> ComposeRequest:
    base = dict(entity_id="u1", bot_id="b1", user_id="u1", engine_type=engine)
    base.update(kw)
    return ComposeRequest(**base)


def test_a_tool_becomes_a_ref_carrying_the_platforms_md5() -> None:
    artifact = _composer(_Collector(cli_tools=[_TOOL])).compose(_req())
    assert len(artifact.cli_tools) == 1
    ref = artifact.cli_tools[0]
    assert (ref.name, ref.store, ref.path) == ("mycli", "bot-data", _TOOL.path)
    assert ref.md5 == _TOOL.md5 and ref.version == "1.4.2"


def test_a_bot_with_no_tools_omits_the_key_entirely() -> None:
    """Byte-identical to a pre-W9 artifact: ``None``, not ``[]``, so ``to_dict``
    leaves the key off the wire."""
    artifact = _composer(_Collector()).compose(_req())
    assert artifact.cli_tools is None
    assert "cli_tools" not in artifact.to_dict()


def test_the_refs_pull_the_bot_data_store_into_the_artifact() -> None:
    """A ref the engine cannot resolve is worse than no ref."""
    artifact = _composer(_Collector(cli_tools=[_TOOL])).compose(_req())
    assert artifact.stores["bot-data"] == _STORES["bot-data"]


def test_the_refs_round_trip_through_the_wire_shape() -> None:
    from agentclaw.community.kernel.bot_config import BotConfigArtifact

    artifact = _composer(_Collector(cli_tools=[_TOOL])).compose(_req())
    assert BotConfigArtifact.from_dict(artifact.to_dict()).cli_tools == artifact.cli_tools


def test_an_arca_artifact_carries_the_refs_too_but_no_ownership_map() -> None:
    """Nothing composes for ARCA at runtime, so the map would state a delivery
    that does not happen — but the composer is engine-blind about the list."""
    artifact = _composer(_Collector(cli_tools=[_TOOL])).compose(_req("openclaw"))
    assert artifact.ownership is None
    assert [t.name for t in artifact.cli_tools] == ["mycli"]


def test_ownership_says_platform_on_every_compose() -> None:
    """``cli_tools`` joins ``mcp`` in the always-platform branch: the platform
    holds the bytes and the table is the desired state, so writing ``engine``
    on a runtime edit would tell the engine to keep tools the platform had just
    removed."""
    for occasion in ComposeOccasion:
        artifact = _composer(_Collector(cli_tools=[_TOOL])).compose(
            _req(occasion=occasion)
        )
        assert artifact.ownership["cli_tools"] == "platform", occasion
