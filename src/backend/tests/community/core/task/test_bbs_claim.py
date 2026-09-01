"""BBS 接力任务根级 CAS 占有(claim_bbs_owner)单测。对齐 task-3 brief(TDD RED→GREEN)。"""
import threading

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    Relation,
    RelationType,
    RuntimeInfo,
    Status,
    TaskGraphPatch,
    TaskInfo,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


def _ti(tid="p1"):
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=tid, title="t", instruction="i"),
            context=Context(background="", extend_props={}),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
        execution_config={},
    )


def _bbs_task(svc, tid):
    svc.initialize_graph(_ti(tid))
    svc.update_task_graph_info(tid, TaskGraphPatch(extend_props_patch={"bbs_mode": True}))


def test_claim_cas_exactly_one_wins():
    svc = TaskGraphService()
    _bbs_task(svc, "p1")
    r1 = svc.claim_bbs_owner("p1", "botA")
    assert r1.success is True
    try:
        svc.claim_bbs_owner("p1", "botB")
        assert False, "second claim should lose CAS"
    except TaskStateError:
        pass


def test_claim_idempotent_for_same_bot():
    svc = TaskGraphService()
    _bbs_task(svc, "p2")
    svc.claim_bbs_owner("p2", "botA")
    r = svc.claim_bbs_owner("p2", "botA")  # 同 bot 重 claim 幂等
    assert r.success is True


def test_claim_rejects_non_bbs_task():
    svc = TaskGraphService()
    svc.initialize_graph(_ti("p3"))  # 未置 bbs_mode
    try:
        svc.claim_bbs_owner("p3", "botA")
        assert False
    except TaskStateError:
        pass


def test_claim_writes_owner_and_claim_at_on_root():
    """root.run_info.extend_props['bbs_owner'/'bbs_claim_at'] 被写入。"""
    import time as _time

    svc = TaskGraphService()
    _bbs_task(svc, "p4")
    before = int(_time.time() * 1000)
    svc.claim_bbs_owner("p4", "botA")
    root = next(n for n in svc._require_graph("p4").tasks if n.node_id == "p4")
    assert root.run_info.extend_props.get("bbs_owner") == "botA"
    assert root.run_info.extend_props.get("bbs_claim_at") >= before


def test_claim_preserves_existing_hung_subtrees():
    """claim 只做根级 CAS，不裁剪已有 HUNG 节点或其依赖关系。"""
    svc = TaskGraphService()
    _bbs_task(svc, "p7")
    graph = svc._require_graph("p7")
    graph.tasks[0].status = Status.PLANNING

    node = TaskNode(
        node_id="h1",
        task_id="p7",
        status=Status.HUNG,
        task_spec=TaskSpec(
            metadata=Metadata(task_id="h1", title="hung", instruction="i"),
            context=Context(background="", extend_props={}),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        run_info=RuntimeInfo(),
        node_run_graph=graph,
    )
    graph.tasks.append(node)
    graph.relations.append(Relation(src_id="p7", dst_id="h1", type=RelationType.DEPENDENCY))

    svc.claim_bbs_owner("p7", "botA")

    g = svc.query_task_dashboard("p7")
    assert {n.node_id for n in g.tasks} == {"p7", "h1"}
    assert any(r.src_id == "p7" and r.dst_id == "h1" for r in g.relations)
    root = next(n for n in g.tasks if n.node_id == "p7")
    assert root.status == Status.PLANNING
    assert root.run_info.extend_props.get("bbs_owner") == "botA"


def test_claim_concurrent_exactly_one_wins():
    """并发 CAS:仅一个 bot 成功占有,其余抛 TaskStateError。"""
    svc = TaskGraphService()
    _bbs_task(svc, "p5")
    results: list = []
    lock = threading.Lock()

    def contender(bot_id):
        try:
            r = svc.claim_bbs_owner("p5", bot_id)
            with lock:
                results.append((bot_id, r.success, None))
        except TaskStateError as e:
            with lock:
                results.append((bot_id, False, str(e)))

    bots = [f"bot{i}" for i in range(8)]
    threads = [threading.Thread(target=contender, args=(b,)) for b in bots]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [b for b, ok, _ in results if ok]
    losers = [b for b, ok, _ in results if not ok]
    assert len(winners) == 1, f"expected exactly 1 winner, got {winners}"
    assert len(losers) == len(bots) - 1
    assert winners[0] in bots


def test_facade_claim_bbs_task_delegates():
    """TaskService.claim_bbs_task facade 委托到 TaskGraphService.claim_bbs_owner。"""
    from agentclaw.community.core.task.task_center.task_service import TaskService

    graph = TaskGraphService()
    svc = TaskService(graph)
    _bbs_task(graph, "p6")
    r = svc.claim_bbs_task("p6", "botA")
    assert r.success is True
    # 再被其他 bot claim 应失败
    try:
        svc.claim_bbs_task("p6", "botB")
        assert False
    except TaskStateError:
        pass


def test_protocol_has_claim_bbs_task_signature():
    """TaskServiceProtocol 声明 claim_bbs_task 签名(runtime_checkable Protocol)。"""
    from agentclaw.community.api.task.task_service import TaskServiceProtocol

    assert hasattr(TaskServiceProtocol, "claim_bbs_task")