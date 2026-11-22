"""Storage operations for Local Skill package dispatches with unknown outcomes."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from agentclaw.community.core.models.local_skill_package_dispatch import LocalSkillPackageDispatch
from agentclaw.community.core.repository.implementations.skill_center.bot_mutation_fence import require_locked_capability_bot
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class LocalSkillPackageDispatches:
    """Record intent before remote I/O; compare by dispatch identity on completion."""

    _db: DatabasePlugin

    def has_pending_local_skill_dispatch(
        self, *, bot_id: str, entity_id: str, name: str | None = None, binding_id: int | None = None,
    ) -> bool:
        with self._db.orm_session() as session:
            query = session.query(LocalSkillPackageDispatch.target_key).filter_by(
                avernet_tenant=get_current_avernet_tenant(), env=get_current_env(),
                entity_id=entity_id, bot_id=bot_id,
            )
            if name is not None:
                query = query.filter_by(name=name.casefold())
            if binding_id is not None:
                query = query.filter_by(binding_id=binding_id)
            return query.first() is not None

    def reserve_local_skill_dispatch(
        self, *, bot_id: str, entity_id: str, binding_id: int, name: str, digest: str,
    ) -> tuple[str, str] | None:
        tenant, env, name = get_current_avernet_tenant(), get_current_env(), name.casefold()
        target = (tenant, env, entity_id, bot_id, binding_id, name)
        target_key = hashlib.sha256(json.dumps(target).encode()).hexdigest()
        dispatch_id = str(uuid4())
        with self._db.transactional_orm_session() as session:
            require_locked_capability_bot(session, bot_id=bot_id, entity_id=entity_id)
            if session.get(LocalSkillPackageDispatch, target_key) is not None:
                return None
            session.add(LocalSkillPackageDispatch(
                target_key=target_key, dispatch_id=dispatch_id, content_digest=digest,
                avernet_tenant=tenant, env=env, entity_id=entity_id, bot_id=bot_id,
                binding_id=binding_id, name=name,
            ))
        return target_key, dispatch_id

    def finish_local_skill_dispatch(self, target_key: str, dispatch_id: str) -> None:
        with self._db.transactional_orm_session() as session:
            session.query(LocalSkillPackageDispatch).filter_by(
                target_key=target_key, dispatch_id=dispatch_id,
                avernet_tenant=get_current_avernet_tenant(), env=get_current_env(),
            ).delete()
