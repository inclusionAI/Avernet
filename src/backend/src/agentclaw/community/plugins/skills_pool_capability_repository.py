"""Runtime capability probe persistence for Skills Pool migration claims."""

from __future__ import annotations

import json

from sqlalchemy import func

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

    def release_not_capable_claim(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        evidence: dict[str, object],
    ) -> bool:
        """Persist old-runtime evidence and release a provably pre-cutover claim."""

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
                        BotSkillLayoutStateModel.last_probe_result: "NOT_CAPABLE",
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
