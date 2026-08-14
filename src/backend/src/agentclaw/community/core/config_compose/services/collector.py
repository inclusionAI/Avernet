"""Concrete :class:`ComposeInputCollector` — fans out to the source-of-truth services.

This is the DI-wired implementation behind the :class:`ComposeInputCollector`
Protocol (Task 15a). It turns a :class:`ComposeRequest` into the composer's
container-view inputs by delegating to the **existing** parsers — no new business
logic, just adaptation:

* skills → ``SkillSetService.get_symlink_mappings`` (per-bot, via the factory)
* mcps → ``SkillSetService.collect_bot_active_mcps`` + per-server
  ``MCPConfigService.build_mcp_sync_payload`` (the same merge the device-sync path
  uses; plaintext secrets in — the composer inlines them into the entry)
* resources → ``ResourceService.list_resources``
* identity_files → ``IdentityService`` sync path (``get_bot_file_path`` over
  ``VALID_IDENTITY_FILES``; keeps ``ConfigComposer.compose`` sync)
* engine_overrides → the bot's active DingTalk channels, read from the channel
  store and emitted under an engine-neutral ``channels`` key (snake_case). Empty
  when the bot has no active channels, so the artifact field defaults to ``{}``.

Turning these into the portable artifact (source resolution → store-agnostic URIs,
secrets inlined) is the composer's job, not this collector's.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.channel.services.engine_overrides_reader import (
    ChannelEngineOverridesReader,
)
from agentclaw.community.core.config_compose.models import (
    CollectedFile,
    CollectedSkill,
    ComposeRequest,
    McpComposeInput,
)
from agentclaw.community.core.config_compose.protocols import ComposeInputCollector
from agentclaw.community.core.config_compose.services.mcporter_composer import (
    mcp_network_priority_for,
)
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.repository.protocols.platform import ResourceRepositoryProtocol
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.workspace.path_factory import (
    WorkspacePathFactory,
    get_bolt_base_dir,
)
from agentclaw.community.log import get_logger


if TYPE_CHECKING:
    # Deferred: importing IdentityService eagerly triggers an identity↔harness
    # import cycle. The collector is constructed via an explicit @provider (not
    # @inject), so this annotation is never resolved at runtime.
    from agentclaw.community.core.services.identity import IdentityService

logger = get_logger()


def bot_data_relpath(host_path: str) -> str:
    """Per-bot host path under bolt_data → ``bot-data`` store-relative key.

    Strips the host bolt_data root (``get_bolt_base_dir()`` =
    ``/aidesktop/aidesktop_{env}/bolt_data``), leaving the store-relative key
    (``{entity}/{bot}/{engine}/workspace/.../<file>``). The ``bot-data`` store
    base re-roots this under the teclaw OSS namespace at delivery. If the path
    isn't under bolt_data it is returned **unchanged** (callers that require
    bot-data — e.g. the teclaw write-key derivation — detect this by the
    unchanged return and guard).

    Single source of truth: both the collector's artifact ref emission and the
    teclaw byte/engine key derivation (``ConfigComposer.store_key_for``) call
    this, so the ref key and the write/read key cannot use divergent relpath
    logic.
    """
    base = str(get_bolt_base_dir())
    if host_path == base or host_path.startswith(base + "/"):
        return host_path[len(base):].lstrip("/")
    return host_path


class ConfigComposerInputCollector(ComposeInputCollector):
    """Gathers a bot's compose inputs from the source-of-truth services."""

    def __init__(
        self,
        skill_set_service_factory: SkillSetServiceFactory,
        mcp_config_service: MCPConfigService,
        resource_repository: ResourceRepositoryProtocol,
        bot_repo: BotRepository,
        path_factory: WorkspacePathFactory,
        identity_service: "IdentityService",
        overrides_reader: ChannelEngineOverridesReader,
    ) -> None:
        self._skill_set_service_factory = skill_set_service_factory
        self._mcp_config_service = mcp_config_service
        self._resource_repository = resource_repository
        self._bot_repo = bot_repo
        self._path_factory = path_factory
        self._identity_service = identity_service
        self._overrides_reader = overrides_reader

    # ── skills ──────────────────────────────────────────────────────────
    def skills(self, req: ComposeRequest) -> list[CollectedSkill]:
        """Adapt the bot's active skill DB records into ``CollectedSkill`` refs.

        Input — ``SkillSetService.get_active_skills`` returns the active, de-duped
        skill rows; each ``git_path`` is the source of truth for the skill's *actual*
        location, so we classify straight from it (no container-view round-trip):

        - ``git://team/weather`` (shared market skill) -> ``skill-repo`` store, the
          repo-relative key ``team/weather``.

        ``local://`` (user-uploaded) skills are **not** emitted here. They are
        engine-owned: the bytes live in the bot's container under
        ``/workspace/skills-local/<name>`` (the engine auto-discovers them), and at
        a publish boundary the file-promotion gather snapshots them into the
        stage's artifact. Emitting a ``bot-data`` ref pointing at an OSS key would
        be wrong — nothing writes those bytes to OSS at edit time.

        The ref carries only ``{store, path}`` — physical placement is the engine
        owner's decision (no ``target``). e.g.::

            [CollectedSkill(name="weather", scope="shared", store="skill-repo",
                            path="team/weather")]
        """
        svc = self._skill_set_service(req)
        collected: list[CollectedSkill] = []
        for r in svc.get_active_skills(user_id=req.user_id, bolt_id=req.bot_id):
            name = r.get("name", "")
            git_path = r.get("git_path", "")
            if git_path.startswith("git://"):
                # shared market skill: git_path IS the skill-repo-relative key.
                collected.append(CollectedSkill(
                    name=name, scope="shared", store="skill-repo",
                    path=git_path[len("git://"):],
                ))
            # local:// (user upload) intentionally skipped — engine-owned; see docstring.
        return collected

    # ── mcps ────────────────────────────────────────────────────────────
    def mcps(self, req: ComposeRequest) -> list[McpComposeInput]:
        """Collect the bot's active MCP servers, each merged with its sync payload.

        Two-step adaptation (mirrors what the device-sync path does today):

        1. ``collect_bot_active_mcps`` -> the market/DB dicts for MCPs in the bot's
           *active* skill sets (de-duped by ``server_code``). e.g.::

               [{"server_code": "github", "name": "GitHub",
                 "run_mode": "REMOTE",
                 "endpoints": [{"env": "PROD", "transportProtocol": "STREAMABLE_HTTP",
                                "url": "https://mcp.example.com/github"}]}]

        2. for each dict, ``build_mcp_sync_payload`` merges the user's saved config
           with the market template and returns the 4-tuple
           ``(api_key, headers, endpoint_env, transport_protocol)`` — note the
           ``api_key``/headers are **plaintext** here. e.g.::

               ("x-ling-auth=SECRET", {"X-Trace": "1"}, "PROD", "STREAMABLE_HTTP")

        Output — one ``McpComposeInput`` bundling the raw dict + merged payload::

            [McpComposeInput(mcp_data={...github...}, api_key="x-ling-auth=SECRET",
                             headers={"X-Trace": "1"}, endpoint_env="PROD",
                             transport_protocol="STREAMABLE_HTTP")]

        The plaintext stays here only as an intermediate — inlining it into the
        artifact entry (endpoint query / headers) is the ``McporterComposer``'s
        job downstream, not this collector's.
        """
        svc = self._skill_set_service(req)
        raw = svc.collect_bot_active_mcps(
            entity_id=req.entity_id,
            bot_id=req.bot_id,
            user_id=req.user_id,
            entity_type=req.entity_type,
            engine_type=req.engine_type,
        )
        # ``collect_bot_active_mcps`` returns only the skill-set association fields
        # (server_code/name/…) — it deliberately does NOT call MCP Center. The
        # composer needs the full detail (``endpoints``/``runMode``/
        # ``transportProtocol``) to select an endpoint, so enrich each MCP here.
        # The device-sync path gets this for free (its caller passes the
        # ``get_mcp_detail`` dict straight through); the whole-artifact compose
        # path re-collects from DB, so it must fetch the detail itself or the
        # composer would see ``endpoints=[]`` and raise "no usable endpoint".
        # Endpoint-selection policy is per-engine: teclaw selects deterministically
        # by network priority (OFFICE > INTERNET > INTRANET), other engines keep
        # the legacy filter + transport-preference selection (network_priority None).
        network_priority = mcp_network_priority_for(req.engine_type)
        inputs: list[McpComposeInput] = []
        for md in raw:
            md = self._enrich_mcp_detail(svc, md)
            api_key, headers, endpoint_env, transport = (
                self._mcp_config_service.build_mcp_sync_payload(
                    user_id=req.user_id,
                    mcp_data=md,
                    engine_type=req.engine_type,
                )
            )
            inputs.append(
                McpComposeInput(
                    mcp_data=md,
                    api_key=api_key,
                    headers=headers,
                    endpoint_env=endpoint_env,
                    transport_protocol=transport,
                    network_priority=network_priority,
                )
            )
        return inputs

    def _enrich_mcp_detail(self, svc: Any, md: dict[str, Any]) -> dict[str, Any]:
        """Merge MCP Center detail (endpoints/runMode/…) over a bare association.

        ``collect_bot_active_mcps`` returns only the skill-set association fields;
        the composer needs ``endpoints`` to select a URL. Fetch the full Center
        detail per server (same source ``add_mcp_to_skill_set`` validates against)
        and merge it over the bare dict — Center is authoritative for endpoints,
        while locally-set fields absent from Center (e.g. default-MCP ``headers``)
        are preserved. Best-effort: a missing detail or a Center error leaves the
        bare dict unchanged (the composer then surfaces the "no usable endpoint"
        error, same as before).
        """
        server_code = md.get("server_code") or md.get("serverCode")
        if not server_code:
            return md
        try:
            detail = svc.mcp_center.get_mcp_detail(server_code)
        except Exception as e:
            logger.warning(
                "[ConfigComposerInputCollector] MCP Center detail fetch failed "
                "for %s: %s", server_code, e,
            )
            return md
        if not detail:
            logger.warning(
                "[ConfigComposerInputCollector] MCP Center has no detail for %s; "
                "composing without endpoints", server_code,
            )
            return md
        return {**md, **detail}

    # ── resources ───────────────────────────────────────────────────────
    def resources(self, req: ComposeRequest) -> list[CollectedFile]:
        """Collect the bot's uploaded **file** resources as ``CollectedFile`` inputs.

        Input — ``ResourceService.list_resources`` returns ``Resource`` rows of
        mixed types. For a FILE resource the useful fields are ``r.name`` and
        ``r.path`` (a path *relative* to the bot's data dir, e.g. ``"docs/a.md"``);
        ``r.source`` is just an origin label (``"upload"``), NOT a path. The bot's
        ``svc.data_dir`` (from ``path_factory``) is the absolute root, e.g.
        ``/aidesktop/aidesktop_prod/bolt_data/staff_u1/bot7/openclaw/workspace/data``.

        Conversion — join ``data_dir`` + ``r.path`` into the absolute container
        path (the exact location the device write/delete targets), and skip
        non-file resources (URL/link/node have no ``r.path``). e.g. given::

            Resource(name="a.md", type=file, attributes={"path": "docs/a.md"}, source="upload")
            Resource(name="wiki", type=url,  attributes={"url": "https://..."})   # no path

        Output (only the file row survives)::

            [CollectedFile(name="a.md",
                           source=".../staff_u1/bot7/openclaw/workspace/data/docs/a.md")]

        teclaw note: these ``ac_resource`` file rows live under the bot's
        ``workspace/data`` subtree — the same tree the running teclaw container
        owns and that the promotion gather lists under ``/workspace``. So for
        teclaw this returns ``[]`` (no DB mirror); the files reach the next
        version's artifact via the engine gather at promotion, not from here.
        """
        if req.engine_type == "teclaw":
            return []

        from agentclaw.community.core.resources.services.resource_service import ResourceService

        svc = ResourceService(
            repository=self._resource_repository,
            bot_repo=self._bot_repo,
            path_factory=self._path_factory,
            entity_id=req.entity_id,
            bot_id=req.bot_id,
            engine_type=req.engine_type,
            entity_type=req.entity_type,
        )
        # Emit each file resource as a bot-data ref: the bot's resource ``data_dir``
        # joined with the resource's stored relative ``path``, then made store-relative
        # (``_bot_data_relpath``). This is the same location the device write/delete
        # targets (``ResourceService`` delete joins ``data_dir + resource.path`` too),
        # so the artifact ref key == the teclaw write key.
        #
        # ``r.source`` is only an *origin label* ('upload'/'filesystem'/'manual',
        # set at resource_service.py) — never a resolvable path — so it must not be
        # used here. Non-file resources (URL/link/node) have no ``path`` and are not
        # device files, so they are skipped.
        return [
            CollectedFile(
                name=r.name,
                store="bot-data",
                path=self._bot_data_relpath(str(svc.data_dir / r.path.lstrip("/"))),
            )
            for r in svc.list_resources(user_id=req.user_id)
            if r.path
        ]

    # ── identity files ──────────────────────────────────────────────────
    def identity_files(self, req: ComposeRequest) -> list[CollectedFile]:
        """Collect the bot's existing identity/persona files as ``CollectedFile``.

        Probe each well-known identity filename (``VALID_IDENTITY_FILES`` —
        ``RULES.md``, ``OKR.md``, ``AGENTS.md``, …) at its on-disk path via
        ``IdentityService.get_bot_file_path`` and keep only the ones that exist.
        ``get_bot_file_path`` already returns an **absolute** path, so (unlike
        resources) no data_dir join is needed — ``str(path)`` is emitted directly.

        e.g. for ``entity_type="staff", entity_id="u1", bot_id="bot7"`` (openclaw),
        ``get_bot_file_path(..., "RULES.md")`` ->
        ``/aidesktop/aidesktop_prod/bolt_data/staff_u1/default/openclaw/workspace/RULES.md``.
        If ``RULES.md`` exists but ``OKR.md`` does not, output is::

            [CollectedFile(name="RULES.md",
                           source=".../staff_u1/default/openclaw/workspace/RULES.md")]

        (``name`` is the identity *filename*, the source-of-truth key the engine
        re-materializes; the composer maps the absolute path to a ``{store, path}``.)
        """
        # teclaw owns its identity files in the running container (under the engine's
        # /identity namespace): draft compose carries none; they are gathered from
        # the engine at promotion, like resources.
        if req.engine_type == "teclaw":
            return []

        from agentclaw.community.core.services.identity import VALID_IDENTITY_FILES

        collected: list[CollectedFile] = []
        for file_type in VALID_IDENTITY_FILES:
            path = self._identity_service.get_bot_file_path(
                req.entity_type, req.entity_id, req.bot_id, file_type
            )
            if path.exists():
                collected.append(
                    CollectedFile(
                        name=file_type,
                        store="bot-data",
                        path=self._bot_data_relpath(str(path)),
                    )
                )
        return collected

    # ── engine overrides ────────────────────────────────────────────────
    def engine_overrides(self, req: ComposeRequest) -> dict[str, Any]:
        """Per-bot engine override knobs — currently only DingTalk channels.

        Delegates to :class:`ChannelEngineOverridesReader` with the **draft** stage
        filter (``{None, "", "draft"}``): only the no-stage/draft rows are a bot's
        own runtime config. This is the live/draft artifact's channels; verify and
        online promotion read the same reader with their own stage filters via the
        publish flow. The result is copied verbatim into
        ``BotConfigArtifact.engine_overrides`` by the composer. A bot with no active
        draft channels yields ``{}``, so the artifact keeps its default.
        """
        return self._overrides_reader.overrides_for_stage(
            user_id=req.user_id,
            bot_id=req.bot_id,
            accept_stages={None, "", "draft"},
        )

    # ── helpers ─────────────────────────────────────────────────────────
    def _bot_data_relpath(self, host_path: str) -> str:
        """Delegate to the module-level :func:`bot_data_relpath` (shared with the
        teclaw write/read key derivation so they cannot drift)."""
        return bot_data_relpath(host_path)

    def _skill_set_service(self, req: ComposeRequest):
        """Build a per-bot ``SkillSetService`` bound to this compose request.

        Both :meth:`skills` and :meth:`mcps` go through the same per-bot service,
        so this centralizes the factory call (unpacking the request's identifiers).
        e.g. for ``ComposeRequest(entity_id="staff_u1", bot_id="bot7", user_id="u1",
        engine_type="openclaw", entity_type="staff")`` it returns the
        ``SkillSetService`` scoped to that staff/bot/engine, from which
        ``get_symlink_mappings`` / ``collect_bot_active_mcps`` read.
        """
        return self._skill_set_service_factory.create(
            user_id=req.user_id,
            entity_id=req.entity_id,
            bot_id=req.bot_id,
            engine_type=req.engine_type,
            entity_type=req.entity_type,
        )
