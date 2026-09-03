"""Folding one phase's report into the next one's (W13).

Its own module rather than a method on the apply service, for the reason the
report codec next door has one: it needs no part of the service beyond a read
and a decode, both of which it takes as arguments — and the service is at the
size the architecture cap allows.
"""

from __future__ import annotations

from typing import Callable, Optional

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyReport,
    derive_status,
)
from agentclaw.community.log import get_logger

logger = get_logger()


def carry_forward(
    report: ApplyReport,
    *,
    ctx: ApplyContext,
    carry_from_apply_id: Optional[str],
    applies,
    to_report: Callable[..., Optional[ApplyReport]],
) -> ApplyReport:
    """Fold an earlier apply's categories into this one's report.

    One creation produces **two** applies — the pre-container phase writes
    ``script``, the post-container phase writes everything else — separated by
    the whole of container provisioning. Each mints its own ``apply_id`` and its
    own record, so without this the report a caller reads at the end names the
    post-container categories and silently omits ``script``: the manifest would
    look as though part of it had vanished.

    The carried categories go **first**, which is ``APPLY_ORDER``'s own order
    (``script`` is position 0), and the summary is re-derived over the union — so
    a failed pre-container phase plus a clean post-container one terminates
    ``PARTIAL`` rather than ``SUCCEEDED``.

    **A missing or foreign id is ignored, not fatal.** This is a reporting
    nicety; losing it must never fail an apply that actually worked. The read is
    scoped to the bot, so an id from another bot resolves to nothing here exactly
    as it does on the poll route.
    """
    if not carry_from_apply_id:
        return report
    earlier = applies.get(
        env=ctx.env,
        entity_id=ctx.entity_id,
        bot_id=ctx.bot_id,
        apply_id=carry_from_apply_id,
    )
    if earlier is None:
        logger.warning(
            "[manifest_apply] carry_from_apply_id=%s not found for bot_id=%s; "
            "reporting this phase alone",
            carry_from_apply_id,
            ctx.bot_id,
        )
        return report
    try:
        carried = to_report(earlier, entity_id=ctx.entity_id, bot_id=ctx.bot_id)
    except Exception:  # noqa: BLE001 — a corrupt earlier row must not fail this apply
        logger.exception(
            "[manifest_apply] could not read carry_from_apply_id=%s",
            carry_from_apply_id,
        )
        return report
    if carried is None:
        return report
    categories = tuple(carried.categories) + tuple(report.categories)
    return ApplyReport(
        apply_id=report.apply_id,
        bot_id=report.bot_id,
        trigger=report.trigger,
        status=derive_status(categories),
        started_at=report.started_at,
        finished_at=report.finished_at,
        categories=categories,
        sources=tuple(carried.sources) + tuple(report.sources),
        # Apply-level notes ride along too: a redeliver that failed after phase
        # A must not vanish because phase B carried it forward.
        notes=tuple(carried.notes) + tuple(report.notes),
    )


__all__ = ["carry_forward"]
