"""Internal working types for the config-compose pipeline.

These are *not* the published cross-boundary contract (that lives in
``agentclaw.community.kernel.bot_config``). They are private inputs/intermediates the
composer services pass around while turning DB state into a ``BotConfigArtifact``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar


__all__ = [
    "McpComposeInput",
    "StdioLaunch",
    "ComposeOccasion",
    "ComposeRequest",
    "CollectedSkill",
    "CollectedFile",
]

_T = TypeVar("_T")


class ComposeOccasion(StrEnum):
    """What a compose is *for* — which decides who owns the artifact's categories (W8).

    Ownership is a property of the operation, not of the bot: a manifest
    apply makes the platform the source of truth for every category, and any
    other operation leaves them the engine's. The composer reads this value
    and nothing else to write the artifact's ``ownership`` map.
    """

    RUNTIME = "runtime"
    """A runtime edit or a publish build: a skill or resource upload, an MCP
    edit, a channel change, the publish flow. The engine is the source of
    truth for every category. The default."""

    PROVISION = "provision"
    """The first artifact for a new container. The platform is the source of
    truth when the bot carries a manifest — the creation job applied it into
    platform state before provisioning — and the engine otherwise."""

    MANIFEST_APPLY = "manifest_apply"
    """The closing redeliver of a manifest apply: the platform has just
    written every category into its own state, and the artifact carries it.
    The platform is the source of truth for every category."""


@dataclass(frozen=True)
class StdioLaunch:
    """A local MCP server's launch instruction, as the collector resolved it.

    Deliberately resolved **before** the composer runs: the launch instruction
    comes from :class:`LocalMCPRegistry` (a local YAML read), not from the
    best-effort MCP Center enrichment. So a Center outage cannot turn a local
    server into a mis-classified remote one — see
    ``ConfigComposerInputCollector._stdio_launch_for``.
    """

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ComposeRequest:
    """Identifies the bot + engine context a single compose runs for.

    One request object serves exactly one ``ConfigComposer.compose`` pass, and
    that is what makes the two non-identity fields at the end safe: they are
    per-compose carry-alongs, scoped by the request's own lifetime. Both are
    excluded from equality — they are *derived from* the identity above, never
    part of it, so two requests naming the same bot are the same request
    whether or not one of them arrived with its MCP set already resolved.
    """

    entity_id: str
    bot_id: str
    user_id: str
    engine_type: str
    entity_type: str = "staff"
    version: int | None = None  # set for a published snapshot; None for live/draft
    occasion: ComposeOccasion = ComposeOccasion.RUNTIME
    """What this compose is for — see :class:`ComposeOccasion`. Part of the
    request's identity, unlike the two carry-alongs below: the same bot
    composed for a manifest apply and for a skill upload yields different
    artifacts."""

    effective_mcps: tuple[dict[str, Any], ...] | None = field(
        default=None, compare=False
    )
    """The bot's effective MCP set, when the caller already resolved it.

    A whole-artifact delivery resolves this set during capability-plan resolution
    (``BotRuntimeProjector._build_capability_plan``) and the composer would otherwise
    re-read the identical set from the same database microseconds later — both
    reads go through ``collect_bot_active_mcps`` with
    ``strict_policy_context=True``, so they are contractually the same answer.
    Handing the resolved value down is what makes the second read unnecessary;
    ``None`` means nobody resolved it yet and the collector reads it itself.

    Empty is *not* ``None``: a bot with no MCPs threads an empty tuple, and the
    collector must not fall back to a second read for it.
    """

    desired_skills: tuple[dict[str, Any], ...] | None = field(
        default=None, compare=False
    )
    """Reader-resolved Skill assets, including exact Center identities.

    Whole-artifact runtime projection already resolved these facts before the
    compose call. ``None`` keeps the build/restart fallback through the same
    Reader-backed SkillSet service; an empty tuple is a complete empty result.
    """

    _memo: dict[str, Any] = field(
        default_factory=dict, compare=False, repr=False
    )
    """Backing store for :meth:`memoized`. Never read or written directly."""

    def memoized(self, key: str, build: Callable[[], _T]) -> _T:
        """``build()``'s result, computed at most once for this request.

        Scoped to the request object rather than to the caller, deliberately.
        The collector that uses this is a process-wide singleton, and compose
        runs inside ``asyncio.to_thread`` — so a memo kept on the collector
        would let one bot's per-bot service be handed to another bot's compose,
        concurrently and silently. A request is created for one compose and
        passed to every collector method within it, which is exactly the
        lifetime a per-compose memo wants.
        """
        if key not in self._memo:
            self._memo[key] = build()
        return self._memo[key]


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
    stdio: StdioLaunch | None = None
    """Set for a LOCAL/stdio server, ``None`` for a remote one — this **is** the
    composer's discriminator, so the local-vs-remote decision is made once, by the
    collector, from a source that does not depend on MCP Center being reachable.
    When set, the remote fields above are ignored (a stdio server has no endpoint
    and needs no credential)."""
