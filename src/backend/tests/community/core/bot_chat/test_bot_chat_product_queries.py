"""Product-query coverage using only local, synthetic records."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_chat.models import (
    AcOtelLogTrace,
    AwLangfuseTrace,
    BcsGroupSession,
)
from agentclaw.community.core.bot_chat.repository import BotChatDbRepository
from agentclaw.community.core.bot_chat.query_support import QueryScope
from agentclaw.community.core.bot_chat.service import (
    BotChatService,
    _apply_client_side_filters,
    _map_observation,
    _map_trace_to_session,
)
from agentclaw.community.core.bot_chat.schemas import ConversationSession
from agentclaw.community.di.config import BotChatConfig
from agentclaw.community.plugin_api.models import Base, BotModel
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


def _write_trace(
    repo: BotChatDbRepository,
    *,
    trace_id: str,
    session_id: str,
    session_key: str,
    biz_scene: str | None = None,
    biz_task_id: str | None = None,
    output: str = "synthetic output",
    user_id: str = "user_fixture",
) -> None:
    repo.upsert_ocb_trace(
        {
            "trace_id": trace_id,
            "session_id": session_id,
            "session_key": session_key,
            "biz_scene": biz_scene,
            "biz_task_id": biz_task_id,
            "user_id": user_id,
            "bot_id": "bot_fixture",
            "name": "Synthetic trace",
            "input": "synthetic input",
            "output": output,
            "start_time_ms": 1_000,
            "usage": {},
        }
    )


def test_task_query_merges_direct_and_relation_matches_without_duplicates():
    db = _LocalDb()
    repo = BotChatDbRepository(db)
    _write_trace(
        repo,
        trace_id="trace_direct",
        session_id="session_direct",
        session_key="agent:main:session_direct",
        biz_scene="scene_fixture",
        biz_task_id="task_fixture",
    )
    _write_trace(
        repo,
        trace_id="trace_relation",
        session_id="session_relation",
        session_key="agent:main:session_relation",
    )
    repo.upsert_biz_refs(
        {
            "biz_scene": "scene_fixture",
            "biz_task_id": "task_fixture",
            "refs": [
                {"ref_type": "trace_id", "ref_value": "trace_relation"},
                {"ref_type": "trace_id", "ref_value": "trace_direct"},
            ],
        }
    )

    rows, total = repo.list_ocb_traces(
        owner_id="user_fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        biz_scene="scene_fixture",
        biz_task_id="task_fixture",
    )

    assert total == 2
    assert {row.id for row in rows} == {"trace_direct", "trace_relation"}
    sources = {row.id: row.match_sources for row in rows}
    assert sources["trace_direct"] == ["direct", "biz_ref"]
    assert sources["trace_relation"] == ["biz_ref"]

    by_task, task_total = repo.list_ocb_traces(
        owner_id="user_fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        biz_task_id="task_fixture",
    )
    by_scene, scene_total = repo.list_ocb_traces(
        owner_id="user_fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        biz_scene="scene_fixture",
    )
    contains, contains_total = repo.list_ocb_traces(
        owner_id="user_fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        biz_scene="scene_fix",
        biz_task_id="task_fix",
        match_mode="contains",
    )

    assert task_total == scene_total == contains_total == 2
    assert {row.id for row in by_task} == {"trace_direct", "trace_relation"}
    assert {row.id for row in by_scene} == {"trace_direct", "trace_relation"}
    assert {row.id for row in contains} == {"trace_direct", "trace_relation"}


def test_task_relations_are_isolated_by_user_when_identity_is_present():
    db = _LocalDb()
    repo = BotChatDbRepository(db)
    _write_trace(
        repo,
        trace_id="trace_current_user",
        session_id="session_current_user",
        session_key="agent:main:session_current_user",
    )
    repo.upsert_biz_refs(
        {
            "biz_scene": "scene_fixture",
            "biz_task_id": "task_fixture",
            "user_id": "different_user_fixture",
            "refs": [
                {"ref_type": "trace_id", "ref_value": "trace_current_user"},
            ],
        }
    )

    rows, total = repo.list_ocb_traces(
        owner_id="user_fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        biz_scene="scene_fixture",
        biz_task_id="task_fixture",
    )

    assert rows == []
    assert total == 0


def test_open_task_query_ignores_trace_and_relation_owner():
    db = _LocalDb()
    repo = BotChatDbRepository(db)
    _write_trace(
        repo,
        trace_id="trace_other_owner",
        session_id="session_other_owner",
        session_key="agent:main:session_other_owner",
        user_id="other_owner_fixture",
    )
    repo.upsert_biz_refs(
        {
            "biz_scene": "scene_fixture",
            "biz_task_id": "task_fixture",
            "user_id": "other_owner_fixture",
            "bot_id": "other_bot_fixture",
            "refs": [
                {"ref_type": "trace_id", "ref_value": "trace_other_owner"},
            ],
        }
    )

    rows, total = repo.list_ocb_traces(
        owner_id=None,
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        biz_scene="scene_fixture",
        biz_task_id="task_fixture",
        query_scope=QueryScope.OPEN,
    )

    assert total == 1
    assert [row.id for row in rows] == ["trace_other_owner"]


def test_owner_scope_remains_the_default_for_session_queries():
    db = _LocalDb()
    repo = BotChatDbRepository(db)
    _write_trace(
        repo,
        trace_id="trace_owned",
        session_id="shared_session",
        session_key="agent:main:shared_session",
    )
    _write_trace(
        repo,
        trace_id="trace_other",
        session_id="shared_session",
        session_key="agent:main:shared_session",
        user_id="other_owner_fixture",
    )

    owned, owned_total = repo.list_ocb_traces(
        owner_id="user_fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        session_key="agent:main:shared_session",
    )
    opened, open_total = repo.list_ocb_traces(
        owner_id=None,
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        session_key="agent:main:shared_session",
        query_scope=QueryScope.OPEN,
    )

    assert owned_total == 1
    assert [row.id for row in owned] == ["trace_owned"]
    assert open_total == 2
    assert {row.id for row in opened} == {"trace_owned", "trace_other"}


def test_group_query_normalizes_session_key_and_returns_optional_labels():
    db = _LocalDb()
    repo = BotChatDbRepository(db)
    session_fragment = "group_fixture:session_fixture"
    _write_trace(
        repo,
        trace_id="trace_group",
        session_id="session_fixture",
        session_key=f"agent:main:{session_fragment}",
        output="result available only in output",
    )
    with db.orm_session() as session:
        session.add(
            BcsGroupSession(
                session_id=session_fragment,
                group_id="group_fixture",
                env=get_current_env(),
                session_kind="chat",
            )
        )
        session.add(
            BotModel(
                bot_id="bot_fixture",
                bot_name="Fixture Bot",
                entity_id="user_fixture",
                entity_type="staff",
                creator_id="user_fixture",
                owner_id="user_fixture",
                env=get_current_env(),
            )
        )

    rows, total = repo.list_ocb_traces(
        owner_id="user_fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        group_id="group_fixture",
        query="available only",
        include_output_match=True,
    )

    assert total == 1
    assert rows[0].group_id == "group_fixture"
    assert rows[0].session_kind == "chat"
    assert rows[0].bot_id == "bot_fixture"
    assert rows[0].bot_name == "Fixture Bot"
    assert rows[0].output_preview == "result available only in output"

    detail_row = repo.get_ocb_trace("trace_group")
    assert detail_row.group_id == "group_fixture"
    assert detail_row.session_kind == "chat"
    assert detail_row.bot_name == "Fixture Bot"


def test_group_query_supports_multiple_sessions_and_existing_agent_prefix():
    db = _LocalDb()
    repo = BotChatDbRepository(db)
    fragment = "group_fixture:session_one"
    prefixed = "agent:custom:group_fixture:session_two"
    _write_trace(
        repo,
        trace_id="trace_group_one",
        session_id="session_one",
        session_key=f"agent:main:{fragment}",
    )
    _write_trace(
        repo,
        trace_id="trace_group_two",
        session_id="session_two",
        session_key=prefixed,
        user_id="other_owner_fixture",
    )
    with db.orm_session() as session:
        session.add_all(
            [
                BcsGroupSession(
                    session_id=fragment,
                    group_id="group_fixture",
                    env=get_current_env(),
                    session_kind="chat",
                ),
                BcsGroupSession(
                    session_id=prefixed,
                    group_id="group_fixture",
                    env=get_current_env(),
                    session_kind="service_invocation",
                ),
            ]
        )

    rows, total = repo.list_ocb_traces(
        owner_id=None,
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        group_id="group_fixture",
        query_scope=QueryScope.OPEN,
    )

    assert total == 2
    assert {row.id for row in rows} == {"trace_group_one", "trace_group_two"}
    assert {row.session_kind for row in rows} == {"chat", "service_invocation"}


def test_group_query_supports_legacy_trace_storage():
    db = _LocalDb()
    repo = BotChatDbRepository(db)
    fragment = "group_fixture:legacy_session"
    full_session_key = f"agent:main:{fragment}"
    with db.orm_session() as session:
        session.add(
            BcsGroupSession(
                session_id=fragment,
                group_id="group_fixture",
                env=get_current_env(),
                session_kind="chat",
            )
        )
        session.add(
            AwLangfuseTrace(
                trace_id="trace_legacy_fixture",
                gmt_trace=1_000,
                session_id=full_session_key,
                real_session_id="legacy_session_fixture",
                user_id="other_owner_fixture",
                bot_id="default",
                name="Synthetic legacy trace",
            )
        )

    rows, total = repo.list_traces(
        owner_id=None,
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
        group_id="group_fixture",
        query_scope=QueryScope.OPEN,
    )

    assert total == 1
    assert rows[0].id == "trace_legacy_fixture"
    assert rows[0].group_id == "group_fixture"


def test_bot_id_falls_back_to_metadata_and_missing_bot_name_stays_null():
    db = _LocalDb()
    repo = BotChatDbRepository(db)
    with db.orm_session() as session:
        session.add(
            AcOtelLogTrace(
                trace_id="trace_metadata_bot",
                user_id="user_fixture",
                bot_id=None,
                name="Synthetic trace",
                metadata_json='{"attributes":{"identity.bot_id":"deleted_bot_fixture"}}',
                start_time_ms=1_000,
            )
        )
        session.add(
            BotModel(
                bot_id="deleted_bot_fixture",
                bot_name="Deleted Fixture Bot",
                entity_id="user_fixture",
                entity_type="staff",
                creator_id="user_fixture",
                owner_id="user_fixture",
                env=get_current_env(),
                is_delete=1,
            )
        )

    rows, total = repo.list_ocb_traces(
        owner_id="user_fixture",
        from_ms=0,
        to_ms=2_000,
        page=1,
        limit=20,
    )

    assert total == 1
    assert rows[0].bot_id == "deleted_bot_fixture"
    assert rows[0].bot_name is None


@pytest.mark.asyncio
async def test_contains_query_accepts_90_days_and_rejects_longer_ranges():
    service = BotChatService(
        MagicMock(),
        BotChatConfig(
            langfuse_base_url="",
            langfuse_public_key="",
            langfuse_secret_key="",
        ),
    )
    service._db_repo = MagicMock()
    service._db_repo.list_ocb_traces.return_value = ([], 0)
    service._db_repo.list_traces.return_value = ([], 0)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await service.list_sessions(
        owner_id="user_fixture",
        from_date=start,
        to_date=start + timedelta(days=90),
        match_mode="contains",
        query="x",
    )

    with pytest.raises(ValueError, match="maximum time range of 90 days"):
        await service.list_sessions(
            owner_id="user_fixture",
            from_date=start,
            to_date=start + timedelta(days=90, seconds=1),
            match_mode="contains",
            query="x",
        )


@pytest.mark.asyncio
async def test_unbounded_time_scope_requires_exact_identifier():
    service = BotChatService(
        MagicMock(),
        BotChatConfig(
            langfuse_base_url="",
            langfuse_public_key="",
            langfuse_secret_key="",
        ),
    )

    with pytest.raises(ValueError, match="requires match_mode=exact"):
        await service.list_sessions(
            owner_id="user_fixture",
            time_scope="all",
        )


def test_observation_keeps_raw_metadata_for_detail_rendering():
    observation = _map_observation(
        {
            "id": "observation_fixture",
            "type": "SPAN",
            "metadata": {
                "attributes": {"fixture.attribute": "fixture-value"},
                "custom": {"safe": True},
            },
        }
    )

    assert observation.metadata == {
        "attributes": {"fixture.attribute": "fixture-value"},
        "custom": {"safe": True},
    }


def test_langfuse_output_filter_uses_full_output_but_does_not_serialize_it():
    full_output = ("a" * 600) + "needle_after_preview"
    session = _map_trace_to_session(
        {
            "id": "trace_fixture",
            "timestamp": "2026-01-01T00:00:00Z",
            "output": full_output,
        }
    )

    matched = _apply_client_side_filters(
        [session],
        bot_id=None,
        trace_id=None,
        session_id=None,
        session_key=None,
        query="needle_after_preview",
        include_output_match=True,
    )

    assert matched == [session]
    assert len(session.output_preview or "") == 500
    assert "search_output" not in session.model_dump()


@pytest.mark.asyncio
async def test_langfuse_filter_scans_later_pages_before_paginating():
    service = BotChatService(
        MagicMock(),
        BotChatConfig(
            langfuse_base_url="https://langfuse.example.com",
            langfuse_public_key="fixture-public",
            langfuse_secret_key="fixture-secret",
        ),
    )
    service._fetch_traces_from_langfuse = AsyncMock(
        side_effect=[
            (
                [
                    {
                        "id": "trace_page_one",
                        "timestamp": "2026-01-02T00:00:00Z",
                        "output": "not a match",
                    }
                ],
                2,
            ),
            (
                [
                    {
                        "id": "trace_page_two",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "output": ("x" * 600) + "later_page_needle",
                    }
                ],
                2,
            ),
        ]
    )

    result = await service._list_sessions_langfuse(
        owner_id="user_fixture",
        from_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        to_date=datetime(2026, 1, 3, tzinfo=timezone.utc),
        page=1,
        limit=20,
        bot_id=None,
        trace_id=None,
        session_id=None,
        session_key=None,
        query="later_page_needle",
        match_mode="exact",
        include_output_match=True,
    )

    assert result.total == 1
    assert [row.id for row in result.sessions] == ["trace_page_two"]
    assert service._fetch_traces_from_langfuse.await_count == 2


@pytest.mark.asyncio
async def test_time_scope_all_accepts_old_exact_trace_and_naive_dates():
    service = BotChatService(
        MagicMock(),
        BotChatConfig(
            langfuse_base_url="",
            langfuse_public_key="",
            langfuse_secret_key="",
        ),
    )
    old_session = ConversationSession(
        id="trace_old_fixture",
        timestamp="2020-01-01T00:00:00Z",
    )
    service._db_repo = MagicMock()
    service._db_repo.list_ocb_traces.return_value = ([old_session], 1)

    result = await service.list_sessions(
        owner_id="user_fixture",
        trace_id="trace_old_fixture",
        from_date=datetime(2020, 1, 1),
        to_date=datetime(2020, 1, 2),
        time_scope="all",
    )

    assert result.total == 1
    kwargs = service._db_repo.list_ocb_traces.call_args.kwargs
    assert kwargs["from_ms"] == 1_577_836_800_000
