"""OpenAPI-only repository tests with synthetic local data."""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_chat.models import AwLangfuseTrace, BcsGroupSession
from agentclaw.community.core.bot_chat.repository import (
    BotChatDbRepository,
    OpenBotChatRepository,
)
from agentclaw.community.plugin_api.models import Base
from agentclaw.community.utils.env_utils import get_current_env


class _LocalDb:
    def __init__(self) -> None:
        self._engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self._engine)
        self._factory = sessionmaker(bind=self._engine)

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


def _write_ocb_trace(
    repository: BotChatDbRepository,
    *,
    trace_id: str,
    user_id: str,
    bot_id: str,
    session_key: str,
) -> None:
    repository.upsert_ocb_trace(
        {
            "trace_id": trace_id,
            "session_id": f"session-{trace_id}",
            "session_key": session_key,
            "user_id": user_id,
            "bot_id": bot_id,
            "name": "OpenAPI synthetic trace",
            "input": "synthetic input",
            "output": "synthetic output",
            "start_time_ms": 1_000,
            "status": "SUCCESS",
            "usage": {},
        }
    )


def test_user_bot_query_is_exact_and_enriches_owned_task_labels() -> None:
    db = _LocalDb()
    writer = BotChatDbRepository(db)
    session_key = "agent:main:open-user-bot"
    _write_ocb_trace(
        writer,
        trace_id="trace-match",
        user_id="user-fixture",
        bot_id="bot-fixture",
        session_key=session_key,
    )
    _write_ocb_trace(
        writer,
        trace_id="trace-other-user",
        user_id="other-user",
        bot_id="bot-fixture",
        session_key="agent:main:other-user",
    )
    _write_ocb_trace(
        writer,
        trace_id="trace-other-bot",
        user_id="user-fixture",
        bot_id="other-bot",
        session_key="agent:main:other-bot",
    )
    writer.upsert_biz_refs(
        {
            "biz_scene": "scene-fixture",
            "biz_task_id": "task-fixture",
            "user_id": "user-fixture",
            "bot_id": "bot-fixture",
            "refs": [{"ref_type": "trace_id", "ref_value": "trace-match"}],
        }
    )
    with db.orm_session() as session:
        session.add(
            BcsGroupSession(
                session_id=session_key,
                group_id="group-fixture",
                session_kind="chat",
                env=get_current_env(),
            )
        )

    result = OpenBotChatRepository(db).list_user_bot_traces(
        user_id="user-fixture",
        bot_id="bot-fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    assert result.total == 1
    assert [row.id for row in result.sessions] == ["trace-match"]
    row = result.sessions[0]
    assert row.session_key == session_key
    assert (row.biz_scene, row.biz_task_id) == (
        "scene-fixture",
        "task-fixture",
    )
    assert row.group_id == "group-fixture"
    assert row.session_kind == "chat"


def test_user_bot_query_falls_back_to_legacy_only_when_otel_is_empty() -> None:
    db = _LocalDb()
    with db.orm_session() as session:
        session.add(
            AwLangfuseTrace(
                trace_id="legacy-match",
                gmt_trace=1_000,
                session_id="agent:main:legacy-match",
                real_session_id="legacy-session",
                user_id="user-fixture",
                bot_id="bot-fixture",
                name="Legacy synthetic trace",
            )
        )

    result = OpenBotChatRepository(db).list_user_bot_traces(
        user_id="user-fixture",
        bot_id="bot-fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    assert result.total == 1
    assert result.sessions[0].id == "legacy-match"
    assert result.sessions[0].session_id == "legacy-session"


def test_task_enrichment_is_isolated_for_same_session_key() -> None:
    db = _LocalDb()
    writer = BotChatDbRepository(db)
    session_key = "agent:main:shared-session"
    _write_ocb_trace(
        writer,
        trace_id="trace-target",
        user_id="target-user",
        bot_id="target-bot",
        session_key=session_key,
    )
    writer.upsert_biz_refs(
        {
            "biz_scene": "wrong-scene",
            "biz_task_id": "wrong-task",
            "user_id": "other-user",
            "bot_id": "target-bot",
            "refs": [{"ref_type": "session_key", "ref_value": session_key}],
        }
    )
    writer.upsert_biz_refs(
        {
            "biz_scene": "target-scene",
            "biz_task_id": "target-task",
            "user_id": "target-user",
            "bot_id": "target-bot",
            "refs": [{"ref_type": "session_key", "ref_value": session_key}],
        }
    )

    result = OpenBotChatRepository(db).list_user_bot_traces(
        user_id="target-user",
        bot_id="target-bot",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    assert result.total == 1
    assert (result.sessions[0].biz_scene, result.sessions[0].biz_task_id) == (
        "target-scene",
        "target-task",
    )


def test_task_enrichment_ignores_unowned_relation() -> None:
    db = _LocalDb()
    writer = BotChatDbRepository(db)
    session_key = "agent:main:unowned-session"
    _write_ocb_trace(
        writer,
        trace_id="trace-target",
        user_id="target-user",
        bot_id="target-bot",
        session_key=session_key,
    )
    writer.upsert_biz_refs(
        {
            "biz_scene": "wrong-scene",
            "biz_task_id": "wrong-task",
            "user_id": "other-user",
            "bot_id": "target-bot",
            "refs": [{"ref_type": "session_key", "ref_value": session_key}],
        }
    )

    result = OpenBotChatRepository(db).list_user_bot_traces(
        user_id="target-user",
        bot_id="target-bot",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    assert result.total == 1
    assert result.sessions[0].biz_scene is None
    assert result.sessions[0].biz_task_id is None
