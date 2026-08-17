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
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


def _ti(tid="p1"):
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=tid, title="t", instruction="i"),
            context=Context(background="", extend_props={}),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        source_channel_type="bot",
        source_channel_id="b1",
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


def _hung_leaf(tid, node_id, parent_id, graph):
    """白盒构造一个 HUNG 叶子节点 + DEPENDENCY 边(parent_id→node_id)。"""
    return TaskNode(
        node_id=node_id,
        task_id=tid,
        status=Status.HUNG,
        task_spec=TaskSpec(
            metadata=Metadata(task_id=node_id, title="hung", instruction="i"),
            context=Context(background="", extend_props={}),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        run_info=RuntimeInfo(),
        node_run_graph=graph,
    ), Relation(src_id=parent_id, dst_id=node_id, type=RelationType.DEPENDENCY)


def test_claim_prunes_hung_subtrees_on_recover():
    """claim 成功即 recover:清掉图中所有 HUNG 子树(HUNG 节点 + 其 DEPENDENCY 后代),
    非 HUNG 兄弟(DONE/PLANNING/PENDING)与根保留。planner 规划不合理的死分支不残留。"""
    svc = TaskGraphService()
    _bbs_task(svc, "p7")
    graph = svc._require_graph("p7")
    graph.tasks[0].status = Status.PLANNING  # 根进可委托态(白盒,同 _bbs_root_planning)
    # 构造:HUNG 子树 h1 → h1c(HUNG 后代);DONE 兄弟 d1(应保留);PENDING 兄弟 s1(应保留)
    h1, r_h1 = _hung_leaf("p7", "h1", "p7", graph)
    h1c, r_h1c = _hung_leaf("p7", "h1c", "h1", graph)
    d1, r_d1 = _hung_leaf("p7", "d1", "p7", graph)
    d1.status = Status.DONE  # 非 HUNG,应保留
    s1, r_s1 = _hung_leaf("p7", "s1", "p7", graph)
    s1.status = Status.PENDING  # 非 HUNG,应保留
    for n, rel in ((h1, r_h1), (h1c, r_h1c), (d1, r_d1), (s1, r_s1)):
        graph.tasks.append(n)
        graph.relations.append(rel)

    svc.claim_bbs_owner("p7", "botA")

    g = svc.query_task_dashboard("p7")
    ids = {n.node_id for n in g.tasks}
    assert ids == {"p7", "d1", "s1"}, f"HUNG 子树未清干净 / 非 HUNG 被误删:remainder={ids}"
    # DEPENDENCY 边里不再触 h1/h1c
    assert all(r.src_id not in {"h1", "h1c"} and r.dst_id not in {"h1", "h1c"} for r in g.relations)
    # 根仍可委托 + 已 claim
    root = next(n for n in g.tasks if n.node_id == "p7")
    assert root.status == Status.PLANNING
    assert root.run_info.extend_props.get("bbs_owner") == "botA"


def test_claim_prune_keeps_root_even_if_root_hung():
    """根(= task_id)永不被 recover 清理(即使被白盒置 HUNG,虽非正常态)。"""
    svc = TaskGraphService()
    _bbs_task(svc, "p8")
    graph = svc._require_graph("p8")
    graph.tasks[0].status = Status.HUNG  # 异常态,仅验证根不被删
    svc.claim_bbs_owner("p8", "botA")
    assert any(n.node_id == "p8" for n in svc.query_task_dashboard("p8").tasks)


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