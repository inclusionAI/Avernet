"""Factories for quality module tests."""
from __future__ import annotations

from agentclaw.community.core.quality.repositories import QualityTaskRecord
from tests.community.framework.world import World


def make_quality_task(
    world: World,
    *,
    task_type: str = "eval",
    biz_type: str = "service_bot_single",
    bot_id: str | None = None,
    owner_id: str | None = None,
    status: str = "init",
    ext: dict | None = None,
) -> QualityTaskRecord:
    """Create a quality task for testing.

    Args:
        world: Test world with injector
        task_type: Task type (default: "eval")
        biz_type: Business type (default: "service_bot_single")
        bot_id: Bot ID
        owner_id: Owner ID
        status: Initial status (default: "init")
        ext: Extension data

    Returns:
        Created QualityTaskRecord
    """
    from agentclaw.community.api.quality_service import QualityTaskServiceProtocol

    svc = world.get(QualityTaskServiceProtocol)
    record = svc.create_task(
        task_type=task_type,
        biz_type=biz_type,
        bot_id=bot_id,
        owner_id=owner_id,
        ext=ext,
    )

    # Update status if not init
    if status != "init":
        svc.update_task_status(record.id, status)

    return record