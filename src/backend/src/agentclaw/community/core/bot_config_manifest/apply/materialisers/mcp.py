"""Materialise the Manifest's complete explicit MCP snapshot as Direct claims.

Every declared server converts ordinary SkillSet supply to Direct, or combines a
Default/platform exclusion with a Direct Installation. Its closed ``config`` is
the complete Bot override and commits atomically with that Installation.

Omitted explicit supply and Bot overrides are removed. Skill dependencies remain
derived: they are not inserted into the Installation table, do not appear in
``removed``, and stale Bot overrides are cleared with an ``updated`` report row.
Source-only conversion can therefore report ``unchanged`` while
``requires_write`` performs the internal transition.
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
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)


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
        capability_reader: BotCapabilityStateReaderProtocol,
    ) -> None:
        self._activation = activation_service
        # The *same* permission service ``DirectActivationService`` consults, so
        # the answer here cannot diverge from the answer the write would get.
        self._mcp_auth = mcp_auth_service
        self._mcp_config = mcp_config_service
        self._reader = capability_reader

    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[dict[str, Any]]
    ) -> ResolveResult:
        """Validate every explicit MCP before the category writes anything."""
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
            config = entry.get("config")
            normalized_config = (
                config if isinstance(config, dict) and config else None
            )
            verdict = self._mcp_config.validate_bot_override(
                user_id=ctx.owner_id,
                server_code=server_code,
                config=normalized_config,
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
                    normalized_config,
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
        """Plan public outcomes separately from required source/config writes."""
        installed = set(
            self._activation.list_installed_mcps(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, actor_id=ctx.actor_id
            )
        )
        platform_owned = set(self._platform_owned(ctx))
        declared = {intent.identity for intent in intents}
        overrides = self._activation.get_mcp_overrides(
            bot_id=ctx.bot_id, owner_id=ctx.owner_id, actor_id=ctx.actor_id
        )
        dependency_codes = ctx.capability_state.final_skill_dependency_codes
        if dependency_codes is None:
            resolved: set[str] = set()
            for asset in self._reader.active_skill_assets(
                bot_id=ctx.bot_id, owner_id=ctx.owner_id, bot=ctx.bot
            ):
                resolved.update(
                    mcp_dependency_codes(
                        getattr(asset, "mcp_dependencies", ()) or ()
                    )
                )
            dependency_codes = frozenset(resolved)
        effective_current = installed | platform_owned | set(dependency_codes)
        current = installed | platform_owned | set(overrides)
        set_managed = self._activation.set_managed_mcp_codes(
            bot_id=ctx.bot_id,
            owner_id=ctx.owner_id,
            actor_id=ctx.actor_id,
            server_codes=current | declared,
        )
        manifest_direct = self._activation.manifest_direct_mcp_codes(
            bot_id=ctx.bot_id,
            owner_id=ctx.owner_id,
            actor_id=ctx.actor_id,
            server_codes=current | declared,
        )
        planned = tuple(
            PlannedEntry(
                intent,
                (
                    EntryOutcome.CREATED.value
                    if intent.identity not in effective_current
                    else (
                        EntryOutcome.UNCHANGED.value
                        if _comparison_config(overrides.get(intent.identity))
                        == _comparison_config(intent.value)
                        else EntryOutcome.UPDATED.value
                    )
                ),
                requires_write=(
                    intent.identity not in installed
                    or (
                        intent.identity in set_managed | platform_owned
                        and intent.identity not in manifest_direct
                    )
                    or _comparison_config(overrides.get(intent.identity))
                    != _comparison_config(intent.value)
                ),
            )
            for intent in intents
        )
        removal_writes = tuple(sorted(current - declared))
        removals = tuple(sorted(set(removal_writes) - set(dependency_codes)))
        retained = sorted(set(removal_writes) & set(dependency_codes))
        retained_entries = tuple(
            PlannedEntry(
                Intent(
                    server_code,
                    None,
                    note=(
                        "explicit MCP supply and Bot override are removed; the "
                        "server remains available as a final Skill dependency"
                    ),
                ),
                EntryOutcome.UPDATED.value,
                requires_write=True,
            )
            for server_code in retained
        )
        return CategoryPlan(
            entries=(*planned, *retained_entries),
            removals=removals,
            removal_writes=removal_writes,
        )

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Apply Direct conversions, overrides, and dependency-aware cleanup."""
        results: list[EntryResult] = []
        for planned in plan.entries:
            if not planned.requires_write:
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
            if planned.intent.identity in (
                plan.removal_writes if plan.removal_writes is not None else ()
            ):
                # Retained dependency cleanup is executed with the removals
                # below, once, so it can keep its explanatory report row.
                results.append(
                    EntryResult(
                        self.construct,
                        planned.intent.identity,
                        EntryOutcome(planned.outcome),
                        note=planned.intent.note,
                    )
                )
                continue
            await self._activation.claim_manifest_mcp(
                server_code=planned.intent.identity,
                config=planned.intent.value,
                bot_id=ctx.bot_id,
                owner_id=ctx.owner_id,
                actor_id=ctx.actor_id,
                apply_id=ctx.apply_id,
            )
            results.append(
                EntryResult(
                    self.construct,
                    planned.intent.identity,
                    EntryOutcome(planned.outcome),
                )
            )

        removal_writes = (
            plan.removal_writes if plan.removal_writes is not None else plan.removals
        )
        for server_code in removal_writes:
            await self._activation.remove_manifest_mcp(
                server_code=server_code,
                bot_id=ctx.bot_id,
                owner_id=ctx.owner_id,
                actor_id=ctx.actor_id,
                apply_id=ctx.apply_id,
            )

        return tuple(results)


__all__ = ["McpMaterialiser"]
