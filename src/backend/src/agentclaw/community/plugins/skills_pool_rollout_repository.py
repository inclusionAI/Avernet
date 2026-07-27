"""Atomic Skills Pool rollout config and audit persistence."""

from __future__ import annotations

import json
from datetime import datetime

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.skills_pool.repository.models import (
    SkillsPoolRolloutAuditModel,
)
from agentclaw.community.core.skills_pool.rollout_config import (
    normalize_rollout_config_value,
)
from agentclaw.community.core.skills_pool.rollout_gate import (
    SKILLS_POOL_ROLLOUT_BUSINESS_CODE,
    SKILLS_POOL_ROLLOUT_PARAM_CODE,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import CommonConfig


class SkillsPoolRolloutRepository:
    @inject
    def __init__(self, database: DatabasePlugin) -> None:
        self._database = database

    def commit_change(
        self,
        *,
        env: str,
        config_id: int | None,
        expected_revision: str | None,
        expected_enable: bool,
        expected_value: dict[str, object],
        next_revision: str,
        enabled: bool,
        value: dict[str, object],
        audit: dict[str, object],
    ) -> bool:
        try:
            return self._commit_change(
                env=env,
                config_id=config_id,
                expected_revision=expected_revision,
                expected_enable=expected_enable,
                expected_value=expected_value,
                next_revision=next_revision,
                enabled=enabled,
                value=value,
                audit=audit,
            )
        except IntegrityError:
            if config_id is None:
                return False
            raise

    def _commit_change(
        self,
        *,
        env: str,
        config_id: int | None,
        expected_revision: str | None,
        expected_enable: bool,
        expected_value: dict[str, object],
        next_revision: str,
        enabled: bool,
        value: dict[str, object],
        audit: dict[str, object],
    ) -> bool:
        with self._database.transactional_orm_session() as session:
            row = (
                session.query(CommonConfig)
                .filter(
                    CommonConfig.business_code
                    == SKILLS_POOL_ROLLOUT_BUSINESS_CODE,
                    CommonConfig.param_code == SKILLS_POOL_ROLLOUT_PARAM_CODE,
                    CommonConfig.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                if config_id is not None:
                    return False
                row = CommonConfig(
                    business_code=SKILLS_POOL_ROLLOUT_BUSINESS_CODE,
                    business_name="Skills Pool",
                    param_code=SKILLS_POOL_ROLLOUT_PARAM_CODE,
                    param_name="Skills Pool layout rollout",
                    param_value=json.dumps(value, ensure_ascii=False),
                    enable="1" if enabled else "0",
                    ext_info=json.dumps(
                        {
                            "revision": next_revision,
                            "last_action": audit["action"],
                            "last_operator": audit["operator"],
                            "operated_at": audit["effective_at"],
                        },
                        ensure_ascii=False,
                    ),
                    env=env,
                )
                session.add(row)
                session.flush()
            else:
                current_ext = json.loads(row.ext_info) if row.ext_info else {}
                current_value = (
                    json.loads(row.param_value) if row.param_value else None
                )
                normalized_current = normalize_rollout_config_value(current_value)
                normalized_expected = normalize_rollout_config_value(expected_value)
                if (
                    row.id != config_id
                    or current_ext.get("revision") != expected_revision
                    or (row.enable == "1") is not expected_enable
                    or normalized_current is None
                    or normalized_expected is None
                    or normalized_current != normalized_expected
                ):
                    return False
                row.param_value = json.dumps(value, ensure_ascii=False)
                row.enable = "1" if enabled else "0"
                row.ext_info = json.dumps(
                    {
                        "revision": next_revision,
                        "last_action": audit["action"],
                        "last_operator": audit["operator"],
                        "operated_at": audit["effective_at"],
                    },
                    ensure_ascii=False,
                )

            session.add(
                SkillsPoolRolloutAuditModel(
                    env=env,
                    config_id=row.id,
                    action=str(audit["action"]),
                    batch_id=(
                        str(audit["batch_id"])
                        if audit.get("batch_id") is not None
                        else None
                    ),
                    operator=str(audit["operator"]),
                    reason=str(audit["reason"]),
                    based_on_config_version=(
                        str(audit["based_on_config_version"])
                        if audit.get("based_on_config_version") is not None
                        else None
                    ),
                    effective_config_version=next_revision,
                    evidence=(
                        json.dumps(audit["evidence"], ensure_ascii=False)
                        if audit.get("evidence") is not None
                        else None
                    ),
                    effective_at=datetime.fromisoformat(
                        str(audit["effective_at"])
                    ),
                )
            )
        return True

    def list_audit_events(self, *, env: str) -> list[dict[str, object]]:
        with self._database.transactional_orm_session() as session:
            rows = (
                session.query(SkillsPoolRolloutAuditModel)
                .filter(SkillsPoolRolloutAuditModel.env == env)
                .order_by(SkillsPoolRolloutAuditModel.id.asc())
                .all()
            )
            return [
                {
                    "env": row.env,
                    "action": row.action,
                    "operator": row.operator,
                    "reason": row.reason,
                    "batch_id": row.batch_id,
                    "based_on_config_version": row.based_on_config_version,
                    "effective_config_version": (
                        row.effective_config_version
                    ),
                    "effective_at": row.effective_at.isoformat(),
                    "evidence": (
                        json.loads(row.evidence) if row.evidence else None
                    ),
                }
                for row in rows
            ]
