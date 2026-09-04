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

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import TYPE_CHECKING, Any, Sequence

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.channel.services.engine_overrides_reader import (
    ChannelEngineOverridesReader,
)
from agentclaw.community.core.config_compose.models import (
    CollectedCliTool,
    CollectedFile,
    CollectedSkill,
    ComposeRequest,
    McpComposeInput,
    StdioLaunch,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.store import (
    BOT_DATA_STORE as CLI_TOOL_STORE,
    CliToolScope,
    CliToolStore,
)
from agentclaw.community.core.config_compose.protocols import (
    ComposeInputCollector,
    ManagedFilesReader,
)
from agentclaw.community.core.config_compose.services.mcporter_composer import (
    McporterComposeError,
    mcp_network_priority_for,
)
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.mcp.services.local_mcp_registry import LocalMCPRegistry
from agentclaw.community.core.repository.protocols.platform import ResourceRepositoryProtocol
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionRef,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.workspace.path_factory import (
    WorkspacePathFactory,
    get_bolt_base_dir,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.bot.cli_tool import (
        BotCliToolRepositoryProtocol,
    )
    # Deferred: importing IdentityService eagerly triggers an identity↔harness
    # import cycle. The collector is constructed via an explicit @provider (not
    # @inject), so this annotation is never resolved at runtime.
    from agentclaw.community.core.services.identity import IdentityService

logger = get_logger()

# Ceiling on simultaneous MCP Center detail lookups. The point of the fan-out is
# to stop paying ``n`` round trips in sequence, not to hand MCP Center ``n``
# simultaneous requests: a bot may hold dozens of MCPs, and several bots can
# compose at once. Eight covers the observed shape (~13 servers, ~90 ms each) in
# two waves while leaving Center's connection pool room to breathe.
_MCP_DETAIL_WORKERS = 8

# ONE pool for the process, deliberately not one per compose. A per-call executor
# would cap each compose at ``_MCP_DETAIL_WORKERS`` and cap nothing across them:
# ``project_skills`` dispatches every projection through ``asyncio.to_thread``, so
# concurrent bot projections would multiply into ``8 x in-flight composes`` threads
# and that many simultaneous Center lookups — the ceiling would describe one
# compose while the service as a whole had none. Sharing the pool makes the number
# mean what it says process-wide, and puts it *below* the sequential behaviour it
# replaced, which already allowed one concurrent Center call per in-flight compose
# with no ceiling at all.
#
# No deadlock risk from sharing: tasks only call ``get_mcp_detail`` and never
# re-enter this pool, and ``mcps()`` itself runs on the default executor's threads,
# not on these.
#
# Worker threads are spawned lazily on first submit, so importing this module costs
# nothing. This deployment serves from a single ``uvicorn.run(app)`` process; a
# pre-fork server would need to build the pool per worker instead, since threads do
# not survive a fork.
_MCP_DETAIL_POOL = ThreadPoolExecutor(
    max_workers=_MCP_DETAIL_WORKERS, thread_name_prefix="mcp-detail"
)


class McpDetailUnavailableError(LookupError):
    """MCP Center returned no record for a server code.

    Distinct from a transport error out of the Center client: this one says the
    lookup *worked* and came back empty, which for a remote server means the code
    is unknown to Center — a bad entry in the bot's MCP set or in the per-engine
    default list. Raised as the chained cause so that distinction survives into
    the traceback.
    """


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
        center_store: CanonicalCenterVersionStore,
        cli_tool_repository: BotCliToolRepositoryProtocol,
        local_mcp_registry: LocalMCPRegistry | None = None,
        managed_files_reader: ManagedFilesReader | None = None,
    ) -> None:
        self._skill_set_service_factory = skill_set_service_factory
        self._mcp_config_service = mcp_config_service
        self._resource_repository = resource_repository
        self._bot_repo = bot_repo
        self._path_factory = path_factory
        self._identity_service = identity_service
        self._overrides_reader = overrides_reader
        self._center_store = center_store
        # Defaulting to the bare registry (its own default config path) matches
        # what ``passport_scope`` already does for every caller, so "is this
        # server local?" has one answer across the codebase. A divergent source
        # here would let passport treat a server as local while compose treated
        # it as remote — and the remote path would then fail looking for an
        # endpoint that never existed. Injectable for tests.
        self._local_mcp_registry = local_mcp_registry or LocalMCPRegistry()
        # W8: the platform's own copy of a teclaw bot's manifest-delivered
        # files, and whether the platform owns a given compose. None (the
        # bare/unit collector) means the engine owns every compose, and every
        # teclaw branch below answers as it did before W8.
        self._managed_files = managed_files_reader
        # W9: ``ac_bot_cli_tool``. Required rather than defaulted, unlike the
        # readers above: this collector missing it is exactly the bug review
        # found — every production compose returned no tool refs while the
        # tests, which wired it, passed. A bot with no tools is an empty table,
        # which is a different thing from a collector that cannot see one.
        self._cli_tool_repository = cli_tool_repository

    # ── platform ownership (W8) ─────────────────────────────────────────
    def platform_owns(self, req: ComposeRequest) -> bool:
        """Whether the platform is the source of truth for this compose.

        Read once per compose from the managed-files reader, which decides
        for its own engine family and from the compose's occasion; ``False``
        when no reader is bound (the bare/unit collector), for an engine the
        reader does not serve, for a runtime edit, and while the
        platform-managed switch is off. The composer turns it into the
        artifact's ``ownership`` map; the three file-category branches below
        read the store when it holds and answer as before W8 when it does
        not. The collector itself never names an engine.
        """
        if self._managed_files is None:
            return False
        reader = self._managed_files
        return req.memoized("platform_owns", lambda: bool(reader.platform_owns(req)))

    def _managed(self, req: ComposeRequest) -> ManagedFilesReader | None:
        """The reader, when the platform owns this compose."""
        return self._managed_files if self.platform_owns(req) else None

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
        active = req.memoized("active_skill_rows", lambda: self._active_skill_rows(req))
        collected: list[CollectedSkill] = []
        for r in active:
            name = r.get("name", "")
            git_path = r.get("git_path", "")
            if git_path.startswith("git://"):
                # shared market skill: git_path IS the skill-repo-relative key.
                collected.append(CollectedSkill(
                    name=name, scope="shared", store="skill-repo",
                    path=git_path[len("git://"):],
                ))
            elif git_path.startswith("center://"):
                try:
                    identity = CanonicalCenterVersionIdentity(
                        skill_uuid=r.get("skill_uuid"),
                        sc_version_number=r.get("sc_version_number"),
                    )
                except Exception as exc:
                    raise ValueError(
                        "Center Skill requires exact canonical identity"
                    ) from exc
                try:
                    ready = self._center_store.verify_version(
                        CanonicalCenterVersionRef(identity)
                    )
                except Exception as exc:
                    raise ValueError(
                        "Center Skill exact Store Version is unavailable"
                    ) from exc
                if not ready:
                    raise ValueError(
                        "Center Skill exact Store Version is unavailable"
                    )
                collected.append(
                    CollectedSkill(
                        name=name,
                        scope="shared",
                        store="skill-center",
                        path=(
                            f"{identity.skill_uuid}/"
                            f"{identity.sc_version_number}"
                        ),
                    )
                )
            # local:// (user upload) intentionally skipped — engine-owned; see
            # docstring — unless the platform owns the compose (W8, below).
        managed = self._managed(req)
        if managed is not None:
            # The platform's copies of the bot's local packages: a ``SkillRef``
            # per package the store holds *and* the bot has active. The store
            # keeps a package the manifest stopped declaring, the way an
            # ARCA host keeps a deactivated skill's files; the active set is
            # what decides delivery, here as everywhere.
            active_local = self._active_local_names(active)
            collected.extend(
                ref for ref in managed.skills(req) if ref.name in active_local
            )
        return collected

    @staticmethod
    def _active_local_names(active) -> frozenset[str]:
        return frozenset(
            str(r.get("name", ""))
            for r in active
            if str(r.get("git_path", "")).startswith("local://")
        )

    def _active_skill_rows(self, req: ComposeRequest) -> tuple[dict[str, Any], ...]:
        active = req.desired_skills
        if active is None:
            svc = self._skill_set_service(req)
            active = tuple(
                svc.get_active_skills(user_id=req.user_id, bolt_id=req.bot_id)
            )
        return active

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

        Step 1 is skipped when the request already carries the effective set
        (``ComposeRequest.effective_mcps``): a whole-artifact delivery resolves
        it during plan resolution and this would otherwise be the *second*
        ``collect_bot_active_mcps`` of one request — the same query, against the
        same database, for the same contractual answer. Step 2 runs either way;
        the threaded value is the bare association set, exactly what step 1
        returns, so nothing downstream can tell the two apart.
        """
        svc = self._skill_set_service(req)
        raw = req.effective_mcps
        if raw is None:
            raw = svc.collect_bot_active_mcps(
                entity_id=req.entity_id,
                bot_id=req.bot_id,
                user_id=req.user_id,
                entity_type=req.entity_type,
                engine_type=req.engine_type,
                strict_policy_context=True,
            )
        # ``collect_bot_active_mcps`` returns only the skill-set association fields
        # (server_code/name/…) — it deliberately does NOT call MCP Center. The
        # composer needs the full detail (``endpoints``/``runMode``/
        # ``transportProtocol``) to select an endpoint, so enrich each MCP here.
        # The device-sync path gets this for free (its caller passes the
        # ``get_mcp_detail`` dict straight through); the whole-artifact compose
        # path re-collects from DB, so it must fetch the detail itself or the
        # composer would see ``endpoints=[]`` and raise "no usable endpoint".
        # The lookups are issued together rather than one at a time — see
        # :meth:`_enrich_mcp_details`; the list it returns still follows ``raw``.
        # Endpoint-selection policy is per-engine: teclaw selects deterministically
        # by network priority (OFFICE > INTERNET > INTRANET), other engines keep
        # the legacy filter + transport-preference selection (network_priority None).
        network_priority = mcp_network_priority_for(req.engine_type)
        inputs: list[McpComposeInput] = []
        for md, detail_failure in self._enrich_mcp_details(svc, raw):
            server_code = md.get("server_code") or md.get("serverCode") or ""
            stdio = self._stdio_launch_for(server_code, md, req.engine_type)
            if stdio is None and detail_failure is not None:
                # Remote server we could not resolve. Fail here, at the point the
                # lookup actually failed, with the cause chained — rather than
                # composing an entry with no endpoints and letting the composer
                # report "no usable endpoint", which reads as a misconfigured
                # server and sends the reader hunting in MCP Center for a record
                # the lookup never retrieved. A local server takes the branch
                # above instead: Center having no record of it is expected.
                raise McporterComposeError(
                    f"MCP {server_code or '<no server_code>'}: could not resolve "
                    "server detail, and it is not a local server either (the "
                    "local-MCP registry has no entry for it). MCP Center is "
                    "unreachable or holds no record for this code."
                ) from detail_failure
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
                    stdio=stdio,
                )
            )
        return inputs

    def _stdio_launch_for(
        self, server_code: str, md: dict[str, Any], engine_type: str
    ) -> StdioLaunch | None:
        """Resolve a LOCAL server's launch instruction; ``None`` if it is remote.

        A ``run_mode`` of REMOTE on the collected entry settles it. That value only
        appears when the MCP Center lookup **succeeded**, and Center is
        authoritative for the servers it knows, so a caller's own remote server is
        never reclassified just because its ``server_code`` collides with a name in
        the bundled registry (``hitl`` / ``clawmind``). The registry is a fallback
        catalog for servers Center does not carry, not an override — which also
        keeps a community deployment, where the MCP Center plugin deliberately
        ignores that bundled file, from inheriting its company-only launch paths.

        Otherwise the launch instruction is taken from the **resolved detail
        first**, and only then from :class:`LocalMCPRegistry`. A deployment that
        registers its own local server — including under a name the bundled
        catalog also ships (``hitl``, ``clawmind``) — must keep its own command
        rather than have it replaced by the bundled ``/home/admin/...`` path,
        which that deployment's image need not even contain.

        The registry is what makes the fallback work when there is nothing to
        prefer: enrichment is best-effort, so on an MCP Center failure ``md``
        carries neither ``runMode`` nor ``stdioConfigs``, and a local server —
        having no endpoint to find — would otherwise fail the remote path
        outright. Reading a local YAML needs no network, so a local server stays
        recognizable exactly when Center cannot vouch for it.

        Either source speaks ``stdioConfigs: [{command, arguments, envVariables}]``
        — the registry normalizes the YAML's flat ``command``/``args``/``env`` into
        exactly that. The list holds one entry per engine layout, discriminated by
        ``engineType``; :meth:`_stdio_config_for_engine` picks the one for this
        engine.
        """
        if not server_code:
            return None
        run_mode = md.get("run_mode") or md.get("runMode")
        if isinstance(run_mode, str) and run_mode.strip().upper() == "REMOTE":
            return None

        cfg = self._stdio_config_for_engine(md, engine_type)
        if cfg is None:
            registry_detail = self._local_mcp_registry.get_mcp_detail(server_code)
            cfg = self._stdio_config_for_engine(registry_detail or {}, engine_type)
        if cfg is None:
            return None
        return StdioLaunch(
            command=str(cfg["command"]),
            args=[str(a) for a in (cfg.get("arguments") or cfg.get("args") or [])],
            env={
                str(k): str(v)
                for k, v in (cfg.get("envVariables") or cfg.get("env") or {}).items()
            },
        )

    @staticmethod
    def _stdio_config_for_engine(
        detail: dict[str, Any], engine_type: str
    ) -> dict[str, Any] | None:
        """The launchable ``stdioConfigs`` entry for ``engine_type``, or ``None``.

        A launch instruction is a path into a specific image, so the same
        ``server_code`` can need a different one per engine — ``hitl`` ships at
        ``/usr/local/bin`` on teclaw and under ``/home/admin`` elsewhere. An entry
        naming this engine in ``engineType`` wins; an entry naming no engine is
        the default for the rest. An entry for a *different* engine is never a
        fallback: launching another image's binary is worse than not launching.

        An entry without a ``command`` is not a launch instruction, so it does not
        count as a hit — the caller then falls through to its next source rather
        than emitting a stdio server the engine cannot start.
        """
        configs = detail.get("stdioConfigs") or detail.get("stdio_configs") or []
        if not isinstance(configs, list):
            return None

        default: dict[str, Any] | None = None
        for cfg in configs:
            if not isinstance(cfg, dict) or not cfg.get("command"):
                continue
            declared = cfg.get("engineType") or cfg.get("engine_type")
            if declared is None:
                default = default if default is not None else cfg
            elif str(declared).strip().lower() == engine_type.strip().lower():
                return cfg
        return default

    def _enrich_mcp_details(
        self, svc: Any, raw: Sequence[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], Exception | None]]:
        """:meth:`_enrich_mcp_detail` over every entry — concurrently, **in order**.

        ``get_mcp_detail`` is one blocking round trip per server with no batch
        form and no cache, so enriching in sequence cost this compose ``n`` round
        trips for a bot holding ``n`` MCPs — ~90 ms each, over *every* MCP the bot
        holds rather than only the ones a mutation touched. Issuing them together
        collapses that to roughly one round trip of wall clock.

        Two properties the sequential loop had for free, and which the caller
        reads as a contract:

        * **Order.** Futures are read back in *submit* order, never completion
          order, so the result still follows ``raw`` — which is the order the
          ``McpComposeInput`` list carries into ``McporterComposer``.
        * **Per-entry failure.** ``_enrich_mcp_detail`` returns its cause instead
          of raising, so an unresolvable lookup stays attached to its own entry
          and the caller keeps deciding per entry whether it is fatal (for a
          local server it is not). Fanning out must not collapse ``n`` separate
          causes into whichever one happened to surface first.

        Threads rather than async: ``get_mcp_detail`` is a synchronous plugin
        call and ``mcps`` is a sync method already reached from ``project_skills``'
        worker thread, so a pool drops into the existing shape where converting
        the call chain to async would not.

        The pool is the process-wide :data:`_MCP_DETAIL_POOL`, not one built per
        call, so the ceiling holds across concurrent composes rather than only
        within one — see the constant for why that distinction matters.

        Each task runs under its own *copy* of the calling context. Pool workers
        do not inherit context vars — the reason ``bind_current_avernet_tenant``
        exists — and a Center lookup reads the request's tenant and mints its log
        lines under the request's trace id; copying the whole context carries both
        without this having to enumerate them. The copy is taken here, on the
        calling thread, one per task: a single ``Context`` cannot be entered by
        two threads at once.

        *Every* lookup goes through the pool, a lone one included. Running a
        single entry inline would cost less — no hand-off, no future — but a
        ceiling with a side door is not a ceiling: concurrent one-MCP composes
        would each add a Center call on top of the pool's
        ``_MCP_DETAIL_WORKERS``, so the process-wide total would be
        ``_MCP_DETAIL_WORKERS + <one-MCP composes in flight>``. The hand-off is
        microseconds against an ~90 ms call, and it buys a bound that actually
        holds. Only the empty case short-circuits, having nothing to submit.
        """
        entries = list(raw)
        if not entries:
            return []
        futures = [
            _MCP_DETAIL_POOL.submit(
                copy_context().run, self._enrich_mcp_detail, svc, md
            )
            for md in entries
        ]
        return [f.result() for f in futures]

    def _enrich_mcp_detail(
        self, svc: Any, md: dict[str, Any]
    ) -> tuple[dict[str, Any], Exception | None]:
        """Merge MCP Center detail (endpoints/runMode/…) over a bare association.

        ``collect_bot_active_mcps`` returns only the skill-set association fields;
        the composer needs ``endpoints`` to select a URL. Fetch the full Center
        detail per server (same source ``add_mcp_to_skill_set`` validates against)
        and merge it over the bare dict — Center is authoritative for endpoints,
        while locally-set fields absent from Center (e.g. default-MCP ``headers``)
        are preserved.

        Returns ``(merged, failure)``. A lookup that raised or returned nothing
        yields the bare dict plus the **cause**, which is deliberately *carried*
        rather than logged and dropped: only the caller knows whether the server
        is local — Center legitimately has no record of a stdio server — so only
        the caller can decide whether the failure is fatal. It raises with this
        exception chained, so the root cause survives into the traceback instead
        of resurfacing three layers down as a misleading "no usable endpoint".
        """
        server_code = md.get("server_code") or md.get("serverCode")
        if not server_code:
            return md, None
        try:
            detail = svc.mcp_center.get_mcp_detail(server_code)
        except Exception as e:  # noqa: BLE001 — returned to the caller, not swallowed
            return md, e
        if not detail:
            return md, McpDetailUnavailableError(
                f"MCP Center returned no detail for {server_code}"
            )
        return {**md, **detail}, None

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
            return self._teclaw_resources(req)

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

    def _teclaw_resources(self, req: ComposeRequest) -> list[CollectedFile]:
        """teclaw: the platform's resources when it owns the compose (W8).

        The files of every platform-held local skill the bot has active ride
        here too, beside their ``SkillRef`` — the shape the publish gather
        produces (engine contract R-O3). Nothing when the engine owns the
        compose, as before W8.
        """
        managed = self._managed(req)
        if managed is None:
            return []
        collected: list[CollectedFile] = list(managed.resources(req))
        active = req.memoized("active_skill_rows", lambda: self._active_skill_rows(req))
        names = self._active_local_names(active)
        if names:
            collected.extend(managed.skill_files(req, names))
        return collected

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
            # W8: the platform's copies when it owns the compose.
            managed = self._managed(req)
            return list(managed.identity_files(req)) if managed is not None else []

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

    # ── cli tools ───────────────────────────────────────────────────────
    def cli_tools(self, req: ComposeRequest) -> list[CollectedCliTool]:
        """The bot's platform-managed CLI tools, from the platform's own table.

        Always the table, on every occasion: this category is platform-managed
        independent of the switch, like ``mcp``, so there is no engine-owned
        reading to fall back to. That is also what lets a *live* CLI install on
        teclaw work — the artifact composed right after it references the tool
        because the row is already there.

        The ref path is built from the bot's coordinates rather than from the
        row's ``oss_key``, because the artifact's ``bot-data`` store carries the
        *current* base and a ref must be relative to it. A tool whose object was
        written under an earlier base is therefore not found under the current
        one, and the next install writes it again — the same rule W8's
        managed-files store states for a file.

        It carries the digest's fingerprint for the reason the store's key does:
        a replacement writes a new object rather than overwriting the one the
        current row describes.
        """
        scope = CliToolScope(
            entity_type=req.entity_type, entity_id=req.entity_id, bot_id=req.bot_id
        )
        return [
            CollectedCliTool(
                name=record.name,
                store=CLI_TOOL_STORE,
                path=(
                    f"{scope.rel_root}/{record.name}"
                    f".{CliToolStore.fingerprint(record.digest)}"
                ),
                md5=record.md5,
                version=record.version,
            )
            for record in self._cli_tool_repository.list(
                env=get_current_env(), entity_id=req.entity_id, bot_id=req.bot_id
            )
        ]

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
        """The per-bot ``SkillSetService`` for this compose request — built once.

        Both :meth:`skills` and :meth:`mcps` go through the same per-bot service,
        so this centralizes the factory call (unpacking the request's identifiers).
        e.g. for ``ComposeRequest(entity_id="staff_u1", bot_id="bot7", user_id="u1",
        engine_type="openclaw", entity_type="staff")`` it returns the
        ``SkillSetService`` scoped to that staff/bot/engine, from which
        ``get_symlink_mappings`` / ``collect_bot_active_mcps`` read.

        Building it is not cheap — the factory re-resolves the bot's workspace
        paths, re-reads the bot row, and mints a ``SkillService`` whose
        construction mkdirs against the shared ``/aidesktop`` mount — and the
        request's identifiers fully determine the result, so the two call sites
        of one compose share a single instance.

        The memo lives on the *request*, not on ``self``: this collector is a
        singleton and compose runs on a thread pool, so a memo held here would
        eventually hand bot A's service to bot B's compose. See
        ``ComposeRequest.memoized``.
        """
        return req.memoized(
            "skill_set_service",
            lambda: self._skill_set_service_factory.create(
                user_id=req.user_id,
                entity_id=req.entity_id,
                bot_id=req.bot_id,
                engine_type=req.engine_type,
                entity_type=req.entity_type,
            ),
        )
