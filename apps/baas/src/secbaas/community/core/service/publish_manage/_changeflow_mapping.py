"""Pure BaaS -> ChangeFlow field mappings.

Kept free of repositories so the mapping contract can be unit-tested without a
database or network.  See the Dima P1 spec for the adopted enums.
"""

from __future__ import annotations

from typing import Any

from secbaas.community.api.publish_manage import (
    BatchStatus,
    PublishRecordResult,
    PublishStatus,
    PublishType,
)
from secbaas.community.spi.changeflow import (
    ChangeFlowBatchContext,
    ChangeFlowBatchTarget,
    ChangeFlowRegisterContext,
    ChangeFlowStage,
    ChangeFlowTerminalContext,
)

CHANGEFLOW_SOURCE = "teamclaw-secbaas"
ONLINE_PUBLISH_STAGE = "online"
CF_AUDITED_PUBLISH_TYPES = frozenset(
    {
        PublishType.CREATE.value,
        PublishType.UPDATE.value,
        PublishType.RESTART.value,
    }
)


def map_environment(env: str) -> str:
    """Map BaaS env to the CF environment enum (P1: prod or pre only)."""
    return "prod" if env == "prod" else "pre"


def is_online_publish(publish_record: Any) -> bool:
    """Whether this publish opted into the online-only ChangeFlow audit."""
    extra = getattr(publish_record, "extra_config", None) or {}
    return (
        extra.get("publish_stage") == ONLINE_PUBLISH_STAGE
        and getattr(publish_record, "publish_type", None) in CF_AUDITED_PUBLISH_TYPES
    )


def map_batch_outcome(batch_status: str) -> str:
    if batch_status == BatchStatus.COMPLETED.value:
        return "succeeded"
    if batch_status == BatchStatus.FAILED.value:
        return "failed"
    return "pending"


def map_target_state(result_status: str) -> str:
    if result_status == PublishRecordResult.SUCCESS.value:
        return "succeeded"
    if result_status == PublishRecordResult.FAILED.value:
        return "failed"
    return "pending"


def map_terminal_outcome(publish_status: str) -> str:
    if publish_status == PublishStatus.SUCCESS.value:
        return "success"
    if publish_status in (PublishStatus.REJECTED.value, PublishStatus.REVOKED.value):
        return "cancelled"
    return "failed"


def candidate_version(publish_record: Any) -> str:
    """Return the confirmed synthetic version for P1.

    ``config_version`` is never populated by the backend in the current
    material, so the adopted decision is the stable ``v<publish_id>`` value.
    """
    return f"v{getattr(publish_record, 'id', 0)}"


def engine_from_bot_config(bot_config: Any) -> str:
    """Best-effort engine label from ``BotConfig.deploy_config.envs``."""
    deploy_config = getattr(bot_config, "deploy_config", None)
    envs = getattr(deploy_config, "envs", None) if deploy_config else None
    if isinstance(envs, dict):
        return str(envs.get("AGENTCLAW_ENGINE") or "")
    return ""


def build_register_context(
    *,
    publish_record: Any,
    bot_record: Any,
    bot_config: Any,
    environment: str,
    batches_by_stage: dict[str, list[ChangeFlowBatchContext]],
) -> ChangeFlowRegisterContext:
    """Build the immutable register plan from local BaaS rows."""
    stages = tuple(
        ChangeFlowStage(name=name, batches=tuple(batches))
        for name, batches in batches_by_stage.items()
    )
    return ChangeFlowRegisterContext(
        tenant=publish_record.tenant,
        publish_id=publish_record.id,
        publish_type=publish_record.publish_type,
        bot_id=getattr(bot_record, "id", publish_record.bot_id),
        bot_name=getattr(bot_record, "name", "") or "",
        engine=engine_from_bot_config(bot_config),
        instance_count=getattr(bot_record, "replica_desired", 0) or 0,
        environment=environment,
        candidate_version=candidate_version(publish_record),
        initiator=publish_record.creator or "",
        stages=stages,
    )


def build_batch_context(
    *,
    publish_record: Any,
    batch: Any,
    records: list[Any],
    change_id: str | None,
    outcome: str | None = None,
    error: str | None = None,
) -> ChangeFlowBatchContext:
    targets = tuple(
        ChangeFlowBatchTarget(
            target=getattr(record, "device_uuid", "") or "",
            state=map_target_state(record.result_status),
            actual_version=candidate_version(publish_record),
        )
        for record in records
        if getattr(record, "device_uuid", None)
    )
    return ChangeFlowBatchContext(
        tenant=publish_record.tenant,
        publish_id=publish_record.id,
        change_id=change_id,
        stage=batch.stage,
        batch_index=batch.batch_index,
        candidate_version=candidate_version(publish_record),
        targets=targets,
        outcome=outcome,
        error=error,
    )


def build_terminal_context(
    *,
    publish_record: Any,
    change_id: str | None,
    publish_status: str,
    reason: str | None = None,
) -> ChangeFlowTerminalContext:
    return ChangeFlowTerminalContext(
        tenant=publish_record.tenant,
        publish_id=publish_record.id,
        change_id=change_id,
        outcome=map_terminal_outcome(publish_status),
        reason=reason,
    )
