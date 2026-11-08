"""SkillSet membership commands, including Default exclusion and restoration.

Composed by SkillSetManagementService, which owns authorization, the shared
mutation flow, runtime projection and audit dependencies.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from agentclaw.community.core.bot_management.addressing import stable_resource_owner_id
from agentclaw.community.core.repository.capability_desired_state_types import (
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.services._mutation_flow import (
    mcp_claim_scope,
    mcp_release_scope,
    skill_claim_scope,
    skill_release_scope,
)
from agentclaw.community.core.skill_center.services.mcp_admission import (
    mcp_catalog_entry,
    require_mcp_delivery_eligibility,
    resolve_mcp_catalog_detail,
)
from agentclaw.community.core.skill_center.skill_set_batch import SkillSetSkillOutcome


class _SkillSetMembershipCommands:
    """Route membership edits to ordinary UoWs or Default opt-out commands."""

    async def add_skills(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        skill_ids: Sequence[str],
    ) -> list[SkillSetSkillOutcome]:
        """Add one or more members, then project their final combined state.

        The BFF's published body has always accepted ``skill_ids`` and permits
        per-member conflicts to be reported as partial success.  The canonical
        Gateway single-member route is the batch-of-one consumer.  One command
        owns the batch so an active Set has exactly one runtime projection,
        never one complete Artifact delivery per member.
        """
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        target = self._target_set(bot=bot, bot_id=bot_id, set_id=set_id)
        if not skill_ids:
            return []

        return await self._mutate_skills_batch(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            target=target,
            skill_ids=skill_ids,
            action="skill_set_add_skill",
            runtime_required=bool(target.get("is_active") or target["is_default"]),
            scope_from_result=skill_claim_scope,
            partial_conflict_codes=frozenset(
                {
                    "RESOURCE_DIRECT_ACTIVE",
                    "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET",
                }
            ),
            mutation_for_skill=lambda skill_id: self._add_skill_membership(
                bot=bot,
                bot_id=bot_id,
                target=target,
                set_id=set_id,
                skill_id=skill_id,
            ),
        )

    async def remove_skills(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        skill_ids: Sequence[str],
    ) -> list[SkillSetSkillOutcome]:
        """Remove one or more members, then project their final combined state."""
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        target = self._target_set(bot=bot, bot_id=bot_id, set_id=set_id)
        return await self._mutate_skills_batch(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            target=target,
            skill_ids=skill_ids,
            action=(
                "default_set_exclude_skill"
                if target["is_default"]
                else "skill_set_remove_skill"
            ),
            runtime_required=bool(target.get("is_active") or target["is_default"]),
            scope_from_result=skill_release_scope,
            partial_conflict_codes=frozenset(),
            mutation_for_skill=lambda skill_id: self._remove_skill_membership(
                bot=bot,
                bot_id=bot_id,
                target=target,
                set_id=set_id,
                skill_id=skill_id,
            ),
        )

    def _add_skill_membership(
        self,
        *,
        bot: dict,
        bot_id: str,
        target: dict,
        set_id: str,
        skill_id: str,
    ) -> DesiredStateMutation:
        if target["is_default"]:
            self._require_excluded_default_skill(
                owner_id=stable_resource_owner_id(bot),
                bot_id=bot_id,
                set_id=str(target["id"]),
                skill_id=skill_id,
            )
            return self._repository.unexclude_default_skill(
                bot_id=bot_id,
                owner_id=stable_resource_owner_id(bot),
                set_id=set_id,
                skill_id=skill_id,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            )
        return self._repository.add_skill(
            bot_id=bot_id,
            owner_id=stable_resource_owner_id(bot),
            set_id=set_id,
            skill_id=skill_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )

    def _remove_skill_membership(
        self,
        *,
        bot: dict,
        bot_id: str,
        target: dict,
        set_id: str,
        skill_id: str,
    ) -> DesiredStateMutation:
        if target["is_default"]:
            return self._repository.exclude_default_skill(
                bot_id=bot_id,
                owner_id=stable_resource_owner_id(bot),
                set_id=set_id,
                skill_id=skill_id,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            )
        return self._repository.remove_skill(
            bot_id=bot_id,
            owner_id=stable_resource_owner_id(bot),
            set_id=set_id,
            skill_id=skill_id,
            engine_type=self._engine(bot),
            default_engine_types=self._default_engine_types(bot),
        )

    async def _mutate_skills_batch(
        self,
        *,
        bot: dict,
        bot_id: str,
        actor_id: str,
        target: dict,
        skill_ids: Sequence[str],
        action: str,
        runtime_required: bool,
        scope_from_result: Callable[[DesiredStateMutation], ProjectionScope],
        partial_conflict_codes: frozenset[str],
        mutation_for_skill: Callable[[str], DesiredStateMutation],
    ) -> list[SkillSetSkillOutcome]:
        if not skill_ids:
            return []

        outcomes: list[SkillSetSkillOutcome] = []
        previous_state = None
        affected_mcp_codes: set[str] = set()

        def mutation() -> DesiredStateMutation:
            nonlocal previous_state
            try:
                for skill_id in skill_ids:
                    try:
                        result = mutation_for_skill(skill_id)
                    except SkillSetControlPlaneNotFoundError as error:
                        outcomes.append(
                            SkillSetSkillOutcome(skill_id=skill_id, error=error)
                        )
                        continue
                    except SkillSetControlPlaneConflictError as error:
                        if str(error) not in partial_conflict_codes:
                            raise
                        outcomes.append(
                            SkillSetSkillOutcome(skill_id=skill_id, error=error)
                        )
                        continue

                    if previous_state is None:
                        previous_state = result.previous_state
                    if result.changed:
                        affected_mcp_codes.update(result.mcp_codes)
                    outcomes.append(
                        SkillSetSkillOutcome(skill_id=skill_id, changed=result.changed)
                    )
            except Exception:
                # Individual repository writes commit desired state before the
                # batch reaches its single projection.  A later infrastructure
                # failure is therefore still pre-projection, but must restore
                # every earlier successful member before surfacing the error.
                if previous_state is not None:
                    self._repository.restore_desired_state(
                        bot_id=bot_id,
                        owner_id=stable_resource_owner_id(bot),
                        state=previous_state,
                        engine_type=self._engine(bot),
                    )
                raise

            return DesiredStateMutation(
                item=target,
                changed=any(outcome.changed for outcome in outcomes),
                previous_state=(
                    previous_state
                    if previous_state is not None
                    else self._repository.snapshot_desired_state(
                        bot_id=bot_id,
                        owner_id=stable_resource_owner_id(bot),
                        engine_type=self._engine(bot),
                    )
                ),
                mcp_codes=frozenset(affected_mcp_codes),
            )

        mutation_result = await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=actor_id,
            action=action,
            runtime_required=runtime_required,
            scope_from_result=scope_from_result,
            mutation=mutation,
            skip_projection_when_unchanged=True,
        )
        runtime_projection = mutation_result["runtime_projection"]
        return [
            replace(outcome, runtime_projection=runtime_projection)
            if outcome.succeeded
            else outcome
            for outcome in outcomes
        ]

    async def add_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        server_code: str,
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        self._require_mcp_permission(actor_id=user_id, server_code=server_code)
        target = self._target_set(bot=bot, bot_id=bot_id, set_id=set_id)
        if target["is_default"]:
            # The MCP twin of add_skill's opt-out half: only an existing
            # exclusion can be removed; the membership stays immutable.
            codes = self._repository.excluded_default_mcp_codes(
                bot_id=bot_id,
                owner_id=stable_resource_owner_id(bot),
                set_id=str(target["id"]),
            )
            if server_code not in codes:
                raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
            detail = resolve_mcp_catalog_detail(self._mcp_center, server_code)
            require_mcp_delivery_eligibility(
                mcp_config=self._mcp_config,
                engine_type=self._engine(bot),
                owner_id=str(bot["owner_id"]),
                bot_id=bot_id,
                entity_id=stable_resource_owner_id(bot),
                server_code=server_code,
                detail=detail,
            )
            return await self._mutate(
                bot=bot,
                bot_id=bot_id,
                actor_id=user_id,
                action="default_set_unexclude_mcp",
                scope_from_result=mcp_claim_scope,
                mutation=lambda: self._repository.unexclude_default_mcp(
                    bot_id=bot_id,
                    owner_id=stable_resource_owner_id(bot),
                    set_id=set_id,
                    server_code=server_code,
                    platform_default_codes=self._platform_default_mcp_codes(
                        bot, bot_id
                    ),
                    engine_type=self._engine(bot),
                    default_engine_types=self._default_engine_types(bot),
                ),
            )
        platform_default_codes = self._platform_default_mcp_codes(bot, bot_id)
        detail = resolve_mcp_catalog_detail(self._mcp_center, server_code)
        require_mcp_delivery_eligibility(
            mcp_config=self._mcp_config,
            engine_type=self._engine(bot),
            owner_id=str(bot["owner_id"]),
            bot_id=bot_id,
            entity_id=stable_resource_owner_id(bot),
            server_code=server_code,
            detail=detail,
        )
        catalog = mcp_catalog_entry(server_code, detail)
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_add_mcp",
            runtime_required=bool(target.get("is_active")),
            scope_from_result=mcp_claim_scope,
            mutation=lambda: self._repository.add_mcp(
                bot_id=bot_id,
                owner_id=stable_resource_owner_id(bot),
                set_id=set_id,
                server_code=server_code,
                name=catalog["name"],
                platform_default_codes=platform_default_codes,
                description=catalog["description"],
                icon=catalog["icon"],
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    async def remove_mcp(
        self,
        *,
        bot_id: str,
        owner_id: str,
        user_id: str,
        set_id: str,
        server_code: str,
    ) -> dict:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, user_id=user_id)
        target = self._target_set(bot=bot, bot_id=bot_id, set_id=set_id)
        if target["is_default"]:
            return await self._mutate(
                bot=bot,
                bot_id=bot_id,
                actor_id=user_id,
                action="default_set_exclude_mcp",
                scope_from_result=mcp_release_scope,
                mutation=lambda: self._repository.exclude_default_mcp(
                    bot_id=bot_id,
                    owner_id=stable_resource_owner_id(bot),
                    set_id=set_id,
                    server_code=server_code,
                    engine_type=self._engine(bot),
                    default_engine_types=self._default_engine_types(bot),
                    platform_default_codes=self._platform_default_mcp_codes(
                        bot, bot_id
                    ),
                ),
            )
        return await self._mutate(
            bot=bot,
            bot_id=bot_id,
            actor_id=user_id,
            action="skill_set_remove_mcp",
            runtime_required=bool(target.get("is_active")),
            scope_from_result=mcp_release_scope,
            mutation=lambda: self._repository.remove_mcp(
                bot_id=bot_id,
                owner_id=stable_resource_owner_id(bot),
                set_id=set_id,
                server_code=server_code,
                engine_type=self._engine(bot),
                default_engine_types=self._default_engine_types(bot),
            ),
        )

    def _require_excluded_default_skill(
        self, *, owner_id: str, bot_id: str, set_id: str, skill_id: str
    ) -> None:
        """Only an excluded member can be "added" to a Default Set."""
        excluded = self._repository.excluded_default_skill_ids(
            bot_id=bot_id, owner_id=owner_id, set_id=set_id
        )
        if not skill_id.isdecimal() or int(skill_id) not in excluded:
            raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
