
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
    Metadata,
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
            metadata=Metadata(task_id=task_id, title="title", instruction="do"),
            context=Context(background="background"),
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
    assert restored.tasks[0].task_spec.metadata.title == "title"
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
