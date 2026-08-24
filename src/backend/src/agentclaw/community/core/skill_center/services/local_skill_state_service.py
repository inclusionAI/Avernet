"""Desired-state activation commands for Bot-owned Local Skills."""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
    SkillManagedBySkillSetError,
    SkillRuntimeNameConflictError,
    SkillSetManagedResourceError,
)
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    RuntimeProjectionResolver,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectionReconcilerProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.core.repository.protocols.skill_installation import (
    SkillInstallationRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolSkillRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    build_logical_skill_mappings,
)
from agentclaw.community.core.skills_pool.models import (
    RegisteredSkillAsset,
)


class LocalSkillStateService:
    """Authorize, mutate desired state, then synchronously reconcile runtime."""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        installations: SkillInstallationRepositoryProtocol,
        bot_repo: BotRepository,
        collaborator_service: CollaboratorServiceProtocol,
        skill_set_service_factory: SkillSetServiceFactory,
        pool_skills: SkillsPoolSkillRepositoryProtocol,
        skill_set_repo: SkillSetRepository,
        runtime_reconciler: BotRuntimeProjectionReconcilerProtocol,
    ) -> None:
        self._skill_repo = skill_repo
        self._installations = installations
        self._bot_repo = bot_repo
        self._collaborators = collaborator_service
        self._skill_set_service_factory = skill_set_service_factory
        self._pool_skills = pool_skills
        self._skill_set_repo = skill_set_repo
        self._runtime_reconciler = runtime_reconciler

    async def set_local_skill_active(
        self, *, skill_id: str, actor_id: str, active: bool
    ) -> dict[str, Any]:
        skill, bot, owner_id, bot_id = self._authorize(skill_id, actor_id)
        self._reject_ordinary_skill_set_member(skill_id=skill_id, bot_id=bot_id)
        if not is_bot_ready(bot):
            raise LocalSkillNotReadyError()
        changed = self._write_desired_state(
            active=active,
            env=str(bot["env"]),
            owner_id=owner_id,
            bot_id=bot_id,
            skill_id=skill_id,
        )

        if active:
            synced = await self._reconcile_runtime(
                bot_id=bot_id,
                owner_id=owner_id,
            )
        else:
            synced = await self._reconcile_deactivation(
                skill=skill,
                owner_id=owner_id,
                bot_id=bot_id,
            )
        if not synced:
            if changed:
                try:
                    self._write_desired_state(
                        active=not active,
                        env=str(bot["env"]),
                        owner_id=owner_id,
                        bot_id=bot_id,
                        skill_id=skill_id,
                    )
                except Exception as exc:
                    raise LocalSkillStorageError() from exc
                # Restore through the established runtime synchronizer.
                # It owns each engine's actual active-root compatibility,
                # including Claude Code's historical workspace root.
                restored = await self._reconcile_runtime(
                    bot_id=bot_id,
                    owner_id=owner_id,
                )
                if not restored:
                    raise LocalSkillRuntimeSyncError()
            raise LocalSkillRuntimeSyncError()
        return {**skill, "active": active, "changed": changed}

    async def set_repo_skill_active(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        active: bool,
    ) -> dict[str, Any]:
        """Apply the same Installation/reconcile transaction to shared Repo assets."""
        raw = self._skill_repo.get_by_id(skill_id) if skill_id.isdecimal() else None
        # Repo records are globally governed assets.  The old scanner persisted
        # ``bolt_id=default`` on some rows; it is a storage sentinel, never
        # ownership, and must not make a shared asset unreadable or inactive.
        if (
            not raw
            or raw.get("user_id")
            or not str(raw.get("git_path") or "").startswith("git://")
        ):
            raise LocalSkillNotFoundError()
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise LocalSkillNotFoundError()
        if actor_id != owner_id:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, owner_id, actor_id, PermissionLevel.MEMBER
            )
            if not permission.get("has_permission"):
                raise LocalSkillNotFoundError()
        if not is_bot_ready(bot):
            raise LocalSkillNotReadyError()
        self._require_no_normal_skill_set_membership(
            skill_id=skill_id,
            bot=bot,
            owner_id=owner_id,
            bot_id=bot_id,
        )
        if active:
            self._require_no_runtime_name_conflict(
                skill=raw, bot=bot, owner_id=owner_id, bot_id=bot_id
            )
        try:
            changed = self._write_desired_state(
                active=active,
                env=str(bot["env"]),
                owner_id=owner_id,
                bot_id=bot_id,
                skill_id=skill_id,
            )
        except Exception as exc:
            raise LocalSkillStorageError() from exc
        if active:
            synced = await self._reconcile_runtime(
                owner_id=owner_id,
                bot_id=bot_id,
            )
        else:
            synced = await self._reconcile_deactivation(
                skill=raw,
                owner_id=owner_id,
                bot_id=bot_id,
            )
        if synced:
            return {
                **raw,
                "bolt_id": bot_id,
                "user_id": owner_id,
                "active": active,
                "changed": changed,
            }
        if changed:
            try:
                self._write_desired_state(
                    active=not active,
                    env=str(bot["env"]),
                    owner_id=owner_id,
                    bot_id=bot_id,
                    skill_id=skill_id,
                )
            except Exception as exc:
                raise LocalSkillStorageError() from exc
            if not await self._reconcile_runtime(
                owner_id=owner_id,
                bot_id=bot_id,
            ):
                raise LocalSkillRuntimeSyncError()
        raise LocalSkillRuntimeSyncError()

    def _authorize(
        self, skill_id: str, actor_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        if not skill_id.isdecimal():
            raise LocalSkillNotFoundError()
        raw = self._skill_repo.get_by_id(skill_id)
        if not self._is_exact_local_skill(raw):
            raise LocalSkillNotFoundError()
        owner_id = str(raw["user_id"])
        bot_id = str(raw["bolt_id"])
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        if actor_id != owner_id:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, owner_id, actor_id, PermissionLevel.MEMBER
            )
            if not permission.get("has_permission"):
                raise LocalSkillNotFoundError()
        skill = self._skill_repo.get_bot_local_skill(
            skill_id=skill_id, bot_id=bot_id, user_id=owner_id
        )
        if skill is None:
            raise LocalSkillNotFoundError()
        return skill, bot, owner_id, bot_id

    @staticmethod
    def _is_exact_local_skill(skill: dict[str, Any] | None) -> bool:
        return bool(
            skill
            and skill.get("user_id")
            and skill.get("bolt_id")
            and str(skill.get("git_path") or "").startswith("local://")
        )

    def _write_desired_state(
        self,
        *,
        active: bool,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
    ) -> bool:
        if active:
            return self._installations.install(
                env=env, owner_id=owner_id, bot_id=bot_id, skill_id=skill_id
            )
        return self._installations.uninstall(
            env=env, owner_id=owner_id, bot_id=bot_id, skill_id=skill_id
        )

    def _require_no_normal_skill_set_membership(
        self,
        *,
        skill_id: str,
        bot: dict[str, Any],
        owner_id: str,
        bot_id: str,
    ) -> None:
        """Direct and normal-SkillSet sources are mutually exclusive."""
        references = self._skill_repo.list_skill_set_references(skill_id)
        if not references:
            return
        service = self._skill_set_service_factory.create(
            user_id=owner_id,
            entity_id=str(bot["entity_id"]),
            bot_id=bot_id,
            engine_type=bot.get("active_engine"),
            entity_type=bot.get("entity_type"),
        )
        for reference in references:
            set_id = str(reference.get("skill_set_id") or "")
            if not set_id:
                continue
            skill_set = service.get_skill_set(set_id, user_id=owner_id)
            if (
                skill_set
                and not skill_set.get("is_default")
                and str(skill_set.get("bolt_id") or bot_id) == bot_id
            ):
                raise SkillManagedBySkillSetError()

    def _require_no_runtime_name_conflict(
        self,
        *,
        skill: dict[str, Any],
        bot: dict[str, Any],
        owner_id: str,
        bot_id: str,
    ) -> None:
        """Validate the resolver's one-name-per-active-entry invariant first."""
        try:
            assets = self._pool_skills.list_bot_active_assets(
                env=str(bot["env"]),
                bot_id=bot_id,
                owner_id=owner_id,
                engine=str(bot["active_engine"]),
            )
            candidate = RegisteredSkillAsset(
                skill_id=int(skill["id"]),
                name=str(skill["name"]),
                git_path=str(skill["git_path"]),
            )
            build_logical_skill_mappings([*assets, candidate])
        except ValueError as exc:
            raise SkillRuntimeNameConflictError() from exc

    def _reject_ordinary_skill_set_member(self, *, skill_id: str, bot_id: str) -> None:
        """Direct state is forbidden once ordinary SkillSet owns the Skill."""
        skill = self._skill_repo.get_by_id(skill_id)
        for reference in self._skill_repo.list_skill_set_references(
            skill_id, skill.get("skill_uuid") if skill else None
        ):
            skill_set = self._skill_set_repo.get_by_id(reference["skill_set_id"])
            if (
                skill_set is not None
                and not skill_set.get("is_default")
                and str(skill_set.get("bolt_id")) == bot_id
            ):
                raise SkillSetManagedResourceError()

    async def _reconcile_runtime(
        self,
        *,
        owner_id: str,
        bot_id: str,
        retired_mappings=(),
    ) -> bool:
        try:
            if retired_mappings:
                await self._runtime_reconciler.reconcile(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    retired_mappings=retired_mappings,
                )
            else:
                await self._runtime_reconciler.reconcile(
                    bot_id=bot_id,
                    owner_id=owner_id,
                )
            return True
        except Exception:
            return False

    async def _reconcile_deactivation(
        self,
        *,
        skill: dict[str, Any],
        owner_id: str,
        bot_id: str,
    ) -> bool:
        try:
            retired_mapping = list(
                RuntimeProjectionResolver()
                .resolve(
                    RuntimeDesiredState(
                        skills=(
                            RegisteredSkillAsset(
                                skill_id=int(skill["id"]),
                                name=str(skill["name"]),
                                git_path=str(skill["git_path"]),
                                skill_uuid=(
                                    str(skill["skill_uuid"])
                                    if skill.get("skill_uuid") is not None
                                    else None
                                ),
                                sc_version_number=(
                                    str(skill["sc_version_number"])
                                    if skill.get("sc_version_number") is not None
                                    else None
                                ),
                            ),
                        )
                    )
                )
                .skill_mappings
            )
        except (KeyError, TypeError, ValueError):
            return False
        return await self._reconcile_runtime(
            owner_id=owner_id,
            bot_id=bot_id,
            retired_mappings=retired_mapping,
        )
