import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.domain.models import RelationType
from agentclaw.community.core.task.repository.types import TaskNodeRelationRecord
from agentclaw.community.core.repository.implementations.task.task_node_relation_repository import (
    TaskNodeRelationRepository,
)


def _rel(src, dst, task_id="T-1", extend_props=None) -> TaskNodeRelationRecord:
    return TaskNodeRelationRecord(
        id=0,
        task_id=task_id,
        src_node_id=src,
        dst_node_id=dst,
        relation_type=RelationType.DEPENDENCY,
        extend_props=extend_props,
    )


def test_add_and_list(db):
    repo = TaskNodeRelationRepository(db)
    n = repo.add_relations([_rel("N-1", "N-2"), _rel("N-1", "N-3", extend_props={"w": 1})])
    assert n == 2
    edges = repo.list_relations("T-1")
    assert {(e.src_node_id, e.dst_node_id) for e in edges} == {("N-1", "N-2"), ("N-1", "N-3")}
    assert repo.list_relations("missing") == []


def test_duplicate_edge_raises(db):
    repo = TaskNodeRelationRepository(db)
    repo.add_relations([_rel("N-1", "N-2")])
    with pytest.raises(IntegrityError):
        repo.add_relations([_rel("N-1", "N-2")])


def test_children_and_parents(db):
    repo = TaskNodeRelationRepository(db)
    repo.add_relations([_rel("N-1", "N-2"), _rel("N-1", "N-3"), _rel("N-2", "N-4")])
    assert {e.dst_node_id for e in repo.children("N-1")} == {"N-2", "N-3"}
    assert {e.src_node_id for e in repo.parents("N-4")} == {"N-2"}
    assert repo.children("N-4") == []


def test_to_relation_projection(db):
    repo = TaskNodeRelationRepository(db)
    repo.add_relations([_rel("N-1", "N-2", extend_props={"w": 1})])
    rec = repo.list_relations("T-1")[0]
    rel = rec.to_relation()
    assert rel.src_id == "N-1"
    assert rel.dst_id == "N-2"
    assert rel.type is RelationType.DEPENDENCY
    assert rel.extend_props == {"w": 1}
