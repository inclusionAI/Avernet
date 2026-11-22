"""Storage operations for Local Skill package dispatches with unknown outcomes."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.devices.services.device_context import (
    RuntimeBindingChangedError,
    expected_runtime_binding,
    verify_runtime_binding,
)
from agentclaw.community.core.models.local_skill_package_dispatch import LocalSkillPackageDispatch
from agentclaw.community.core.repository.implementations.skill_center.bot_mutation_fence import require_locked_capability_bot
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def verify_local_skill_metadata_binding(
    session: Session, *, bot_id: str, entity_id: str,
) -> None:
    expected = expected_runtime_binding(bot_id, entity_id)
    if expected is None:
        return
    bot = require_locked_capability_bot(session, bot_id=bot_id, entity_id=entity_id)
    binding = session.query(EntityDeviceBinding).filter_by(
        id=bot.binding_id, env=bot.env,
    ).with_for_update().one_or_none()
    if binding is None:
        raise RuntimeBindingChangedError("Bot Runtime binding changed before metadata commit")
    verify_runtime_binding(
        bot_id=bot_id, user_id=entity_id, binding_id=binding.id, device_id=binding.device_id,
    )


class LocalSkillPackageDispatches:
    """Record intent before remote I/O; compare by dispatch identity on completion."""

    _db: DatabasePlugin

    @staticmethod
    def _pending_local_skill_dispatch(
        session: Session, *, bot_id: str, entity_id: str, name: str | None, device_id: str | None,
    ) -> bool:
        query = session.query(LocalSkillPackageDispatch.target_key).filter_by(
            avernet_tenant=get_current_avernet_tenant(), env=get_current_env(),
            entity_id=entity_id, bot_id=bot_id,
        )
        if name is not None:
            query = query.filter_by(name=name.casefold())
        if device_id:
            query = query.filter(or_(
                LocalSkillPackageDispatch.device_id == device_id,
                LocalSkillPackageDispatch.device_id.is_(None),
            ))
        return query.first() is not None

    def has_pending_local_skill_dispatch(
        self, *, bot_id: str, entity_id: str, name: str | None = None, device_id: str | None = None,
    ) -> bool:
        with self._db.orm_session() as session:
            return self._pending_local_skill_dispatch(
                session, bot_id=bot_id, entity_id=entity_id, name=name, device_id=device_id,
            )

    def reserve_local_skill_dispatch(
        self, *, bot_id: str, entity_id: str, binding_id: int, device_id: str | None,
        name: str, digest: str,
    ) -> tuple[str, str] | None:
        tenant, env, name = get_current_avernet_tenant(), get_current_env(), name.casefold()
        target = (tenant, env, entity_id, bot_id, device_id, name)
        target_key = hashlib.sha256(json.dumps(target).encode()).hexdigest()
        dispatch_id = str(uuid4())
        with self._db.transactional_orm_session() as session:
            require_locked_capability_bot(session, bot_id=bot_id, entity_id=entity_id)
            if self._pending_local_skill_dispatch(
                session, bot_id=bot_id, entity_id=entity_id, device_id=device_id, name=name,
            ):
                return None
            session.add(LocalSkillPackageDispatch(
                target_key=target_key, dispatch_id=dispatch_id, content_digest=digest,
                avernet_tenant=tenant, env=env, entity_id=entity_id, bot_id=bot_id,
                binding_id=binding_id, device_id=device_id or None, name=name,
            ))
        return target_key, dispatch_id

    def finish_local_skill_dispatch(self, target_key: str, dispatch_id: str) -> None:
        with self._db.transactional_orm_session() as session:
            session.query(LocalSkillPackageDispatch).filter_by(
                target_key=target_key, dispatch_id=dispatch_id,
                avernet_tenant=get_current_avernet_tenant(), env=get_current_env(),
            ).delete()
