"""Batch persistence projection using the runtime owner's stage-selection rule."""

from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session, Query
from sqlalchemy import or_
from agentclaw.community.plugin_api.models import BotModel

from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.engine_runtime.stage import published_stage_binding, require_known_stage
from agentclaw.community.core.service_bot.repository.models import BotPublishModel, PublishStatus
from agentclaw.community.utils.env_utils import get_current_env


def stage_views(session: Session, bots: list[BotModel]) -> dict[int, dict[str, Any]]:
    """Return display-safe online availability per exact Bot PK, without credentials."""
    services = [bot for bot in bots if bot.bot_type == "service"]
    if not services:
        return {}
    env = get_current_env()
    records = session.query(BotPublishModel).filter(
        BotPublishModel.source_bot_pk.in_([bot.id for bot in services]),
        BotPublishModel.env == env,
    ).order_by(BotPublishModel.gmt_create.desc(), BotPublishModel.id.desc()).all()
    by_pk = defaultdict(list)
    for row in records:
        by_pk[row.source_bot_pk].append(row)
    views = {}
    for bot in services:
        rows = by_pk[bot.id]
        success = next((row for row in rows if row.status == PublishStatus.SUCCESS.value), None)
        try:
            published_stage_binding(
                rows, bot_id=bot.bot_id, stage="online", env=env,
                binding_is_active=lambda _: False,
            )
            online = True
        except EngineStageNotLiveError:
            online = False
        views[bot.id] = {
            "online_available": online,
            "live_version": success.version if success and online else None,
            "conversation_stage": "online",
            "records": rows,
        }
    return views


def apply_stage_filter(session: Session, query: Query, model: type[BotModel], stage: str) -> Query:
    """Apply the service-only projection before count/offset/limit.

    Batch reads intentionally do not resolve devices or make provider calls.
    Existing authorization, tenant and environment predicates stay on query.
    """
    require_known_stage(stage)
    if stage == "draft":
        return query.filter(or_(model.bot_type.is_(None), model.bot_type != "service", model.binding_id > 0))
    bots = query.all()
    views = stage_views(session, bots)
    active_ids = set()
    if stage == "verify":
        # The runtime rule only consults ACTIVE for retained verify bindings.
        retained_ids = set()

        def remember_retained_binding(binding_id: int) -> bool:
            retained_ids.add(binding_id)
            return False

        for bot in bots:
            if bot.bot_type != "service":
                continue
            try:
                published_stage_binding(
                    views[bot.id]["records"], bot_id=bot.bot_id, stage=stage,
                    env=get_current_env(),
                    binding_is_active=remember_retained_binding,
                )
            except EngineStageNotLiveError:
                pass
        active_ids = {row.id for row in session.query(EntityDeviceBinding).filter(
            EntityDeviceBinding.env == get_current_env(), EntityDeviceBinding.status == "ACTIVE",
            EntityDeviceBinding.id.in_(retained_ids),
        ).all()}
    allowed = []
    for bot in bots:
        if bot.bot_type != "service":
            allowed.append(bot.id)
            continue
        try:
            published_stage_binding(
                views[bot.id]["records"], bot_id=bot.bot_id, stage=stage, env=get_current_env(),
                binding_is_active=lambda binding_id: binding_id in active_ids,
            )
        except EngineStageNotLiveError:
            continue
        allowed.append(bot.id)
    return query.filter(model.id.in_(allowed))


def attach_stage_metadata(session: Session, bots: list[BotModel]) -> list[dict[str, Any]]:
    views = stage_views(session, bots)
    return [{
        **bot.to_dict(),
        "online_available": views.get(bot.id, {}).get("online_available"),
        "live_version": views.get(bot.id, {}).get("live_version"),
        "conversation_stage": "online" if bot.bot_type == "service" else "draft",
    } for bot in bots]
