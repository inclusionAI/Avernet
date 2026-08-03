"""Database reads owned exclusively by the Bot Chat OpenAPI surface."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, func, or_

from agentclaw.community.core.bot_chat.models import (
    AcOtelLogBizRef,
    AcOtelLogTrace,
    AwLangfuseTrace,
)
from agentclaw.community.core.bot_chat.query_support import (
    enrich_bot_names,
    enrich_group_labels,
)
from agentclaw.community.core.bot_chat.schemas import (
    ConversationSession,
    SessionListResponse,
    SessionMetadata,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

_OUTPUT_PREVIEW_LENGTH = 500


def _json_value(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _metadata_identity(metadata_json: str | None, *keys: str) -> str | None:
    metadata = _json_value(metadata_json, {})
    attributes = metadata.get("attributes") if isinstance(metadata, dict) else None
    if not isinstance(attributes, dict):
        return None
    for key in keys:
        value = attributes.get(key)
        if value:
            return str(value)
    return None


def _extract_user_input(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, list):
        for message in reversed(value):
            if isinstance(message, dict) and message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    return content
        return str(value[0]) if value else None
    if isinstance(value, dict):
        return str(value.get("content") or value)
    return str(value)


def _output_preview(value: Any) -> str | None:
    if value is None:
        return None
    rendered = (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    )
    return rendered[:_OUTPUT_PREVIEW_LENGTH]


def _bot_ids(user_id: str, bot_id: str) -> list[str]:
    if bot_id == "default":
        return ["default", f"{user_id}_default"]
    return [bot_id]


class OpenBotChatRepository:
    """OpenAPI-only trace reads with no product authorization dependency."""

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def list_user_bot_traces(
        self,
        *,
        user_id: str,
        bot_id: str,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
    ) -> SessionListResponse:
        """Return one atomic page for the exact user-and-Bot pair.

        OTel is authoritative. The legacy table is queried only when OTel has
        no match, preserving the existing migration fallback without invoking
        the product-facing list service or its access checks.
        """
        sessions, total = self._list_ocb(
            user_id=user_id,
            bot_id=bot_id,
            from_ms=from_ms,
            to_ms=to_ms,
            page=page,
            limit=limit,
        )
        if total == 0:
            sessions, total = self._list_legacy(
                user_id=user_id,
                bot_id=bot_id,
                from_ms=from_ms,
                to_ms=to_ms,
                page=page,
                limit=limit,
            )
        return SessionListResponse(
            sessions=sessions,
            total=total,
            page=page,
            limit=limit,
            has_more=page * limit < total,
        )

    def _list_ocb(
        self,
        *,
        user_id: str,
        bot_id: str,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
    ) -> tuple[list[ConversationSession], int]:
        with self._db.orm_session() as session:
            where = and_(
                AcOtelLogTrace.start_time_ms >= from_ms,
                AcOtelLogTrace.start_time_ms <= to_ms,
                AcOtelLogTrace.user_id == user_id,
                AcOtelLogTrace.bot_id.in_(_bot_ids(user_id, bot_id)),
            )
            total = (
                session.query(func.count(AcOtelLogTrace.id)).filter(where).scalar() or 0
            )
            rows = (
                session.query(AcOtelLogTrace)
                .filter(where)
                .order_by(AcOtelLogTrace.start_time_ms.desc(), AcOtelLogTrace.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
                .all()
            )
            detached = [self._detach_ocb(row) for row in rows]
            self._enrich(
                session,
                detached,
                user_id=user_id,
                bot_id=bot_id,
            )
            return [self._to_session(row) for row in detached], total

    def _list_legacy(
        self,
        *,
        user_id: str,
        bot_id: str,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
    ) -> tuple[list[ConversationSession], int]:
        with self._db.orm_session() as session:
            where = and_(
                AwLangfuseTrace.gmt_trace >= from_ms,
                AwLangfuseTrace.gmt_trace <= to_ms,
                AwLangfuseTrace.user_id == user_id,
                AwLangfuseTrace.bot_id.in_(_bot_ids(user_id, bot_id)),
            )
            total = (
                session.query(func.count(AwLangfuseTrace.id)).filter(where).scalar()
                or 0
            )
            rows = (
                session.query(AwLangfuseTrace)
                .filter(where)
                .order_by(AwLangfuseTrace.gmt_trace.desc(), AwLangfuseTrace.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
                .all()
            )
            detached = [self._detach_legacy(row) for row in rows]
            self._enrich(
                session,
                detached,
                user_id=user_id,
                bot_id=bot_id,
            )
            return [self._to_session(row) for row in detached], total

    @classmethod
    def _enrich(
        cls,
        session: Any,
        rows: list[Any],
        *,
        user_id: str,
        bot_id: str,
    ) -> None:
        enrich_group_labels(session, rows)
        cls._enrich_task_labels(
            session,
            rows,
            user_id=user_id,
            bot_id=bot_id,
        )
        enrich_bot_names(session, rows)

    @staticmethod
    def _enrich_task_labels(
        session: Any,
        rows: list[Any],
        *,
        user_id: str,
        bot_id: str,
    ) -> None:
        """Fill task labels from relations owned by the requested identity."""
        candidate_rows: dict[tuple[str, str], list[Any]] = {}
        digests_by_type: dict[str, set[str]] = {}
        for row in rows:
            for ref_type, attribute in (
                ("trace_id", "trace_id"),
                ("session_id", "session_id"),
                ("session_key", "session_key"),
            ):
                ref_value = getattr(row, attribute, None)
                if not ref_value:
                    continue
                candidate_rows.setdefault((ref_type, ref_value), []).append(row)
                digest = f"sha256:{sha256(ref_value.encode('utf-8')).hexdigest()}"
                digests_by_type.setdefault(ref_type, set()).add(digest)

        if not candidate_rows:
            return
        ref_conditions = [
            and_(
                AcOtelLogBizRef.ref_type == ref_type,
                AcOtelLogBizRef.ref_digest.in_(digests),
            )
            for ref_type, digests in digests_by_type.items()
        ]
        relations = (
            session.query(AcOtelLogBizRef)
            .filter(
                AcOtelLogBizRef.user_id == user_id,
                AcOtelLogBizRef.bot_id.in_(_bot_ids(user_id, bot_id)),
                or_(*ref_conditions),
            )
            .order_by(
                AcOtelLogBizRef.gmt_modified.desc(),
                AcOtelLogBizRef.gmt_create.desc(),
                AcOtelLogBizRef.id.desc(),
            )
            .all()
        )
        relations_by_row: dict[int, list[Any]] = {}
        for relation in relations:
            relation_type = getattr(relation, "ref_type", None)
            relation_value = getattr(relation, "ref_value", None)
            if not relation_type or not relation_value:
                continue
            for row in candidate_rows.get((relation_type, relation_value), ()):
                relations_by_row.setdefault(id(row), []).append(relation)

        for row in rows:
            scene = getattr(row, "biz_scene", None)
            task_id = getattr(row, "biz_task_id", None)
            if scene and task_id:
                continue
            for relation in relations_by_row.get(id(row), ()):
                if scene and relation.biz_scene != scene:
                    continue
                if task_id and relation.biz_task_id != task_id:
                    continue
                row.biz_scene = relation.biz_scene
                row.biz_task_id = relation.biz_task_id
                break

    @staticmethod
    def _detach_ocb(row: AcOtelLogTrace) -> Any:
        return SimpleNamespace(
            trace_id=row.trace_id,
            gmt_trace=row.start_time_ms,
            name=row.name,
            input=row.input,
            output=row.output,
            biz_task_id=row.biz_task_id,
            biz_scene=row.biz_scene,
            session_id=row.session_id,
            session_key=row.session_key or row.session_id,
            real_session_id=None,
            user_id=row.user_id
            or _metadata_identity(row.metadata_json, "identity.owner_id", "user.id"),
            trace_metadata=row.metadata_json,
            latency=row.latency_ms,
            total_cost=row.total_cost,
            usage_total_tokens=row.usage_total_tokens,
            bot_id=row.bot_id
            or _metadata_identity(row.metadata_json, "identity.bot_id"),
            bot_name=None,
            group_id=None,
            session_kind=None,
            match_sources=[],
        )

    @staticmethod
    def _detach_legacy(row: AwLangfuseTrace) -> Any:
        return SimpleNamespace(
            trace_id=row.trace_id,
            gmt_trace=row.gmt_trace,
            name=row.name,
            input=row.input,
            output=row.output,
            biz_task_id=getattr(row, "biz_task_id", None),
            biz_scene=getattr(row, "biz_scene", None),
            session_id=row.session_id,
            session_key=row.session_id,
            real_session_id=row.real_session_id,
            user_id=row.user_id
            or _metadata_identity(row.trace_metadata, "identity.owner_id", "user.id"),
            trace_metadata=row.trace_metadata,
            latency=row.latency,
            total_cost=row.total_cost,
            usage_total_tokens=None,
            bot_id=row.bot_id
            or _metadata_identity(row.trace_metadata, "identity.bot_id"),
            bot_name=None,
            group_id=None,
            session_kind=None,
            match_sources=[],
        )

    @staticmethod
    def _to_session(row: Any) -> ConversationSession:
        input_value = _json_value(row.input, row.input)
        output_value = _json_value(row.output, row.output)
        metadata = _json_value(row.trace_metadata, {})
        attributes = (
            dict(metadata.get("attributes") or {}) if isinstance(metadata, dict) else {}
        )
        session_id = row.real_session_id or row.session_id
        session_key = row.session_key or row.session_id
        if session_id:
            attributes.setdefault("gen_ai.session.id", session_id)
        if session_key:
            attributes.setdefault("session_id", session_key)
            attributes.setdefault("gen_ai.conversation.id", session_key)
        timestamp = ""
        if row.gmt_trace:
            timestamp = (
                datetime.fromtimestamp(row.gmt_trace / 1000, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        return ConversationSession(
            id=row.trace_id or "",
            biz_task_id=row.biz_task_id
            or attributes.get("agentic.biz_task_id")
            or attributes.get("identity.biz_task_id"),
            biz_scene=row.biz_scene
            or attributes.get("agentic.biz_scene")
            or attributes.get("identity.biz_scene"),
            session_id=session_id,
            session_key=session_key,
            bot_id=row.bot_id,
            bot_name=row.bot_name,
            group_id=row.group_id,
            session_kind=row.session_kind,
            name=row.name or "未命名会话",
            input=_extract_user_input(input_value),
            output_preview=_output_preview(output_value),
            match_sources=list(row.match_sources or []),
            status="SUCCESS",
            timestamp=timestamp,
            user_id=row.user_id,
            metadata=SessionMetadata(attributes=attributes),
            total_cost=float(row.total_cost) if row.total_cost else 0.0,
            latency_ms=float(row.latency) if row.latency else 0.0,
            total_tokens=int(row.usage_total_tokens or 0),
        )
