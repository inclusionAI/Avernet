"""OpenAPI-only repository tests with synthetic local data."""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_chat.models import (
    AcOtelLogObservation,
    AwLangfuseObservation,
    AwLangfuseTrace,
    BcsGroupSession,
)
from agentclaw.community.core.repository.implementations.chat.db import BotChatDbRepository
from agentclaw.community.core.repository.implementations.chat.open import OpenBotChatRepository
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
    session_id: str | None = None,
    start_time_ms: int = 1_000,
    biz_scene: str | None = None,
    biz_task_id: str | None = None,
) -> None:
    repository.upsert_ocb_trace(
        {
            "trace_id": trace_id,
            "session_id": session_id or f"session-{trace_id}",
            "session_key": session_key,
            "user_id": user_id,
            "bot_id": bot_id,
            "name": "OpenAPI synthetic trace",
            "input": "synthetic input",
            "output": "synthetic output",
            "start_time_ms": start_time_ms,
            "biz_scene": biz_scene,
            "biz_task_id": biz_task_id,
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


def test_session_scope_prefers_otel_and_paginates_stably() -> None:
    db = _LocalDb()
    writer = BotChatDbRepository(db)
    for trace_id in ("trace-a", "trace-b", "trace-c"):
        _write_ocb_trace(
            writer,
            trace_id=trace_id,
            user_id="user-fixture",
            bot_id="bot-fixture",
            session_key="shared-session",
            start_time_ms=1_000,
        )
    with db.orm_session() as session:
        session.add(
            AwLangfuseTrace(
                trace_id="legacy-must-not-mix",
                gmt_trace=2_000,
                session_id="shared-session",
                user_id="user-fixture",
                bot_id="bot-fixture",
            )
        )

    result = OpenBotChatRepository(db).list_scope_traces(
        session_key="shared-session",
        from_ms=0,
        to_ms=3_000,
        page=2,
        limit=2,
    )

    assert result.total == 3
    assert result.has_more is False
    assert [row.id for row in result.sessions] == ["trace-a"]


def test_session_scope_falls_back_to_legacy() -> None:
    db = _LocalDb()
    with db.orm_session() as session:
        session.add(
            AwLangfuseTrace(
                trace_id="legacy-session-trace",
                gmt_trace=1_000,
                session_id="legacy-session-key",
                real_session_id="legacy-session-id",
                user_id="legacy-user",
                bot_id="legacy-bot",
            )
        )

    result = OpenBotChatRepository(db).list_scope_traces(
        session_key="legacy-session-key",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    assert result.total == 1
    assert result.sessions[0].id == "legacy-session-trace"
    assert result.sessions[0].session_id == "legacy-session-id"


def test_task_scope_merges_direct_and_owned_relation_matches() -> None:
    db = _LocalDb()
    writer = BotChatDbRepository(db)
    _write_ocb_trace(
        writer,
        trace_id="direct-task-trace",
        user_id="direct-user",
        bot_id="direct-bot",
        session_key="direct-session",
        biz_scene="scene-fixture",
        biz_task_id="task-fixture",
    )
    _write_ocb_trace(
        writer,
        trace_id="relation-task-trace",
        user_id="relation-user",
        bot_id="relation-bot",
        session_key="relation-session",
    )
    writer.upsert_biz_refs(
        {
            "biz_scene": "scene-fixture",
            "biz_task_id": "task-fixture",
            "user_id": "relation-user",
            "bot_id": "relation-bot",
            "refs": [
                {"ref_type": "trace_id", "ref_value": "relation-task-trace"}
            ],
        }
    )

    result = OpenBotChatRepository(db).list_scope_traces(
        biz_scene="scene-fixture",
        biz_task_id="task-fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    assert result.total == 2
    by_id = {row.id: row for row in result.sessions}
    assert by_id["direct-task-trace"].match_sources == ["direct"]
    assert by_id["relation-task-trace"].match_sources == ["biz_ref"]
    assert by_id["relation-task-trace"].biz_task_id == "task-fixture"


def test_task_scope_falls_back_to_legacy_relation() -> None:
    db = _LocalDb()
    writer = BotChatDbRepository(db)
    with db.orm_session() as session:
        session.add(
            AwLangfuseTrace(
                trace_id="legacy-task-trace",
                gmt_trace=1_000,
                session_id="legacy-task-key",
                real_session_id="legacy-task-session",
                user_id="legacy-user",
                bot_id="legacy-bot",
            )
        )
    writer.upsert_biz_refs(
        {
            "biz_scene": "legacy-scene",
            "biz_task_id": "legacy-task",
            "user_id": "legacy-user",
            "bot_id": "legacy-bot",
            "refs": [
                {"ref_type": "session_key", "ref_value": "legacy-task-key"}
            ],
        }
    )

    result = OpenBotChatRepository(db).list_scope_traces(
        biz_scene="legacy-scene",
        biz_task_id="legacy-task",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    assert result.total == 1
    assert result.sessions[0].id == "legacy-task-trace"
    assert result.sessions[0].biz_task_id == "legacy-task"


def test_group_scope_normalizes_current_and_legacy_session_keys() -> None:
    db = _LocalDb()
    writer = BotChatDbRepository(db)
    bcs_session_id = "group-session:abcdef12"
    _write_ocb_trace(
        writer,
        trace_id="group-otel-trace",
        user_id="group-user",
        bot_id="group-bot",
        session_key=f"agent:main:bcs:group:{bcs_session_id}",
    )
    with db.orm_session() as session:
        session.add(
            BcsGroupSession(
                session_id=bcs_session_id,
                group_id="group-fixture",
                session_kind="chat",
                env=get_current_env(),
            )
        )
        session.add(
            AwLangfuseTrace(
                trace_id="legacy-must-not-mix",
                gmt_trace=1_000,
                session_id=f"agent:main:{bcs_session_id}",
                user_id="group-user",
                bot_id="group-bot",
            )
        )

    result = OpenBotChatRepository(db).list_scope_traces(
        group_id="group-fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    assert result.total == 1
    assert result.sessions[0].id == "group-otel-trace"
    assert result.sessions[0].group_id == "group-fixture"


def test_group_scope_falls_back_to_legacy_key() -> None:
    db = _LocalDb()
    bcs_session_id = "legacy-group:abcdef12"
    with db.orm_session() as session:
        session.add_all(
            [
                BcsGroupSession(
                    session_id=bcs_session_id,
                    group_id="legacy-group",
                    session_kind="chat",
                    env=get_current_env(),
                ),
                AwLangfuseTrace(
                    trace_id="legacy-group-trace",
                    gmt_trace=1_000,
                    session_id=f"agent:main:{bcs_session_id}",
                    user_id="legacy-user",
                    bot_id="legacy-bot",
                ),
            ]
        )

    result = OpenBotChatRepository(db).list_scope_traces(
        group_id="legacy-group",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    assert result.total == 1
    assert result.sessions[0].id == "legacy-group-trace"
    assert result.sessions[0].group_id == "legacy-group"


def test_scope_enrichment_uses_each_trace_identity() -> None:
    db = _LocalDb()
    writer = BotChatDbRepository(db)
    shared_key = "shared-open-session"
    for trace_id, user_id in (("trace-user-a", "user-a"), ("trace-user-b", "user-b")):
        _write_ocb_trace(
            writer,
            trace_id=trace_id,
            user_id=user_id,
            bot_id="shared-bot",
            session_key=shared_key,
        )
        writer.upsert_biz_refs(
            {
                "biz_scene": f"scene-{user_id}",
                "biz_task_id": f"task-{user_id}",
                "user_id": user_id,
                "bot_id": "shared-bot",
                "refs": [{"ref_type": "trace_id", "ref_value": trace_id}],
            }
        )

    result = OpenBotChatRepository(db).list_scope_traces(
        session_key=shared_key,
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    labels = {row.user_id: row.biz_task_id for row in result.sessions}
    assert labels == {"user-a": "task-user-a", "user-b": "task-user-b"}


def test_detail_prefers_otel_and_builds_observation_tree() -> None:
    db = _LocalDb()
    writer = BotChatDbRepository(db)
    _write_ocb_trace(
        writer,
        trace_id="shared-detail-trace",
        user_id="otel-user",
        bot_id="otel-bot",
        session_key="otel-detail-session",
    )
    with db.orm_session() as session:
        session.add_all(
            [
                AcOtelLogObservation(
                    observation_id="root-observation",
                    trace_id="shared-detail-trace",
                    type="CHAT",
                    name="root",
                    start_time_ms=1_000,
                ),
                AcOtelLogObservation(
                    observation_id="child-observation",
                    trace_id="shared-detail-trace",
                    parent_observation_id="root-observation",
                    type="LLM",
                    name="child",
                    start_time_ms=1_001,
                ),
                AwLangfuseTrace(
                    trace_id="shared-detail-trace",
                    gmt_trace=2_000,
                    session_id="legacy-must-not-win",
                    user_id="legacy-user",
                    bot_id="legacy-bot",
                ),
            ]
        )

    result = OpenBotChatRepository(db).get_trace_detail("shared-detail-trace")

    assert result.user_id == "otel-user"
    assert [item.id for item in result.observations] == ["root-observation"]
    assert [item.id for item in result.observations[0].children] == [
        "child-observation"
    ]


def test_detail_falls_back_to_legacy_observation_tree() -> None:
    db = _LocalDb()
    with db.orm_session() as session:
        session.add_all(
            [
                AwLangfuseTrace(
                    trace_id="legacy-detail-trace",
                    gmt_trace=1_000,
                    session_id="legacy-detail-session",
                    user_id="legacy-user",
                    bot_id="legacy-bot",
                ),
                AwLangfuseObservation(
                    observation_id="legacy-root",
                    trace_id="legacy-detail-trace",
                    type="SPAN",
                    name="legacy root",
                    start_time=1_000,
                ),
                AwLangfuseObservation(
                    observation_id="legacy-child",
                    trace_id="legacy-detail-trace",
                    parent_observation_id="legacy-root",
                    type="GENERATION",
                    name="legacy child",
                    start_time=1_001,
                ),
            ]
        )

    result = OpenBotChatRepository(db).get_trace_detail("legacy-detail-trace")

    assert result.session_key == "legacy-detail-session"
    assert result.observations[0].id == "legacy-root"
    assert result.observations[0].children[0].id == "legacy-child"
