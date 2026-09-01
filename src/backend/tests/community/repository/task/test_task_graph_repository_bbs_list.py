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
) -> None:
    """落 task_info(可选) + task_node + task_node_run_info 三行,返回控制 bbs 过滤的种子。"""
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
            task_spec=_TASK_SPEC,
            status=Status.RUNNING,
        )
    )
    TaskNodeRunInfoRepository(db).insert(
        TaskNodeRunInfoRecord(
            id=0,
            node_id=node_id,
            task_id=task_id,
            run_mode=run_mode,
            assignee=assignee,
            output=None,
            acceptance_result={"verdict": "PASS", "acceptances_metric": [], "gaps": []},
            retry=0,
            session_id=None,
            extend_props={"assignee_name": "Alice"},
            start_time=1000,
            update_time=None,
            end_time=None,
        )
    )


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


def test_list_bbs_tasks_overview_publisher_none_when_task_info_missing(db):
    # run_info(bbs) + node 存在,但无 task_info 行 → publisher=None(不报错)。
    _seed_task(db, task_id="orphan-1", node_id="n1", run_mode="bbs", with_task_info=False)

    rows, total = TaskGraphRepository(db).list_bbs_tasks_overview()

    assert total == 1
    assert len(rows) == 1
    assert rows[0].task_id == "orphan-1"
    assert rows[0].publisher is None


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
