"""Token usage aggregation service.

Read-only queries against ``ac_otel_log_trace`` returning aggregated token
consumption per Bot and per day.  No writes, no side effects.

The aggregation runs in SQL and works on both MySQL (production) and SQLite
(singlebox / tests) by grouping on integer epoch-day buckets instead of
vendor-specific date functions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.bot_chat.models import AcOtelLogTrace
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()

_MS_PER_DAY = 86_400_000


def _bucket_to_date(day_bucket: int) -> str:
    """Convert an epoch-day bucket into an ISO date string (UTC)."""
    return datetime.fromtimestamp(
        day_bucket * _MS_PER_DAY / 1000, tz=timezone.utc,
    ).strftime("%Y-%m-%d")


class TokenUsageService:
    """Aggregate token consumption from OTEL trace records."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def bots_token_summary(
        self,
        *,
        bot_ids: Sequence[str] | None = None,
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> list[dict]:
        """Return per-Bot token and cost totals for ``[from_ms, to_ms)``.

        When *bot_ids* is omitted, every Bot with a trace in the window is
        included.  Rows are ordered by total token usage, descending.
        """
        with self._db.orm_session() as session:
            columns = [
                AcOtelLogTrace.bot_id,
                func.sum(AcOtelLogTrace.usage_input_tokens).label(
                    "total_input_tokens",
                ),
                func.sum(AcOtelLogTrace.usage_output_tokens).label(
                    "total_output_tokens",
                ),
                func.sum(AcOtelLogTrace.usage_total_tokens).label("total_tokens"),
                func.sum(AcOtelLogTrace.total_cost).label("total_cost"),
            ]
            query = session.query(*columns).filter(
                AcOtelLogTrace.bot_id.isnot(None),
                AcOtelLogTrace.bot_id != "",
            )
            if from_ms is not None:
                query = query.filter(AcOtelLogTrace.start_time_ms >= from_ms)
            if to_ms is not None:
                query = query.filter(AcOtelLogTrace.start_time_ms < to_ms)
            if bot_ids:
                query = query.filter(AcOtelLogTrace.bot_id.in_(list(bot_ids)))

            query = query.group_by(AcOtelLogTrace.bot_id).order_by(
                func.sum(AcOtelLogTrace.usage_total_tokens).desc(),
            )
            rows = query.all()

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

    def bot_token_daily(
        self,
        bot_id: str,
        *,
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> list[dict]:
        """Return per-day token and cost totals for one Bot.

        Days are bucketed with integer division on epoch milliseconds, which
        keeps the expression portable across MySQL and SQLite.
        """
        day_bucket = func.floor(
            AcOtelLogTrace.start_time_ms / _MS_PER_DAY,
        ).label("day_bucket")

        with self._db.orm_session() as session:
            query = session.query(
                day_bucket,
                func.sum(AcOtelLogTrace.usage_input_tokens).label("input_tokens"),
                func.sum(AcOtelLogTrace.usage_output_tokens).label("output_tokens"),
                func.sum(AcOtelLogTrace.usage_total_tokens).label("total_tokens"),
                func.sum(AcOtelLogTrace.total_cost).label("cost"),
            ).filter(AcOtelLogTrace.bot_id == bot_id)
            if from_ms is not None:
                query = query.filter(AcOtelLogTrace.start_time_ms >= from_ms)
            if to_ms is not None:
                query = query.filter(AcOtelLogTrace.start_time_ms < to_ms)

            rows = (
                query.group_by("day_bucket")
                .order_by("day_bucket")
                .all()
            )

        return [
            {
                "date": _bucket_to_date(int(row.day_bucket)),
                "input_tokens": int(row.input_tokens or 0),
                "output_tokens": int(row.output_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "cost": float(row.cost or 0),
            }
            for row in rows
        ]
