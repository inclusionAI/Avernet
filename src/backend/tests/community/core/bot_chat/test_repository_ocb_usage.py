from contextlib import contextmanager
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_chat.models import AcOtelLogObservation, AcOtelLogTrace, AwLangfuseTrace
from agentclaw.community.core.bot_chat.repository import BotChatDbRepository
from agentclaw.community.core.bot_chat.repository.product import _decimal_for_column_or_none
from agentclaw.community.plugin_api.models import Base


class _Db:
    def __init__(self):
        self._engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
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


def test_ocb_trace_usage_falls_back_to_observation_usage():
    db = _Db()
    repo = BotChatDbRepository(db)

    repo.upsert_ocb_observation(
        {
            "observation_id": "llm-1",
            "trace_id": "trace-usage",
            "type": "LLM",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
            "total_cost": "0.0003",
        }
    )
    repo.upsert_ocb_trace(
        {
            "trace_id": "trace-usage",
            "user_id": "197444",
            "bot_id": "default",
            "usage": {},
            "total_cost": None,
        }
    )

    with db.orm_session() as session:
        row = session.query(AcOtelLogTrace).filter(AcOtelLogTrace.trace_id == "trace-usage").one()
        assert row.usage_input_tokens == 100
        assert row.usage_output_tokens == 20
        assert row.usage_total_tokens == 120
        assert float(row.total_cost) == 0.0003


def test_ocb_trace_usage_is_populated_when_trace_is_written_after_observations():
    db = _Db()
    repo = BotChatDbRepository(db)

    repo.upsert_ocb_observation(
        {
            "observation_id": "llm-written",
            "trace_id": "trace-written-usage",
            "type": "LLM",
            "model": "Kimi-K2.5",
            "usage": {
                "input_tokens": 10000,
                "output_tokens": 2000,
                "total_tokens": 12000,
            },
            "total_cost": 0.06,
        }
    )
    repo.upsert_ocb_trace(
        {
            "trace_id": "trace-written-usage",
            "user_id": "197444",
            "bot_id": "default",
            "usage": {},
            "total_cost": None,
        }
    )

    with db.orm_session() as session:
        db_row = session.query(AcOtelLogTrace).filter(AcOtelLogTrace.trace_id == "trace-written-usage").one()
        assert db_row.usage_input_tokens == 10000
        assert db_row.usage_output_tokens == 2000
        assert db_row.usage_total_tokens == 12000
        assert float(db_row.total_cost) == 0.06


def test_ocb_trace_empty_usage_overwrites_zero_with_observation_usage():
    db = _Db()
    repo = BotChatDbRepository(db)

    repo.upsert_ocb_trace(
        {
            "trace_id": "trace-zero",
            "user_id": "197444",
            "bot_id": "default",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "total_cost": 0,
        }
    )
    repo.upsert_ocb_observation(
        {
            "observation_id": "llm-zero",
            "trace_id": "trace-zero",
            "type": "LLM",
            "usage": {
                "input_tokens": 132487,
                "output_tokens": 826,
                "total_tokens": 133313,
            },
            "total_cost": 0.0092551,
        }
    )
    repo.upsert_ocb_trace(
        {
            "trace_id": "trace-zero",
            "user_id": "197444",
            "bot_id": "default",
            "usage": {},
            "total_cost": None,
        }
    )

    with db.orm_session() as session:
        db_row = session.query(AcOtelLogTrace).filter(AcOtelLogTrace.trace_id == "trace-zero").one()
        assert db_row.usage_input_tokens == 132487
        assert db_row.usage_output_tokens == 826
        assert db_row.usage_total_tokens == 133313
        assert float(db_row.total_cost) == 0.0092551


def test_upsert_ocb_observation_quantizes_total_cost_to_db_scale():
    db = _Db()
    repo = BotChatDbRepository(db)

    repo.upsert_ocb_observation(
        {
            "observation_id": "llm-cost-scale",
            "trace_id": "trace-cost-scale",
            "type": "LLM",
            "usage": {
                "input_tokens": 18273,
                "output_tokens": 88,
                "total_tokens": 18361,
            },
            "total_cost": 0.001273847263123,
        }
    )

    with db.orm_session() as session:
        row = session.query(AcOtelLogObservation).filter(
            AcOtelLogObservation.observation_id == "llm-cost-scale"
        ).one()
        assert str(row.total_cost) == "0.0012738473"


def test_decimal_normalization_uses_column_scale_and_drops_overflow():
    assert str(_decimal_for_column_or_none(0.001273847263123, AwLangfuseTrace.total_cost)) == "0.001274"
    assert _decimal_for_column_or_none(12345.1, AwLangfuseTrace.total_cost) is None


def test_upsert_ocb_trace_normalizes_user_default_bot_and_preserves_breakdown_metadata():
    db = _Db()
    repo = BotChatDbRepository(db)

    status = repo.upsert_ocb_trace(
        {
            "trace_id": "trace-meta",
            "user_id": "197444",
            "bot_id": "197444_default",
            "engine": "openclaw",
            "collector": "observ-openclaw",
            "metadata": {"attributes": {"existing": "value"}},
            "usage": {
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
            },
            "usage_details": {
                "input": 10,
                "output": 2,
                "total": 12,
            },
            "cost_details": {
                "input": 0.1,
                "output": 0.2,
                "total": 0.3,
            },
            "total_cost": 0.3,
        }
    )

    assert status == "inserted"
    with db.orm_session() as session:
        row = session.query(AcOtelLogTrace).filter(AcOtelLogTrace.trace_id == "trace-meta").one()
        metadata = json.loads(row.metadata_json)
        assert row.bot_id == "default"
        assert metadata["original_bot_id"] == "197444_default"
        assert metadata["attributes"]["existing"] == "value"
        assert metadata["attributes"]["usage_details"]["total"] == 12
        assert metadata["attributes"]["cost_details"]["total"] == 0.3


def test_list_ocb_traces_filters_by_owner_default_bot_and_sessions():
    db = _Db()
    repo = BotChatDbRepository(db)

    repo.upsert_ocb_trace(
        {
            "trace_id": "trace-list",
            "biz_task_id": "biz-task-1",
            "biz_scene": "biz-scene-a",
            "session_id": "real-session",
            "session_key": "conversation-key",
            "user_id": "197444",
            "bot_id": "default",
            "name": "Deploy Plan",
            "input": [{"role": "user", "content": "hello"}],
            "metadata": {"attributes": {}},
            "start_time_ms": 1000,
            "usage": {"total_tokens": 5},
        }
    )
    repo.upsert_ocb_trace(
        {
            "trace_id": "trace-other-user",
            "user_id": "other",
            "bot_id": "default",
            "start_time_ms": 1001,
            "usage": {},
        }
    )

    sessions, total = repo.list_ocb_traces(
        owner_id="197444",
        from_ms=0,
        to_ms=2000,
        page=1,
        limit=20,
        bot_id="default",
        session_id="real-session",
        session_key="conversation-key",
        query="Deploy",
    )

    assert total == 1
    assert len(sessions) == 1
    assert sessions[0].biz_task_id == "biz-task-1"
    assert sessions[0].biz_scene == "biz-scene-a"
    assert sessions[0].session_id == "real-session"
    assert sessions[0].session_key == "conversation-key"

    sessions_by_session_id, total_by_session_id = repo.list_ocb_traces(
        owner_id="197444",
        from_ms=0,
        to_ms=2000,
        page=1,
        limit=20,
        bot_id="default",
        session_id="real-session",
    )
    assert total_by_session_id == 1
    assert sessions_by_session_id[0].id == "trace-list"

    sessions_by_session_key, total_by_session_key = repo.list_ocb_traces(
        owner_id="197444",
        from_ms=0,
        to_ms=2000,
        page=1,
        limit=20,
        bot_id="default",
        session_key="conversation-key",
    )
    assert total_by_session_key == 1
    assert sessions_by_session_key[0].id == "trace-list"
    session = sessions[0]
    assert session.id == "trace-list"
    assert session.input == "hello"
    assert session.session_id == "real-session"
    assert session.session_key == "conversation-key"
    assert session.total_tokens == 5
    assert session.metadata.attributes["gen_ai.session.id"] == "real-session"
    assert session.metadata.attributes["gen_ai.conversation.id"] == "conversation-key"
    detail = repo._row_to_detail(repo.get_ocb_trace("trace-list"), [])
    assert detail.biz_task_id == "biz-task-1"
    assert detail.biz_scene == "biz-scene-a"


def test_list_ocb_observations_builds_tree_and_parses_payloads():
    db = _Db()
    repo = BotChatDbRepository(db)

    repo.upsert_ocb_observation(
        {
            "observation_id": "root",
            "trace_id": "trace-tree",
            "biz_task_id": "biz-task-tree",
            "biz_scene": "biz-scene-tree",
            "type": "CHAT",
            "name": "Root",
            "input": [{"role": "user", "content": "hello"}],
            "output": {"role": "assistant", "content": "hi"},
            "metadata": {
                "attributes": {
                    "gen_ai.response.model": "model-a",
                }
            },
            "start_time_ms": 1,
        }
    )
    repo.upsert_ocb_observation(
        {
            "observation_id": "child",
            "trace_id": "trace-tree",
            "parent_observation_id": "root",
            "type": "LLM",
            "name": "Child",
            "model": "model-b",
            "usage": {"total_tokens": 9},
            "total_cost": 0.04,
            "start_time_ms": 2,
        }
    )

    observations = repo.list_ocb_observations("trace-tree")

    assert len(observations) == 1
    root = observations[0]
    assert root.id == "root"
    assert root.biz_task_id == "biz-task-tree"
    assert root.biz_scene == "biz-scene-tree"
    assert root.input == [{"role": "user", "content": "hello"}]
    assert root.output == {"role": "assistant", "content": "hi"}
    assert root.model_name == "model-a"
    assert len(root.children) == 1
    assert root.children[0].id == "child"
    assert root.children[0].model_name == "model-b"
    assert root.children[0].total_tokens == 9
    assert root.children[0].total_cost == 0.04


def test_ocb_upsert_validation_errors():
    db = _Db()
    repo = BotChatDbRepository(db)

    with pytest.raises(ValueError, match="trace_id is required"):
        repo.upsert_ocb_trace({})

    with pytest.raises(ValueError, match="observation_id is required"):
        repo.upsert_ocb_observation({"trace_id": "trace"})

    with pytest.raises(ValueError, match="trace_id is required"):
        repo.upsert_ocb_observation({"observation_id": "obs"})


def test_upsert_ocb_observation_updates_existing_row():
    db = _Db()
    repo = BotChatDbRepository(db)

    assert repo.upsert_ocb_observation(
        {
            "observation_id": "obs-update",
            "trace_id": "trace-update",
            "name": "Before",
            "usage_details": {"total": 1},
            "cost_details": {"total": 0.01},
        }
    ) == "inserted"
    assert repo.upsert_ocb_observation(
        {
            "observation_id": "obs-update",
            "trace_id": "trace-update",
            "name": "After",
            "usage": {"input_tokens": 3},
            "usage_details": {"total": 2},
            "cost_details": {"total": 0.02},
        }
    ) == "updated"

    with db.orm_session() as session:
        row = session.query(AcOtelLogObservation).filter(AcOtelLogObservation.observation_id == "obs-update").one()
        metadata = json.loads(row.metadata_json)
        assert row.name == "After"
        assert row.usage_input_tokens == 3
        assert metadata["attributes"]["usage_details"]["total"] == 2
        assert metadata["attributes"]["cost_details"]["total"] == 0.02
