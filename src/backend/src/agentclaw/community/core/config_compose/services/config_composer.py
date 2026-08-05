"""ConfigComposer — the single backend assembler: DB state → BotConfigArtifact.

This is the one place that aggregates a bot's skills (shared + user), MCP
servers, resources, user-authored identity files, and engine overrides into the
portable, engine-agnostic :class:`BotConfigArtifact`. It is **engine-agnostic**
and pure: given the same collected inputs it produces the same artifact, with no
container access and no I/O of its own.

Responsibilities:
- Embed each file ref ``{store, path}`` the collector classified from the DB
  record (skills, resources, identity files) — no path rewriting here.
- Delegate MCP assembly to :class:`McporterComposer` (secrets inlined).
- Carry ``engine_type`` / ``version`` / ``engine_overrides`` through.

Explicitly **not** its job:
- ``engine_ext`` — left at its default (empty). It is owned and injected by the
  deploy producer (``ExternalComposeProducer`` / ``TeclawComposeProducer``),
  which is the only layer that talks to the engine. The composer never fetches
  or interprets it (keeps the backend free of engine-specific logic).
"""
from __future__ import annotations

from agentclaw.community.core.config_compose.models import ComposeRequest
from agentclaw.community.core.config_compose.protocols import ComposeInputCollector
from agentclaw.community.core.config_compose.services.mcporter_composer import McporterComposer
from agentclaw.community.kernel.bot_config import (
    SCHEMA_VERSION,
    BotConfigArtifact,
    FileRef,
    ResourceRef,
    SkillRef,
    StoreRef,
)
from agentclaw.community.log import get_logger


logger = get_logger()

__all__ = ["ConfigComposer"]


class ConfigComposer:
    """Aggregates a bot's config-contributing state into a BotConfigArtifact."""

    def __init__(
        self,
        *,
        mcporter_composer: McporterComposer,
        collector: ComposeInputCollector,
        stores: dict[str, StoreRef] | None = None,
    ) -> None:
        self._mcporter = mcporter_composer
        self._collector = collector
        # store_id -> physical coordinates (location only, no credentials). Only
        # the stores actually referenced by a source are embedded in the artifact.
        self._stores = dict(stores or {})

    def compose(self, req: ComposeRequest) -> BotConfigArtifact:
        """Build the artifact for ``req``. ``engine_ext`` is left to the producer.

        End-to-end worked example. Say the collector returns, for a bot, one shared
        skill, one MCP server, one uploaded resource, and one identity file. Each
        block below turns a *container-view* collector input into a portable,
        store-relative artifact field. The resulting artifact looks like::

            BotConfigArtifact(
                skills=[SkillRef(name="weather", scope="shared",
                                 store="skill-repo", path="team/weather")],
                mcp=McpManifest(servers=[McpServerRef(server_code="github", ...,
                                 headers={"x-ling-auth": "<token>"})]),  # secret inlined
                resources=[ResourceRef(name="sales.csv", store="bot-data",
                                 path="staff_u1/bot7/openclaw/workspace/data/sales.csv")],
                identity_files=[FileRef(name="RULES.md", store="bot-data",
                                 path="staff_u1/default/openclaw/workspace/RULES.md")],
                stores={"skill-repo": StoreRef(...), "bot-data": StoreRef(...)},
                engine_overrides={"temperature": 0.2}, engine_type=req.engine_type, ...)
        """
        # ── skills ──────────────────────────────────────────────────────────
        # Collector yields CollectedSkill(name, scope, store, path) — already
        # classified from the DB record. e.g. CollectedSkill("weather", "shared",
        # store="skill-repo", path="team/weather"). Embedded verbatim into the
        # SkillRef (no rewriting; placement is the engine's decision — no target):
        skills = [
            SkillRef(
                name=s.name,
                scope=s.scope,
                store=s.store,
                path=s.path,
            )
            for s in self._collector.skills(req)
        ]

        # ── mcp ─────────────────────────────────────────────────────────────
        # Delegate to McporterComposer: it takes the collector's McpComposeInput
        # list (each carries PLAINTEXT api_key/headers) and returns an McpManifest
        # whose servers carry the resolved secret inlined (endpoint query / header),
        # mirroring the device path. e.g. api_key="x-ling-auth=SECRET" ->
        # headers={"x-ling-auth": "SECRET"}.
        mcp = self._mcporter.compose(self._collector.mcps(req))

        # ── resources ───────────────────────────────────────────────────────
        # Collector yields CollectedFile(name, store="bot-data", path=<bolt_data-relative>).
        # e.g. CollectedFile("sales.csv", store="bot-data",
        #   path="staff_u1/bot7/openclaw/workspace/data/sales.csv"). Embedded verbatim
        # (``resources()`` = ac_resource file rows, all bot-data refs):
        resources = [
            ResourceRef(name=r.name, store=r.store, path=r.path)
            for r in self._collector.resources(req)
        ]

        # ── identity files ──────────────────────────────────────────────────
        # Same shape as resources, the persona/identity subtree. e.g.
        # CollectedFile("RULES.md", store="bot-data",
        #   path="staff_u1/default/openclaw/workspace/RULES.md"). Embedded verbatim:
        identity_files = [
            FileRef(name=f.name, store=f.store, path=f.path)
            for f in self._collector.identity_files(req)
        ]

        # ── engine overrides ────────────────────────────────────────────────
        # Opaque per-bot engine knobs, carried through verbatim (copied into a new
        # dict so the artifact never aliases collector state). e.g. {"temperature": 0.2}.
        engine_overrides = dict(self._collector.engine_overrides(req))

        # ── stores ──────────────────────────────────────────────────────────
        # Collect the store ids every ref above points at, then embed ONLY those
        # stores' physical coordinates. Here the refs use "skill-repo" (skill) and
        # "bot-data" (resource + identity), so:
        #   _referenced_stores(["skill-repo", "bot-data", "bot-data"])
        #   -> {"skill-repo": StoreRef(...), "bot-data": StoreRef(...)}
        # A configured-but-unreferenced store is omitted; a referenced-but-unknown
        # store id (e.g. a "https" scheme from a URL resource) contributes nothing.
        stores = self._referenced_stores(
            [s.store for s in skills]
            + [r.store for r in resources]
            + [f.store for f in identity_files]
        )
        # teclaw's file refs are added to the artifact at **promotion** — AFTER
        # compose — by gathering the running container's files into OSS (the
        # container, not the DB, owns the live files, so compose itself sees no
        # bot-data ref). Those refs carry ``store="bot-data"`` and need the
        # ``bot-data`` store's coordinates (bucket/base) in the artifact to
        # resolve. The composer is the only place that holds the configured
        # ``StoreRef``, so embed it here for teclaw even when compose saw no ref;
        # the promotion step only appends ``{store, path}`` refs, it can't supply
        # the store coordinates.
        if (
            req.engine_type == "teclaw"
            and "bot-data" not in stores
            and self._stores.get("bot-data") is not None
        ):
            stores = {**stores, "bot-data": self._stores["bot-data"]}

        return BotConfigArtifact(
            schema_version=SCHEMA_VERSION,
            engine_type=req.engine_type,
            mcp=mcp,
            skills=skills,
            resources=resources,
            identity_files=identity_files,
            stores=stores,
            engine_overrides=engine_overrides,
            version=req.version,
            # engine_ext intentionally left default ({}) — owned by the producer.
        )

    def store_key_for(self, host_path: str) -> str:
        """Canonical object key for a bot-data file: ``bot-data base + '/' + relpath``.

        Single source of truth for where a teclaw file's bytes live in OSS **and**
        the path the engine is addressed by on read — identical to the artifact ref
        for that file (same ``stores['bot-data'].base`` + same ``bot_data_relpath``),
        so write key == read path == artifact ref by construction.

        Only the per-bot ``bot-data`` namespace flows through the teclaw file seam
        (resources, identity, local skills); a path **not** under bolt_data is a bug
        and raises rather than being silently mis-keyed. When ``bot-data`` is not a
        configured store (bare/unit composers) it falls back to the leading-slash-
        stripped path, preserving prior behavior.
        """
        # Local import: keep the (heavy) collector module off the composer's
        # import path; cheap after first call (import cache).
        from agentclaw.community.core.config_compose.services.collector import bot_data_relpath

        store = self._stores.get("bot-data")
        if store is None or not store.base:
            # Bare/unit composers (no stores configured) keep prior behavior. In
            # prod the DI provider always registers bot-data, so reaching here is a
            # misconfiguration worth surfacing rather than silently mis-keying.
            logger.warning(
                "store_key_for: no 'bot-data' store configured; falling back to "
                "lstripped path for %r", host_path,
            )
            return host_path.lstrip("/")
        rel = bot_data_relpath(host_path)
        if not rel or rel == host_path:  # empty (==base) or unchanged ⇒ not a bot-data file
            raise ValueError(
                f"store_key_for: path not under bolt_data, refusing to mis-key: {host_path!r}"
            )
        return f"{store.base}/{rel}"

    def _referenced_stores(self, store_ids: list[str]) -> dict[str, StoreRef]:
        """Embed only the stores actually referenced by the file refs' ``store``.

        Keeps the artifact's ``stores`` block minimal: a store configured on the
        composer but not pointed at by any ref is skipped, and a store id that is
        referenced but absent from the registry (e.g. the ``"https"`` scheme from a
        URL resource) contributes no entry. Example — composer configured with
        ``{"skill-repo": ..., "bot-data": ..., "unused": ...}``::

            _referenced_stores(["skill-repo", "bot-data", "bot-data", "https"])
            #   "skill-repo" -> in registry, kept
            #   "bot-data"   -> in registry, kept (de-duped — added once)
            #   "https"      -> not in registry, skipped
            #   "unused"     -> never passed in, absent
            #   -> {"skill-repo": StoreRef(...), "bot-data": StoreRef(...)}
        """
        referenced: dict[str, StoreRef] = {}
        for store_id in store_ids:
            store = self._stores.get(store_id)
            if store is not None:
                referenced[store_id] = store
        return referenced
