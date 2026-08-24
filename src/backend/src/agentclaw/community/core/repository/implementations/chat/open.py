"""Repository reads owned exclusively by the Bot Chat OpenAPI surface."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

from injector import inject
from sqlalchemy import and_, func, or_

from agentclaw.community.core.bot_chat.models import (
    AcOtelLogBizRef,
    AcOtelLogObservation,
    AcOtelLogTrace,
    AwLangfuseObservation,
    AwLangfuseTrace,
)
from agentclaw.community.core.bot_chat.query_support import (
    QueryScope,
    enrich_bot_names,
    enrich_group_labels,
    list_group_sessions,
    load_task_refs,
    task_trace_condition,
)
from agentclaw.community.core.bot_chat.errors import SessionNotFoundError
from agentclaw.community.core.bot_chat.schemas import (
    ConversationDetail,
    ConversationObservation,
    ConversationSession,
    SessionListResponse,
    SessionMetadata,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.chat import OpenBotChatRepositoryProtocol

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


class OpenBotChatRepository(
    OpenBotChatRepositoryProtocol,
):
    """OpenAPI-only trace reads with no product authorization dependency."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def list_scope_traces(
        self,
        *,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
        session_key: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
    ) -> SessionListResponse:
        """List one exact Session, Task, or Group scope without product policy."""
        sessions, total = self._list_scope_ocb(
            from_ms=from_ms,
            to_ms=to_ms,
            page=page,
            limit=limit,
            session_key=session_key,
            biz_scene=biz_scene,
            biz_task_id=biz_task_id,
            group_id=group_id,
        )
        if total == 0:
            sessions, total = self._list_scope_legacy(
                from_ms=from_ms,
                to_ms=to_ms,
                page=page,
                limit=limit,
                session_key=session_key,
                biz_scene=biz_scene,
                biz_task_id=biz_task_id,
                group_id=group_id,
            )
        return SessionListResponse(
            sessions=sessions,
            total=total,
            page=page,
            limit=limit,
            has_more=page * limit < total,
        )

    def _list_scope_ocb(
        self,
        *,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
        session_key: str | None,
        biz_scene: str | None,
        biz_task_id: str | None,
        group_id: str | None,
    ) -> tuple[list[ConversationSession], int]:
        with self._db.orm_session() as session:
            conditions = [
                AcOtelLogTrace.start_time_ms >= from_ms,
                AcOtelLogTrace.start_time_ms <= to_ms,
            ]
            if session_key:
                conditions.append(AcOtelLogTrace.session_key == session_key)
            task_refs = load_task_refs(
                session,
                biz_scene,
                biz_task_id,
                "exact",
                None,
                None,
                QueryScope.OPEN,
            )
            if biz_scene or biz_task_id:
                conditions.append(
                    task_trace_condition(
                        task_refs, biz_scene, biz_task_id, "exact"
                    )
                )
            group_sessions: dict[str, str | None] = {}
            if group_id:
                group_sessions = list_group_sessions(session, group_id)
                if not group_sessions:
                    return [], 0
                conditions.append(AcOtelLogTrace.session_key.in_(group_sessions))
            where = and_(*conditions)
            total = session.query(func.count(AcOtelLogTrace.id)).filter(where).scalar() or 0
            rows = (
                session.query(AcOtelLogTrace)
                .filter(where)
                .order_by(AcOtelLogTrace.start_time_ms.desc(), AcOtelLogTrace.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
                .all()
            )
            detached = [self._detach_ocb(row) for row in rows]
            self._enrich_open_scope(
                session,
                detached,
                biz_scene=biz_scene,
                biz_task_id=biz_task_id,
                group_id=group_id,
                group_sessions=group_sessions,
                task_refs=task_refs,
            )
            return [self._to_session(row) for row in detached], total

    def _list_scope_legacy(
        self,
        *,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
        session_key: str | None,
        biz_scene: str | None,
        biz_task_id: str | None,
        group_id: str | None,
    ) -> tuple[list[ConversationSession], int]:
        with self._db.orm_session() as session:
            conditions = [
                AwLangfuseTrace.gmt_trace >= from_ms,
                AwLangfuseTrace.gmt_trace <= to_ms,
            ]
            if session_key:
                conditions.append(AwLangfuseTrace.session_id == session_key)
            task_refs = load_task_refs(
                session,
                biz_scene,
                biz_task_id,
                "exact",
                None,
                None,
                QueryScope.OPEN,
            )
            if biz_scene or biz_task_id:
                ref_conditions = []
                if task_refs.get("trace_id"):
                    ref_conditions.append(
                        AwLangfuseTrace.trace_id.in_(task_refs["trace_id"])
                    )
                if task_refs.get("session_id"):
                    ref_conditions.append(
                        AwLangfuseTrace.real_session_id.in_(task_refs["session_id"])
                    )
                if task_refs.get("session_key"):
                    ref_conditions.append(
                        AwLangfuseTrace.session_id.in_(task_refs["session_key"])
                    )
                if not ref_conditions:
                    return [], 0
                conditions.append(or_(*ref_conditions))
            group_sessions: dict[str, str | None] = {}
            if group_id:
                group_sessions = list_group_sessions(session, group_id)
                if not group_sessions:
                    return [], 0
                conditions.append(AwLangfuseTrace.session_id.in_(group_sessions))
            where = and_(*conditions)
            total = session.query(func.count(AwLangfuseTrace.id)).filter(where).scalar() or 0
            rows = (
                session.query(AwLangfuseTrace)
                .filter(where)
                .order_by(AwLangfuseTrace.gmt_trace.desc(), AwLangfuseTrace.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
                .all()
            )
            detached = [self._detach_legacy(row) for row in rows]
            self._enrich_open_scope(
                session,
                detached,
                biz_scene=biz_scene,
                biz_task_id=biz_task_id,
                group_id=group_id,
                group_sessions=group_sessions,
                task_refs=task_refs,
            )
            return [self._to_session(row) for row in detached], total

    @classmethod
    def _enrich_open_scope(
        cls,
        session: Any,
        rows: list[Any],
        *,
        biz_scene: str | None,
        biz_task_id: str | None,
        group_id: str | None,
        group_sessions: dict[str, str | None],
        task_refs: dict[str, set[str]],
    ) -> None:
        enrich_group_labels(session, rows, group_id, group_sessions)
        enrich_bot_names(session, rows)
        ref_trace_ids = task_refs.get("trace_id", set())
        ref_session_ids = task_refs.get("session_id", set())
        ref_session_keys = task_refs.get("session_key", set())
        for row in rows:
            sources = []
            if (biz_scene or biz_task_id) and (
                (not biz_scene or row.biz_scene == biz_scene)
                and (not biz_task_id or row.biz_task_id == biz_task_id)
            ):
                sources.append("direct")
            if (
                row.trace_id in ref_trace_ids
                or row.session_id in ref_session_ids
                or row.session_key in ref_session_keys
            ):
                sources.append("biz_ref")
            row.match_sources = sources
            if biz_scene and biz_task_id and "biz_ref" in sources:
                row.biz_scene = biz_scene
                row.biz_task_id = biz_task_id
        cls._enrich_owned_task_labels(session, rows)

    def get_trace_detail(self, trace_id: str) -> ConversationDetail:
        """Return one exact Trace and observations without product authorization."""
        with self._db.orm_session() as session:
            row = (
                session.query(AcOtelLogTrace)
                .filter(AcOtelLogTrace.trace_id == trace_id)
                .first()
            )
            if row is not None:
                detached = self._detach_ocb(row)
                self._enrich_display_labels(session, [detached])
                observations = (
                    session.query(AcOtelLogObservation)
                    .filter(AcOtelLogObservation.trace_id == trace_id)
                    .order_by(
                        AcOtelLogObservation.start_time_ms.asc(),
                        AcOtelLogObservation.id.asc(),
                    )
                    .all()
                )
                return self._to_detail(
                    detached, self._build_observation_tree(observations)
                )

            row = (
                session.query(AwLangfuseTrace)
                .filter(AwLangfuseTrace.trace_id == trace_id)
                .first()
            )
            if row is None:
                raise SessionNotFoundError(
                    f"Trace {trace_id} not found or not accessible"
                )
            detached = self._detach_legacy(row)
            self._enrich_display_labels(session, [detached])
            observations = (
                session.query(AwLangfuseObservation)
                .filter(AwLangfuseObservation.trace_id == trace_id)
                .order_by(
                    AwLangfuseObservation.start_time.asc(),
                    AwLangfuseObservation.id.asc(),
                )
                .all()
            )
            return self._to_detail(
                detached, self._build_observation_tree(observations)
            )

    @classmethod
    def _enrich_display_labels(cls, session: Any, rows: list[Any]) -> None:
        enrich_group_labels(session, rows)
        cls._enrich_owned_task_labels(session, rows)
        enrich_bot_names(session, rows)

    @classmethod
    def _enrich_owned_task_labels(cls, session: Any, rows: list[Any]) -> None:
        """Enrich each Trace only from relations owned by that Trace identity."""
        rows_by_identity: dict[tuple[str, str], list[Any]] = {}
        for row in rows:
            user_id = getattr(row, "user_id", None)
            bot_id = getattr(row, "bot_id", None)
            if user_id and bot_id:
                rows_by_identity.setdefault((user_id, bot_id), []).append(row)
        for (user_id, bot_id), owned_rows in rows_by_identity.items():
            cls._enrich_task_labels(
                session,
                owned_rows,
                user_id=user_id,
                bot_id=bot_id,
            )

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

    @classmethod
    def _to_detail(
        cls,
        row: Any,
        observations: list[ConversationObservation],
    ) -> ConversationDetail:
        summary = cls._to_session(row)
        return ConversationDetail(
            id=summary.id,
            biz_task_id=summary.biz_task_id,
            biz_scene=summary.biz_scene,
            session_id=summary.session_id,
            session_key=summary.session_key,
            bot_id=summary.bot_id,
            bot_name=summary.bot_name,
            group_id=summary.group_id,
            session_kind=summary.session_kind,
            name=summary.name,
            input=_json_value(row.input, row.input),
            output=_json_value(row.output, row.output),
            status=summary.status,
            timestamp=summary.timestamp,
            user_id=summary.user_id,
            metadata=summary.metadata,
            total_cost=summary.total_cost,
            latency_ms=summary.latency_ms,
            total_tokens=summary.total_tokens,
            observations=observations,
        )

    @staticmethod
    def _to_observation(row: Any) -> ConversationObservation:
        metadata = _json_value(getattr(row, "metadata_json", None), {})
        attributes = (
            metadata.get("attributes") or {} if isinstance(metadata, dict) else {}
        )
        total_cost = getattr(row, "total_cost", None)
        if total_cost is None:
            total_cost = getattr(row, "calculated_total_cost", None)
        latency_ms = getattr(row, "latency_ms", None)
        if latency_ms is None:
            latency = getattr(row, "latency", None)
            latency_ms = float(latency) * 1000 if latency is not None else 0
        return ConversationObservation(
            id=getattr(row, "observation_id", "") or "",
            biz_task_id=(
                getattr(row, "biz_task_id", None)
                or attributes.get("agentic.biz_task_id")
                or attributes.get("identity.biz_task_id")
            ),
            biz_scene=(
                getattr(row, "biz_scene", None)
                or attributes.get("agentic.biz_scene")
                or attributes.get("identity.biz_scene")
            ),
            name=getattr(row, "name", None) or "",
            type=getattr(row, "type", None) or "SPAN",
            latency_ms=float(latency_ms or 0),
            total_cost=float(total_cost or 0),
            total_tokens=int(getattr(row, "usage_total_tokens", None) or 0),
            input=_json_value(getattr(row, "input", None), getattr(row, "input", None)),
            output=_json_value(
                getattr(row, "output", None), getattr(row, "output", None)
            ),
            metadata=metadata if isinstance(metadata, dict) else None,
            model_name=(
                getattr(row, "model", None)
                or attributes.get("gen_ai.response.model")
                or attributes.get("gen_ai.request.model")
            ),
            parent_observation_id=getattr(row, "parent_observation_id", None),
            children=[],
        )

    @classmethod
    def _build_observation_tree(
        cls, rows: list[Any]
    ) -> list[ConversationObservation]:
        observations = [cls._to_observation(row) for row in rows]
        by_id = {item.id: item for item in observations}
        roots: list[ConversationObservation] = []
        for item in observations:
            if item.parent_observation_id in by_id:
                by_id[item.parent_observation_id].children.append(item)
            else:
                roots.append(item)
        return roots
