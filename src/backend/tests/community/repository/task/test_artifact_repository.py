"""``task_artifact`` repository tests (SQLite harness, mirroring
``test_task_node_run_info_repository.py``)."""
import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.implementations.task.artifact_repository import (
    TaskArtifactRepository,
)
from agentclaw.community.core.task.domain.artifact import Artifact
from agentclaw.community.core.task.repository.types import ArtifactRecord


def _artifact(
    artifact_id="art_1",
    task_id="T-1",
    node_id="N-1",
    attempt=0,
    supersedes=None,
    created_at=1000,
    **kw,
) -> ArtifactRecord:
    base = dict(
        id=0,
        artifact_id=artifact_id,
        task_id=task_id,
        node_id=node_id,
        session_id="s_1",
        run_id="1",
        attempt=attempt,
        artifact_kind="summary",
        content={"kind": "text", "text": "报告正文", "media_type": "text/markdown"},
        lineage={"derived_from": [], "source_message_ids": []},
        supersedes=supersedes,
        created_by={"actor_type": "bot", "actor_id": "B-1:U1"},
        created_at=created_at,
    )
    base.update(kw)
    return ArtifactRecord(**base)


def test_insert_get_returns_stored_record_with_row_metadata(db):
    repo = TaskArtifactRepository(db)
    stored = repo.insert(_artifact())
    assert stored.id > 0
    assert stored.gmt_create is not None
    assert repo.get("art_1") == stored
    assert repo.get("missing") is None


def test_duplicate_artifact_id_raises_integrity_error(db):
    repo = TaskArtifactRepository(db)
    repo.insert(_artifact())
    with pytest.raises(IntegrityError):
        repo.insert(_artifact())  # 同 artifact_id 重放 → 唯一键拒绝(调用方按幂等处理)
    # 不同内容(不同 artifact_id)允许共存 —— 一节点多产物(文档 §8)。
    repo.insert(_artifact(artifact_id="art_2", created_at=1001))


def test_list_by_task_and_node_with_attempt_filter(db):
    repo = TaskArtifactRepository(db)
    repo.insert(_artifact(artifact_id="a1", created_at=1000))
    repo.insert(_artifact(artifact_id="a2", node_id="N-2", created_at=1001))
    repo.insert(_artifact(artifact_id="a3", attempt=1, created_at=1002))
    assert [r.artifact_id for r in repo.list_by_task("T-1")] == ["a1", "a2", "a3"]
    assert [r.artifact_id for r in repo.list_by_node("T-1", "N-1")] == ["a1", "a3"]
    assert [r.artifact_id for r in repo.list_by_node("T-1", "N-1", attempt=1)] == ["a3"]
    assert repo.list_by_node("T-1", "missing") == []


def test_latest_by_node_is_newest_created_at(db):
    repo = TaskArtifactRepository(db)
    repo.insert(_artifact(artifact_id="old", created_at=1000))
    repo.insert(_artifact(artifact_id="new", created_at=2000))
    repo.insert(_artifact(artifact_id="mid", created_at=1500))
    latest = repo.latest_by_node("T-1", "N-1")
    assert latest is not None and latest.artifact_id == "new"
    assert repo.latest_by_node("T-1", "N-1", attempt=1) is None


def test_list_superseding_returns_replacement_chain(db):
    repo = TaskArtifactRepository(db)
    repo.insert(_artifact(artifact_id="v1", created_at=1000))
    repo.insert(_artifact(artifact_id="v2", supersedes="v1", created_at=2000))
    repo.insert(_artifact(artifact_id="v3", supersedes="v2", created_at=3000))
    assert [r.artifact_id for r in repo.list_superseding("v1")] == ["v2"]
    assert [r.artifact_id for r in repo.list_superseding("v2")] == ["v3"]
    assert repo.list_superseding("v3") == []


def test_record_projects_onto_domain_artifact(db):
    repo = TaskArtifactRepository(db)
    stored = repo.insert(
        _artifact(supersedes="art_prev", lineage={"derived_from": ["art_src"], "source_message_ids": ["m1"]})
    )
    projected: Artifact = stored.to_artifact()
    assert projected.artifact_id == "art_1"
    assert projected.supersedes == "art_prev"
    assert projected.lineage.derived_from == ("art_src",)
    assert projected.lineage.source_message_ids == ("m1",)
    assert projected.scope.task_id == "T-1"
    assert projected.scope.attempt == 0
    assert projected.created_by.to_dict() == {"actor_type": "bot", "actor_id": "B-1:U1"}
    # 去投影后对称:domain to_dict → from_dict 往返一致
    assert projected.to_dict()["content"] == stored.content