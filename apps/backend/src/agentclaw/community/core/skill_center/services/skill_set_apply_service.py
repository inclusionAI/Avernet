"""Authorized bulk SkillSet Apply, with one post-commit projection."""

from __future__ import annotations

from agentclaw.community.core.bot_management.addressing import stable_resource_owner_id
from agentclaw.community.core.skill_center.errors import (
    ApplyDesiredStateCommittedError,
    SkillSetControlPlaneConflictError,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.services.mcp_admission import (
    mcp_catalog_entry,
    require_mcp_delivery_eligibility,
    resolve_mcp_catalog_detail,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    BotCapabilityScope,
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
    SkillSetApplyResult,
    SkillSetApplySnapshot,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class SkillSetApplyService:
    """Behavior composed by the existing SkillSetManagementService."""

    def _apply_scope(self, bot: dict, bot_id: str) -> BotCapabilityScope:
        return BotCapabilityScope(
            bot_id,
            stable_resource_owner_id(bot),
            self._engine(bot),
            self._default_engine_types(bot),
            get_current_avernet_tenant(),
            get_current_env(),
        )

    def read_apply_snapshot(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> SkillSetApplySnapshot:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=actor_id)
        return self._repository.read_apply_snapshot(
            scope=self._apply_scope(bot, bot_id),
            platform_default_codes=self._platform_default_mcp_codes(bot, bot_id),
        )

    async def replace_apply_skill_sets(
        self,
        *,
        scope: BotCapabilityScope,
        actor_id: str,
        sets: tuple[ResolvedSkillSetWrite, ...],
        remove_names: tuple[str, ...],
        default_change: DefaultExclusionChange,
        platform_default_codes: frozenset[str],
        project: bool,
    ) -> SkillSetApplyResult:
        bot = self._bot(bot_id=scope.bot_id, owner_id=scope.owner_id, user_id=actor_id)
        if self._apply_scope(bot, scope.bot_id) != scope:
            raise SkillSetControlPlaneConflictError("CAPABILITY_SCOPE_CHANGED")
        actual_policy = self._platform_default_mcp_codes(bot, scope.bot_id)
        if actual_policy != platform_default_codes:
            raise SkillSetControlPlaneConflictError("DEFAULT_MCP_POLICY_CHANGED")
        codes = {code for target in sets for code in target.mcp_codes}
        if default_change.remove_mcps:
            current = self._repository.read_apply_snapshot(
                scope=scope, platform_default_codes=actual_policy
            )
            restoring = {
                (item.id, code)
                for item in current.sets
                if item.is_default
                for code in set(item.mcp_codes) | set(actual_policy)
            }
            codes.update(
                code
                for set_id, code in default_change.remove_mcps & current.mcp_exclusions
                if (set_id, code) in restoring
            )
        mcp_catalog_entries = {}
        for code in sorted(codes):
            self._require_mcp_permission(actor_id=actor_id, server_code=code)
            detail = resolve_mcp_catalog_detail(self._mcp_center, code)
            mcp_catalog_entries[code] = mcp_catalog_entry(code, detail)
            require_mcp_delivery_eligibility(
                mcp_config=self._mcp_config,
                engine_type=scope.engine_type,
                owner_id=str(bot["owner_id"]),
                bot_id=scope.bot_id,
                entity_id=scope.owner_id,
                server_code=code,
                detail=detail,
            )
        mutation_result = None

        def mutation():
            nonlocal mutation_result
            mutation_result = self._repository.replace_apply_skill_sets(
                scope=scope,
                sets=sets,
                remove_names=remove_names,
                default_change=default_change,
                platform_default_codes=platform_default_codes,
                mcp_catalog_entries=mcp_catalog_entries,
            )
            return mutation_result

        try:
            result = await self._mutate(
                bot=bot,
                bot_id=scope.bot_id,
                actor_id=actor_id,
                action="skill_sets_apply_replace",
                runtime_required=project,
                scope_from_result=lambda mutation: ProjectionScope(
                    skills=True,
                    mcp=bool(mutation.mcp_codes),
                    claimed_mcp=mutation.mcp_codes,
                    released_mcp=mutation.mcp_codes,
                ),
                mutation=mutation,
            )
        except Exception:
            if mutation_result is not None and mutation_result.changed:
                raise ApplyDesiredStateCommittedError(
                    "Manifest desired state committed before follow-up failure"
                ) from None
            raise
        return SkillSetApplyResult(
            result["created"],
            result["updated"],
            result["unchanged"],
            result["removed"],
            result["changed"],
            result["runtime_projection"],
            result["default_changed"],
            result["default_removed"],
        )
