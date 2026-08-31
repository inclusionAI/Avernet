"""Accept durable SC Public Reference commands and ensure their queue task."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from uuid import uuid4

from agentclaw.community.core.repository.protocols.skill_center_reference import (
    SkillCenterReferenceRepositoryProtocol,
)
from agentclaw.community.core.skill_center.reference_contract import (
    ReferenceBatchSizeError,
    ReferenceTaskUnavailableError,
    ReferenceNotFoundError,
    ReferenceValidationError,
    SkillCenterReferenceBatch,
    SkillCenterReferenceItem,
    SkillCenterReferencePage,
    SkillCenterReferenceStatus,
)
from agentclaw.community.core.skill_center.skill_center_reference_service_protocol import (
    SkillCenterReferenceServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_set_management_service_protocol import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.utils.env_utils import get_current_env


SKILL_CENTER_REFERENCE_TASK = "skill_center.public_reference"
SKILL_CENTER_REFERENCE_DEADLINE_SECONDS = 30 * 60


class SkillCenterReferenceService(SkillCenterReferenceServiceProtocol):
    """Own the HTTP acceptance transaction, not the asynchronous workflow."""

    def __init__(
        self,
        *,
        references: SkillCenterReferenceRepositoryProtocol,
        skill_sets: SkillSetManagementServiceProtocol,
        tasks: TaskQueueService,
        env_provider: Callable[[], str] = get_current_env,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._references = references
        self._skill_sets = skill_sets
        self._tasks = tasks
        self._env_provider = env_provider
        self._id_factory = id_factory

    def create(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_set_id: str,
        idempotency_key: str,
        skill_codes: tuple[str, ...],
    ) -> SkillCenterReferenceBatch:
        codes = self._codes(skill_codes)
        self._validate_key(idempotency_key)
        env = self._env_provider()
        request_hash = self._request_hash(
            bot_id=bot_id,
            owner_id=owner_id,
            actor_id=actor_id,
            skill_set_id=skill_set_id,
            skill_codes=codes,
        )
        existing = self._references.get_batch_by_idempotency_key(
            env=env, idempotency_key=idempotency_key
        )
        if existing is not None:
            batch, persisted_hash = existing
            if persisted_hash != request_hash:
                from agentclaw.community.core.skill_center.reference_contract import (
                    ReferenceIdempotencyConflictError,
                )

                raise ReferenceIdempotencyConflictError(
                    "Idempotency-Key was reused for a different Reference request"
                )
        else:
            self._skill_sets.get_set(
                bot_id=bot_id,
                owner_id=owner_id,
                user_id=actor_id,
                set_id=skill_set_id,
            )
            result = self._references.create_or_get_batch(
                env=env,
                bot_id=bot_id,
                owner_id=owner_id,
                skill_set_id=skill_set_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                skill_codes=codes,
                request_id=self._id_factory(),
                reference_ids=tuple(self._id_factory() for _ in codes),
            )
            batch = result.batch
        try:
            self._tasks.enqueue(
                SKILL_CENTER_REFERENCE_TASK,
                {"request_id": batch.request_id},
                deadline_seconds=SKILL_CENTER_REFERENCE_DEADLINE_SECONDS,
                idempotency_key=(
                    f"skill-center-reference:{batch.request_id}"
                ),
            )
        except Exception as exc:
            raise ReferenceTaskUnavailableError(
                "Reference operations were persisted but the task is unavailable"
            ) from exc
        return batch

    def list(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_set_id: str,
        request_id: str | None,
        status: SkillCenterReferenceStatus | None,
        page: int,
        page_size: int,
    ) -> SkillCenterReferencePage:
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("page must be >=1 and page_size must be 1..100")
        total, items = self._references.list_items(
            env=self._env_provider(),
            bot_id=bot_id,
            owner_id=owner_id,
            skill_set_id=skill_set_id,
            request_id=request_id,
            status=status,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return SkillCenterReferencePage(total=total, items=items)

    def get(
        self,
        *,
        bot_id: str,
        owner_id: str,
        skill_set_id: str,
        reference_id: str,
    ) -> SkillCenterReferenceItem:
        item = self._references.get_item(
            env=self._env_provider(),
            bot_id=bot_id,
            owner_id=owner_id,
            skill_set_id=skill_set_id,
            reference_id=reference_id,
        )
        if item is None:
            raise ReferenceNotFoundError("Reference item not found")
        return item

    @staticmethod
    def _codes(skill_codes: tuple[str, ...]) -> tuple[str, ...]:
        codes: list[str] = []
        seen: set[str] = set()
        for code in skill_codes:
            if not isinstance(code, str) or not code.strip() or code != code.strip():
                raise ReferenceValidationError(
                    "skill_code must be a non-empty unpadded string"
                )
            if len(code) > 512:
                raise ReferenceValidationError("skill_code exceeds 512 characters")
            if code not in seen:
                seen.add(code)
                codes.append(code)
        if not 1 <= len(codes) <= 20:
            raise ReferenceBatchSizeError(
                "Reference requires between one and twenty unique skill_codes"
            )
        return tuple(codes)

    @staticmethod
    def _validate_key(value: str) -> None:
        if not value or value != value.strip() or len(value) > 190:
            raise ReferenceValidationError(
                "Idempotency-Key must be 1..190 unpadded characters"
            )

    @staticmethod
    def _request_hash(
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_set_id: str,
        skill_codes: tuple[str, ...],
    ) -> str:
        payload = json.dumps(
            {
                "actor_id": actor_id,
                "bot_id": bot_id,
                "owner_id": owner_id,
                "skill_codes": skill_codes,
                "skill_set_id": skill_set_id,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()


__all__ = [
    "SKILL_CENTER_REFERENCE_DEADLINE_SECONDS",
    "SKILL_CENTER_REFERENCE_TASK",
    "SkillCenterReferenceService",
]
