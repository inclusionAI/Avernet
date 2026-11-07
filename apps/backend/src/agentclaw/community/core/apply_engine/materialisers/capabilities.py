"""One aggregate materialiser owns Skill, SkillSet/Default and MCP replacement."""

from __future__ import annotations

import asyncio
from typing import Sequence

from agentclaw.community.core.apply_engine.capabilities import Category
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.desired_state import (
    DesiredCapabilities,
    DesiredSkillSet,
)
from agentclaw.community.core.apply_engine.registry import (
    CategoryPlan,
    CategoryWriteResult,
    Intent,
    Materialiser,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.content_store import ContentReader
from agentclaw.community.core.mcp.mcp_auth_service_protocol import (
    MCPAuthServiceProtocol,
)
from agentclaw.community.core.mcp.mcp_config_service_protocol import (
    MCPConfigServiceProtocol,
)
from agentclaw.community.core.ports.activation_port import ActivationPort
from agentclaw.community.core.ports.shared_skill_port import SharedSkillPort
from agentclaw.community.core.ports.skill_package_upload_port import (
    SkillPackageUploadPort,
)
from agentclaw.community.core.apply_engine.skill_set_apply_port import SkillSetApplyPort
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.skill_center.skill_reference_contract import (
    SkillReferenceServiceProtocol,
)
from agentclaw.community.core.skill_center.services.mcp_admission import (
    require_mcp_delivery_eligibility,
)
from .capabilities_types import CapabilityPlan, SkillSet
from .capabilities_skills import resolve_skill
from .capabilities_resolve import resolve_supply
from .capabilities_plan import build_plan
from .capabilities_write import write_capabilities


class CapabilitiesMaterialiser(Materialiser[DesiredCapabilities]):
    construct = Category.CAPABILITIES

    def __init__(
        self,
        upload_service: SkillPackageUploadPort,
        activation_service: ActivationPort,
        validator: SkillPackageValidator,
        content: ContentReader,
        shared_skills: SharedSkillPort,
        skill_references: SkillReferenceServiceProtocol,
        skill_sets: SkillSetApplyPort,
        mcp_auth_service: MCPAuthServiceProtocol,
        mcp_config_service: MCPConfigServiceProtocol,
    ) -> None:
        self._uploads, self._activation = upload_service, activation_service
        self._validator, self._content = validator, content
        self._shared, self._references = shared_skills, skill_references
        self._sets = skill_sets
        self._auth, self._config = mcp_auth_service, mcp_config_service

    def _snapshot(self, ctx):
        return self._sets.read_apply_snapshot(
            bot_id=ctx.bot_id, owner_id=ctx.entity_id, actor_id=ctx.actor_id
        )

    def _validate_mcp(self, ctx, code, config):
        verdict = self._auth.check_mcp_permission_detail(ctx.actor_id, code)
        if not (verdict.get("has_permission") and verdict.get("access_level")):
            raise ValueError(
                "this tenant does not have permission to enable this MCP server"
            )
        validation = self._config.validate_bot_override(
            user_id=ctx.owner_id,
            server_code=code,
            config=config,
            engine_type=ctx.engine_type,
        )
        if not validation.get("valid"):
            raise ValueError(str(validation.get("error") or "invalid MCP config"))
        return validation

    def _validate_set_mcp(self, ctx, code, config):
        validation = self._validate_mcp(ctx, code, config)
        require_mcp_delivery_eligibility(
            mcp_config=self._config,
            engine_type=ctx.engine_type,
            owner_id=ctx.owner_id,
            bot_id=ctx.bot_id,
            entity_id=ctx.entity_id,
            server_code=code,
            detail=validation["mcp_data"],
        )

    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[DesiredCapabilities]
    ) -> ResolveResult:
        if len(entries) != 1:
            raise ValueError(
                "capability declaration requires exactly one aggregate target"
            )
        target = entries[0]
        snapshot = await asyncio.to_thread(self._snapshot, ctx)
        failures, direct, groups, mcp = [], [], [], []
        # Repeated exact identities reuse the same read/preview within this Apply.
        cache = {}

        async def skill(entry):
            cache_key = (entry.name, entry.source)
            if cache_key not in cache:
                cache[cache_key] = await resolve_skill(self, ctx, snapshot, entry)
            return cache[cache_key]

        if target.skills is not None:
            for entry in target.skills.entries:
                try:
                    direct.append(await skill(entry))
                except Exception as exc:
                    failures.append(ResolveFailure("skills/" + entry.name, str(exc)))
        if target.skill_sets is not None:
            for group in target.skill_sets.entries:
                if not isinstance(group, DesiredSkillSet):
                    continue
                members = []
                for entry in group.skills:
                    try:
                        members.append(await skill(entry))
                    except Exception as exc:
                        failures.append(
                            ResolveFailure(
                                "skill_sets/" + group.name, f"{entry.name}: {exc}"
                            )
                        )
                for code in group.mcp:
                    try:
                        await asyncio.to_thread(
                            self._validate_set_mcp,
                            ctx,
                            code,
                            snapshot.mcp_overrides.get(code),
                        )
                    except Exception as exc:
                        failures.append(
                            ResolveFailure("skill_sets/" + group.name, f"{code}: {exc}")
                        )
                groups.append(
                    SkillSet(
                        group.name,
                        group.description,
                        group.is_active,
                        tuple(members),
                        group.mcp,
                    )
                )
        if target.mcp is not None:
            for entry in target.mcp.entries:
                config = (
                    entry.override.as_config() if entry.override is not None else None
                )
                try:
                    await asyncio.to_thread(
                        self._validate_mcp, ctx, entry.server_code, config
                    )
                    mcp.append((entry.server_code, config))
                except Exception as exc:
                    failures.append(
                        ResolveFailure("mcp/" + entry.server_code, str(exc))
                    )
        resolved, conflicts = resolve_supply(target, snapshot, direct, groups, mcp)
        failures.extend(conflicts)
        # Default restoration requires the same permission/config preflight as explicit members.
        if (
            resolved is not None
            and target.skill_sets is not None
            and resolved.default_enabled
        ):
            for code in sorted(resolved.set_codes):
                try:
                    await asyncio.to_thread(
                        self._validate_set_mcp, ctx, code, snapshot.mcp_overrides.get(code)
                    )
                except Exception as exc:
                    failures.append(
                        ResolveFailure("skill_sets/default", f"{code}: {exc}")
                    )
        if failures:
            reasons = {}
            for failure in failures:
                reasons.setdefault(failure.identity, []).append(failure.reason)
            return ResolveResult(
                failures=tuple(
                    ResolveFailure(k, "; ".join(dict.fromkeys(v)))
                    for k, v in reasons.items()
                )
            )
        return ResolveResult((Intent("capabilities", resolved),))

    async def plan(self, ctx: ApplyContext, intents: Sequence[Intent]) -> CategoryPlan:
        if len(intents) != 1:
            raise ValueError("capability planning requires one aggregate intent")
        return build_plan(intents[0].value)

    async def write(self, ctx: ApplyContext, plan: CategoryPlan) -> CategoryWriteResult:
        if not isinstance(plan, CapabilityPlan):
            raise ValueError("capability writer requires its own complete plan")
        return await write_capabilities(self, ctx, plan)
