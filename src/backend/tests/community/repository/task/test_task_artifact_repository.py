"""``TaskArtifactRepository`` tests (PR2;in-memory SQLite via conftest db fixture).

覆盖 spec 2026-09-23-task-artifact-manifest 的持久层不变量:dedupe 幂等
(create_or_get 双闸第二重)、不可变(无 update)、排序契约
(attempt DESC, created_at DESC, id DESC)、content JSON 中文回程、
record→domain 投影红线。
"""
from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.implementations.task.task_artifact_repository import (
    TaskArtifactRepository,
)
from agentclaw.community.core.task.domain.errors import TaskArtifactContentError
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactKind,
    FileArtifactContent,
)


def _hash(content: dict) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _record(task_id="T-1", node_id="n1", attempt=0, artifact_id="art_1",
            content=None, *, kind="node_result", supersedes=None,
            created_at=100, content_hash=None) -> TaskArtifactRecord:
    content = content if content is not None else {
        "kind": "text", "text": "# 报告\n正文", "media_type": "text/markdown",
    }
    return TaskArtifactRecord(
        id=0,
        artifact_id=artifact_id,
        task_id=task_id,
        node_id=node_id,
        attempt=attempt,
        artifact_kind=kind,
        content_kind=content["kind"],
        content=content,
        content_hash=content_hash or _hash(content),
        supersedes=supersedes,
        derived_from=["art_up"] if supersedes is None else None,
        created_by="bot-x",
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# create_or_get:插入 / dedupe 幂等 / 越界冲突上抛
# ---------------------------------------------------------------------------


def test_create_or_get_inserts_and_returns_stored_id(db):
    repo = TaskArtifactRepository(db)
    rec = repo.create_or_get(_record())
    assert rec.id > 0
    assert rec.artifact_id == "art_1"
    # content 中文 JSON 原样回程(ensure_ascii=False 落库)
    assert rec.content["text"] == "# 报告\n正文"
    assert rec.content_kind == "text"


def test_create_or_get_dedupe_returns_existing_row(db):
    """同 (task,node,attempt,hash) 二次发布 → 返回已存行,不重复。"""
    repo = TaskArtifactRepository(db)
    first = repo.create_or_get(_record(artifact_id="art_a"))
    second = repo.create_or_get(_record(artifact_id="art_b"))  # 换 id 仍收敛
    assert second.id == first.id
    assert second.artifact_id == "art_a"          # 以先入库者为准
    assert len(repo.list_by_task("T-1")) == 1


def test_create_or_get_unknown_conflict_reraises(db):
    """撞非 dedupe 唯一键(artifact_id 重复、内容不同)→ 原样上抛不吞。"""
    repo = TaskArtifactRepository(db)
    repo.create_or_get(_record(artifact_id="art_dup",
                               content={"kind": "text", "text": "one"}))
    with pytest.raises(IntegrityError):
        repo.create_or_get(_record(artifact_id="art_dup",
                                   content={"kind": "text", "text": "two"}))


# ---------------------------------------------------------------------------
# get / list / latest:排序契约与过滤
# ---------------------------------------------------------------------------


def test_get_by_artifact_id(db):
    repo = TaskArtifactRepository(db)
    stored = repo.create_or_get(_record())
    assert repo.get_by_artifact_id(stored.artifact_id).id == stored.id
    assert repo.get_by_artifact_id("art_ghost") is None


def test_list_by_node_orders_attempt_desc_then_created_desc(db):
    repo = TaskArtifactRepository(db)
    repo.create_or_get(_record(artifact_id="a1", attempt=0, created_at=500,
                               content={"kind": "text", "text": "旧"}))
    repo.create_or_get(_record(artifact_id="a2", attempt=1, created_at=300,
                               content={"kind": "text", "text": "重试1"}))
    repo.create_or_get(_record(artifact_id="a3", attempt=1, created_at=900,
                               content={"kind": "text", "text": "重试2"}))
    rows = repo.list_by_node("T-1", "n1")
    assert [r.artifact_id for r in rows] == ["a3", "a2", "a1"]  # attempt 先、created_at 次
    # attempt 过滤 = 重试序隔离读侧
    assert [r.artifact_id for r in repo.list_by_node("T-1", "n1", attempt=1)] == ["a3", "a2"]


def test_list_by_task_and_node_scoping(db):
    repo = TaskArtifactRepository(db)
    repo.create_or_get(_record(task_id="T-1", node_id="n1", artifact_id="x1"))
    repo.create_or_get(_record(task_id="T-2", node_id="n1", artifact_id="x2"))
    repo.create_or_get(_record(task_id="T-1", node_id="n2", artifact_id="x3"))
    assert {r.artifact_id for r in repo.list_by_task("T-1")} == {"x1", "x3"}
    assert {r.artifact_id for r in repo.list_by_node("T-1", "n1")} == {"x1"}


def test_latest_for_node_and_kind_filter(db):
    repo = TaskArtifactRepository(db)
    repo.create_or_get(_record(artifact_id="old", attempt=0, created_at=100,
                               content={"kind": "text", "text": "v0"}))
    repo.create_or_get(_record(artifact_id="new", attempt=1, created_at=200,
                               content={"kind": "text", "text": "v1"}))
    latest = repo.latest_for_node("T-1", "n1")
    assert latest.artifact_id == "new"          # attempt 最大者最新行
    assert repo.latest_for_node("T-1", "n1", kind="node_result").artifact_id == "new"
    assert repo.latest_for_node("T-1", "n1", kind="graph_rollup") is None
    assert repo.latest_for_node("T-1", "ghost") is None


def test_supersedes_chain_preserved_across_rows(db):
    """同 attempt 内容演进:两行 + 新行 supersedes 指向旧行(不可变,旧行保留)。"""
    repo = TaskArtifactRepository(db)
    v1 = repo.create_or_get(_record(artifact_id="v1", created_at=100,
                                    content={"kind": "text", "text": "v1"}))
    v2 = repo.create_or_get(_record(artifact_id="v2", created_at=200,
                                    content={"kind": "text", "text": "v2"},
                                    supersedes=v1.artifact_id))
    assert v2.supersedes == v1.artifact_id
    rows = repo.list_by_node("T-1", "n1")
    assert len(rows) == 2 and rows[0].artifact_id == "v2"  # 旧 v1 不被删改


# ---------------------------------------------------------------------------
# record → domain 投影红线
# ---------------------------------------------------------------------------


def test_record_to_artifact_projects_domain_manifest(db):
    repo = TaskArtifactRepository(db)
    stored = repo.create_or_get(_record(
        content={"kind": "file", "resource_id": "sr_abc", "file_name": "r.pdf",
                 "media_type": "application/pdf", "size_bytes": 12,
                 "sha256": "sha256:" + "b" * 64},
    ))
    art = stored.to_artifact()
    assert art.kind == ArtifactKind.NODE_RESULT == "node_result"
    assert isinstance(art.content, FileArtifactContent)
    assert art.content.resource_id == "sr_abc"
    assert art.lineage.derived_from == ["art_up"]
    assert art.scope.attempt == 0


def test_record_to_artifact_rejects_corrupted_content(db):
    repo = TaskArtifactRepository(db)
    from agentclaw.community.core.task.repository.models import TaskArtifactModel
    with repo._db.orm_session() as session:
        session.add(TaskArtifactModel(
            task_id="T-9", node_id="n9", artifact_id="art_bad",
            artifact_kind="node_result", content_kind="hologram",
            content=json.dumps({"kind": "hologram"}),
            attempt=0, content_hash="sha256:" + "c" * 64, created_at=1,
        ))
    row = repo.get_by_artifact_id("art_bad")
    with pytest.raises(TaskArtifactContentError):
        row.to_artifact()