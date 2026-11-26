from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_chat.models import AcOtelLogTrace
from agentclaw.community.core.bot_chat.token_usage_service import (
    TokenUsageService,
    _bucket_to_date,
)
from agentclaw.community.plugin_api.models import Base


_MS_PER_DAY = 86_400_000
_BASE_MS = 1731859200000  # 2024-11-17 16:00:00 UTC

_counter: int = 0


def _uniq() -> str:
    global _counter
    _counter += 1
    return str(_counter)


class _Db:
    """In-memory SQLite mimicking ``DatabasePlugin.orm_session()``."""

    def __init__(self):
        self._engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self._engine)
        self._factory = sessionmaker(bind=self._engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _add(
    db: _Db,
    bot_id: str,
    offset_day: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    cost: float = 0.0,
) -> None:
    with db.orm_session() as session:
        session.add(
            AcOtelLogTrace(
                trace_id=f"tr-{bot_id}-d{offset_day}-{_uniq()}",
                bot_id=bot_id,
                start_time_ms=_BASE_MS + offset_day * _MS_PER_DAY,
                usage_input_tokens=input_tokens,
                usage_output_tokens=output_tokens,
                usage_total_tokens=total_tokens,
                total_cost=cost,
            ),
        )


def test_batch_summary_empty():
    svc = TokenUsageService(_Db())
    assert svc.bots_token_summary(from_ms=_BASE_MS, to_ms=_BASE_MS + _MS_PER_DAY) == []


def test_batch_summary_one_bot():
    db = _Db()
    _add(db, "bot-a", input_tokens=100, output_tokens=50, total_tokens=150, cost=0.001)
    rows = TokenUsageService(db).bots_token_summary(
        from_ms=_BASE_MS - 1, to_ms=_BASE_MS + _MS_PER_DAY,
    )
    assert len(rows) == 1
    assert rows[0]["bot_id"] == "bot-a"
    assert rows[0]["total_input_tokens"] == 100
    assert rows[0]["total_output_tokens"] == 50
    assert rows[0]["total_tokens"] == 150
    assert rows[0]["total_cost"] == 0.001


def test_batch_summary_aggregates_multiple_traces():
    db = _Db()
    _add(db, "bot-a", offset_day=0, total_tokens=100)
    _add(db, "bot-a", offset_day=1, total_tokens=200)
    rows = TokenUsageService(db).bots_token_summary(
        from_ms=_BASE_MS - 1, to_ms=_BASE_MS + 2 * _MS_PER_DAY,
    )
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 300


def test_batch_summary_multiple_bots():
    db = _Db()
    _add(db, "bot-a", total_tokens=10)
    _add(db, "bot-b", total_tokens=20)
    rows = TokenUsageService(db).bots_token_summary(
        from_ms=_BASE_MS - 1, to_ms=_BASE_MS + _MS_PER_DAY,
    )
    assert {row["bot_id"] for row in rows} == {"bot-a", "bot-b"}


def test_batch_summary_filter_by_bot_ids():
    db = _Db()
    _add(db, "bot-a", total_tokens=1)
    _add(db, "bot-b", total_tokens=2)
    _add(db, "bot-c", total_tokens=3)
    rows = TokenUsageService(db).bots_token_summary(
        from_ms=_BASE_MS - 1,
        to_ms=_BASE_MS + _MS_PER_DAY,
        bot_ids=["bot-a", "bot-c"],
    )
    assert {row["bot_id"] for row in rows} == {"bot-a", "bot-c"}


def test_batch_summary_respects_time_range():
    db = _Db()
    _add(db, "inside", offset_day=0, total_tokens=10)
    _add(db, "outside", offset_day=-10, total_tokens=999)
    rows = TokenUsageService(db).bots_token_summary(
        from_ms=_BASE_MS - _MS_PER_DAY, to_ms=_BASE_MS + _MS_PER_DAY,
    )
    ids = {row["bot_id"] for row in rows}
    assert "inside" in ids
    assert "outside" not in ids


def test_daily_trend_empty():
    svc = TokenUsageService(_Db())
    assert svc.bot_token_daily("bot-x", from_ms=_BASE_MS, to_ms=_BASE_MS + _MS_PER_DAY) == []


def test_daily_trend_groups_by_day():
    db = _Db()
    _add(db, "bot-a", offset_day=0, total_tokens=10)
    _add(db, "bot-a", offset_day=0, total_tokens=20)
    _add(db, "bot-a", offset_day=1, total_tokens=5)
    rows = TokenUsageService(db).bot_token_daily(
        "bot-a", from_ms=_BASE_MS - 1, to_ms=_BASE_MS + 2 * _MS_PER_DAY,
    )
    assert len(rows) == 2
    assert rows[0]["total_tokens"] == 30
    assert rows[1]["total_tokens"] == 5
    assert rows[0]["date"] != rows[1]["date"]


def test_daily_trend_filters_by_bot():
    db = _Db()
    _add(db, "bot-a", total_tokens=50)
    _add(db, "bot-b", total_tokens=100)
    rows = TokenUsageService(db).bot_token_daily(
        "bot-a", from_ms=_BASE_MS - 1, to_ms=_BASE_MS + _MS_PER_DAY,
    )
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 50


def test_daily_trend_sorted_ascending():
    db = _Db()
    for day in (2, 0, 1):
        _add(db, "bot-a", offset_day=day, total_tokens=day)
    rows = TokenUsageService(db).bot_token_daily(
        "bot-a", from_ms=_BASE_MS - 1, to_ms=_BASE_MS + 3 * _MS_PER_DAY,
    )
    dates = [row["date"] for row in rows]
    assert dates == sorted(dates)


def test_bucket_to_date():
    day = _BASE_MS // _MS_PER_DAY
    assert _bucket_to_date(day) == "2024-11-17"
