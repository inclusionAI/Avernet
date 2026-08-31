"""``mcp`` → ``DirectActivationService``. Converges the enabled-server set.

The area this overwrites is the one work-items §3.2 names for this category:
**已启用的 server 集合** — the set of MCP servers active on *this bot*, stored in
``ac_bot_mcp_installation`` and keyed ``(bot_id, owner_id, env, server_code)``.
Declared and not active ⇒ activated. Active and no longer declared ⇒
deactivated. Already active ⇒ ``unchanged``, and nothing is called.

**Nothing here touches account-scoped MCP configuration.** ``ac_user_mcp_config``
is keyed ``(user_id, server_code)`` and writing it calls
``sync_mcp_detail_to_all_bots`` — so a per-bot apply reaching that write would
change configuration for every bot the owner has. That is why ``mcp[].config``
left schema v1 (see ``manifest-schema`` §3.1), and a structural test asserts this
module cannot reach ``update_user_unified_config``, ``write_unified_config`` or
``sync_mcp_detail_to_all_bots``.

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
from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)


class McpMaterialiser(Materialiser):
    """Converges this bot's enabled MCP servers toward the declaration."""

    construct = ManifestCategory.MCP

    def __init__(
        self,
        activation_service: DirectActivationServiceProtocol,
        mcp_auth_service: Any,
    ) -> None:
        self._activation = activation_service
        # The *same* permission service ``DirectActivationService`` consults, so
        # the answer here cannot diverge from the answer the write would get.
        self._mcp_auth = mcp_auth_service

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
        """
        intents: list[Intent] = []
        failures: list[ResolveFailure] = []
        seen: set[str] = set()

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

            if not self._permitted(ctx, server_code):
                failures.append(
                    ResolveFailure(
                        server_code,
                        "this tenant does not have permission to enable this MCP "
                        "server",
                    )
                )
                continue
            intents.append(Intent(server_code, server_code))

        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

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
        """Diff the declared set against what is actually installed."""
        current = set(
            self._activation.list_installed_mcps(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, actor_id=ctx.actor_id
            )
        )
        declared = {intent.identity for intent in intents}

        planned = tuple(
            PlannedEntry(
                intent,
                (
                    EntryOutcome.UNCHANGED.value
                    if intent.identity in current
                    else EntryOutcome.CREATED.value
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
            await self._activation.activate_mcp(
                server_code=planned.intent.identity,
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
