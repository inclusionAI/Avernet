"""Internal working types for the config-compose pipeline.

These are *not* the published cross-boundary contract (that lives in
``agentclaw.community.kernel.bot_config``). They are private inputs/intermediates the
composer services pass around while turning DB state into a ``BotConfigArtifact``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


__all__ = [
    "McpComposeInput",
    "ComposeRequest",
    "CollectedSkill",
    "CollectedFile",
]


@dataclass(frozen=True)
class ComposeRequest:
    """Identifies the bot + engine context a single compose runs for."""

    entity_id: str
    bot_id: str
    user_id: str
    engine_type: str
    entity_type: str = "staff"
    version: int | None = None  # set for a published snapshot; None for live/draft


@dataclass(frozen=True)
class CollectedSkill:
    """A skill gathered for composing, as a store-relative ref.

    The collector classifies each skill straight from its DB record: ``store`` is
    the logical store id (``skill-repo`` for shared market skills, ``bot-data``
    for per-bot user uploads) and ``path`` is the key within that store. The
    composer embeds ``store``/``path`` into the artifact verbatim — no
    container-path round-trip through a resolver.
    """

    name: str
    scope: str  # "shared" | "user"
    store: str
    path: str


@dataclass(frozen=True)
class CollectedFile:
    """A resource/identity file gathered for composing, as a store-relative ref.

    ``store`` + ``path`` are the logical store id and key (per-bot files live in
    the ``bot-data`` store); the composer embeds them verbatim.
    """

    name: str
    store: str
    path: str


@dataclass(frozen=True)
class McpComposeInput:
    """One MCP server's already-merged config, ready to compose.

    Mirrors the inputs the device-sync path uses today: ``mcp_data`` is the
    market/DB dict (``collect_bot_active_mcps`` element), and ``api_key`` /
    ``headers`` / ``endpoint_env`` / ``transport_protocol`` are the merged values
    produced by ``MCPConfigService.build_mcp_sync_payload``.

    ``api_key`` and any secret headers carry **plaintext** here (that is what the
    upstream merge produces); the composer inlines them into the artifact entry
    (endpoint query / headers), mirroring the device ``/api/mcp`` path.
    """

    mcp_data: dict[str, Any]
    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    endpoint_env: str = "PROD"
    transport_protocol: str | None = None
    network_priority: tuple[str, ...] | None = None
    """Network types in descending priority for endpoint selection (e.g. teclaw's
    ``("OFFICE", "INTERNET", "INTRANET")``). When set, the composer picks the
    endpoint deterministically by ``(network rank, transport rank)``; when None,
    it uses the legacy filter + ``transport_protocol`` preference."""
