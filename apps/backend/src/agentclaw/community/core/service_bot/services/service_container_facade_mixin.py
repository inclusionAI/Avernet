"""Container observation, restart and delta-scale operations for service Bots."""

from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
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
from agentclaw.community.core.service_bot.errors import (
    ServiceContainerConflictError,
    ServiceContainerNotFoundError,
    ServiceContainerUpstreamError,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError
from agentclaw.community.utils.env_utils import get_current_env


class ServiceContainerFacadeMixin:
    """Keep the container surface's authorization and IDOR fences together."""

    @staticmethod
    def _scale_approval(record: BotPublishRecord | None) -> dict[str, Any] | None:
        """Project only the scale approval, never another lifecycle approval."""
        if record is None:
            return None
        approval = (record.ext or {}).get("approval")
        if not isinstance(approval, dict) or approval.get("action") != "scale":
            return None
        return {
            "status": approval.get("status"),
            "approval_url": approval.get("approval_url"),
            "current_count": approval.get("current_count"),
            "delta": approval.get("delta"),
            "target_count": approval.get("target_count"),
        }

    def _latest_live_record(
        self, bot: dict[str, Any], records: list[BotPublishRecord] | None = None
    ) -> BotPublishRecord | None:
        candidates = (
            records
            if records is not None
            else self._publish_repo.list_by_source_bot(bot["id"], get_current_env())
        )
        return max(
            (
                item
                for item in candidates
                if item.status == PublishStatus.SUCCESS.value
                and item.source_bot_pk == bot["id"]
                and item.source_bot_id == bot.get("bot_id")
                and item.env == get_current_env()
            ),
            key=lambda item: ((item.version or 0), (item.id or 0)),
            default=None,
        )

    @staticmethod
    def _online_binding_id(record: BotPublishRecord) -> int:
        """Read the live binding from the already-authorized publication."""
        ext = record.ext or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except (TypeError, json.JSONDecodeError):
                ext = {}
        binding_id = (ext.get("binding") or {}).get("online") if isinstance(ext, dict) else None
        if isinstance(binding_id, bool):
            raise ServiceContainerConflictError("service runtime is not live")
        try:
            parsed = int(binding_id)
        except (TypeError, ValueError):
            raise ServiceContainerConflictError("service runtime is not live") from None
        if parsed < 1:
            raise ServiceContainerConflictError("service runtime is not live")
        return parsed

    def _live_instances(
        self, binding_id: int
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Read instances through the live binding, never by a non-unique bot_id."""
        try:
            result = self._device_service.get_instances(
                binding_id=binding_id,
                health_check=True,
            )
        except (BotPublishNotFoundError, BindingNotFoundError) as exc:
            raise ServiceContainerConflictError("service runtime is not live") from exc
        except (DeviceServiceError, BaasServiceError) as exc:
            raise ServiceContainerUpstreamError("container provider failed") from exc
        return list(result.get("devices", [])), result

    def list_containers(
        self, bot_id: str, *, actor_id: str, owner_id: str
    ) -> dict[str, Any]:
        """Return live service-Bot instances after collaborator authorization."""
        bot, level = self._resolve_bot(bot_id, actor_id=actor_id, owner_id=owner_id)
        live = self._latest_live_record(bot)
        if live is None:
            raise ServiceContainerConflictError("service runtime is not live")
        instances, _ = self._live_instances(self._online_binding_id(live))
        result: dict[str, Any] = {"bot_id": bot_id, "instances": instances}
        scale_approval = self._scale_approval(live)
        # The approval URL is a capability for the Owner, not a MEMBER read.
        if scale_approval is not None and level >= PermissionLevel.OWNER:
            result["scale_approval"] = scale_approval
        return result

    async def scale_containers(
        self,
        bot_id: str,
        *,
        delta: int,
        actor_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        """Create or reuse an approval for a delta-based service-Bot scale.

        ``delta`` is deliberately a delta, not a target: the current live
        instance count is read inside the authorized scope immediately before
        the approval is created, then frozen into the approval record.
        """
        bot, _ = self._resolve_bot(
            bot_id,
            actor_id=actor_id,
            owner_id=owner_id,
            required_level=PermissionLevel.OWNER,
        )
        if str(bot.get("active_engine") or "").strip().lower() == "teclaw":
            raise ServiceContainerConflictError("teclaw service bots do not support scaling")
        if delta == 0:
            raise ServiceContainerConflictError("delta must not be zero")
        records = self._publish_repo.list_by_source_bot(bot["id"], get_current_env())
        live = self._latest_live_record(bot, records)
        if live is None:
            raise ServiceContainerConflictError("service runtime is not live")
        instances, _ = self._live_instances(self._online_binding_id(live))
        current_count = len(instances)
        if current_count < 1:
            raise ServiceContainerConflictError("service runtime is not live")
        target_count = current_count + delta
        if target_count < 1:
            raise ServiceContainerConflictError("target count must be at least 1")

        existing = self._scale_approval(live)
        if existing is not None and existing.get("status") in {
            "PROCESSING", "AGREED", "EXECUTING",
        }:
            if existing.get("target_count") != target_count:
                raise ServiceContainerConflictError(
                    "an existing scale approval targets "
                    f"{existing.get('target_count')} instances"
                )
            return {
                "bot_id": bot_id,
                "accepted": True,
                "current_count": existing.get("current_count"),
                "delta": existing.get("delta"),
                "target_count": existing.get("target_count"),
                "approval": existing,
            }

        result = await self._approval_service.request_scale_approval(
            live,
            operator=actor_id,
            current_count=current_count,
            delta=delta,
            target_count=target_count,
        )
        if result.status != "PROCESSING":
            raise ServiceContainerConflictError(result.message)
        raw_approval = dict(result.approval or {})
        approval = {
            "status": raw_approval.get("status"),
            "approval_url": raw_approval.get("approval_url"),
            "current_count": raw_approval.get("current_count"),
            "delta": raw_approval.get("delta"),
            "target_count": raw_approval.get("target_count"),
        }
        return {
            "bot_id": bot_id,
            "accepted": True,
            "current_count": current_count,
            "delta": delta,
            "target_count": target_count,
            "approval": approval,
        }

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
