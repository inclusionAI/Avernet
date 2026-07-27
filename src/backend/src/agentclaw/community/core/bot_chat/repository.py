"""DB repository for bot-chat queries."""

import json
from datetime import timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, func, or_

from agentclaw.community.core.bot_chat.models import (
    AwLangfuseObservation,
    AwLangfuseTrace,
    AcOtelLogBizRef,
    AcOtelLogObservation,
    AcOtelLogTrace,
)
from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_chat.query_support import (
    QueryScope,
    enrich_group_labels,
    enrich_task_labels,
    enrich_trace_labels,
    list_group_sessions,
    load_bot_names,
    load_task_refs,
    match_column,
    task_trace_condition,
)
from agentclaw.community.core.bot_chat.schemas import (
    ConversationObservation,
    ConversationDetail,
    ConversationSession,
    SessionMetadata,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env

_OUTPUT_PREVIEW_LENGTH = 500


def _extract_user_input(trace_input: Any) -> str | None:
    """Extract the last user message from trace input."""
    if trace_input is None:
        return None
    if isinstance(trace_input, str):
        return trace_input
    if isinstance(trace_input, list):
        for msg in reversed(trace_input):
            if (
                isinstance(msg, dict)
                and msg.get("role") == "user"
            ):
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    for part in content:
                        if (
                            isinstance(part, dict)
                            and part.get("type") == "text"
                        ):
                            return part.get("text", "")
                    first_text = next(
                        (
                            p
                            for p in content
                            if isinstance(p, str)
                        ),
                        None,
                    )
                    if first_text:
                        return first_text
        first = trace_input[0] if trace_input else None
        if isinstance(first, dict):
            return first.get("content", str(first))
        return str(first) if first else None
    if isinstance(trace_input, dict):
        return trace_input.get("content", str(trace_input))
    return str(trace_input)


def _output_preview(trace_output: Any) -> str | None:
    if trace_output is None:
        return None
    if isinstance(trace_output, str):
        value = trace_output
    else:
        value = json.dumps(trace_output, ensure_ascii=False)
    return value[:_OUTPUT_PREVIEW_LENGTH]


def _decimal_for_column_or_none(value: Any, column: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
        column_type = column.property.columns[0].type
        scale = getattr(column_type, "scale", None)
        precision = getattr(column_type, "precision", None)
        if scale is not None:
            decimal_value = decimal_value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
        if precision is not None and scale is not None:
            max_integer_digits = precision - scale
            integer_digits = max(decimal_value.adjusted() + 1, 1)
            if integer_digits > max_integer_digits:
                return None
        return decimal_value
    except (AttributeError, InvalidOperation, TypeError, ValueError):
        return None


class BotChatDbRepository:
    """Synchronous DB repository for bot-chat queries."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def _safe_json_loads(self, value: str | None, default: Any = None) -> Any:
        """Safely parse JSON string."""
        if not value:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

    def _json_dumps(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def _metadata_bot_id(self, metadata_json: str | None) -> str | None:
        metadata = self._safe_json_loads(metadata_json, {})
        if not isinstance(metadata, dict):
            return None
        attributes = metadata.get("attributes") or {}
        if not isinstance(attributes, dict):
            return None
        value = attributes.get("identity.bot_id")
        return str(value) if value else None

    def _ref_digest(self, ref_value: str) -> str:
        return f"sha256:{sha256(ref_value.encode('utf-8')).hexdigest()}"

    def _detach_trace_row(self, row: Any) -> Any:
        """Copy fields used by bot-chat before the ORM session closes."""
        return SimpleNamespace(
            id=row.id,
            trace_id=row.trace_id,
            gmt_trace=row.gmt_trace,
            name=row.name,
            input=row.input,
            output=row.output,
            biz_task_id=getattr(row, "biz_task_id", None),
            biz_scene=getattr(row, "biz_scene", None),
            session_id=row.session_id,
            session_key=row.session_id,
            user_id=row.user_id,
            trace_metadata=row.trace_metadata,
            latency=row.latency,
            total_cost=row.total_cost,
            observations=row.observations,
            bot_id=row.bot_id or self._metadata_bot_id(row.trace_metadata),
            device_id=row.device_id,
            real_session_id=row.real_session_id,
            usage_total_tokens=None,
            bot_name=None,
            group_id=None,
            session_kind=None,
            match_sources=[],
        )

    def _detach_ocb_trace_row(self, row: Any) -> Any:
        return SimpleNamespace(
            id=row.id,
            trace_id=row.trace_id,
            gmt_trace=row.start_time_ms,
            name=row.name,
            input=row.input,
            output=row.output,
            biz_task_id=row.biz_task_id,
            biz_scene=row.biz_scene,
            session_id=row.session_id,
            session_key=row.session_key or row.session_id,
            user_id=row.user_id,
            trace_metadata=row.metadata_json,
            latency=row.latency_ms,
            total_cost=row.total_cost,
            observations=None,
            bot_id=row.bot_id or self._metadata_bot_id(row.metadata_json),
            device_id=None,
            usage_total_tokens=row.usage_total_tokens,
            bot_name=None,
            group_id=None,
            session_kind=None,
            match_sources=[],
        )

    def _fill_ocb_trace_usage_from_observations(self, session: Any, row: AcOtelLogTrace) -> None:
        if (
            row.usage_input_tokens is not None
            and row.usage_output_tokens is not None
            and row.usage_total_tokens is not None
            and row.total_cost is not None
        ):
            return

        usage = (
            session.query(
                func.sum(AcOtelLogObservation.usage_input_tokens),
                func.sum(AcOtelLogObservation.usage_output_tokens),
                func.sum(AcOtelLogObservation.usage_total_tokens),
                func.sum(AcOtelLogObservation.total_cost),
            )
            .filter(AcOtelLogObservation.trace_id == row.trace_id)
            .one()
        )
        if (
            row.usage_input_tokens is None
            and usage[0] is not None
        ):
            row.usage_input_tokens = int(usage[0])
        if (
            row.usage_output_tokens is None
            and usage[1] is not None
        ):
            row.usage_output_tokens = int(usage[1])
        if (
            row.usage_total_tokens is None
            and usage[2] is not None
        ):
            row.usage_total_tokens = int(usage[2])
        if (
            row.total_cost is None
            and usage[3] is not None
        ):
            row.total_cost = usage[3]

    def _row_to_session(self, row: Any) -> ConversationSession:
        """Convert DB row to ConversationSession."""
        from datetime import datetime

        input_data = self._safe_json_loads(row.input, None)
        output_data = self._safe_json_loads(row.output, row.output)
        metadata_raw = self._safe_json_loads(row.trace_metadata, {})
        attributes = dict(metadata_raw.get("attributes") or {}) if isinstance(metadata_raw, dict) else {}
        biz_task_id = (
            getattr(row, "biz_task_id", None)
            or attributes.get("agentic.biz_task_id")
            or attributes.get("identity.biz_task_id")
        )
        biz_scene = (
            getattr(row, "biz_scene", None)
            or attributes.get("agentic.biz_scene")
            or attributes.get("identity.biz_scene")
        )
        session_id = getattr(row, "real_session_id", None) or getattr(row, "session_id", None)
        session_key = getattr(row, "session_key", None) or getattr(row, "session_id", None)
        if session_id:
            attributes.setdefault("gen_ai.session.id", session_id)
        if session_key:
            attributes.setdefault("session_id", session_key)
            attributes.setdefault("gen_ai.conversation.id", session_key)

        # Convert gmt_trace (ms) to ISO 8601 UTC string
        timestamp = ""
        if row.gmt_trace:
            dt = datetime.fromtimestamp(row.gmt_trace / 1000, tz=timezone.utc)
            timestamp = dt.isoformat().replace("+00:00", "Z")

        return ConversationSession(
            id=row.trace_id or "",
            biz_task_id=biz_task_id,
            biz_scene=biz_scene,
            session_id=session_id,
            session_key=session_key,
            bot_id=getattr(row, "bot_id", None),
            bot_name=getattr(row, "bot_name", None),
            group_id=getattr(row, "group_id", None),
            session_kind=getattr(row, "session_kind", None),
            name=row.name or "未命名会话",
            input=_extract_user_input(input_data),
            output_preview=_output_preview(output_data),
            match_sources=list(getattr(row, "match_sources", None) or []),
            status="SUCCESS",
            timestamp=timestamp,
            user_id=row.user_id,
            metadata=SessionMetadata(attributes=attributes),
            total_cost=float(row.total_cost) if row.total_cost else 0.0,
            latency_ms=float(row.latency) if row.latency else 0.0,
            total_tokens=int(getattr(row, "usage_total_tokens", None) or 0),
        )

    def _row_to_detail(self, row: Any, observations: list | None = None) -> ConversationDetail:
        """Convert DB row to ConversationDetail."""
        from datetime import datetime

        input_data = self._safe_json_loads(row.input, None)
        output_data = self._safe_json_loads(row.output, None)
        metadata_raw = self._safe_json_loads(row.trace_metadata, {})
        attributes = dict(metadata_raw.get("attributes") or {}) if isinstance(metadata_raw, dict) else {}
        biz_task_id = (
            getattr(row, "biz_task_id", None)
            or attributes.get("agentic.biz_task_id")
            or attributes.get("identity.biz_task_id")
        )
        biz_scene = (
            getattr(row, "biz_scene", None)
            or attributes.get("agentic.biz_scene")
            or attributes.get("identity.biz_scene")
        )
        session_id = getattr(row, "real_session_id", None) or getattr(row, "session_id", None)
        session_key = getattr(row, "session_key", None) or getattr(row, "session_id", None)
        if session_id:
            attributes.setdefault("gen_ai.session.id", session_id)
        if session_key:
            attributes.setdefault("session_id", session_key)
            attributes.setdefault("gen_ai.conversation.id", session_key)

        timestamp = ""
        if row.gmt_trace:
            dt = datetime.fromtimestamp(row.gmt_trace / 1000, tz=timezone.utc)
            timestamp = dt.isoformat().replace("+00:00", "Z")

        return ConversationDetail(
            id=row.trace_id or "",
            biz_task_id=biz_task_id,
            biz_scene=biz_scene,
            session_id=session_id,
            session_key=session_key,
            bot_id=getattr(row, "bot_id", None),
            bot_name=getattr(row, "bot_name", None),
            group_id=getattr(row, "group_id", None),
            session_kind=getattr(row, "session_kind", None),
            name=row.name or "未命名会话",
            input=input_data,
            output=output_data,
            status="SUCCESS",
            timestamp=timestamp,
            user_id=row.user_id,
            metadata=SessionMetadata(attributes=attributes),
            total_cost=float(row.total_cost) if row.total_cost else 0.0,
            latency_ms=float(row.latency) if row.latency else 0.0,
            total_tokens=int(getattr(row, "usage_total_tokens", None) or 0),
            observations=observations or [],
        )

    def _observation_row_to_schema(self, row: Any) -> ConversationObservation:
        metadata = self._safe_json_loads(getattr(row, "metadata_json", None), {})
        attributes = metadata.get("attributes") or {} if isinstance(metadata, dict) else {}
        output = self._safe_json_loads(getattr(row, "output", None), getattr(row, "output", None))
        input_value = self._safe_json_loads(getattr(row, "input", None), getattr(row, "input", None))
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
            input=input_value,
            output=output,
            metadata=metadata if isinstance(metadata, dict) else None,
            model_name=getattr(row, "model", None) or attributes.get("gen_ai.response.model") or attributes.get("gen_ai.request.model"),
            parent_observation_id=getattr(row, "parent_observation_id", None),
            children=[],
        )

    def _build_observation_tree(self, rows: list[Any]) -> list[ConversationObservation]:
        observations = [
            self._observation_row_to_schema(row)
            for row in rows
        ]
        by_id = {item.id: item for item in observations}
        roots: list[ConversationObservation] = []
        for item in observations:
            parent_id = item.parent_observation_id
            if (
                parent_id
                and parent_id in by_id
            ):
                by_id[parent_id].children.append(item)
            else:
                roots.append(item)
        return roots

    def owns_bot(self, owner_id: str, bot_id: str) -> bool:
        """Check if owner owns the specified bot via ac_bots table.

        Filters by current environment and excludes soft-deleted bots to match
        the collaborator check semantics in ``is_bot_collaborator``.
        """
        with self._db.orm_session() as session:
            row = (
                session.query(BotModel)
                .filter(
                    BotModel.entity_id == owner_id,
                    BotModel.bot_id == bot_id,
                    BotModel.is_delete == 0,
                    BotModel.env == get_current_env(),
                )
                .order_by(BotModel.id.desc())
                .first()
            )
            return row is not None

    def is_bot_owner(self, owner_id: str, bot_id: str) -> bool:
        """Check if owner_id is the owner of bot_id via ac_bots table."""
        return self.owns_bot(owner_id, bot_id)

    def is_bot_collaborator(self, user_id: str, bot_id: str) -> bool:
        """Check if user_id is a collaborator of bot_id via ac_bot_collaborator."""
        with self._db.orm_session() as session:
            row = (
                session.query(BotCollaboratorModel)
                .filter(
                    BotCollaboratorModel.bot_id == bot_id,
                    BotCollaboratorModel.user_id == user_id,
                    BotCollaboratorModel.env == get_current_env(),
                )
                .first()
            )
            return row is not None

    def has_bot_access(self, user_id: str, bot_id: str) -> bool:
        """Check if user_id is either owner or collaborator of bot_id."""
        return self.is_bot_owner(user_id, bot_id) or self.is_bot_collaborator(user_id, bot_id)

    def get_bot_name(self, bot_id: str | None) -> str | None:
        if not bot_id:
            return None
        with self._db.orm_session() as session:
            return load_bot_names(session, {bot_id}).get(bot_id)

    def get_group_labels(
        self, session_key: str | None
    ) -> tuple[str | None, str | None]:
        if not session_key:
            return None, None
        probe = SimpleNamespace(
            bot_id=None,
            bot_name=None,
            session_key=session_key,
            group_id=None,
            session_kind=None,
        )
        with self._db.orm_session() as session:
            enriched = enrich_trace_labels(session, probe)
        return enriched.group_id, enriched.session_kind

    def list_traces(
        self,
        owner_id: str | None,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
        bot_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        session_key: str | None = None,
        query: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
        match_mode: str = "exact",
        include_output_match: bool = False,
        query_scope: QueryScope = QueryScope.OWNER,
    ) -> tuple[list[ConversationSession], int]:
        """List traces from DB with pagination."""
        with self._db.orm_session() as session:
            # Build query conditions
            conditions = [
                AwLangfuseTrace.gmt_trace >= from_ms,
                AwLangfuseTrace.gmt_trace <= to_ms,
            ]

            # Bot ownership logic per spec:
            # - bot_id is None: filter by user_id = owner_id
            # - bot_id == "default": filter by user_id = owner_id AND bot_id = "default"
            # - bot_id != "default": caller must verify ownership; here only filter by bot_id
            if query_scope == QueryScope.OWNER:
                if not owner_id:
                    raise ValueError("owner_id is required for owner-scoped queries")
                if bot_id is None:
                    conditions.append(AwLangfuseTrace.user_id == owner_id)
                elif bot_id == "default":
                    conditions.append(AwLangfuseTrace.user_id == owner_id)
                    conditions.append(AwLangfuseTrace.bot_id == "default")
                else:
                    # Non-default bot: only filter by bot_id (ownership verified by caller)
                    conditions.append(AwLangfuseTrace.bot_id == bot_id)

            if trace_id:
                conditions.append(match_column(AwLangfuseTrace.trace_id, trace_id, match_mode))

            # session_key maps to DB session_id
            if session_key:
                conditions.append(match_column(AwLangfuseTrace.session_id, session_key, match_mode))

            # session_id maps to DB real_session_id
            if session_id:
                conditions.append(match_column(AwLangfuseTrace.real_session_id, session_id, match_mode))

            task_refs = load_task_refs(
                session,
                biz_scene,
                biz_task_id,
                match_mode,
                owner_id,
                bot_id,
                query_scope,
            )
            if biz_scene or biz_task_id:
                ref_conditions = []
                if task_refs.get("trace_id"):
                    ref_conditions.append(AwLangfuseTrace.trace_id.in_(task_refs["trace_id"]))
                if task_refs.get("session_id"):
                    ref_conditions.append(AwLangfuseTrace.real_session_id.in_(task_refs["session_id"]))
                if task_refs.get("session_key"):
                    ref_conditions.append(AwLangfuseTrace.session_id.in_(task_refs["session_key"]))
                if not ref_conditions:
                    return [], 0
                conditions.append(or_(*ref_conditions))

            group_sessions: dict[str, str | None] = {}
            if group_id:
                group_sessions = list_group_sessions(session, group_id)
                if not group_sessions:
                    return [], 0
                conditions.append(AwLangfuseTrace.session_id.in_(group_sessions))

            if query:
                like_pattern = f"%{query}%"
                text_conditions = [
                    AwLangfuseTrace.name.like(like_pattern),
                    AwLangfuseTrace.input.like(like_pattern),
                ]
                if include_output_match:
                    text_conditions.append(AwLangfuseTrace.output.like(like_pattern))
                conditions.append(or_(*text_conditions))

            where_clause = and_(*conditions)

            # Get total count
            total = (
                session.query(func.count(AwLangfuseTrace.id))
                .filter(where_clause)
                .scalar()
            ) or 0

            # Get paginated results
            offset = (page - 1) * limit
            rows = (
                session.query(AwLangfuseTrace)
                .filter(where_clause)
                .order_by(AwLangfuseTrace.gmt_trace.desc(), AwLangfuseTrace.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )

            detached = [self._detach_trace_row(row) for row in rows]
            enrich_group_labels(session, detached, group_id, group_sessions)
            enrich_task_labels(session, detached)
            bot_names = load_bot_names(
                session, {row.bot_id for row in detached if row.bot_id}
            )
            for row in detached:
                row.bot_name = bot_names.get(row.bot_id)
                if biz_scene or biz_task_id:
                    row.match_sources = ["biz_ref"]
            sessions = [self._row_to_session(row) for row in detached]
            return sessions, total

    def list_ocb_traces(
        self,
        owner_id: str | None,
        from_ms: int,
        to_ms: int,
        page: int,
        limit: int,
        bot_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        session_key: str | None = None,
        query: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
        match_mode: str = "exact",
        include_output_match: bool = False,
        query_scope: QueryScope = QueryScope.OWNER,
    ) -> tuple[list[ConversationSession], int]:
        with self._db.orm_session() as session:
            conditions = [
                AcOtelLogTrace.start_time_ms >= from_ms,
                AcOtelLogTrace.start_time_ms <= to_ms,
            ]
            if query_scope == QueryScope.OWNER:
                if not owner_id:
                    raise ValueError("owner_id is required for owner-scoped queries")
                if bot_id is None:
                    conditions.append(AcOtelLogTrace.user_id == owner_id)
                elif bot_id == "default":
                    conditions.append(AcOtelLogTrace.user_id == owner_id)
                    conditions.append(AcOtelLogTrace.bot_id.in_(["default", f"{owner_id}_default"]))
                else:
                    conditions.append(AcOtelLogTrace.bot_id == bot_id)
            if trace_id:
                conditions.append(match_column(AcOtelLogTrace.trace_id, trace_id, match_mode))
            if session_key:
                conditions.append(match_column(AcOtelLogTrace.session_key, session_key, match_mode))
            if session_id:
                conditions.append(match_column(AcOtelLogTrace.session_id, session_id, match_mode))
            task_refs = load_task_refs(
                session,
                biz_scene,
                biz_task_id,
                match_mode,
                owner_id,
                bot_id,
                query_scope,
            )
            if biz_scene or biz_task_id:
                conditions.append(
                    task_trace_condition(
                        task_refs, biz_scene, biz_task_id, match_mode
                    )
                )
            group_sessions: dict[str, str | None] = {}
            if group_id:
                group_sessions = list_group_sessions(session, group_id)
                if not group_sessions:
                    return [], 0
                conditions.append(AcOtelLogTrace.session_key.in_(group_sessions))
            if query:
                like_pattern = f"%{query}%"
                text_conditions = [
                    AcOtelLogTrace.name.like(like_pattern),
                    AcOtelLogTrace.input.like(like_pattern),
                ]
                if include_output_match:
                    text_conditions.append(AcOtelLogTrace.output.like(like_pattern))
                conditions.append(or_(*text_conditions))

            where_clause = and_(*conditions)
            total = session.query(func.count(AcOtelLogTrace.id)).filter(where_clause).scalar() or 0
            rows = (
                session.query(AcOtelLogTrace)
                .filter(where_clause)
                .order_by(AcOtelLogTrace.start_time_ms.desc(), AcOtelLogTrace.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
                .all()
            )
            detached = [
                self._detach_ocb_trace_row(row)
                for row in rows
            ]
            enrich_group_labels(session, detached, group_id, group_sessions)
            bot_names = load_bot_names(
                session,
                {row.bot_id for row in detached if row.bot_id},
            )
            ref_trace_ids = task_refs.get("trace_id", set())
            ref_session_ids = task_refs.get("session_id", set())
            ref_session_keys = task_refs.get("session_key", set())
            for row in detached:
                row.bot_name = bot_names.get(row.bot_id)
                sources = []
                direct_scene = not biz_scene or (
                    biz_scene in (row.biz_scene or "")
                    if match_mode == "contains"
                    else row.biz_scene == biz_scene
                )
                direct_task = not biz_task_id or (
                    biz_task_id in (row.biz_task_id or "")
                    if match_mode == "contains"
                    else row.biz_task_id == biz_task_id
                )
                if (biz_scene or biz_task_id) and direct_scene and direct_task:
                    sources.append("direct")
                if (
                    row.trace_id in ref_trace_ids
                    or row.session_id in ref_session_ids
                    or row.session_key in ref_session_keys
                ):
                    sources.append("biz_ref")
                row.match_sources = sources
            enrich_task_labels(session, detached)
            sessions = [
                self._row_to_session(row)
                for row in detached
            ]
            return sessions, total

    def get_trace(self, trace_id: str) -> Any | None:
        """Get single trace by ID."""
        with self._db.orm_session() as session:
            row = (
                session.query(AwLangfuseTrace)
                .filter(AwLangfuseTrace.trace_id == trace_id)
                .first()
            )
            return (
                enrich_trace_labels(session, self._detach_trace_row(row))
                if row is not None
                else None
            )

    def get_ocb_trace(self, trace_id: str) -> Any | None:
        with self._db.orm_session() as session:
            row = session.query(AcOtelLogTrace).filter(AcOtelLogTrace.trace_id == trace_id).first()
            return (
                enrich_trace_labels(session, self._detach_ocb_trace_row(row))
                if row is not None
                else None
            )

    def list_ocb_observations(self, trace_id: str) -> list[ConversationObservation]:
        with self._db.orm_session() as session:
            rows = (
                session.query(AcOtelLogObservation)
                .filter(AcOtelLogObservation.trace_id == trace_id)
                .order_by(AcOtelLogObservation.start_time_ms.asc(), AcOtelLogObservation.id.asc())
                .all()
            )
            return self._build_observation_tree(rows)

    def list_legacy_observations(self, trace_id: str) -> list[ConversationObservation]:
        with self._db.orm_session() as session:
            rows = (
                session.query(AwLangfuseObservation)
                .filter(AwLangfuseObservation.trace_id == trace_id)
                .order_by(AwLangfuseObservation.start_time.asc(), AwLangfuseObservation.id.asc())
                .all()
            )
            return self._build_observation_tree(rows)

    def upsert_ocb_trace(self, trace: dict[str, Any], source: dict[str, Any] | None = None) -> str:
        source = source or {}
        trace_id = trace.get("trace_id")
        if not trace_id:
            raise ValueError("trace_id is required")
        usage = dict(trace.get("usage") or {})
        metadata = trace.get("metadata") or {}
        if (
            trace.get("usage_details")
            or trace.get("cost_details")
        ):
            metadata = dict(metadata)
            attributes = dict(metadata.get("attributes") or {})
            if trace.get("usage_details"):
                attributes["usage_details"] = trace.get("usage_details")
            if trace.get("cost_details"):
                attributes["cost_details"] = trace.get("cost_details")
            metadata["attributes"] = attributes
        user_id = trace.get("user_id")
        bot_id = trace.get("bot_id") or "default"
        if (
            user_id
            and bot_id == f"{user_id}_default"
        ):
            metadata = dict(metadata)
            metadata.setdefault("original_bot_id", bot_id)
            bot_id = "default"

        with self._db.orm_session() as session:
            row = session.query(AcOtelLogTrace).filter(AcOtelLogTrace.trace_id == trace_id).first()
            status = "updated" if row is not None else "inserted"
            if row is None:
                row = AcOtelLogTrace(trace_id=trace_id)
                session.add(row)

            row.biz_task_id = trace.get("biz_task_id")
            row.biz_scene = trace.get("biz_scene")
            row.session_id = trace.get("session_id")
            row.session_key = trace.get("session_key")
            row.user_id = user_id
            row.bot_id = bot_id
            row.engine = source.get("engine") or trace.get("engine")
            row.collector = source.get("collector") or trace.get("collector")
            row.name = trace.get("name") or "bot_chat"
            row.input = self._json_dumps(trace.get("input"))
            row.output = self._json_dumps(trace.get("output"))
            row.metadata_json = self._json_dumps(metadata)
            row.start_time_ms = trace.get("start_time_ms")
            row.end_time_ms = trace.get("end_time_ms")
            row.latency_ms = trace.get("latency_ms")
            has_trace_usage = (
                usage.get("input_tokens") is not None
                or usage.get("output_tokens") is not None
                or usage.get("total_tokens") is not None
                or trace.get("total_cost") is not None
            )
            if has_trace_usage:
                row.usage_input_tokens = (
                    usage.get("input_tokens")
                    if usage.get("input_tokens") is not None
                    else row.usage_input_tokens
                )
                row.usage_output_tokens = (
                    usage.get("output_tokens")
                    if usage.get("output_tokens") is not None
                    else row.usage_output_tokens
                )
                row.usage_total_tokens = (
                    usage.get("total_tokens")
                    if usage.get("total_tokens") is not None
                    else row.usage_total_tokens
                )
                row.total_cost = (
                    _decimal_for_column_or_none(trace.get("total_cost"), AcOtelLogTrace.total_cost)
                    if trace.get("total_cost") is not None
                    else row.total_cost
                )
            else:
                # Root CHAT spans normally omit usage; clear stale zeroes so child LLM spans can aggregate.
                row.usage_input_tokens = None
                row.usage_output_tokens = None
                row.usage_total_tokens = None
                row.total_cost = None
            self._fill_ocb_trace_usage_from_observations(session, row)
            row.payload_digest = trace.get("payload_digest")
            return status

    def upsert_ocb_observation(self, observation: dict[str, Any]) -> str:
        observation_id = observation.get("observation_id")
        trace_id = observation.get("trace_id")
        if not observation_id:
            raise ValueError("observation_id is required")
        if not trace_id:
            raise ValueError("trace_id is required")
        usage = observation.get("usage") or {}
        metadata = observation.get("metadata") or {}
        if (
            observation.get("usage_details")
            or observation.get("cost_details")
        ):
            metadata = dict(metadata)
            attributes = dict(metadata.get("attributes") or {})
            if observation.get("usage_details"):
                attributes["usage_details"] = observation.get("usage_details")
            if observation.get("cost_details"):
                attributes["cost_details"] = observation.get("cost_details")
            metadata["attributes"] = attributes

        with self._db.orm_session() as session:
            row = session.query(AcOtelLogObservation).filter(AcOtelLogObservation.observation_id == observation_id).first()
            status = "updated" if row is not None else "inserted"
            if row is None:
                row = AcOtelLogObservation(observation_id=observation_id)
                session.add(row)

            row.trace_id = trace_id
            row.parent_observation_id = observation.get("parent_observation_id")
            row.biz_task_id = observation.get("biz_task_id")
            row.biz_scene = observation.get("biz_scene")
            row.session_id = observation.get("session_id")
            row.session_key = observation.get("session_key")
            row.type = observation.get("type") or "SPAN"
            row.name = observation.get("name") or ""
            row.model = observation.get("model")
            row.input = self._json_dumps(observation.get("input"))
            row.output = self._json_dumps(observation.get("output"))
            row.metadata_json = self._json_dumps(metadata)
            row.start_time_ms = observation.get("start_time_ms")
            row.end_time_ms = observation.get("end_time_ms")
            row.latency_ms = observation.get("latency_ms")
            row.status = observation.get("status")
            row.status_message = observation.get("status_message")
            row.usage_input_tokens = usage.get("input_tokens")
            row.usage_output_tokens = usage.get("output_tokens")
            row.usage_total_tokens = usage.get("total_tokens")
            row.total_cost = _decimal_for_column_or_none(observation.get("total_cost"), AcOtelLogObservation.total_cost)
            row.payload_digest = observation.get("payload_digest")
            return status

    def upsert_biz_refs(self, relation: dict[str, Any]) -> dict[str, int]:
        biz_scene = relation.get("biz_scene")
        biz_task_id = relation.get("biz_task_id")
        refs = relation.get("refs") or []
        if not biz_scene:
            raise ValueError("biz_scene is required")
        if not biz_task_id:
            raise ValueError("biz_task_id is required")
        if not refs:
            raise ValueError("refs is required")

        inserted = 0
        updated = 0
        with self._db.orm_session() as session:
            dialect = session.get_bind().dialect.name
            table = AcOtelLogBizRef.__table__
            for ref in refs:
                ref_type = ref.get("ref_type")
                ref_value = ref.get("ref_value")
                if not ref_type:
                    raise ValueError("ref_type is required")
                if not ref_value:
                    raise ValueError("ref_value is required")

                ref_digest = self._ref_digest(str(ref_value))
                row = (
                    session.query(AcOtelLogBizRef)
                    .filter(
                        AcOtelLogBizRef.biz_scene == biz_scene,
                        AcOtelLogBizRef.biz_task_id == biz_task_id,
                        AcOtelLogBizRef.ref_type == ref_type,
                        AcOtelLogBizRef.ref_digest == ref_digest,
                    )
                    .first()
                )
                if row is None:
                    inserted += 1
                else:
                    updated += 1

                metadata = relation.get("metadata") or {}
                if ref.get("metadata"):
                    metadata = {
                        **dict(metadata),
                        "ref_metadata": ref.get("metadata"),
                    }
                values = {
                    "biz_scene": biz_scene,
                    "biz_task_id": biz_task_id,
                    "ref_type": ref_type,
                    "ref_value": str(ref_value),
                    "ref_digest": ref_digest,
                    "engine": relation.get("engine"),
                    "collector": relation.get("collector"),
                    "user_id": relation.get("user_id"),
                    "bot_id": relation.get("bot_id"),
                    "metadata": self._json_dumps(metadata),
                }
                update_values = {
                    "ref_value": values["ref_value"],
                    "engine": values["engine"],
                    "collector": values["collector"],
                    "user_id": values["user_id"],
                    "bot_id": values["bot_id"],
                    "metadata": values["metadata"],
                    "gmt_modified": func.now(),
                }
                if dialect == "sqlite":
                    from sqlalchemy.dialects.sqlite import insert as _insert

                    stmt = _insert(table).values(**values)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[
                            "biz_scene",
                            "biz_task_id",
                            "ref_type",
                            "ref_digest",
                        ],
                        set_=update_values,
                    )
                else:
                    from sqlalchemy.dialects.mysql import insert as _insert

                    stmt = _insert(table).values(**values)
                    stmt = stmt.on_duplicate_key_update(**update_values)
                session.execute(stmt)

        return {
            "inserted": inserted,
            "updated": updated,
            "total": inserted + updated,
        }
