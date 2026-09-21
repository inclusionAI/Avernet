"""``mcp`` → ``DirectActivationService``. Converges installation + Bot config.

**The entry shape.** ``identity`` is ``server_code``; optional ``config`` is a
closed Bot-scoped connection override. This category fetches no content::

    manifest:
      mcp:
        - server_code: gh
          config:
            endpoint_env: PRE
            transport_protocol: STREAMABLE_HTTP
        - server_code: slack

An entry reaches ``resolve`` with the normalized config preserved. A category
declared empty (``mcp: []``) deactivates every server the manifest owns.

The area this overwrites is the enabled-server set for this category —
"the enabled-server set" — the MCP servers active on *this bot*, stored in
``ac_bot_mcp_installation`` and keyed ``(bot_id, owner_id, env, server_code)``.
Declared and not active ⇒ activated. Active and no longer declared ⇒
deactivated. A changed or removed override ⇒ ``updated``. Matching installation
and override ⇒ ``unchanged``, and nothing is called.

**Nothing here writes account-scoped MCP configuration.** Explicit values go to
``ac_bot_mcp_config`` in the same Desired-State transaction as the installation.
``ac_user_mcp_config`` remains the inherited default and is never mutated by a
Manifest apply.

**Deactivating servers a user turned on through the UI is intended**, and is the
cost §3.2 accepted when it made a declared category overwrite its area. It is
called out in the route's docstring because the first person surprised by it
will be a real user.
"""
from __future__ import annotations

from typing import Any, Sequence

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    EntryOutcome,
    EntryResult,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    CategoryPlan,
    Intent,
    Materialiser,
    PlannedEntry,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.bot_config_manifest.capabilities import ManifestCategory
from agentclaw.community.core.mcp.mcp_auth_service_protocol import (
    MCPAuthServiceProtocol,
)
from agentclaw.community.core.mcp.mcp_config_service_protocol import (
    MCPConfigServiceProtocol,
)
from agentclaw.community.core.ports.activation_port import ActivationPort


def _comparison_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize only semantics that HTTP defines as case-insensitive."""
    if config is None:
        return None
    comparable = dict(config)
    headers = comparable.get("headers")
    if isinstance(headers, dict):
        comparable["headers"] = {str(name).lower(): value for name, value in headers.items()}
    return comparable


class McpMaterialiser(Materialiser):
    """Converges this bot's enabled MCP servers toward the declaration.

    ``identity`` is the server code and ``Intent.value`` is the optional
    normalized Bot override::

        resolve -> ResolveResult(intents=(Intent("gh", {"headers": {}}),))
        plan    -> CategoryPlan(
                       entries=(PlannedEntry(Intent("gh", {"headers": {}}), "updated"),),
                       removals=("old",))
        write   -> (EntryResult(ManifestCategory.MCP, "gh", EntryOutcome.UPDATED),)

    ``plan`` compares both the installed set and persisted override, so it can
    answer ``created``, ``updated`` or ``unchanged``.
    """

    construct = ManifestCategory.MCP

    def __init__(
        self,
        activation_service: ActivationPort,
        mcp_auth_service: MCPAuthServiceProtocol,
        mcp_config_service: MCPConfigServiceProtocol,
    ) -> None:
        self._activation = activation_service
        # The *same* permission service ``DirectActivationService`` consults, so
        # the answer here cannot diverge from the answer the write would get.
        self._mcp_auth = mcp_auth_service
        self._mcp_config = mcp_config_service

    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[dict[str, Any]]
    ) -> ResolveResult:
        """Check every declared ``server_code`` **before** anything is written.

        The permission check has to happen here rather than being left to
        ``activate_mcp``, which performs its own. If it were left there, a
        declaration of ``{A, B}`` where B is not permitted would activate A and
        *then* fail — and under overwrite a half-written set is a deletion of
        the rest. Checking up front is what makes the category all-or-nothing.

        The check itself is reused, not copied: the same
        ``check_mcp_permission_detail`` call, and the same **fail-closed**
        reading of its answer that ``DirectActivationService._require_mcp_permission``
        applies. That endpoint is advisory and fail-open during an upstream
        outage — an empty ``access_level`` is its documented outage sentinel —
        and a desired-state write must not act on that.

        Permission is not the only way the write can refuse, and asking about
        only one of them is what made the all-or-nothing claim above untrue.
        ``activate_mcp`` and ``deactivate_mcp`` *also* refuse a code the Bot's
        engine/template policy owns, raising
        ``SkillSetControlPlaneConflictError`` from a guard that runs before the
        permission check. So a declaration of ``{A, B}`` where A is permitted and
        B is a platform default passed resolve, activated A for real, and then
        raised on B — leaving the category half-written and reported aborted.
        Both refusals are now asked here, before anything is written.
        """
        intents: list[Intent] = []
        failures: list[ResolveFailure] = []
        seen: set[str] = set()
        platform_owned = self._platform_owned(ctx)
        declared_codes = {
            entry.get("server_code")
            for entry in entries
            if isinstance(entry.get("server_code"), str)
            and entry.get("server_code")
        }
        set_managed = self._activation.set_managed_mcp_codes(
            bot_id=ctx.bot_id,
            owner_id=ctx.owner_id,
            actor_id=ctx.actor_id,
            server_codes=declared_codes,
        )

        for index, entry in enumerate(entries):
            server_code = entry.get("server_code") if isinstance(entry, dict) else None
            if not isinstance(server_code, str) or not server_code:
                failures.append(
                    ResolveFailure(
                        f"[{index}]", "an mcp entry must name a 'server_code'"
                    )
                )
                continue
            if server_code in seen:
                # A set cannot contain a duplicate. Refusing rather than
                # de-duplicating silently: two entries for one server means the
                # author believes something that is not true of the result.
                failures.append(
                    ResolveFailure(
                        server_code, "declared more than once in this category"
                    )
                )
                continue
            seen.add(server_code)

            if server_code in platform_owned:
                # Refused rather than accepted as a no-op. It *is* active, so
                # "already satisfied" is tempting — but the manifest does not
                # control it, and pretending otherwise would turn the
                # declaration into a real install the day the platform stopped
                # making it a default. The author is told instead.
                failures.append(
                    ResolveFailure(
                        server_code,
                        "this MCP server is a platform default for this bot: it "
                        "is managed by engine/template policy, not by a "
                        "manifest, and is enabled without being declared",
                    )
                )
                continue
            if server_code in set_managed:
                failures.append(
                    ResolveFailure(
                        server_code,
                        "this MCP server is managed by a SkillSet and cannot be "
                        "configured directly by a manifest",
                    )
                )
                continue
            if not self._permitted(ctx, server_code):
                failures.append(
                    ResolveFailure(
                        server_code,
                        "this tenant does not have permission to enable this MCP "
                        "server",
                    )
                )
                continue
            config = entry.get("config")
            if isinstance(config, dict) and config:
                verdict = self._mcp_config.validate_bot_override(
                    user_id=ctx.owner_id,
                    server_code=server_code,
                    config=config,
                    engine_type=ctx.engine_type,
                )
                if not verdict.get("valid"):
                    failures.append(
                        ResolveFailure(
                            server_code, str(verdict.get("error") or "invalid config")
                        )
                    )
                    continue
            intents.append(
                Intent(
                    server_code,
                    config if isinstance(config, dict) and config else None,
                )
            )

        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

    def _platform_owned(self, ctx: ApplyContext) -> frozenset[str]:
        """The codes the write would refuse on policy grounds.

        Fail-closed for the same reason ``_permitted`` is, but the closed
        direction is the opposite one: an unanswerable question here must not
        widen what the manifest may touch, and the safe reading of "I could not
        find out which codes are platform-owned" is *none of them are mine to
        write*. Returning an empty set on failure would restore exactly the bug
        this method exists to close, so the failure is raised and the
        orchestrator aborts the category with nothing written.
        """
        return frozenset(
            self._activation.platform_default_mcp_codes(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, actor_id=ctx.actor_id
            )
        )

    def _permitted(self, ctx: ApplyContext, server_code: str) -> bool:
        """The activation service's own verdict, asked the same way.

        Any failure to reach the marketplace reads as "not permitted": this is
        a desired-state write, and the fail-open answer the catalogue endpoint
        gives during an outage is explicitly not usable here.
        """
        try:
            result = self._mcp_auth.check_mcp_permission_detail(
                ctx.actor_id, server_code
            )
        except Exception:  # noqa: BLE001 - an unreachable check is not a yes
            return False
        return bool(result.get("has_permission")) and bool(result.get("access_level"))

    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """Diff the declared set against what is actually installed.

        The installed set is narrowed by the codes the platform owns before the
        diff, so a platform default can never become a removal. Overwrite reads
        an absent entry as "remove it", but that reading only makes sense for
        entries the manifest could have declared — and a platform default is one
        this materialiser refuses in ``resolve``. Leaving it in would mean every
        apply on such a bot called ``deactivate_mcp`` on a code the policy
        refuses, failing a category for something no author could fix from the
        document.

        A default is normally absent from ``ac_bot_mcp_installation`` (it is code
        policy, not installation provenance), so this is usually a no-op. It
        stops being one when a bot's ``active_engine`` or ``template_type``
        changes and turns an ordinary installed server into a default.
        """
        current = set(
            self._activation.list_installed_mcps(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, actor_id=ctx.actor_id
            )
        ) - self._platform_owned(ctx)
        declared = {intent.identity for intent in intents}
        set_managed = self._activation.set_managed_mcp_codes(
            bot_id=ctx.bot_id,
            owner_id=ctx.owner_id,
            actor_id=ctx.actor_id,
            server_codes=current | declared,
        )
        if set_managed:
            raise ValueError(
                "Manifest cannot directly manage SkillSet-owned MCP servers: "
                + ", ".join(sorted(set_managed))
            )
        overrides = self._activation.get_mcp_overrides(
            bot_id=ctx.bot_id, owner_id=ctx.owner_id, actor_id=ctx.actor_id
        )

        planned = tuple(
            PlannedEntry(
                intent,
                (
                    EntryOutcome.CREATED.value
                    if intent.identity not in current
                    else (
                        EntryOutcome.UNCHANGED.value
                        if _comparison_config(overrides.get(intent.identity))
                        == _comparison_config(intent.value)
                        else EntryOutcome.UPDATED.value
                    )
                ),
            )
            for intent in intents
        )
        # Sorted so a report — and a test — reads deterministically.
        removals = tuple(sorted(current - declared))
        return CategoryPlan(entries=planned, removals=removals)

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Activate what is missing, deactivate what is no longer declared.

        An ``unchanged`` entry calls nothing — that absence is what proves
        convergence, rather than an equal-looking result.
        """
        results: list[EntryResult] = []
        for planned in plan.entries:
            if planned.outcome == EntryOutcome.UNCHANGED.value:
                results.append(
                    EntryResult(
                        self.construct,
                        planned.intent.identity,
                        EntryOutcome.UNCHANGED,
                    )
                )
                continue
            # One UoW owns installation and override convergence. Even a newly
            # installed bare entry must pass ``config=None`` so a historical
            # orphan override cannot survive and silently regain effect.
            await self._activation.set_mcp_override(
                server_code=planned.intent.identity,
                config=planned.intent.value,
                bot_id=ctx.bot_id,
                owner_id=ctx.owner_id,
                actor_id=ctx.actor_id,
            )
            results.append(
                EntryResult(
                    self.construct,
                    planned.intent.identity,
                    EntryOutcome(planned.outcome),
                )
            )

        for server_code in plan.removals:
            await self._activation.deactivate_mcp(
                server_code=server_code,
                bot_id=ctx.bot_id,
                owner_id=ctx.owner_id,
                actor_id=ctx.actor_id,
            )

        return tuple(results)


__all__ = ["McpMaterialiser"]
