"""Application facade for service-Bot publication OpenAPI operations.

The legacy HTTP router accepts a bare ``publish_id`` and lets interceptors recover
the Bot afterwards.  The public contract is stricter: every operation first
resolves the tenant-scoped Bot, authorizes the actor, and then proves that the
publication belongs to that exact Bot and environment before touching a domain
service.  That relation check is the IDOR boundary for publication identifiers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    CollaboratorLockService,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.bot_inventory.policies.combo_policy import (
    assert_service_upgrade,
)
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.errors import (
    DeviceServiceError,
    InvalidDeviceStatusError,
)
from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.services.device_instance_service import (
    BindingNotFoundError,
    BotPublishNotFoundError,
    InstanceHealthStatus,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.protocols import ServiceRuntimeDevicePort
from agentclaw.community.core.service_bot.errors import (
    ServiceContainerConflictError,
    ServiceContainerNotFoundError,
    ServiceContainerUpstreamError,
    ServicePublicationConflictError,
    ServicePublicationLockedError,
    ServicePublicationNotFoundError,
    ServicePublicationUnsupportedError,
)
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    BotPublishService,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError
from agentclaw.community.core.service_bot.services.publish_approval_service import (
    PublishApprovalService,
)
from agentclaw.community.core.service_bot.services.publish_flow_service import (
    PublishFlowService,
)
from agentclaw.community.utils.env_utils import get_current_env


_DEPLOYING = {
    PublishStatus.BUILDING.value,
    PublishStatus.BUILT.value,
    PublishStatus.VALIDATE_PUB.value,
    PublishStatus.ONLINE_PUB.value,
}
_PUBLISHED_HISTORY = {
    PublishStatus.SUCCESS.value,
    PublishStatus.UPGRADED.value,
    PublishStatus.RELEASED.value,
}


@dataclass(frozen=True)
class _ServiceEditLockInfo:
    """Public lock projection enriched with service-draft applicability."""

    lock: Any
    holder_name: str | None
    has_collaborators: bool
    is_owner: bool
    need_lock: bool


class ServicePublicationFacade:
    """One authorization and projection boundary for service publications."""

    def __init__(
        self,
        bot_repo: BotRepository,
        publish_repo: BotPublishRepositoryProtocol,
        publish_service: BotPublishService,
        flow_service: PublishFlowService,
        approval_service: PublishApprovalService,
        collaborator_service: CollaboratorService,
        lock_service: CollaboratorLockService,
        bot_service: BotService,
        device_service: ServiceRuntimeDevicePort,
    ) -> None:
        self._bot_repo = bot_repo
        self._publish_repo = publish_repo
        self._publish_service = publish_service
        self._flow_service = flow_service
        self._approval_service = approval_service
        self._collaborator_service = collaborator_service
        self._lock_service = lock_service
        self._bot_service = bot_service
        self._device_service = device_service

    def _resolve_bot(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
        required_level: PermissionLevel = PermissionLevel.MEMBER,
        require_service: bool = True,
    ) -> tuple[dict[str, Any], PermissionLevel]:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise ServicePublicationNotFoundError("bot not found")

        level = self._collaborator_service.get_permission_level(
            bot_pk=bot["id"],
            user_id=actor_id,
            owner_id=bot["owner_id"],
            env=get_current_env(),
        )
        if level < required_level:
            # COSEC: mask authorization failures as absence to prevent Bot-ID probing.
            raise ServicePublicationNotFoundError("bot not found")
        if require_service and bot.get("bot_type") != "service":
            raise ServicePublicationUnsupportedError("bot is not a service bot")
        return bot, level

    def _resolve_publication(
        self,
        bot_id: str,
        publication_id: int,
        *,
        actor_id: str,
        owner_id: str,
        required_level: PermissionLevel = PermissionLevel.MEMBER,
    ) -> tuple[dict[str, Any], BotPublishRecord, PermissionLevel]:
        bot, level = self._resolve_bot(
            bot_id,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=required_level,
        )
        publication = self._publish_repo.get_by_id(publication_id)
        # COSEC: a publication id is never authority. Bind it to the already
        # tenant-scoped Bot PK, source id and runtime environment before use.
        if (
            publication is None
            or publication.source_bot_pk != bot["id"]
            or publication.source_bot_id != bot_id
            or publication.env != get_current_env()
        ):
            raise ServicePublicationNotFoundError("publication not found")
        return bot, publication, level

    @staticmethod
    def _product_status(record: BotPublishRecord) -> str:
        status = record.status
        if status == PublishStatus.DRAFT.value:
            return "draft"
        if status in _DEPLOYING or status == PublishStatus.FAILED.value:
            return "deploying"
        if status == PublishStatus.VALIDATING.value:
            return "prestable"
        if status == PublishStatus.SUCCESS.value:
            return "running"
        if status == PublishStatus.RELEASED.value:
            return "offline"
        raise ServicePublicationConflictError("publication is historical")

    @staticmethod
    def _deployment(record: BotPublishRecord) -> dict[str, Any] | None:
        status = record.status
        if status not in _DEPLOYING and status != PublishStatus.FAILED.value:
            return None
        source_status = str((record.ext or {}).get("source_status") or "")
        effective = source_status if status == PublishStatus.FAILED.value else status
        if effective in {
            PublishStatus.BUILDING.value,
            PublishStatus.BUILT.value,
            PublishStatus.VALIDATE_PUB.value,
        }:
            action, target = "publish_staging", "prestable"
        elif effective == PublishStatus.ONLINE_PUB.value:
            action, target = "publish_online", "running"
        elif effective == PublishStatus.SUCCESS.value:
            action, target = "restart_publish", "running"
        else:
            action, target = "publish_staging", "prestable"
        return {
            "action": action,
            "target": target,
            "status": "failed" if status == PublishStatus.FAILED.value else "running",
            # Raw BaaS/device failures may contain credentials, internal hosts or
            # stack traces. Public callers get a stable message and use logs for detail.
            "error_message": (
                "Deployment failed; inspect Bot logs for details."
                if status == PublishStatus.FAILED.value
                else None
            ),
            "started_at": record.gmt_modified,
            "finished_at": (
                record.gmt_modified if status == PublishStatus.FAILED.value else None
            ),
        }

    @staticmethod
    def _approval(record: BotPublishRecord) -> dict[str, Any] | None:
        approval = (record.ext or {}).get("approval")
        if not isinstance(approval, dict):
            return None
        return {
            "required": True,
            "status": approval.get("status"),
            "approval_id": approval.get("puid"),
            "approval_url": approval.get("approval_url"),
        }

    def _actions(
        self,
        record: BotPublishRecord,
        *,
        level: PermissionLevel,
        all_records: Iterable[BotPublishRecord],
    ) -> list[str]:
        if level < PermissionLevel.MEMBER:
            return []
        actions: list[str] = []
        if record.status == PublishStatus.DRAFT.value:
            actions.append("publish_staging")
            if level >= PermissionLevel.OWNER and not any(
                item.status in _PUBLISHED_HISTORY for item in all_records
            ):
                actions.append("delete")
        elif record.status == PublishStatus.VALIDATING.value:
            actions.extend(("publish_online", "restart_publish", "cancel_staging"))
        elif record.status == PublishStatus.SUCCESS.value:
            actions.extend(("restart_publish", "offline"))
        elif record.status == PublishStatus.FAILED.value:
            actions.append("retry")
        return actions

    def _project(
        self,
        record: BotPublishRecord,
        *,
        level: PermissionLevel,
        all_records: list[BotPublishRecord],
    ) -> dict[str, Any]:
        live = max(
            (
                item
                for item in all_records
                if item.status == PublishStatus.SUCCESS.value
            ),
            key=lambda item: ((item.version or 0), (item.id or 0)),
            default=None,
        )
        return {
            "bot_id": record.source_bot_id,
            "publication_id": record.id,
            "card_id": f"service:{record.source_bot_id}:{record.id}",
            "version": record.version or 0,
            "status": self._product_status(record),
            "internal_status": record.status,
            "live_version": live.version if live else None,
            "deployment": self._deployment(record),
            "approval": self._approval(record),
            "available_actions": self._actions(
                record, level=level, all_records=all_records
            ),
            "created_at": record.gmt_create,
            "updated_at": record.gmt_modified,
        }

    @staticmethod
    def _visible_records(records: list[BotPublishRecord]) -> list[BotPublishRecord]:
        candidates = [
            record
            for record in records
            if record.status != PublishStatus.UPGRADED.value
        ]
        released = max(
            (
                record
                for record in candidates
                if record.status == PublishStatus.RELEASED.value
            ),
            key=lambda item: ((item.version or 0), (item.id or 0)),
            default=None,
        )
        candidates = [
            record
            for record in candidates
            if record.status != PublishStatus.RELEASED.value or record is released
        ]
        candidates.sort(
            key=lambda item: ((item.version or 0), (item.id or 0)), reverse=True
        )
        return candidates[:2]

    def list_publications(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]:
        bot, level = self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        records = self._publish_repo.list_by_source_bot(bot["id"], get_current_env())
        cards = [
            self._project(record, level=level, all_records=records)
            for record in self._visible_records(records)
        ]
        return {"bot_id": bot_id, "items": cards}

    def list_containers(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]:
        """Return live service-Bot instances after collaborator authorization."""
        self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        try:
            result = self._device_service.get_instances_by_bot(
                bot_id=bot_id,
                health_check=True,
            )
        except (BotPublishNotFoundError, BindingNotFoundError) as exc:
            raise ServiceContainerConflictError("service runtime is not live") from exc
        except (DeviceServiceError, BaasServiceError) as exc:
            raise ServiceContainerUpstreamError("container provider failed") from exc
        return {"bot_id": bot_id, "instances": result.get("devices", [])}

    def restart_container(
        self,
        bot_id: str,
        instance_id: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        """Restart one abnormal live instance; only the Bot owner may do so."""
        self._resolve_bot(
            bot_id,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.OWNER,
        )
        current = self.list_containers(
            bot_id,
            actor_id=actor_id,
            owner_id=owner_id,
        )
        instance = next(
            (
                item
                for item in current["instances"]
                if item.get("device_uuid") == instance_id
            ),
            None,
        )
        # COSEC: bind the path instance id to the authorized Bot's live device
        # inventory before forwarding it to BaaS; an instance id is not authority.
        if instance is None:
            raise ServiceContainerNotFoundError("container not found")
        if instance.get("health_status") != InstanceHealthStatus.ABNORMAL:
            raise ServiceContainerConflictError("only abnormal containers may restart")

        operator = OperatorContext(
            staff_id=actor_id,
            staff=actor_id,
            nick_name=actor_id,
            operator_name=actor_id,
        )
        try:
            result = self._device_service.restart_device_by_bot(
                bot_id=bot_id,
                device_uuid=instance_id,
                operator=operator,
            )
        except (BotPublishNotFoundError, BindingNotFoundError) as exc:
            raise ServiceContainerConflictError("service runtime is not live") from exc
        except InvalidDeviceStatusError as exc:
            raise ServiceContainerNotFoundError("container not found") from exc
        except (DeviceServiceError, BaasServiceError) as exc:
            raise ServiceContainerUpstreamError("container provider failed") from exc
        return {
            "bot_id": bot_id,
            "instance_id": instance_id,
            "publish_id": result.get("publish_id"),
            "accepted": True,
        }

    def get_publication(
        self,
        bot_id: str,
        publication_id: int,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        bot, record, level = self._resolve_publication(
            bot_id, publication_id, actor_id=actor_id, owner_id=owner_id
        )
        records = self._publish_repo.list_by_source_bot(bot["id"], get_current_env())
        return self._project(record, level=level, all_records=records)

    def convert_to_service(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]:
        bot, _ = self._resolve_bot(
            bot_id,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.OWNER,
            require_service=False,
        )
        if bot.get("bot_type") == "service":
            raise ServicePublicationConflictError("bot is already a service bot")
        decision = assert_service_upgrade(str(bot.get("active_engine") or ""))
        if bot.get("bot_type") == "desktop" or not decision.ok:
            raise ServicePublicationUnsupportedError(
                decision.reason or "local bots cannot be serviced"
            )
        result = self._publish_service.upgrade_bot_to_service(
            bot_id=bot_id, owner_id=bot["owner_id"]
        )
        record = result.get("publish_record")
        if record is None:
            records = self._publish_repo.list_by_source_bot(
                bot["id"], get_current_env()
            )
            record = max(
                records,
                key=lambda item: (item.version or 0, item.id or 0),
                default=None,
            )
        if record is None:
            raise ServicePublicationConflictError(
                "service conversion did not create a publication"
            )
        return self.get_publication(
            bot_id,
            record.id,
            actor_id=actor_id,
            owner_id=owner_id,
        )

    @staticmethod
    def _service_config(bot: dict[str, Any]) -> dict[str, Any]:
        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except (json.JSONDecodeError, TypeError):
                ext = {}
        if not isinstance(ext, dict):
            return {}
        config = ext.get("service_bot_config") or {}
        return config if isinstance(config, dict) else {}

    def get_service_config(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]:
        bot, _ = self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        config = self._service_config(bot)
        return {
            "bot_id": bot_id,
            "should_approval": config.get("should_approval") is True,
        }

    def update_service_config(
        self,
        bot_id: str,
        *,
        actor_id: str,
        owner_id: str,
        should_approval: bool,
    ) -> dict[str, Any]:
        bot, _ = self._resolve_bot(
            bot_id,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.OWNER,
        )
        config = {
            **self._service_config(bot),
            "should_approval": should_approval,
        }
        # COSEC: write through the owner-scoped Bot service after the live Owner
        # permission check above; caller-supplied owner_id is not direct authority.
        self._bot_service.update_bot_ext(
            bot_id,
            bot["owner_id"],
            {"service_bot_config": config},
        )
        return {"bot_id": bot_id, "should_approval": should_approval}

    def _require_status(
        self, record: BotPublishRecord, *allowed: PublishStatus
    ) -> None:
        if record.status not in {status.value for status in allowed}:
            raise ServicePublicationConflictError("invalid publication state")

    def _resolve_action_record(
        self,
        bot_id: str,
        *statuses: PublishStatus,
        actor_id: str,
        owner_id: str,
        required_level: PermissionLevel = PermissionLevel.MEMBER,
    ) -> tuple[dict[str, Any], BotPublishRecord]:
        bot, _ = self._resolve_bot(
            bot_id,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=required_level,
        )
        allowed = {status.value for status in statuses}
        records = self._publish_repo.list_by_source_bot(bot["id"], get_current_env())
        record = max(
            (item for item in records if item.status in allowed),
            key=lambda item: ((item.version or 0), (item.id or 0)),
            default=None,
        )
        if record is None:
            raise ServicePublicationConflictError("no actionable publication")
        return bot, record

    def _require_draft_lock(self, bot_id: str, owner_id: str, actor_id: str) -> None:
        info = self._lock_service.get_lock_info(bot_id, owner_id, actor_id)
        if not info.has_collaborators:
            return
        if info.lock is None or info.lock.holder_user_id != actor_id:
            raise ServicePublicationLockedError("edit lock required")

    def _operation(
        self,
        record: BotPublishRecord,
        *,
        action: str,
        result: Any,
        approval: Any = None,
    ) -> dict[str, Any]:
        approval_data = None
        if approval is not None:
            approval_data = {
                "required": approval.should_approval,
                "status": approval.status,
                "approval_id": (approval.approval or {}).get("puid"),
                "approval_url": (approval.approval or {}).get("approval_url"),
            }
        return {
            "bot_id": record.source_bot_id,
            "publication_id": record.id,
            "action": action,
            "accepted": True,
            "approval": approval_data,
            # The domain result may carry BaaS workflow ids, device ids and raw
            # upstream messages.  The public acknowledgement deliberately does
            # not serialize it; callers poll the publication projection instead.
            "operation_status": (
                "waiting_approval"
                if approval is not None and approval.should_approval
                else "pending"
            ),
        }

    async def advance(
        self,
        bot_id: str,
        stage: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        if stage in {"staging", "prestable"}:
            bot, record = self._resolve_action_record(
                bot_id,
                PublishStatus.DRAFT,
                actor_id=actor_id,
                owner_id=owner_id,
            )
            self._require_draft_lock(bot_id, bot["owner_id"], actor_id)
            result = await self._flow_service.process(record.id, actor_id)
            return self._operation(
                record, action="publish_staging", result=result.model_dump()
            )
        if stage == "online":
            _, record = self._resolve_action_record(
                bot_id,
                PublishStatus.VALIDATING,
                actor_id=actor_id,
                owner_id=owner_id,
            )
            approval = await self._approval_service.check_and_process_should_approval(
                record, actor_id
            )
            result: Any = None
            if not approval.should_approval:
                flow_result = await self._flow_service.process(record.id, actor_id)
                result = flow_result.model_dump()
            return self._operation(
                record, action="publish_online", result=result, approval=approval
            )
        raise ServicePublicationConflictError("unsupported lifecycle stage")

    def restart(
        self,
        bot_id: str,
        stage: str,
        *,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        status = {
            "staging": PublishStatus.VALIDATING,
            "prestable": PublishStatus.VALIDATING,
            "online": PublishStatus.SUCCESS,
        }.get(stage)
        if status is None:
            raise ServicePublicationConflictError("unsupported lifecycle stage")
        _, record = self._resolve_action_record(
            bot_id,
            status,
            actor_id=actor_id,
            owner_id=owner_id,
        )
        result = self._flow_service.restart_bot(record.id, actor_id)
        if not result.get("success"):
            raise ServicePublicationConflictError("restart is unavailable")
        return self._operation(record, action="restart_publish", result=result)

    async def cancel_staging(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]:
        _, record = self._resolve_action_record(
            bot_id,
            PublishStatus.VALIDATING,
            actor_id=actor_id,
            owner_id=owner_id,
        )
        result = await self._publish_service.offline_publish(record.id)
        return self._operation(record, action="cancel_staging", result=result)

    async def offline(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]:
        _, record = self._resolve_action_record(
            bot_id,
            PublishStatus.SUCCESS,
            actor_id=actor_id,
            owner_id=owner_id,
        )
        approval = await self._approval_service.check_and_process_offline_approval(
            record, actor_id
        )
        result: Any = None
        if not approval.should_approval:
            result = await self._publish_service.offline_publish(record.id)
        return self._operation(
            record, action="offline", result=result, approval=approval
        )

    async def retry(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]:
        _, record = self._resolve_action_record(
            bot_id,
            PublishStatus.FAILED,
            actor_id=actor_id,
            owner_id=owner_id,
        )
        result = await self._flow_service.retry(record.id, actor_id)
        return self._operation(record, action="retry", result=result.model_dump())

    def delete_initial_draft(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> bool:
        _, record = self._resolve_action_record(
            bot_id,
            PublishStatus.DRAFT,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.OWNER,
        )
        if not self._publish_service.can_delete_bot(record.id):
            raise ServicePublicationConflictError("service bot cannot be deleted")
        deleted = bool(self._publish_service.delete_service_bot(record.id))
        if not deleted:
            raise ServicePublicationConflictError("service bot deletion failed")
        return True

    def _service_lock_info(
        self, bot: dict[str, Any], *, actor_id: str
    ) -> _ServiceEditLockInfo:
        info = self._lock_service.get_lock_info(
            bot["bot_id"], bot["owner_id"], actor_id
        )
        records = self._publish_repo.list_by_source_bot(bot["id"], get_current_env())
        has_draft = any(
            record.status == PublishStatus.DRAFT.value for record in records
        )
        return _ServiceEditLockInfo(
            lock=info.lock,
            holder_name=info.holder_name,
            has_collaborators=info.has_collaborators,
            is_owner=info.is_owner,
            need_lock=info.has_collaborators and has_draft,
        )

    def get_lock(self, bot_id: str, *, actor_id: str, owner_id: str) -> Any:
        bot, _ = self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        return self._service_lock_info(bot, actor_id=actor_id)

    def _lockable_draft(self, bot: dict[str, Any], *, actor_id: str) -> bool:
        info = self._service_lock_info(bot, actor_id=actor_id)
        if not info.has_collaborators:
            return False
        if not info.need_lock:
            raise ServicePublicationConflictError(
                "edit lock is only available for a service bot draft"
            )
        return True

    def acquire_lock(self, bot_id: str, *, actor_id: str, owner_id: str) -> Any:
        bot, _ = self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        if not self._lockable_draft(bot, actor_id=actor_id):
            return None
        return self._lock_service.acquire_lock(bot_id, bot["owner_id"], actor_id)

    def release_lock(self, bot_id: str, *, actor_id: str, owner_id: str) -> bool:
        bot, _ = self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        return bool(
            self._lock_service.release_lock(bot_id, bot["owner_id"], actor_id, False)
        )

    def steal_lock(self, bot_id: str, *, actor_id: str, owner_id: str) -> Any:
        bot, _ = self._resolve_bot(
            bot_id,
            actor_id=actor_id,
            owner_id=owner_id,
            # COSEC: the lock service assumes authorization was already checked.
            # PD explicitly grants lock takeover to every Bot member, so prove
            # that relationship here before invoking the forceful operation.
            required_level=PermissionLevel.MEMBER,
        )
        if not self._lockable_draft(bot, actor_id=actor_id):
            return None
        return self._lock_service.steal_lock(bot_id, bot["owner_id"], actor_id)
