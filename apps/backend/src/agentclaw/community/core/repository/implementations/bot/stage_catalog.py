"""Database-side stage predicates and bounded display-safe publication reads."""

from typing import Any

from sqlalchemy import BigInteger, String, and_, case, cast, func, or_, select
from sqlalchemy.orm import Query, Session, aliased

from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.engine_runtime.stage import require_known_stage
from agentclaw.community.core.service_bot.repository.models import BotPublishModel, PublishStatus
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


def _latest_id(source_pk, status: str, env: str, correlate):
    """Same exact identity and deterministic order as runtime stage selection."""
    latest = aliased(BotPublishModel)
    return select(latest.id).where(
        latest.source_bot_pk == source_pk, latest.env == env, latest.status == status,
    ).order_by(latest.gmt_create.desc(), latest.id.desc()).limit(1).correlate(correlate).scalar_subquery()


def _binding_projection(session: Session, ext, stage: str):
    """Extract just an ID in SQL, guarding malformed JSON and scalar types.

    SQLite and MySQL/OceanBase expose different JSON scalar/type conventions.
    Accept integer IDs and decimal strings, never prefix-cast malformed strings.
    """
    document = func.coalesce(func.nullif(ext, ""), "{}")
    valid_json = func.json_valid(document) == 1
    safe_document = case((valid_json, document), else_="{}")
    path = f"$.binding.{stage}"
    extracted = func.json_extract(safe_document, path)
    if session.get_bind().dialect.name == "sqlite":
        scalar = extracted
        supported_type = func.json_type(safe_document, path).in_(("integer", "text"))
    else:
        scalar = func.json_unquote(extracted)
        supported_type = func.json_type(extracted).in_(("INTEGER", "STRING"))
    decimal = func.trim(cast(scalar, String))
    numeric = and_(supported_type, decimal.regexp_match(r"^[+]?[0-9]+$"))
    # Keep CAST inside CASE so invalid values do not reach strict conversion.
    identifier = case((numeric, cast(decimal, BigInteger)), else_=None)
    positive_id = case((identifier > 0, identifier), else_=None)
    malformed = or_(~valid_json, and_(extracted.is_not(None), positive_id.is_(None)))
    return positive_id, malformed


def _published_available(session: Session, model, stage: str, *, retained: bool = False):
    record = aliased(BotPublishModel)
    env = get_current_env()
    status = PublishStatus.SUCCESS.value if stage == "online" or retained else PublishStatus.VALIDATING.value
    binding, _ = _binding_projection(session, record.ext, stage)
    predicates = [
        record.source_bot_pk == model.id, record.env == env, record.status == status,
        record.id == _latest_id(model.id, status, env, model), binding.is_not(None),
    ]
    if retained:
        predicates.append(select(1).where(
            EntityDeviceBinding.id == binding, EntityDeviceBinding.env == env,
            EntityDeviceBinding.status == "ACTIVE",
        ).correlate(record).exists())
    return select(1).where(*predicates).correlate(model).exists()


def apply_stage_filter(session: Session, query: Query, model: type[BotModel], stage: str) -> Query:
    """Filter before COUNT/LIMIT without materializing candidate Bots/history."""
    require_known_stage(stage)
    other_kind = or_(model.bot_type.is_(None), model.bot_type != "service")
    if stage == "draft":
        available = model.binding_id > 0
    elif stage == "online":
        available = _published_available(session, model, stage)
    else:
        validating_exists = select(1).where(
            BotPublishModel.source_bot_pk == model.id,
            BotPublishModel.env == get_current_env(),
            BotPublishModel.status == PublishStatus.VALIDATING.value,
        ).correlate(model).exists()
        available = or_(
            _published_available(session, model, stage),
            and_(~validating_exists, _published_available(session, model, stage, retained=True)),
        )
    return query.filter(or_(other_kind, available))


def stage_views(session: Session, bots: list[BotModel]) -> dict[int, dict[str, Any]]:
    """Project at most one latest-success row per service Bot on this page.

    Bad data only makes its own Bot unavailable, never falls back to an older
    release, and never leaks raw configuration into responses or diagnostics.
    """
    ids = [bot.id for bot in bots if bot.bot_type == "service"]
    if not ids:
        return {}
    env = get_current_env()
    binding, malformed = _binding_projection(session, BotPublishModel.ext, "online")
    rows = session.query(
        BotPublishModel.id, BotPublishModel.source_bot_pk, BotPublishModel.version,
        binding.label("online_binding"), malformed.label("malformed"),
    ).filter(
        BotPublishModel.source_bot_pk.in_(ids), BotPublishModel.env == env,
        BotPublishModel.status == PublishStatus.SUCCESS.value,
        BotPublishModel.id == _latest_id(
            BotPublishModel.source_bot_pk, PublishStatus.SUCCESS.value, env, BotPublishModel,
        ),
    ).all()
    views = {}
    for row in rows:
        if row.malformed:
            logger.warning("stage catalog invalid publication binding bot_pk=%s publish_id=%s", row.source_bot_pk, row.id)
        online = row.online_binding is not None
        views[row.source_bot_pk] = {
            "online_available": online, "live_version": row.version if online else None,
        }
    return views


def attach_stage_metadata(session: Session, bots: list[BotModel]) -> list[dict[str, Any]]:
    views = stage_views(session, bots)
    return [{
        **bot.to_dict(),
        "online_available": views.get(bot.id, {}).get("online_available", False) if bot.bot_type == "service" else None,
        "live_version": views.get(bot.id, {}).get("live_version"),
        "conversation_stage": "online" if bot.bot_type == "service" else "draft",
    } for bot in bots]
