"""SQL query construction and display-label enrichment for bot-chat reads."""

from typing import Any

from sqlalchemy import and_, or_

from agentclaw.community.core.bot_chat.models import (
    AcOtelLogBizRef,
    AcOtelLogTrace,
    BcsGroupSession,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

_GROUP_SESSION_KEY_PREFIX = "agent:main:"


def match_column(column: Any, value: str, match_mode: str) -> Any:
    """Build exact or substring matching for a SQLAlchemy column."""
    return column.like(f"%{value}%") if match_mode == "contains" else column == value


def load_task_refs(
    session: Any,
    biz_scene: str | None,
    biz_task_id: str | None,
    match_mode: str,
    owner_id: str,
    bot_id: str | None,
) -> dict[str, set[str]]:
    """Load task references visible to the current user/Bot scope."""
    if not biz_scene and not biz_task_id:
        return {}
    query = session.query(AcOtelLogBizRef)
    if biz_scene:
        query = query.filter(
            match_column(AcOtelLogBizRef.biz_scene, biz_scene, match_mode)
        )
    if biz_task_id:
        query = query.filter(
            match_column(AcOtelLogBizRef.biz_task_id, biz_task_id, match_mode)
        )
    query = query.filter(
        or_(
            AcOtelLogBizRef.user_id == owner_id,
            AcOtelLogBizRef.user_id.is_(None),
        )
    )
    if bot_id:
        relation_bot_ids = [bot_id]
        if bot_id == "default":
            relation_bot_ids.append(f"{owner_id}_default")
        query = query.filter(
            or_(
                AcOtelLogBizRef.bot_id.in_(relation_bot_ids),
                AcOtelLogBizRef.bot_id.is_(None),
            )
        )

    refs: dict[str, set[str]] = {}
    for row in query.all():
        refs.setdefault(row.ref_type, set()).add(row.ref_value)
    return refs


def task_trace_condition(
    refs: dict[str, set[str]],
    biz_scene: str | None,
    biz_task_id: str | None,
    match_mode: str,
) -> Any:
    """Combine direct task fields with relation-derived trace identifiers."""
    direct = []
    if biz_scene:
        direct.append(match_column(AcOtelLogTrace.biz_scene, biz_scene, match_mode))
    if biz_task_id:
        direct.append(
            match_column(AcOtelLogTrace.biz_task_id, biz_task_id, match_mode)
        )
    candidates = [and_(*direct)]
    if refs.get("trace_id"):
        candidates.append(AcOtelLogTrace.trace_id.in_(refs["trace_id"]))
    if refs.get("session_id"):
        candidates.append(AcOtelLogTrace.session_id.in_(refs["session_id"]))
    if refs.get("session_key"):
        candidates.append(AcOtelLogTrace.session_key.in_(refs["session_key"]))
    return or_(*candidates)


def list_group_sessions(
    session: Any, group_id: str
) -> dict[str, str | None]:
    """Resolve every BCS session in a group to its normalized log session key."""
    rows = (
        session.query(BcsGroupSession)
        .filter(
            BcsGroupSession.env == get_current_env(),
            BcsGroupSession.group_id == group_id,
            BcsGroupSession.session_id.isnot(None),
        )
        .order_by(BcsGroupSession.gmt_create.desc(), BcsGroupSession.id.desc())
        .all()
    )
    result: dict[str, str | None] = {}
    for row in rows:
        value = row.session_id.strip()
        if not value:
            continue
        session_key = (
            value
            if value.startswith("agent:")
            else f"{_GROUP_SESSION_KEY_PREFIX}{value}"
        )
        result.setdefault(session_key, row.session_kind)
    return result


def load_bot_names(session: Any, bot_ids: set[str]) -> dict[str, str]:
    """Batch-load active Bot names, degrading to an empty map on read failure."""
    if not bot_ids:
        return {}
    try:
        rows = (
            session.query(BotModel)
            .filter(
                BotModel.bot_id.in_(bot_ids),
                BotModel.is_delete == 0,
                BotModel.env == get_current_env(),
            )
            .order_by(BotModel.id.desc())
            .all()
        )
    except Exception as exc:
        logger.warning(
            "Bot name enrichment failed; returning null labels: error_type=%s",
            type(exc).__name__,
        )
        return {}

    result: dict[str, str] = {}
    for row in rows:
        row_bot_id = getattr(row, "bot_id", None)
        bot_name = getattr(row, "bot_name", None)
        if row_bot_id and row_bot_id not in result and bot_name:
            result[row_bot_id] = bot_name
    return result


def enrich_trace_labels(session: Any, row: Any) -> Any:
    """Attach optional Bot and group labels to a detached trace row."""
    if row.bot_id:
        row.bot_name = load_bot_names(session, {row.bot_id}).get(row.bot_id)
    session_key = row.session_key
    if not session_key:
        return row

    candidates = {session_key}
    if session_key.startswith(_GROUP_SESSION_KEY_PREFIX):
        candidates.add(session_key[len(_GROUP_SESSION_KEY_PREFIX):])
    group_session = (
        session.query(BcsGroupSession)
        .filter(
            BcsGroupSession.env == get_current_env(),
            BcsGroupSession.session_id.in_(candidates),
        )
        .order_by(BcsGroupSession.gmt_create.desc(), BcsGroupSession.id.desc())
        .first()
    )
    if group_session and getattr(group_session, "group_id", None):
        row.group_id = group_session.group_id
        row.session_kind = getattr(group_session, "session_kind", None)
    return row
