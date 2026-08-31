"""Application service for Publication impact, resources, and durable execution."""

from __future__ import annotations

from typing import Callable

from agentclaw.community.core.repository.protocols.space_skill_publication import (
    SpaceSkillPublicationRepositoryProtocol,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.errors import PublicationTaskUnavailableError
from agentclaw.community.core.skill_center.publication_contract import (
    PublicationAttemptRecord,
    PublicationAttemptStatus,
    PublicationImpactItem,
    PublicationRecoveryState,
    PublicationRetryResult,
)
from agentclaw.community.core.skill_center.services.space_skill_publication_task import (
    enqueue_publication_task,
)
from agentclaw.community.core.skill_center.space_skill_publication_service_protocol import (
    SpaceSkillPublicationServiceProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class SpaceSkillPublicationService(SpaceSkillPublicationServiceProtocol):
    def __init__(
        self,
        *,
        access: SpaceAccessServiceProtocol,
        repository: SpaceSkillPublicationRepositoryProtocol,
        capability_reader: BotCapabilityStateReaderProtocol,
        task_queue: TaskQueueService,
        env_provider: Callable[[], str],
    ) -> None:
        self._access = access
        self._repository = repository
        self._reader = capability_reader
        self._task_queue = task_queue
        self._env_provider = env_provider

    def list_publication_impact(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        page: int,
        page_size: int,
    ) -> tuple[int, list[PublicationImpactItem]]:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        env = self._env_provider()
        self._repository.require_publisher(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=env,
        )
        items: list[PublicationImpactItem] = []
        for candidate in self._repository.list_impact_candidates(
            skill_id=skill_id, env=env
        ):
            assets = self._reader.active_skill_assets(
                bot_id=candidate.bot_id,
                owner_id=candidate.owner_id,
                bot=candidate.bot,
            )
            if any(asset.skill_id == skill_id for asset in assets):
                items.append(
                    PublicationImpactItem(
                        owner_id=candidate.owner_id,
                        bot_id=candidate.bot_id,
                        bot_name=candidate.bot_name,
                    )
                )
        total = len(items)
        offset = (page - 1) * page_size
        return total, items[offset : offset + page_size]

    def create_publication(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        request_id: str,
    ) -> PublicationAttemptRecord:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        creation = self._repository.create_or_replay_attempt(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            request_id=request_id,
            env=self._env_provider(),
        )
        if creation.created or (
            creation.attempt.status
            not in (
                PublicationAttemptStatus.SUCCEEDED,
                PublicationAttemptStatus.FAILED,
            )
            and creation.attempt.recovery.state
            is PublicationRecoveryState.AUTO_RETRYING
        ):
            self._ensure_task(creation.attempt.attempt_id)
        return creation.attempt

    def list_publications(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        page: int,
        page_size: int,
    ) -> tuple[int, list[PublicationAttemptRecord]]:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        return self._repository.list_attempts(
            space_id=space_id,
            skill_id=skill_id,
            env=self._env_provider(),
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    def get_publication(
        self,
        *,
        space_id: int,
        skill_id: int,
        attempt_id: int,
        actor_id: str,
    ) -> PublicationAttemptRecord:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        return self._repository.get_attempt(
            space_id=space_id,
            skill_id=skill_id,
            attempt_id=attempt_id,
            env=self._env_provider(),
        )

    def retry_publication(
        self,
        *,
        space_id: int,
        skill_id: int,
        attempt_id: int,
        actor_id: str,
    ) -> PublicationRetryResult:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        env = self._env_provider()
        result = self._repository.restart_recovery(
            space_id=space_id,
            skill_id=skill_id,
            attempt_id=attempt_id,
            actor_id=actor_id,
            env=env,
        )
        if not result.task_required:
            return result
        try:
            self._ensure_task(attempt_id)
        except PublicationTaskUnavailableError:
            recovery = result.attempt.recovery
            assert recovery.kind is not None
            self._repository.mark_recovery_available(
                attempt_id=attempt_id,
                kind=recovery.kind,
                error_code="TASK_ENQUEUE_FAILED",
                error_message="Publication task could not be ensured",
                env=env,
            )
            raise
        return result

    def _ensure_task(self, attempt_id: int) -> None:
        try:
            enqueue_publication_task(
                self._task_queue,
                attempt_id=attempt_id,
                tenant=get_current_avernet_tenant(),
            )
        except Exception as exc:
            raise PublicationTaskUnavailableError(
                "Publication task could not be ensured"
            ) from exc


__all__ = ["SpaceSkillPublicationService"]
