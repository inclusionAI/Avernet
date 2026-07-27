"""Draft restore business logic for service Bot publish records."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.publish_exceptions import BotPublishServiceError
from agentclaw.community.log import get_logger


if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.repository.bot_publish_repository import BotPublishRepositoryProtocol
    from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowService

logger = get_logger()

# Keep strong references to fire-and-forget tasks until they finish.  This is
# process-local task bookkeeping, not the operation state source; the durable
# state exposed to clients is publish.ext.draft_restore.
_DRAFT_RESTORE_TASKS: set[asyncio.Task[None]] = set()


class PublishDraftRestoreMixin:
    """Restore a DRAFT source container from its immediately previous artifact.

    The publish state deliberately remains ``DRAFT`` throughout the operation.
    Progress is persisted under ``publish.ext.draft_restore`` so clients can
    observe it after refresh.  Verify/online bindings are never touched.
    """

    _repo: BotPublishRepositoryProtocol
    _publish_flow_service_provider: Callable[[], PublishFlowService]

    def _resolve_draft_restore_target(
        self, publish_id: int
    ) -> tuple[BotPublishRecord | None, BotPublishRecord | None, str]:
        draft = self._repo.get_by_id(publish_id)
        if not draft:
            return None, None, f"发布单不存在: publish_id={publish_id}"
        if draft.status != PublishStatus.DRAFT:
            return draft, None, f"只有 DRAFT 状态可以恢复草稿，当前状态: {draft.status}"

        restore_state = (draft.ext or {}).get("draft_restore") or {}
        if restore_state.get("status") == "restoring":
            return draft, None, "草稿正在恢复中，请勿重复操作"

        if not draft.last_pub_id or draft.last_pub_id <= 0:
            return draft, None, "首次创建的草稿没有历史版本构造物"

        target = self._repo.get_by_id(draft.last_pub_id)
        if not target:
            return draft, None, f"上一版本不存在: last_pub_id={draft.last_pub_id}"
        if target.source_bot_pk != draft.source_bot_pk or target.env != draft.env:
            return draft, None, "上一版本与当前草稿不属于同一个 Bot 或环境"

        target_ext = target.ext or {}
        if not target_ext.get("migration_path"):
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

    def _update_draft_restore_state(
        self,
        publish_id: int,
        task_id: str,
        patch: dict[str, Any],
        *,
        require_existing_task: bool,
    ) -> bool:
        """Merge one restore-state update without changing ``publish.status``.

        Completion updates are accepted only while the same ``task_id`` is still
        recorded.  This prevents an old task from overwriting the state of a
        later retry.
        """
        current = self._repo.get_by_id(publish_id)
        if not current or current.status != PublishStatus.DRAFT:
            logger.warning(
                "[draft_restore] skip state update because draft changed: "
                "publish_id=%s task_id=%s",
                publish_id,
                task_id,
            )
            return False

        ext = dict(current.ext or {})
        previous = dict(ext.get("draft_restore") or {})
        if require_existing_task and previous.get("task_id") != task_id:
            logger.warning(
                "[draft_restore] skip stale task update: publish_id=%s "
                "task_id=%s current_task_id=%s",
                publish_id,
                task_id,
                previous.get("task_id"),
            )
            return False

        ext["draft_restore"] = {**previous, **patch, "task_id": task_id}
        self.update_publish_ext(publish_id, ext)
        return True

    async def _execute_restore_draft_background(
        self,
        *,
        draft_publish_id: int,
        source_publish_id: int,
        task_id: str,
        operator: str,
    ) -> None:
        """Execute the slow file copy and persist its terminal state."""
        try:
            result = await self._publish_flow_service_provider().execute_restore_draft(
                draft_publish_id=draft_publish_id,
                source_publish_id=source_publish_id,
                operator=operator,
            )
        except Exception as exc:
            completed_at = datetime.now().isoformat()
            try:
                self._update_draft_restore_state(
                    draft_publish_id,
                    task_id,
                    {
                        "status": "failed",
                        "completed_at": completed_at,
                        "error": str(exc),
                    },
                    require_existing_task=True,
                )
            except Exception:
                logger.exception(
                    "[draft_restore] failed to persist failure state: "
                    "publish_id=%s task_id=%s",
                    draft_publish_id,
                    task_id,
                )
            logger.exception(
                "[draft_restore] background restore failed: publish_id=%s "
                "source_publish_id=%s task_id=%s",
                draft_publish_id,
                source_publish_id,
                task_id,
            )
            return

        completed_at = datetime.now().isoformat()
        terminal_patch: dict[str, Any] = {
            "status": "success",
            "completed_at": completed_at,
            "restored_at": completed_at,
            "restored_by": operator,
            "error": None,
        }
        # Keep small operation metadata useful for troubleshooting, but never
        # copy historical artifact/binding/publish pointers onto the draft row.
        for key in ("restore_type", "draft_binding_id", "artifact_path", "draft_path"):
            if key in result:
                terminal_patch[key] = result[key]

        try:
            self._update_draft_restore_state(
                draft_publish_id,
                task_id,
                terminal_patch,
                require_existing_task=True,
            )
        except Exception:
            logger.exception(
                "[draft_restore] restored files but failed to persist success state: "
                "publish_id=%s task_id=%s",
                draft_publish_id,
                task_id,
            )

    async def restore_draft(self, publish_id: int, operator: str) -> dict:
        """Start restoring the draft and return immediately with ``restoring``."""
        draft, target, reason = self._resolve_draft_restore_target(publish_id)
        if not draft or not target or reason != "可以恢复草稿":
            raise BotPublishServiceError(f"无法恢复草稿: {reason}")

        task_id = f"draft_restore_{uuid.uuid4().hex}"
        started_at = datetime.now().isoformat()
        started = self._update_draft_restore_state(
            publish_id,
            task_id,
            {
                "status": "restoring",
                "source_publish_id": target.id,
                "source_version": target.version,
                "operator": operator,
                "started_at": started_at,
                "completed_at": None,
                "error": None,
            },
            require_existing_task=False,
        )
        if not started:
            raise BotPublishServiceError("草稿状态已发生变化，无法启动恢复")

        try:
            task = asyncio.create_task(
                self._execute_restore_draft_background(
                    draft_publish_id=publish_id,
                    source_publish_id=target.id,
                    task_id=task_id,
                    operator=operator,
                ),
                name=task_id,
            )
        except Exception as exc:
            self._update_draft_restore_state(
                publish_id,
                task_id,
                {
                    "status": "failed",
                    "completed_at": datetime.now().isoformat(),
                    "error": f"后台任务启动失败: {exc}",
                },
                require_existing_task=True,
            )
            raise BotPublishServiceError(f"后台恢复任务启动失败: {exc}") from exc

        _DRAFT_RESTORE_TASKS.add(task)
        task.add_done_callback(_DRAFT_RESTORE_TASKS.discard)

        return {
            "draft_publish_id": publish_id,
            "source_publish_id": target.id,
            "source_version": target.version,
            "status": "restoring",
            "task_id": task_id,
            "started_at": started_at,
        }
