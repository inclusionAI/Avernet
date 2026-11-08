
from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    Relation,
    RelationType,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskInfo,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TaskInfoRecord


def _graph(task_id="T-GRAPH"):
    root = TaskNode(
        node_id=task_id,
        task_id=task_id,
        status=Status.PENDING,
        task_spec=TaskSpec(
            context=Context(title="title", background="background"),
            goal=Goal(objective="objective", acceptances=[]),
        ),
        run_info=RuntimeInfo(),
        node_run_graph=None,
    )
    graph = TaskExecutionGraph(
        run_id=7,
        loop_round=2,
        status=Status.RUNNING,
        output={"answer": "ok"},
        extend_props={"bbs_mode": True},
        tasks=[root],
        task_id=task_id,
    )
    root.node_run_graph = graph
    return graph


def test_graph_create_and_load_round_trips_shared_state(db):
    task_id = "T-GRAPH"
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={"task_type": "dynamic"},
            task_spec={
                "metadata": {"task_id": task_id, "title": "title", "instruction": "do"},
                "context": {"background": "background", "extend_props": {}},
                "goal": {"objective": "objective", "acceptances": []},
            },
            status=Status.PENDING,
        )
    )
    repo = TaskGraphRepository(db)
    graph = _graph(task_id)
    assert repo.create_graph(graph, runtime_status=Status.PENDING) == 1

    restored = repo.load_graph(task_id)
    assert restored is not None
    assert restored.run_id == 7
    assert restored.loop_round == 2
    assert restored.status is Status.RUNNING
    assert restored.output == {"answer": "ok"}
    assert restored.extend_props == {"bbs_mode": True}
    assert restored.tasks[0].task_spec.context.title == "title"
    assert restored.tasks[0].status is Status.PENDING


def test_graph_version_rejects_stale_writer(db):
    from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
        GraphVersionConflictError,
    )

    task_id = "T-VERSION"
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={},
            task_spec={"metadata": {"task_id": task_id}},
            status=Status.PENDING,
        )
    )
    repo = TaskGraphRepository(db)
    graph = _graph(task_id)
    repo.create_graph(graph, runtime_status=Status.PENDING)
    graph.output["next"] = "value"
    assert repo.save_graph(
        graph,
        expected_version=1,
        runtime_status=Status.PENDING,
        action_events=[],
    ) == 2
    try:
        repo.save_graph(
            graph,
            expected_version=1,
            runtime_status=Status.PENDING,
            action_events=[],
        )
    except GraphVersionConflictError:
        pass
    else:
        raise AssertionError("stale graph writer was accepted")


def test_second_graph_service_hydrates_from_shared_store(db):
    from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService

    task_id = "T-CROSS-INSTANCE"
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={},
            task_spec={"metadata": {"task_id": task_id}},
            status=Status.PENDING,
        )
    )
    repo = TaskGraphRepository(db)
    first = TaskGraphService(graph_repo=repo)
    first.initialize_graph(TaskInfo(
        task_id=task_id,
        task_spec=_graph(task_id).tasks[0].task_spec,
        source_type="bot",
        owner_bot_id="B-1",
        execution_config={},
    ))
    second = TaskGraphService(graph_repo=repo)
    restored = second.query_task_dashboard(task_id)
    assert restored.task_id == task_id
    assert restored.tasks[0].node_id == task_id


def test_bbs_claim_db_cas_one_winner(db):
    """DB-level BBS claim: first claimer wins, second different bot loses."""
    task_id = "T-BBS-CAS"
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={},
            task_spec={"metadata": {"task_id": task_id}},
            status=Status.PLANNING,
        )
    )
    repo = TaskGraphRepository(db)
    # Create persistent graph state so the root run_info row exists.
    graph = _graph(task_id)
    graph.extend_props["bbs_mode"] = True
    graph.status = Status.PLANNING
    graph.tasks[0].status = Status.PLANNING
    repo.create_graph(graph, runtime_status=Status.PLANNING)

    assert repo.claim_bbs_owner(task_id, "bot-A") is True
    # idempotent re-claim by same bot
    assert repo.claim_bbs_owner(task_id, "bot-A") is True
    # different bot loses
    assert repo.claim_bbs_owner(task_id, "bot-B") is False
    # release returns False for non-owner, then True for owner
    assert repo.release_bbs_owner(task_id, "bot-B") is False
    assert repo.release_bbs_owner(task_id, "bot-A") is True
    # after release another bot can claim
    assert repo.claim_bbs_owner(task_id, "bot-B") is True



def test_group_formation_round_trips_through_shared_store(db):
    """A live GroupFormation carried on run_info.extend_props (HIT_MULTI_BOTS,
    dispatcher -> _drain) must not crash json.dumps on save_graph and must
    survive a DB round-trip so a cross-instance _prepare_into/_drain can still
    do attribute access (gf.collab_mode / bot_ids / extend_props / form_coop_group)."""
    from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

    task_id = "T-GF"
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={},
            task_spec={"metadata": {"task_id": task_id}},
            status=Status.PENDING,
        )
    )
    repo = TaskGraphRepository(db)
    graph = _graph(task_id)
    assert repo.create_graph(graph, runtime_status=Status.PENDING) == 1

    gf = GroupFormation(
        bot_ids=["B-1", "B-2"],
        collab_mode="manager_worker",
        group_name="arch-review",
        members_info=[{"bot_id": "B-1", "role": "manager", "responsibility": "drive"}],
        extend_props={"definition_yaml": "yaml...", "manager_bot_id": "B-1"},
    )
    graph.tasks[0].run_info.extend_props["pending_group_formation"] = gf

    # save_graph must not raise: GroupFormation serialized to a dict for the row.
    assert repo.save_graph(
        graph, expected_version=1, runtime_status=Status.PENDING, action_events=[]
    ) == 2
    # Persistence conversion must not mutate the live in-memory node.
    assert graph.tasks[0].run_info.extend_props["pending_group_formation"] is gf

    restored = repo.load_graph(task_id)
    assert restored is not None
    rgf = restored.tasks[0].run_info.extend_props["pending_group_formation"]
    assert isinstance(rgf, GroupFormation)
    assert rgf.bot_ids == ["B-1", "B-2"]
    assert rgf.collab_mode == "manager_worker"
    assert rgf.group_name == "arch-review"
    assert rgf.members_info == [{"bot_id": "B-1", "role": "manager", "responsibility": "drive"}]
    assert rgf.extend_props["definition_yaml"] == "yaml..."
    assert rgf.extend_props["manager_bot_id"] == "B-1"


def test_removed_graph_node_is_logically_deleted_and_not_hard_deleted(db):
    task_id = "T-LOGICAL-DELETE"
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0, task_id=task_id, source_type="bot", owner_user_id="U-1",
            owner_bot_id="B-1", execution_config={},
            task_spec={"metadata": {"task_id": task_id}}, status=Status.PENDING,
        )
    )
    repo = TaskGraphRepository(db)
    graph = _graph(task_id)
    repo.create_graph(graph, runtime_status=Status.PENDING)
    child = TaskNode(
        node_id="child-1", task_id=task_id, status=Status.FAILED,
        task_spec=graph.tasks[0].task_spec, run_info=RuntimeInfo(), node_run_graph=graph,
    )
    graph.tasks.append(child)
    graph.relations.append(Relation(src_id=task_id, dst_id="child-1", type=RelationType.DEPENDENCY))
    repo.save_graph(graph, expected_version=1, runtime_status=Status.PENDING, action_events=[])

    graph.tasks = [node for node in graph.tasks if node.node_id != "child-1"]
    graph.relations = [rel for rel in graph.relations if rel.dst_id != "child-1"]
    repo.save_graph(graph, expected_version=2, runtime_status=Status.PENDING, action_events=[])

    from agentclaw.community.core.task.repository.models import TaskNodeModel
    with db.orm_session() as session:
        row = (session.query(TaskNodeModel)
               .filter(TaskNodeModel.task_id == task_id, TaskNodeModel.node_id == "child-1")
               .one())
        assert row.is_deleted is True
    restored = repo.load_graph(task_id)
    assert restored is not None
    assert {node.node_id for node in restored.tasks} == {task_id}


# ---------------------------------------------------------------------------
# task_artifact: save_graph 同事务内嵌写(spec 2026-10-20-task-artifact-domain §5.2)


def _artifact_record(
    *,
    task_id="T-ART",
    node_id="T-ART",
    attempt=0,
    content=None,
    content_hash="auto",
    storage="inline",
    kind="text",
):
    from agentclaw.community.core.task.repository.types import TaskArtifactRecord
    from agentclaw.community.core.task.task_context.task_artifact import (
        TextContent,
        artifact_content_hash,
        new_artifact_id,
    )

    text = content or TextContent(text="done")
    return TaskArtifactRecord(
        artifact_id=new_artifact_id(),
        artifact_name=None,
        task_id=task_id,
        node_id=node_id,
        attempt=attempt,
        scope_session_id="sess-1",
        kind=kind,
        storage=storage,
        content=text,
        content_hash=(
            artifact_content_hash(text) if content_hash == "auto" else content_hash
        ),
        creator="bot-1",
        modifier=None,
        created_at=1730000000000,
        gmt_create=None,
    )


def _artifact_setup(db, task_id="T-ART"):
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={},
            task_spec={"metadata": {"task_id": task_id}},
            status=Status.PENDING,
        )
    )
    repo = TaskGraphRepository(db)
    graph = _graph(task_id)
    repo.create_graph(graph, runtime_status=Status.PENDING)
    return repo, graph


def _artifact_rows(db, task_id="T-ART"):
    from agentclaw.community.core.task.repository.models import TaskArtifactModel

    with db.orm_session() as session:
        rows = (
            session.query(TaskArtifactModel)
            .filter(TaskArtifactModel.task_id == task_id)
            .order_by(TaskArtifactModel.id)
            .all()
        )
        return [row.to_record() for row in rows]


def test_save_graph_persists_published_artifacts_in_same_transaction(db):
    repo, graph = _artifact_setup(db)
    record = _artifact_record()
    assert repo.save_graph(
        graph,
        expected_version=1,
        runtime_status=Status.PENDING,
        action_events=[],
        published_artifacts=[record],
    ) == 2
    rows = _artifact_rows(db)
    assert len(rows) == 1
    stored = rows[0]
    assert stored.artifact_id == record.artifact_id
    assert stored.creator == "bot-1"
    # modifier 恒等于 creator(行不可变,房型惯例列)
    assert stored.modifier == record.creator
    assert stored.modifier == "bot-1"
    assert stored.content == record.content
    assert stored.content_hash == record.content_hash
    assert stored.attempt == 0
    assert stored.scope_session_id == "sess-1"
    from datetime import datetime

    assert stored.gmt_create == datetime.fromtimestamp(record.created_at / 1000)
    assert stored.created_at == record.created_at


def test_save_graph_select_first_dedupes_identical_key(db):
    repo, graph = _artifact_setup(db)
    record = _artifact_record()
    repo.save_graph(
        graph,
        expected_version=1,
        runtime_status=Status.PENDING,
        action_events=[],
        published_artifacts=[record],
    )
    graph.output["again"] = "x"
    # 同五元键重放:第二次不重复插入
    repo.save_graph(
        graph,
        expected_version=2,
        runtime_status=Status.PENDING,
        action_events=[],
        published_artifacts=[record],
    )
    assert len(_artifact_rows(db)) == 1


def test_save_graph_null_content_hash_bypasses_dedupe(db):
    """查重键含 NULL(人工修数残留/容错行)——不崩溃、不误判重,直接落新行。"""

    repo, graph = _artifact_setup(db)
    first = _artifact_record(content_hash=None)
    second = _artifact_record(content_hash=None)
    repo.save_graph(
        graph,
        expected_version=1,
        runtime_status=Status.PENDING,
        action_events=[],
        published_artifacts=[first],
    )
    graph.output["again"] = "x"
    repo.save_graph(
        graph,
        expected_version=2,
        runtime_status=Status.PENDING,
        action_events=[],
        published_artifacts=[second],
    )
    assert len(_artifact_rows(db)) == 2


def test_save_graph_artifact_write_failure_rolls_back_whole_transaction(db):
    from agentclaw.community.core.task.domain.errors import TaskArtifactContentError
    from agentclaw.community.core.task.task_context.task_artifact import (
        ArtifactContent,
        new_artifact_id,
    )

    repo, graph = _artifact_setup(db)

    class _Unserializable(ArtifactContent):
        pass

    from agentclaw.community.core.task.repository.types import TaskArtifactRecord

    boom = TaskArtifactRecord(
        artifact_id=new_artifact_id(),
        artifact_name=None,
        task_id="T-ART",
        node_id="T-ART",
        attempt=0,
        scope_session_id="sess-1",
        kind="text",
        storage="inline",
        content=_Unserializable(),
        content_hash="deadbeef" * 8,
        creator="bot-1",
        modifier=None,
        created_at=1730000000000,
        gmt_create=None,
    )
    import pytest

    with pytest.raises(TaskArtifactContentError):
        repo.save_graph(
            graph,
            expected_version=1,
            runtime_status=Status.PENDING,
            action_events=[],
            published_artifacts=[boom],
        )
    # 图版本未推进、产物无行、任务仍可读——整体回滚,不吞不降级
    assert repo.get_version("T-ART") == 1
    assert _artifact_rows(db) == []
    assert repo.load_graph("T-ART") is not None


def test_save_graph_without_artifacts_keeps_existing_surface(db):
    """不传 published_artifacts(存量调用):行为与改前一致,零产物行。"""

    repo, graph = _artifact_setup(db)
    graph.output["plain"] = True
    assert repo.save_graph(
        graph,
        expected_version=1,
        runtime_status=Status.PENDING,
        action_events=[],
    ) == 2
    assert _artifact_rows(db) == []
