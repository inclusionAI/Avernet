"""Draft restore business logic for service Bot publish records."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishOperationRecord,
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.core.repository.protocols.publishing import PublishOperationRepository
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
    resolve_device_provider,
)
from agentclaw.community.core.service_bot.services.publish_exceptions import (
    BotPublishServiceError,
    PublishNotFoundError,
)
from agentclaw.community.core.service_bot.types import PublishStage


if TYPE_CHECKING:
    from agentclaw.community.core.task_queue.services.task_queue_service import (
        TaskQueueService,
    )
    from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
    from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
    from agentclaw.community.core.service_bot.services.publish_flow_service import (
        PublishFlowService,
    )

_DRAFT_RESTORE_TIMEOUT_SECONDS = 1800
_DRAFT_RESTORE_TIMEOUT_ERROR = "恢复草稿超时（默认限制 30 分钟）"


class PublishDraftRestoreMixin:
    """Restore a DRAFT source container from its immediately previous artifact.

    Every attempt is recorded as a ``DRAFT_RESTORE`` row in the publish operation
    ledger. The publish record deliberately remains ``DRAFT`` throughout, and its
    ``ext`` is not used as an operation-state store.
    """

    _repo: BotPublishRepositoryProtocol
    _publish_operation_repo: PublishOperationRepository
    _publish_flow_service_provider: Callable[[], PublishFlowService]
    _task_queue_service: TaskQueueService

    def _latest_draft_restore_operation(
        self, publish_id: int
    ) -> PublishOperationRecord | None:
        operation = self._publish_operation_repo.get_latest_by_kind(
            publish_id,
            str(PublishOperationKind.DRAFT_RESTORE),
            PublishStage.DRAFT.value,
        )
        return self._converge_expired_draft_restore_operation(operation)

    def _converge_expired_draft_restore_operation(
        self, operation: PublishOperationRecord | None
    ) -> PublishOperationRecord | None:
        """Read-repair an expired non-terminal restore operation to ``FAILED``.

        The generic task queue retires an overdue row as ``TIMED_OUT`` without
        invoking its handler, so it cannot update the service-bot ledger. Applying
        this repair at every draft-restore read/start boundary prevents a stale
        ``PENDING``/``ID_RECORDED`` row from blocking the draft forever after a
        worker outage. The repository transition is CAS-protected and is therefore
        safe when a worker completes concurrently.
        """
        if operation is None or operation.state in PublishOperationState.terminal():
            return operation

        deadline_at_raw = (operation.params or {}).get("deadline_at")
        if not isinstance(deadline_at_raw, str) or not deadline_at_raw:
            return operation
        try:
            deadline_at = datetime.fromisoformat(deadline_at_raw)
        except ValueError:
            return operation
        if datetime.now(tz=deadline_at.tzinfo) < deadline_at:
            return operation

        failed = self._publish_operation_repo.fail(
            operation.id, _DRAFT_RESTORE_TIMEOUT_ERROR
        )
        return (
            failed
            or self._publish_operation_repo.get_by_id(operation.id)
            or operation
        )

    def _resolve_draft_restore_target(
        self, publish_id: int
    ) -> tuple[BotPublishRecord | None, BotPublishRecord | None, str]:
        draft = self._repo.get_by_id(publish_id)
        if not draft:
            return None, None, f"发布单不存在: publish_id={publish_id}"
        if draft.status != PublishStatus.DRAFT:
            return draft, None, f"只有 DRAFT 状态可以恢复草稿，当前状态: {draft.status}"

        latest_op = self._latest_draft_restore_operation(publish_id)
        if latest_op is not None and latest_op.state not in PublishOperationState.terminal():
            return draft, None, "草稿正在恢复中，请勿重复操作"

        if not draft.last_pub_id or draft.last_pub_id <= 0:
            return draft, None, "首次创建的草稿没有历史版本构造物"

        target = self._repo.get_by_id(draft.last_pub_id)
        if not target:
            return draft, None, f"上一版本不存在: last_pub_id={draft.last_pub_id}"
        if target.source_bot_pk != draft.source_bot_pk or target.env != draft.env:
            return draft, None, "上一版本与当前草稿不属于同一个 Bot 或环境"

        bot = self._bot_service.get_bot(
            bot_id=draft.source_bot_id,
            user_id=draft.owner_id,
        )
        if bot is None:
            return draft, target, f"Bot不存在: {draft.source_bot_id}"

        target_ext = target.ext or {}
        device_provider = resolve_device_provider(
            bot.get("active_engine") if isinstance(bot, dict) else None
        )
        is_teclaw = device_provider == TECLAW_DEVICE_PROVIDER
        if is_teclaw:
            config_artifact = target_ext.get("config_artifact")
            if not isinstance(config_artifact, dict):
                return draft, target, "上一版本没有可用的 config_artifact 构造物"
            if config_artifact.get("engine_type") != TECLAW_DEVICE_PROVIDER:
                return draft, target, "上一版本的 config_artifact 不是 teclaw 构造物"
        elif not target_ext.get("migration_path"):
            return draft, target, "上一版本没有可用的 migration_path 构造物"
        return draft, target, "可以恢复草稿"

    def can_restore_draft(self, publish_id: int) -> tuple[bool, str, dict | None]:
        """Return whether a draft can be restored and the selected source version."""
        _draft, target, reason = self._resolve_draft_restore_target(publish_id)
        if not target or reason != "可以恢复草稿":
            return False, reason, None
        return (
            True,
            reason,
            {
                "source_publish_id": target.id,
                "source_version": target.version,
            },
        )

    def get_draft_restore_status(
        self,
        publish_id: int,
        operation_id: int,
    ) -> dict:
        """Return one draft-restore attempt from the durable operation ledger.

        The publish record remains ``DRAFT`` throughout a restore, so its status
        cannot represent operation progress.  The operation id returned by
        :meth:`restore_draft` is the stable polling handle.
        """
        if self._repo.get_by_id(publish_id) is None:
            raise PublishNotFoundError(
                f"发布单不存在: publish_id={publish_id}"
            )

        operation = self._publish_operation_repo.get_by_id(operation_id)
        if (
            operation is None
            or operation.publish_id != publish_id
            or operation.operation_kind
            != str(PublishOperationKind.DRAFT_RESTORE)
            or operation.stage != PublishStage.DRAFT.value
        ):
            # Return the same not-found response for a missing id and an id that
            # belongs to another publish/kind.  Apart from preventing accidental
            # cross-operation reads, this avoids exposing whether an arbitrary
            # ledger id exists.
            raise PublishNotFoundError(
                "草稿恢复操作不存在: "
                f"publish_id={publish_id}, operation_id={operation_id}"
            )

        operation = self._converge_expired_draft_restore_operation(operation)
        if operation is None:  # defensive: ownership was established immediately above
            raise PublishNotFoundError(
                "草稿恢复操作不存在: "
                f"publish_id={publish_id}, operation_id={operation_id}"
            )

        operation_state = operation.state
        if operation_state in {
            PublishOperationState.PENDING.value,
            PublishOperationState.ID_RECORDED.value,
        }:
            status = "restoring"
        elif operation_state == PublishOperationState.COMPLETED.value:
            status = "success"
        else:
            status = "failed"

        params = operation.params or {}
        result = operation.result or {}
        is_terminal = operation_state in PublishOperationState.terminal()
        return {
            "draft_publish_id": publish_id,
            "operation_id": operation.id,
            "task_id": operation.request_id,
            "attempt": operation.attempt,
            "status": status,
            "operation_state": operation_state,
            "source_publish_id": params.get("source_publish_id"),
            "source_version": params.get("source_version"),
            "baas_publish_id": operation.baas_publish_id,
            "baas_status": result.get("baas_status"),
            "restore_type": result.get("restore_type"),
            "draft_binding_id": result.get("draft_binding_id"),
            "started_at": operation.gmt_create.isoformat(),
            "completed_at": (
                operation.gmt_modified.isoformat() if is_terminal else None
            ),
            "error": operation.last_error,
        }

    def _open_draft_restore_operation(
        self,
        *,
        draft: BotPublishRecord,
        target: BotPublishRecord,
        operator: str,
        bot_uuid: str,
    ) -> PublishOperationRecord:
        """Insert one ledger attempt, rejecting a concurrent in-flight restore."""
        # Local import avoids BotPublishService -> mixin -> publish_flow package ->
        # ext_state -> BotPublishService during module initialization.
        from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
            OperationAlreadyInFlightError,
            open_publish_operation,
        )

        try:
            return open_publish_operation(
                self._publish_operation_repo,
                publish_id=draft.id,
                kind=PublishOperationKind.DRAFT_RESTORE,
                stage=PublishStage.DRAFT,
                operator=operator,
                bot_uuid=bot_uuid,
                params={
                    # The API does not accept this value. Freeze the source
                    # derived from last_pub_id so retries and audit read the
                    # exact input selected when this attempt was opened.
                    "source_publish_id": target.id,
                    "source_version": target.version,
                    # Persist the business deadline with the operation. This
                    # avoids relying on application-vs-DB clock alignment
                    # when a worker resumes the operation after a restart.
                    "deadline_at": (
                        datetime.now()
                        + timedelta(seconds=_DRAFT_RESTORE_TIMEOUT_SECONDS)
                    ).isoformat(),
                },
                reject_if_in_flight=True,
            )
        except OperationAlreadyInFlightError as exc:
            raise BotPublishServiceError("草稿正在恢复中，请勿重复操作") from exc

    async def restore_draft(self, publish_id: int, operator: str) -> dict:
        """Start restoring the draft and return its persisted operation handle."""
        # Local import avoids BotPublishService -> mixin -> publish_flow package ->
        # ext_state -> BotPublishService during module initialization.
        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            enqueue_draft_restore,
        )

        draft, target, reason = self._resolve_draft_restore_target(publish_id)
        if not draft or not target or reason != "可以恢复草稿":
            raise BotPublishServiceError(f"无法恢复草稿: {reason}")

        bot = self._bot_service.get_bot(
            bot_id=draft.source_bot_id,
            user_id=draft.owner_id,
        )
        if not bot or not bot.get("binding_id"):
            raise BotPublishServiceError("草稿 Bot 缺少有效的设备绑定")
        binding = self.get_device_binding_by_id(bot["binding_id"])
        if not binding or not binding.device_id:
            raise BotPublishServiceError("草稿设备绑定不存在或缺少 device_id")

        op = self._open_draft_restore_operation(
            draft=draft,
            target=target,
            operator=operator,
            bot_uuid=binding.device_id,
        )
        if op.id is None:
            raise BotPublishServiceError("草稿恢复 operation 创建失败")

        try:
            enqueue_draft_restore(
                self._task_queue_service,
                draft_publish_id=publish_id,
                operation_id=op.id,
                operator=operator,
            )
        except Exception as exc:
            self._publish_operation_repo.fail(
                op.id, f"持久化恢复任务入队失败: {exc}"
            )
            raise BotPublishServiceError(f"恢复任务入队失败: {exc}") from exc

        return {
            "draft_publish_id": publish_id,
            "source_publish_id": target.id,
            "source_version": target.version,
            "status": "restoring",
            "operation_id": op.id,
            # Keep the old response key as a compatibility alias. The durable
            # identity is now operation_id/request_id from the ledger.
            "task_id": op.request_id,
            "started_at": op.gmt_create.isoformat(),
        }
