"""``TaskGraphRepository.list_bbs_tasks_overview`` 契约测试。

验证 GET /api/v1/collaboration/tasks/bbs/list 后端查询(忠实翻译给定 SQL):
- ``task_node_run_info`` (run_mode='bbs') ⋈ ``task_node`` (task_id+node_id) 联合;
- 逐行投影为 ``BbsTaskOverviewRecord``(assignee→assignee_id,node.status,n.task_spec,
  n.gmt_create→relay_create_time,r.gmt_create→relay_begin_time,r.gmt_modified→relay_end_time);
- 按 distinct task_id 批量补 ``task_info.owner_bot_id``→publisher(缺失 → None);
- 非 bbs 的 run 行被排除。

分页(1-based):``list_bbs_tasks_overview(page=1, page_size=20) → (records, total)``,
``total`` 为 run_mode='bbs' 联合行数;当前页按 ``task_node_run_info.id`` 降序(最新优先)稳定切片
(LIMIT/OFFSET);页越界 → 空列表 + 真实 total。
"""
from __future__ import annotations

from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_repository import (
    TaskNodeRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_run_info_repository import (
    TaskNodeRunInfoRepository,
)
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import (
    BbsTaskOverviewRecord,
    TaskInfoRecord,
    TaskNodeRecord,
    TaskNodeRunInfoRecord,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)

_TASK_SPEC = {
    "metadata": {"task_id": "bbs-1", "title": "BBS 任务标题", "instruction": "执行"},
    "context": {"background": "bg"},
    "goal": {
        "objective": "达成目标",
        "acceptances": [{"id": "a1", "description": "验收1"}],
    },
}


def _seed_task(
    db,
    *,
    task_id: str,
    node_id: str,
    run_mode: str,
    assignee: str = "asg-1",
    publisher: str = "pub-1",
    with_task_info: bool = True,
    status: Status = Status.RUNNING,
    task_spec: dict | None = None,
    extend_props: dict | None = None,
    output: dict | None = None,
) -> None:
    """落 task_info(可选) + task_node + task_node_run_info 三行;status 为 task_node 态,
    task_spec/extend_props 控制搜索匹配列(默认 _TASK_SPEC / {"assignee_name":"Alice"})。"""
    spec = task_spec if task_spec is not None else _TASK_SPEC
    props = {"assignee_name": "Alice"} if extend_props is None else extend_props
    if with_task_info:
        TaskInfoRepository(db).insert(
            TaskInfoRecord(
                id=0,
                task_id=task_id,
                source_type="bot",
                owner_user_id="u1",
                owner_bot_id=publisher,
                execution_config={},
                task_spec=_TASK_SPEC,
                status=Status.PENDING,
            )
        )
    TaskNodeRepository(db).insert(
        TaskNodeRecord(
            id=0,
            task_id=task_id,
            node_id=node_id,
            task_spec=spec,
            status=status,
        )
    )
    TaskNodeRunInfoRepository(db).insert(
        TaskNodeRunInfoRecord(
            id=0,
            node_id=node_id,
            task_id=task_id,
            run_mode=run_mode,
            assignee=assignee,
            output=output,
            acceptance_result={"verdict": "PASS", "acceptances_metric": [], "gaps": []},
            retry=0,
            session_id=None,
            extend_props=props,
            start_time=1000,
            update_time=None,
            end_time=None,
        )
    )


def test_list_bbs_tasks_overview_injects_output_into_extend_props_when_non_empty(db):
    """run_info.output 非空时,/bbs/list 投影把它并入 extend_props(key=output,值=output 本身)。"""
    _seed_task(
        db, task_id="bbs-1", node_id="n1", run_mode="bbs",
        output={"output": "存储行业尽调报告正文……"},
    )
    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview()
    assert total == 1
    assert rows[0].extend_props["output"] == {"output": "存储行业尽调报告正文……"}
    assert rows[0].extend_props["assignee_name"] == "Alice"  # 原 extend_props 键保留


def test_list_bbs_tasks_overview_does_not_inject_output_when_empty(db):
    """run_info.output 为空时,extend_props 不应多出 output 键。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs")  # output 默认 None
    rows, _ = TaskGraphRepository(db).list_bbs_tasks_overview()
    assert "output" not in (rows[0].extend_props or {})


def _seed_bbs(db, task_ids: list[str]) -> None:
    """落多行 run_mode='bbs' 任务,task_id 升序即稳定分页序。"""
    for i, tid in enumerate(task_ids, start=1):
        _seed_task(
            db,
            task_id=tid,
            node_id=f"n{i}",
            run_mode="bbs",
            assignee=f"asg-{i}",
            publisher=f"pub-{i}",
        )


def test_list_bbs_tasks_overview_joins_run_info_and_node(db):
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs")

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview()

    assert total == 1
    assert len(rows) == 1
    r = rows[0]
    assert isinstance(r, BbsTaskOverviewRecord)
    assert r.task_id == "bbs-1"
    assert r.node_id == "n1"
    assert r.run_mode == "bbs"
    assert r.assignee_id == "asg-1"
    assert r.status is Status.RUNNING
    assert r.acceptance_result == {"verdict": "PASS", "acceptances_metric": [], "gaps": []}
    assert r.extend_props == {"assignee_name": "Alice"}
    assert r.task_spec == _TASK_SPEC
    assert r.publisher == "pub-1"
    # relay 时间三态:node 建表时间:r 建表时间:r 改表时间。
    assert r.relay_create_time is not None
    assert r.relay_begin_time is not None
    assert r.relay_end_time is not None


def test_list_bbs_tasks_overview_excludes_non_bbs_run_mode(db):
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs")
    _seed_task(db, task_id="single-1", node_id="n1", run_mode="single_bot", assignee="other")

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview()

    assert total == 1
    assert [r.task_id for r in rows] == ["bbs-1"]
    assert all(r.run_mode == "bbs" for r in rows)


def test_list_bbs_tasks_overview_empty_when_no_bbs(db):
    _seed_task(db, task_id="single-2", node_id="n1", run_mode="coop_group")
    assert TaskGraphRepository(db).list_bbs_tasks_overview() == ([], 0)


# ── BBS 判定二选一:run_mode='bbs' 或 extend_props.actual_run_mode='bbs'(经理-员工群派发留痕)──


def test_list_bbs_tasks_overview_includes_coop_group_with_actual_run_mode_bbs(db):
    """scoped 节点经 BBS 经理-员工群派发:run_mode='coop_group' 但 extend_props.actual_run_mode='bbs'
    (见 bbs_modal_executor.notify 落库)→ 应被 /bbs/list 命中(新增的第二判定条件)。"""
    _seed_task(
        db, task_id="bbs-coop-1", node_id="n1", run_mode="coop_group",
        extend_props={"assignee_name": "Alice", "actual_run_mode": "bbs"},
    )

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview()

    assert total == 1
    assert [r.task_id for r in rows] == ["bbs-coop-1"]


def test_list_bbs_tasks_overview_excludes_coop_group_with_non_bbs_actual_run_mode(db):
    """extend_props.actual_run_mode='single_bot'(singlebot_2_group 落库留痕)不应被算作 BBS 任务。"""
    _seed_task(
        db, task_id="coop-1", node_id="n1", run_mode="coop_group",
        extend_props={"assignee_name": "Alice", "actual_run_mode": "single_bot"},
    )

    assert TaskGraphRepository(db).list_bbs_tasks_overview() == ([], 0)


def test_list_bbs_tasks_overview_unions_run_mode_bbs_and_actual_run_mode_bbs(db):
    """两种 BBS 判定都命中,各自计入 total 与页(run_mode='bbs' ⋃ actual_run_mode='bbs')。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs")
    _seed_task(
        db, task_id="bbs-coop-1", node_id="n2", run_mode="coop_group",
        extend_props={"assignee_name": "Bob", "actual_run_mode": "bbs"},
    )

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview()

    assert total == 2
    assert {r.task_id for r in rows} == {"bbs-1", "bbs-coop-1"}


def test_list_bbs_tasks_overview_publisher_none_when_task_info_missing(db):
    # run_info(bbs) + node 存在,但无 task_info 行 → publisher=None(不报错);owner_user_id / publisher_name 也 None。
    _seed_task(db, task_id="orphan-1", node_id="n1", run_mode="bbs", with_task_info=False)

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview()

    assert total == 1
    assert len(rows) == 1
    assert rows[0].task_id == "orphan-1"
    assert rows[0].publisher is None
    assert rows[0].owner_user_id is None
    assert rows[0].publisher_name is None


def test_list_bbs_tasks_overview_paginates_and_reports_total(db):
    """page/page_size 1-based 切片,total 为全量 bbs 行数;按 run_info.id 降序(最新优先)稳定。"""
    _seed_bbs(db, ["bbs-1", "bbs-2", "bbs-3"])

    page1, total = TaskGraphRepository(db).list_bbs_tasks_overview(page=1, page_size=2)
    assert total == 3
    assert [r.task_id for r in page1] == ["bbs-3", "bbs-2"]

    page2, total = TaskGraphRepository(db).list_bbs_tasks_overview(page=2, page_size=2)
    assert total == 3
    assert [r.task_id for r in page2] == ["bbs-1"]


def test_list_bbs_tasks_overview_total_excludes_non_bbs(db):
    """total 只计 run_mode='bbs',非 bbs 行不计入总数也不进入页。"""
    _seed_bbs(db, ["bbs-1", "bbs-2"])
    _seed_task(db, task_id="single-1", node_id="n1", run_mode="single_bot")

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(page=1, page_size=10)
    assert total == 2
    assert [r.task_id for r in rows] == ["bbs-2", "bbs-1"]


def test_list_bbs_tasks_overview_page_beyond_range_empty_with_total(db):
    """页越界 → 空列表但 total 仍为真实全量(非报错)。"""
    _seed_bbs(db, ["bbs-1", "bbs-2"])
    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(page=5, page_size=10)
    assert total == 2
    assert rows == []


def test_list_bbs_tasks_overview_defaults_return_first_page(db):
    """缺省 page=1/page_size=20:行数 < page_size 时返回全部(按 id 降序),total 同步。"""
    _seed_bbs(db, ["bbs-1", "bbs-2"])
    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview()
    assert total == 2
    assert [r.task_id for r in rows] == ["bbs-2", "bbs-1"]


def test_graph_service_list_bbs_tasks_overview_forwards_to_repo(db):
    """TaskGraphService 转发 graph_repo.list_bbs_tasks_overview(repo 绑定分支)。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs")
    _seed_task(db, task_id="single-1", node_id="n1", run_mode="single_bot")

    rows, total = TaskGraphService(graph_repo=TaskGraphRepository(db)).list_bbs_tasks_overview()

    assert total == 1
    assert [r.task_id for r in rows] == ["bbs-1"]


def test_graph_service_list_bbs_tasks_overview_empty_when_no_repo():
    """无 graph_repo 绑定(纯内核/测试)→ ([], 0),不阻断(None 守卫分支)。"""
    assert TaskGraphService().list_bbs_tasks_overview() == ([], 0)


def test_task_service_facade_list_bbs_tasks(db):
    """TaskService facade 转发到 graph service → repo(repo 绑定路径,端到端无 stub)。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs", publisher="pub-1")

    service = TaskService(TaskGraphService(graph_repo=TaskGraphRepository(db)))
    rows, total = service.list_bbs_tasks()
    assert total == 1
    assert len(rows) == 1
    assert rows[0].task_id == "bbs-1"
    assert rows[0].publisher == "pub-1"


# ── status 过滤(repo 层)──


def test_list_bbs_tasks_overview_filters_by_status(db):
    """status 等值过滤:只返回匹配,与 total 一致。"""
    _seed_task(db, task_id="run-1", node_id="n1", run_mode="bbs", status=Status.RUNNING)
    _seed_task(db, task_id="done-1", node_id="n2", run_mode="bbs", status=Status.DONE)
    _seed_task(db, task_id="run-2", node_id="n3", run_mode="bbs", status=Status.RUNNING)

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(status="RUNNING")

    assert total == 2
    assert {r.task_id for r in rows} == {"run-1", "run-2"}


def test_list_bbs_tasks_overview_status_no_match(db):
    """status 无匹配 → total=0、rows=[]。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs", status=Status.RUNNING)
    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(status="DONE")
    assert total == 0
    assert rows == []


def test_list_bbs_tasks_overview_status_excludes_non_bbs(db):
    """status 过滤只在 bbs 行内生效;非 bbs 行既不计 total 也进不了页。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs", status=Status.RUNNING)
    _seed_task(db, task_id="single-1", node_id="n2", run_mode="single_bot", status=Status.RUNNING)

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(status="RUNNING")
    assert total == 1
    assert rows[0].task_id == "bbs-1"


# ── search_word 模糊匹配(repo 层,task_spec / extend_props 两列 OR)──


def test_list_bbs_tasks_overview_search_word_matches_task_spec(db):
    """search_word 命中 task_node.task_spec(整列文本 like,大小写不敏感)。"""
    _seed_task(
        db, task_id="bbs-1", node_id="n1", run_mode="bbs",
        task_spec={"metadata": {"title": "AlphaUnique"}},
    )
    _seed_task(
        db, task_id="bbs-2", node_id="n2", run_mode="bbs",
        task_spec={"metadata": {"title": "BetaUnique"}},
    )

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(search_word="alphaunique")

    assert total == 1
    assert rows[0].task_id == "bbs-1"


def test_list_bbs_tasks_overview_search_word_matches_extend_props(db):
    """search_word 命中 task_node_run_info.extend_props(task_spec 不含该词)。"""
    _seed_task(
        db, task_id="bbs-1", node_id="n1", run_mode="bbs",
        extend_props={"assignee_name": "ZoeUnique"},
    )
    _seed_task(
        db, task_id="bbs-2", node_id="n2", run_mode="bbs",
        extend_props={"assignee_name": "Other"},
    )

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(search_word="zoeunique")

    assert total == 1
    assert rows[0].task_id == "bbs-1"


def test_list_bbs_tasks_overview_search_word_case_insensitive(db):
    """search_word 大小写不敏感(func.lower 两端)。"""
    _seed_task(
        db, task_id="bbs-1", node_id="n1", run_mode="bbs",
        task_spec={"metadata": {"title": "MixedCaseTitle"}},
    )
    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(search_word="mixedcasetitle")
    assert total == 1
    assert rows[0].task_id == "bbs-1"


def test_list_bbs_tasks_overview_search_word_no_match(db):
    """search_word 无命中 → total=0、rows=[]。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs")
    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(search_word="zzznotfound")
    assert total == 0
    assert rows == []


def test_list_bbs_tasks_overview_combines_status_and_search_word(db):
    """status + search_word 组合;total 为交集。"""
    _seed_task(
        db, task_id="bbs-1", node_id="n1", run_mode="bbs", status=Status.RUNNING,
        task_spec={"metadata": {"title": "Alpha"}},
    )
    _seed_task(
        db, task_id="bbs-2", node_id="n2", run_mode="bbs", status=Status.DONE,
        task_spec={"metadata": {"title": "AlphaBeta"}},
    )
    _seed_task(
        db, task_id="bbs-3", node_id="n3", run_mode="bbs", status=Status.RUNNING,
        task_spec={"metadata": {"title": "Gamma"}},
    )

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(
        status="RUNNING", search_word="alpha"
    )
    assert total == 1
    assert rows[0].task_id == "bbs-1"


def test_list_bbs_tasks_overview_none_filters_degrade_to_pure_paging(db):
    """status=None / search_word=None → 退化为纯分页(行为不变)。"""
    _seed_bbs(db, ["bbs-1", "bbs-2"])
    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview(search_word=None, status=None)
    assert total == 2


def test_graph_service_list_bbs_tasks_overview_forwards_filters(db):
    """TaskGraphService 透传 status/search_word 到 repo(过滤生效)。"""
    _seed_task(
        db, task_id="run-1", node_id="n1", run_mode="bbs", status=Status.RUNNING,
        task_spec={"metadata": {"title": "Alpha"}},
    )
    _seed_task(
        db, task_id="done-1", node_id="n2", run_mode="bbs", status=Status.DONE,
        task_spec={"metadata": {"title": "Alpha"}},
    )

    svc = TaskGraphService(graph_repo=TaskGraphRepository(db))
    rows, total = svc.list_bbs_tasks_overview(status="RUNNING", search_word="alpha")
    assert total == 1
    assert rows[0].task_id == "run-1"


# ── owner_user_id 中间量 + publisher_name enrich(repo 不查 BotService)──


def test_list_bbs_tasks_overview_fills_owner_user_id(db):
    """repo 顺带取 owner_user_id 填进 record(供 service 批量查 name);publisher_name 恒 None。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs", publisher="pub-1")
    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview()
    assert total == 1
    r = rows[0]
    assert r.publisher == "pub-1"
    assert r.owner_user_id == "u1"  # _seed_task owner_user_id="u1"
    assert r.publisher_name is None  # repo 不查 BotService


class _StubBotService:
    """最小 stub:实现 list_bots_by_owner_bot_pairs,记录调用以验证批量一次。"""

    def __init__(self, mapping: dict | None = None, *, raise_on_call: bool = False) -> None:
        self._mapping = mapping or {}
        self._raise = raise_on_call
        self.calls = 0

    def list_bots_by_owner_bot_pairs(self, *, pairs, page, page_size):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self._raise:
            raise RuntimeError("bot service down")
        return {"items": list(self._mapping.values())}


def test_task_service_enriches_publisher_name_via_bot_service(db):
    """TaskService 用 list_bots_by_owner_bot_pairs 批量查,填 publisher_name;一次批量调用。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs", publisher="bot-A")
    bot = _StubBotService(
        {("bot-A", "u1"): {"bot_id": "bot-A", "owner_id": "u1", "bot_name": "客服Bot-A"}}
    )

    service = TaskService(TaskGraphService(graph_repo=TaskGraphRepository(db)), bot_service=bot)
    rows, total = service.list_bbs_tasks()
    assert total == 1
    assert rows[0].publisher == "bot-A"
    assert rows[0].publisher_name == "客服Bot-A"
    assert bot.calls == 1


def test_task_service_publisher_name_none_when_bot_service_missing(db):
    """无 bot_service → publisher_name 降级 None,不阻断。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs", publisher="bot-A")
    service = TaskService(TaskGraphService(graph_repo=TaskGraphRepository(db)))
    rows, _ = service.list_bbs_tasks()
    assert rows[0].publisher == "bot-A"
    assert rows[0].publisher_name is None


def test_task_service_publisher_name_none_when_bot_service_raises(db):
    """bot_service 查询抛错 → 降级 None,不阻断列表。"""
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs", publisher="bot-A")
    bot = _StubBotService(raise_on_call=True)
    service = TaskService(TaskGraphService(graph_repo=TaskGraphRepository(db)), bot_service=bot)
    rows, _ = service.list_bbs_tasks()
    assert rows[0].publisher == "bot-A"
    assert rows[0].publisher_name is None
