"""Close the capability delivery and record its result in the Apply report."""

import json
from dataclasses import replace

from agentclaw.community.core.apply_engine import triggers
from agentclaw.community.core.apply_engine.capabilities import Category
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.outcomes import ApplyReport
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.core.skill_center.capability_projection_reconcile import (
    PROJECTION_RETRY_FIELD,
    CapabilityProjectionRetry,
    DeferredCapabilityProjection,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import RuntimeProjectionStatus
from agentclaw.community.log import get_logger


logger = get_logger()
_RETRY_ACTION = "Retry synchronization by applying the current Manifest again."


def previous_projection_retry(
    repository: BotConfigManifestApplyRepositoryProtocol, ctx: ApplyContext,
) -> CapabilityProjectionRetry | None:
    record = repository.latest_projection_checkpoint(
        env=ctx.env, entity_id=ctx.entity_id, bot_id=ctx.bot_id,
    )
    if record is None:
        return None
    try:
        return CapabilityProjectionRetry.from_payload(json.loads(record.report).get(PROJECTION_RETRY_FIELD))
    except (ValueError, AttributeError):
        logger.warning("Invalid capability retry evidence apply_id=%s", record.apply_id)
        return None


async def finish_capability_projection(
    delivery: DeferredCapabilityProjection, report: ApplyReport,
) -> ApplyReport:
    category = next(
        (result for result in report.categories if result.construct is Category.CAPABILITIES),
        None,
    )
    if category is None:
        return report
    result, retry = await delivery.finish(
        initialize=report.trigger == triggers.EXPLICIT and not category.aborted,
    )
    if result.status in {RuntimeProjectionStatus.PENDING, RuntimeProjectionStatus.DEGRADED}:
        degraded = RuntimeProjectionStatus.DEGRADED
        pending = RuntimeProjectionStatus.PENDING
        result = replace(
            result,
            status=degraded,
            components={
                name: degraded if status is pending else status
                for name, status in result.components.items()
            },
            issues=tuple(
                replace(
                    issue, status=degraded if issue.status is pending else issue.status,
                    suggested_action=issue.suggested_action or _RETRY_ACTION,
                )
                for issue in result.issues
            ),
            reason=f"{result.reason} {_RETRY_ACTION}" if result.reason else _RETRY_ACTION,
        )
    return replace(report, runtime_projection=result, capability_projection_retry=retry)
