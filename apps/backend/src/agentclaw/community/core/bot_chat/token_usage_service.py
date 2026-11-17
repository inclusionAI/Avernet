"""
Token usage aggregation service.

Read-only queries against ``ac_otel_log_trace``, returning aggregated
token consumption per Bot / per day. No writes, no side effects.

Cross-database compatible: works on MySQL (production) and SQLite
(singlebox / test) by avoiding vendor-specific date functions.
"""

from datetime import datetime, timezone
from typing import Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.bot_chat.models import AcOtelLogTrace
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.log import get_logger

logger = get_logger()

_MS_PER_DAY = 86_400_000


def _bucket_to_date(day_bucket: int) -> str:
    """Convert a day-bucket integer (epoch days) to ISO date string."""
    return datetime.fromtimestamp(
        day_bucket * _MS_PER_DAY / 1000, tz=timezone.utc,
    ).strftime("%Y-%m-%d")


class TokenUsageService:
    """Aggregate token consumption from OTEL trace records."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

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
        with self._db.orm_session() as session:
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
        """Return per-day token & cost totals for one Bot.

        Groups by day using integer division on epoch-ms (portable across
        MySQL and SQLite), then converts to an ISO date string.
        """
        day_bucket = func.floor(AcOtelLogTrace.start_time_ms / _MS_PER_DAY).label(
            "day_bucket"
        )

        with self._db.orm_session() as session:
            q = (
                session.query(
                    day_bucket,
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
                .group_by("day_bucket")
                .order_by("day_bucket")
            )
            return [
                {
                    "date": _bucket_to_date(int(row.day_bucket)),
                    "input_tokens": int(row.input_tokens or 0),
                    "output_tokens": int(row.output_tokens or 0),
                    "total_tokens": int(row.total_tokens or 0),
                    "cost": float(row.cost or 0),
                }
                for row in q.all()
            ]
