"""Runtime capability probe persistence for Skills Pool migration claims."""

from __future__ import annotations

import json

from sqlalchemy import func

from agentclaw.community.core.devices.protocols import (
    LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY,
)
from agentclaw.community.core.devices.startup_identity import (
    resolve_startup_identity,
)
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayout,
    SkillLayoutPhase,
)


class SkillsPoolCapabilityRepositoryMixin:
    """Release a pre-cutover claim when the runtime lacks Pool capability."""

    _database: object

    def confirm_pool_initializing(
        self,
        *,
        scope: BotSkillLayoutScope,
        layout_contract_version: str,
        binding_id: int,
        startup_identity: str,
    ) -> bool:
        """Confirm Pool layout only for the still-current startup attempt."""

        from agentclaw.community.core.devices.repository.models import (
            EntityDeviceBinding,
        )

        with self._database.transactional_orm_session() as session:
            binding = (
                session.query(EntityDeviceBinding)
                .filter(
                    EntityDeviceBinding.id == binding_id,
                    EntityDeviceBinding.env == scope.env,
                    EntityDeviceBinding.entity_id == scope.entity_id,
                    EntityDeviceBinding.status.in_(
                        (
                            DeviceBindingStatus.PENDING.value,
                            DeviceBindingStatus.ACTIVE.value,
                        )
                    ),
                )
                .with_for_update()
                .one_or_none()
            )
            if binding is None:
                return False
            try:
                props = (
                    json.loads(binding.device_props)
                    if isinstance(binding.device_props, str)
                    else binding.device_props
                )
            except (json.JSONDecodeError, TypeError):
                return False
            if not isinstance(props, dict) or (
                resolve_startup_identity(props) != startup_identity
            ):
                return False

            current = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    BotSkillLayoutStateModel.env == scope.env,
                    BotSkillLayoutStateModel.entity_id == scope.entity_id,
                    BotSkillLayoutStateModel.bot_id == scope.bot_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if current is None:
                return False
            native_active = (
                current.active_layout == SkillLayout.POOL.value
                and current.target_layout is None
                and current.phase == SkillLayoutPhase.POOL_ACTIVE.value
                and current.migration_generation is None
                and current.preparation_id is None
                and current.layout_contract_version == layout_contract_version
            )
            migrated_active = (
                current.active_layout == SkillLayout.POOL.value
                and current.target_layout is None
                and current.phase == SkillLayoutPhase.POOL_ACTIVE.value
                and bool(current.migration_generation)
                and bool(current.preparation_id)
                and current.layout_contract_version == layout_contract_version
                and bool(current.data_plane_cutover_committed)
            )
            pool_initializing = (
                current.active_layout == SkillLayout.POOL.value
                and current.target_layout is None
                and current.phase == SkillLayoutPhase.POOL_INITIALIZING.value
                and current.migration_generation is None
                and current.preparation_id is None
                and current.layout_contract_version == layout_contract_version
                and not bool(current.data_plane_cutover_committed)
            )
            if not (native_active or migrated_active or pool_initializing):
                return False
            if pool_initializing:
                current.phase = SkillLayoutPhase.POOL_ACTIVE.value
                current.pool_activated_at = func.now()
                current.lease_owner = None
                current.lease_expires_at = None
            props[LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY] = startup_identity
            binding.device_props = json.dumps(props, ensure_ascii=False)
            binding.gmt_modified = func.now()
        return True

    def _release_pre_cutover_claim(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        result: str,
        evidence: dict[str, object],
    ) -> bool:
        """Persist evidence and release a provably pre-cutover claim."""

        evidence_json = json.dumps(evidence, ensure_ascii=False)
        with self._database.transactional_orm_session() as session:
            affected = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    BotSkillLayoutStateModel.env == scope.env,
                    BotSkillLayoutStateModel.entity_id == scope.entity_id,
                    BotSkillLayoutStateModel.bot_id == scope.bot_id,
                    BotSkillLayoutStateModel.active_layout == SkillLayout.LEGACY.value,
                    BotSkillLayoutStateModel.target_layout == SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.phase.in_(
                        (
                            SkillLayoutPhase.POOL_PREPARING.value,
                            SkillLayoutPhase.POOL_READY.value,
                        )
                    ),
                    BotSkillLayoutStateModel.data_plane_cutover_committed == 0,
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.lease_owner == lease_owner,
                    BotSkillLayoutStateModel.lease_expires_at > func.now(),
                )
                .update(
                    {
                        BotSkillLayoutStateModel.target_layout: None,
                        BotSkillLayoutStateModel.phase: (
                            SkillLayoutPhase.LEGACY_ACTIVE.value
                        ),
                        BotSkillLayoutStateModel.migration_generation: None,
                        BotSkillLayoutStateModel.preparation_id: None,
                        BotSkillLayoutStateModel.last_probe_result: result,
                        BotSkillLayoutStateModel.last_probe_evidence: evidence_json,
                        BotSkillLayoutStateModel.last_failure_code: None,
                        BotSkillLayoutStateModel.last_failure_stage: None,
                        BotSkillLayoutStateModel.last_failure_retryable: None,
                        BotSkillLayoutStateModel.last_failure_evidence: None,
                        BotSkillLayoutStateModel.last_failure_at: None,
                        BotSkillLayoutStateModel.lease_owner: None,
                        BotSkillLayoutStateModel.lease_expires_at: None,
                    },
                    synchronize_session=False,
                )
            )
        return affected == 1

    def release_not_capable_claim(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        evidence: dict[str, object],
    ) -> bool:
        """Persist old-runtime evidence and release a safe claim."""

        return self._release_pre_cutover_claim(
            scope=scope,
            migration_generation=migration_generation,
            lease_owner=lease_owner,
            result="NOT_CAPABLE",
            evidence=evidence,
        )

    def release_changed_engine_claim(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        evidence: dict[str, object],
    ) -> bool:
        """Release a safe claim whose immutable engine identity changed."""

        return self._release_pre_cutover_claim(
            scope=scope,
            migration_generation=migration_generation,
            lease_owner=lease_owner,
            result="BOT_CHANGED",
            evidence=evidence,
        )
