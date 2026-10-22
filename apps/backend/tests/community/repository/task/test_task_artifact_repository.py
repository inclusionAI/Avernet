"""``TaskArtifactRepository`` 读侧契约:按 task / (task, node) 列产物,attempt 倒序(spec §6)。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from agentclaw.community.core.repository.implementations.task.task_artifact_repository import (
    TaskArtifactRepository,
)
from agentclaw.community.core.task.repository.models import TaskArtifactModel
from agentclaw.community.core.task.repository.types import TaskArtifactRecord

_BASE_TIME = datetime(2026, 10, 20, 12, 0, 0)


def _row(
    *,
    artifact_id: str,
    task_id: str = "t-1",
    node_id: str = "n-1",
    attempt: int = 0,
    kind: str = "text",
    created_offset: int = 0,
) -> TaskArtifactModel:
    moment = _BASE_TIME + timedelta(seconds=created_offset)
    return TaskArtifactModel(
        artifact_id=artifact_id,
        artifact_name=None,
        task_id=task_id,
        node_id=node_id,
        attempt=attempt,
        scope_session_id="sess-1",
        kind=kind,
        storage="inline",
        content='{"kind":"text","text":"done"}',
        content_hash=f"hash-{artifact_id}",
        creator="bot-1",
        modifier="bot-1",
        gmt_create=moment,
        gmt_modify=moment,
    )


@pytest.fixture()
def seeded(db):
    rows = [
        _row(artifact_id="task-art-0000000000000001", node_id="n-1", attempt=0, created_offset=0),
        _row(artifact_id="task-art-0000000000000002", node_id="n-1", attempt=2, created_offset=10),
        _row(artifact_id="task-art-0000000000000003", node_id="n-1", attempt=1, created_offset=20),
        _row(artifact_id="task-art-0000000000000004", node_id="n-2", attempt=5, created_offset=30),
        # 其它任务的行不得混入
        _row(artifact_id="task-art-0000000000000005", task_id="t-2", node_id="n-1", attempt=9),
    ]
    with db.orm_session() as session:
        session.add_all(rows)
    return db


def test_list_by_task_node_orders_attempt_desc(seeded):
    repo = TaskArtifactRepository(seeded)
    records = repo.list_by_task_node("t-1", "n-1")
    assert [r.artifact_id for r in records] == [
        "task-art-0000000000000002",  # attempt=2
        "task-art-0000000000000003",  # attempt=1
        "task-art-0000000000000001",  # attempt=0
    ]
    assert all(isinstance(r, TaskArtifactRecord) for r in records)


def test_list_by_task_aggregates_all_nodes(seeded):
    repo = TaskArtifactRepository(seeded)
    records = repo.list_by_task("t-1")
    assert {r.node_id for r in records} == {"n-1", "n-2"}
    assert len(records) == 4


def test_limit_applies(seeded):
    repo = TaskArtifactRepository(seeded)
    assert len(repo.list_by_task("t-1", limit=2)) == 2
    assert len(repo.list_by_task_node("t-1", "n-1", limit=1)) == 1


def test_lists_empty_for_unknown_task(seeded):
    repo = TaskArtifactRepository(seeded)
    assert repo.list_by_task("t-nope") == []
    assert repo.list_by_task_node("t-nope", "n-1") == []


def test_same_attempt_orders_gmt_create_desc(db):
    later = _row(artifact_id="task-art-00000000000000aa", attempt=1, created_offset=99)
    earlier = _row(artifact_id="task-art-00000000000000bb", attempt=1, created_offset=0)
    with db.orm_session() as session:
        session.add_all([earlier, later])
    repo = TaskArtifactRepository(db)
    records = repo.list_by_task_node("t-1", "n-1")
    assert [r.artifact_id for r in records] == [
        "task-art-00000000000000aa",
        "task-art-00000000000000bb",
    ]

def _text_record(
    *,
    artifact_id: str,
    task_id: str = "t-1",
    node_id: str = "n-1",
    attempt: int = 0,
    text: str = "done",
) -> TaskArtifactRecord:
    """TEXT 产物 record(append 写口契约测试用)。"""
    from agentclaw.community.core.task.task_context.task_artifact import (
        TextContent,
        artifact_content_hash,
    )

    content = TextContent(text=text)
    return TaskArtifactRecord(
        artifact_id=artifact_id,
        artifact_name=None,
        task_id=task_id,
        node_id=node_id,
        attempt=attempt,
        scope_session_id="sess-1",
        kind="text",
        storage="inline",
        content=content,
        content_hash=artifact_content_hash(content),
        creator="bot-1",
        modifier="bot-1",
        created_at=1730000000000,
        gmt_create=None,
    )


def test_append_inserts_and_reads_back(db):
    repo = TaskArtifactRepository(db)
    record = _text_record(artifact_id="task-art-00000000000000a1")
    returned = repo.append(record)
    assert returned.artifact_id == "task-art-00000000000000a1"
    rows = repo.list_by_task_node("t-1", "n-1")
    assert len(rows) == 1
    assert rows[0].content.text == "done"
    assert rows[0].creator == "bot-1"


def test_append_dedupe_returns_existing_row(db):
    repo = TaskArtifactRepository(db)
    first = repo.append(_text_record(artifact_id="task-art-00000000000000a1"))
    # 幂等键 (task_id, node_id, attempt, kind, content_hash) 相同,artifact_id 不同:
    # select-first 查重应命中并返回既有行,不插入第二行(spec §5.3 口径)。
    second = repo.append(_text_record(artifact_id="task-art-00000000000000b2"))
    assert second.artifact_id == first.artifact_id
    assert second.artifact_id == "task-art-00000000000000a1"
    assert len(repo.list_by_task_node("t-1", "n-1")) == 1


def test_append_distinct_content_creates_distinct_rows(db):
    repo = TaskArtifactRepository(db)
    repo.append(_text_record(artifact_id="task-art-00000000000000a1", text="one"))
    repo.append(_text_record(artifact_id="task-art-00000000000000b2", text="two"))
    rows = repo.list_by_task_node("t-1", "n-1")
    assert {r.artifact_id for r in rows} == {
        "task-art-00000000000000a1",
        "task-art-00000000000000b2",
    }
