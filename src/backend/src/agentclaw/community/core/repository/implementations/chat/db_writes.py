"""Persistence write operations for the bot-chat repository."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from typing import Any

from sqlalchemy import func

from agentclaw.community.core.bot_chat.models import (
    AcOtelLogBizRef,
    AcOtelLogObservation,
    AcOtelLogTrace,
)


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


class BotChatDbWriteMixin:
    """Write-side persistence operations mixed into the chat repository."""

    def _json_dumps(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def _ref_digest(self, ref_value: str) -> str:
        return f"sha256:{sha256(ref_value.encode('utf-8')).hexdigest()}"

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
