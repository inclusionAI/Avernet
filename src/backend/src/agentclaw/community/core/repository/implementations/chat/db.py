"""DB repository for product-facing bot-chat queries."""

import json
from datetime import timezone
from types import SimpleNamespace
from typing import Any

from injector import inject
from sqlalchemy import and_, func, or_

from agentclaw.community.core.bot_chat.models import (
    AwLangfuseObservation,
    AwLangfuseTrace,
    AcOtelLogObservation,
    AcOtelLogTrace,
    BcsGroupSession,
)
from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_chat.query_support import (
    QueryScope,
    enrich_bot_names,
    enrich_group_labels,
    enrich_task_labels,
    enrich_trace_labels,
    bcs_session_candidates,
    trace_keys_for_bcs_session,
    list_group_sessions,
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
from agentclaw.community.core.repository.implementations.chat.db_writes import (
    BotChatDbWriteMixin,
    _decimal_for_column_or_none,
)
from agentclaw.community.core.repository.protocols.chat import BotChatDbRepositoryProtocol
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.env_utils import get_current_env

__all__ = ["BotChatDbRepository", "_decimal_for_column_or_none"]

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



class BotChatDbRepository(
    BotChatDbWriteMixin,
    BotChatDbRepositoryProtocol,
):
    """Synchronous DB repository for bot-chat queries."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
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

    def _metadata_owner_id(self, metadata_json: str | None) -> str | None:
        metadata = self._safe_json_loads(metadata_json, {})
        if not isinstance(metadata, dict):
            return None
        attributes = metadata.get("attributes") or {}
        if not isinstance(attributes, dict):
            return None
        value = attributes.get("identity.owner_id") or attributes.get("user.id")
        return str(value) if value else None

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
            user_id=row.user_id or self._metadata_owner_id(row.trace_metadata),
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
            user_id=row.user_id or self._metadata_owner_id(row.metadata_json),
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

    def has_group_trace_access(
        self, user_id: str, session_id: str | None, session_key: str | None
    ) -> bool:
        """Allow a group participant to read traces from every bot in that group.

        A trace detail request has historically carried only ``trace_id`` and
        user identity. Resolve the trace's BCS group from its session key, then
        grant access when the user owns or collaborates on any bot that has a
        trace in that group. This keeps ordinary bot-scoped access unchanged
        while making the group aggregation/detail contract consistent.
        """
        values = {value.strip() for value in (session_id, session_key) if value and value.strip()}
        if not values:
            return False

        with self._db.orm_session() as session:
            group_rows = (
                session.query(BcsGroupSession)
                .filter(
                    BcsGroupSession.env == get_current_env(),
                    BcsGroupSession.session_id.in_(
                        candidate
                        for value in values
                        for candidate in bcs_session_candidates(value)
                    ),
                )
                .all()
            )
            if not group_rows:
                return False

            group_ids = {row.group_id for row in group_rows}
            group_session_ids = [
                row.session_id
                for row in session.query(BcsGroupSession).filter(
                    BcsGroupSession.env == get_current_env(),
                    BcsGroupSession.group_id.in_(group_ids),
                ).all()
            ]
            log_keys = {
                key
                for value in group_session_ids
                for key in (trace_keys_for_bcs_session(value) | {value})
            }
            if not log_keys:
                return False

            bot_ids = {
                bot_id
                for (bot_id,) in session.query(AcOtelLogTrace.bot_id)
                .filter(
                    AcOtelLogTrace.session_key.in_(log_keys),
                    AcOtelLogTrace.bot_id.isnot(None),
                )
                .distinct()
                .all()
            }
            bot_ids.update(
                bot_id
                for (bot_id,) in session.query(AwLangfuseTrace.bot_id)
                .filter(
                    AwLangfuseTrace.session_id.in_(log_keys),
                    AwLangfuseTrace.bot_id.isnot(None),
                )
                .distinct()
                .all()
            )
            owner_match = (
                session.query(BotModel.id)
                .filter(
                    BotModel.entity_id == user_id,
                    BotModel.bot_id.in_(bot_ids),
                    BotModel.is_delete == 0,
                    BotModel.env == get_current_env(),
                )
                .first()
            )
            if owner_match is not None:
                return True

            collaborator_match = (
                session.query(BotCollaboratorModel.id)
                .filter(
                    BotCollaboratorModel.user_id == user_id,
                    BotCollaboratorModel.bot_id.in_(bot_ids),
                    BotCollaboratorModel.env == get_current_env(),
                )
                .first()
            )
            return collaborator_match is not None

    def enrich_labels(
        self,
        rows: list[Any],
        preferred_biz_scene: str | None = None,
        preferred_biz_task_id: str | None = None,
    ) -> None:
        """Batch-fill display labels for one final response page."""
        with self._db.orm_session() as session:
            enrich_group_labels(session, rows)
            enrich_task_labels(
                session, rows, preferred_biz_scene, preferred_biz_task_id
            )
            enrich_bot_names(session, rows)

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
            enrich_bot_names(session, detached)
            for row in detached:
                if biz_scene or biz_task_id:
                    row.match_sources = ["biz_ref"]
            enrich_task_labels(session, detached, biz_scene, biz_task_id)
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
            enrich_bot_names(session, detached)
            ref_trace_ids = task_refs.get("trace_id", set())
            ref_session_ids = task_refs.get("session_id", set())
            ref_session_keys = task_refs.get("session_key", set())
            for row in detached:
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
            enrich_task_labels(session, detached, biz_scene, biz_task_id)
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
