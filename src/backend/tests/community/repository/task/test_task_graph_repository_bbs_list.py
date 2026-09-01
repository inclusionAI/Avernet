"""``TaskGraphRepository.list_bbs_tasks_overview`` 契约测试。

验证 GET /api/v1/collaboration/tasks/bbs/list 后端查询(忠实翻译给定 SQL):
- ``task_node_run_info`` (run_mode='bbs') ⋈ ``task_node`` (task_id+node_id) 联合;
- 逐行投影为 ``BbsTaskOverviewRecord``(assignee→assignee_id,node.status,n.task_spec,
  n.gmt_create→relay_create_time,r.gmt_create→relay_begin_time,r.gmt_modified→relay_end_time);
- 按 distinct task_id 批量补 ``task_info.owner_bot_id``→publisher(缺失 → None);
- 非 bbs 的 run 行被排除。
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


def test_list_bbs_tasks_overview_joins_run_info_and_node(db):
    _seed_task(db, task_id="bbs-1", node_id="n1", run_mode="bbs")

    rows = TaskGraphRepository(db).list_bbs_tasks_overview()

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

    rows = TaskGraphRepository(db).list_bbs_tasks_overview()

    assert [r.task_id for r in rows] == ["bbs-1"]
    assert all(r.run_mode == "bbs" for r in rows)


def test_list_bbs_tasks_overview_empty_when_no_bbs(db):
    _seed_task(db, task_id="single-2", node_id="n1", run_mode="coop_group")
    assert TaskGraphRepository(db).list_bbs_tasks_overview() == []


def test_list_bbs_tasks_overview_publisher_none_when_task_info_missing(db):
    # run_info(bbs) + node 存在,但无 task_info 行 → publisher=None(不报错)。
    _seed_task(db, task_id="orphan-1", node_id="n1", run_mode="bbs", with_task_info=False)

    rows = TaskGraphRepository(db).list_bbs_tasks_overview()

    assert len(rows) == 1
    assert rows[0].task_id == "orphan-1"
    assert rows[0].publisher is None
