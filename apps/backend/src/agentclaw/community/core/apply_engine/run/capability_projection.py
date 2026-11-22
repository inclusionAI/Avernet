"""Translate the capability synchronization observation into an Apply report."""
from dataclasses import replace

from agentclaw.community.core.apply_engine.capabilities import Category
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.outcomes import ApplyReport
from agentclaw.community.core.skill_center.capability_projection_reconcile import (
    CapabilityProjectionReconcileProtocol,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    RuntimeProjectionResult,
)
from agentclaw.community.log import get_logger


logger = get_logger()


async def finish_capability_projection(
    projection: CapabilityProjectionReconcileProtocol,
    ctx: ApplyContext,
    report: ApplyReport,
) -> ApplyReport:
    category = next(
        (result for result in report.categories if result.construct is Category.CAPABILITIES),
        None,
    )
    if category is None:
        return report
    try:
        revision, result = await projection.finish_apply(
            bot_id=ctx.bot_id,
            entity_id=ctx.entity_id,
            initialize=not category.aborted,
        )
    except Exception:
        logger.exception("Apply capability projection failed apply_id=%s", ctx.apply_id)
        revision = None
        result = RuntimeProjectionResult.pending(
            code="CAPABILITY_PROJECTION_UNAVAILABLE",
            reason="Capability synchronization could not be confirmed",
        )
    return replace(report, runtime_projection=result, runtime_projection_revision=revision)
