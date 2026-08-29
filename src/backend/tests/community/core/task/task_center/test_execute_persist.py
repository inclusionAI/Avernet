"""execute persists a task_info row (status PENDING, domain-shape task_spec)
before initialize_graph, and returns the server-generated task_id."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
import agentclaw.community.core.task.repository.models  # noqa: F401  register task_info table
import agentclaw.community.core.task_queue.repository.models  # noqa: F401  idx_status sibling table
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.task.domain.models import (
    Status, TaskNodePatch, TaskSourceType, TaskType,
)
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance, RequestContext, RequestGoal, RequestMetadata,
    RequestTaskSpec, TaskInfoRequest,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.repository.types import TaskInfoRecord


class _SqliteDB:
    def __init__(self, engine):
        self._f = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._f()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def repo():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return TaskInfoRepository(_SqliteDB(eng))


def _request() -> TaskInfoRequest:
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="T", instruction="do"),
            context=RequestContext(background="bg"),
            goal=RequestGoal(objective="o", acceptances=[RequestAcceptance(id="ac1", acceptance="acc")]),
        ),
        source_type=TaskSourceType.API,
        owner_user_id="U1",
        owner_bot_id="B1",
        execution_config={"task_type": TaskType.DYNAMIC},
    )


def _service(repo, task_id="persist-tid"):
    # task_id_provider gives a deterministic id; TaskService defaults to uuid4 in prod.
    return TaskService(TaskGraphService(), task_info_repo=repo, task_id_provider=lambda: task_id)


def _exec(facade, request):
    """execute(fire-and-forget)→drain_background 等首帧落定(测试确定性 seam;同一事件循环)。"""
    import asyncio

    async def _go():
        r = await facade.execute(request)
        await facade.drain_background()
        return r

    return asyncio.new_event_loop().run_until_complete(_go())


def test_execute_persists_task_info_row(repo):
    facade = _service(repo, task_id="persist-tid")
    result = _exec(facade, _request())
    assert result.success is True
    assert result.task_id == "persist-tid"
    row = repo.get("persist-tid")
    assert row is not None
    assert row.status is Status.PENDING
    assert row.source_type == "api"
    assert row.owner_user_id == "U1" and row.owner_bot_id == "B1"
    assert row.task_spec["metadata"]["task_id"] == "persist-tid"
    assert row.task_spec["goal"]["acceptances"] == [{"id": "ac1", "description": "acc"}]



def test_task_info_status_follows_root_status(repo):
    task_id = "status-sync-tid"
    repo.insert(TaskInfoRecord(
        id=0, task_id=task_id, source_type="api", owner_user_id="U1", owner_bot_id="B1",
        execution_config={"task_type": "dynamic"},
        task_spec={"metadata": {"task_id": task_id}}, status=Status.PENDING,
    ))
    graph = TaskGraphService(task_info_repo=repo)
    graph.initialize_graph(_request().to_task_info(task_id))

    graph.update_task_node_info(
        TaskNodePatch(task_id=task_id, node_id=task_id, status=Status.PLANNING)
    )

    assert repo.get(task_id).status is Status.PLANNING


def test_execute_persist_failure_returns_failure(repo):
    # Pre-insert the same task_id so the execute insert hits uk_task_id.
    from agentclaw.community.core.task.repository.types import TaskInfoRecord
    repo.insert(TaskInfoRecord(
        id=0, task_id="persist-tid", source_type="api", owner_user_id="U1", owner_bot_id="B1",
        execution_config={"task_type": "dynamic"}, task_spec={"metadata": {"task_id": "persist-tid"}},
        status=Status.PENDING,
    ))
    facade = _service(repo, task_id="persist-tid")
    result = _exec(facade, _request())
    assert result.success is False
    assert result.task_id == "persist-tid"
    assert result.error is not None
