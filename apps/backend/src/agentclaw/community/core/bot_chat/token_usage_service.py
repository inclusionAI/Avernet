"""
Token usage aggregation service.

Read-only queries against ``ac_otel_log_trace``, returning aggregated
token consumption per Bot / per day. No writes, no side effects.
"""

from datetime import datetime
from typing import Optional

from injector import inject
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from agentclaw.community.core.bot_chat.models import AcOtelLogTrace
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.log import get_logger

logger = get_logger()

# The "day" expression casts the epoch-ms start_time_ms to a date in UTC.
_DATE_EXPR = func.date(
    func.from_unixtime(AcOtelLogTrace.start_time_ms / 1000)
).label("date")


class TokenUsageService:
    """Aggregate token consumption from OTEL trace records."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _session(self) -> Session:
        return self._db.get_session()

    # ------------------------------------------------------------------
    # Batch summary — one row per bot_id for a time range
    # ------------------------------------------------------------------

    def batch_summary(
        self,
        from_ms: int,
        to_ms: int,
        bot_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """Return per-Bot token & cost totals for ``[from_ms, to_ms)``.

        When *bot_ids* is omitted, every Bot with any trace in the window
        is included.
        """
        session = self._session()
        cols = [
            AcOtelLogTrace.bot_id,
            func.sum(AcOtelLogTrace.usage_input_tokens).label("total_input_tokens"),
            func.sum(AcOtelLogTrace.usage_output_tokens).label("total_output_tokens"),
            func.sum(AcOtelLogTrace.usage_total_tokens).label("total_tokens"),
            func.sum(AcOtelLogTrace.total_cost).label("total_cost"),
        ]
        q = session.query(*cols).filter(
            AcOtelLogTrace.bot_id.isnot(None),
            AcOtelLogTrace.bot_id != "",
            AcOtelLogTrace.start_time_ms >= from_ms,
            AcOtelLogTrace.start_time_ms < to_ms,
        )
        if bot_ids:
            q = q.filter(AcOtelLogTrace.bot_id.in_(bot_ids))

        q = q.group_by(AcOtelLogTrace.bot_id).order_by(
            func.sum(AcOtelLogTrace.usage_total_tokens).desc()
        )

        rows = q.all()
        return [
            {
                "bot_id": row.bot_id,
                "total_input_tokens": int(row.total_input_tokens or 0),
                "total_output_tokens": int(row.total_output_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "total_cost": float(row.total_cost or 0),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Daily trend for a single Bot
    # ------------------------------------------------------------------

    def daily_trend(
        self,
        bot_id: str,
        from_ms: int,
        to_ms: int,
    ) -> list[dict]:
        """Return per-day token & cost totals for one Bot."""
        session = self._session()
        q = (
            session.query(
                _DATE_EXPR,
                func.sum(AcOtelLogTrace.usage_input_tokens).label("input_tokens"),
                func.sum(AcOtelLogTrace.usage_output_tokens).label("output_tokens"),
                func.sum(AcOtelLogTrace.usage_total_tokens).label("total_tokens"),
                func.sum(AcOtelLogTrace.total_cost).label("cost"),
            )
            .filter(
                AcOtelLogTrace.bot_id == bot_id,
                AcOtelLogTrace.start_time_ms >= from_ms,
                AcOtelLogTrace.start_time_ms < to_ms,
            )
            .group_by("date")
            .order_by("date")
        )
        return [
            {
                "date": str(row.date),
                "input_tokens": int(row.input_tokens or 0),
                "output_tokens": int(row.output_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "cost": float(row.cost or 0),
            }
            for row in q.all()
        ]
