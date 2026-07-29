"""Post-cutover runtime evidence persistence for Skills Pool migrations."""

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
from agentclaw.community.plugins.skills_pool_cutover_diagnostics import (
    log_missing_quarantine_path,
)


class SkillsPoolPostCutoverRepositoryMixin:
    """Reconcile runtime evidence after the irreversible cutover boundary."""

    _database: object

    def record_post_cutover_evidence(
        self,
        *,
        scope: BotSkillLayoutScope,
        migration_generation: str,
        lease_owner: str,
        preparation_id: str,
        evidence: dict[str, object],
    ) -> bool:
        """Reconcile runtime evidence without re-crossing the cutover boundary."""

        evidence_json = json.dumps(evidence, ensure_ascii=False)
        runtime_evidence = evidence.get("evidence")
        quarantine_path = (
            runtime_evidence.get("quarantine")
            if isinstance(runtime_evidence, dict)
            else None
        )
        with self._database.transactional_orm_session() as session:
            row = (
                session.query(BotSkillLayoutStateModel)
                .filter(
                    *self._scope_filter(scope),
                    BotSkillLayoutStateModel.active_layout == SkillLayout.LEGACY.value,
                    BotSkillLayoutStateModel.target_layout == SkillLayout.POOL.value,
                    BotSkillLayoutStateModel.phase.in_(
                        (
                            SkillLayoutPhase.POOL_CUTOVER_FINALIZING.value,
                            SkillLayoutPhase.POOL_CUTOVER_COMMITTED.value,
                        )
                    ),
                    BotSkillLayoutStateModel.data_plane_cutover_committed == 1,
                    BotSkillLayoutStateModel.migration_generation
                    == migration_generation,
                    BotSkillLayoutStateModel.preparation_id == preparation_id,
                    BotSkillLayoutStateModel.lease_owner == lease_owner,
                    BotSkillLayoutStateModel.lease_expires_at > func.now(),
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None or row.rollout_evidence is None:
                return False
            engine = json.loads(row.rollout_evidence).get("engine_type")
            if not isinstance(engine, str) or not engine:
                return False
            if not isinstance(quarantine_path, str) or not quarantine_path:
                log_missing_quarantine_path(scope, migration_generation)
                return False
            if not self._upsert_quarantine(
                session,
                scope=scope,
                migration_generation=migration_generation,
                engine=engine,
                path=quarantine_path,
                evidence_json=evidence_json,
            ):
                return False
            row.phase = SkillLayoutPhase.POOL_CUTOVER_COMMITTED.value
            row.last_probe_evidence = evidence_json
        return True
