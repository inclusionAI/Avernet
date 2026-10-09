"""TaskSummary.bbs_mode 直出测试(Task 1)。

校验 BBS-relay 升级后,图服务内部 ``TaskSummary`` 投影仍能透出 ``bbs_mode`` 标志。
HTTP ``/list`` 已改为返回持久化 ``TaskInfoRecord``;BBS 调用方经 ``/dashboard`` 读取图级标志。
"""
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    TaskGraphPatch,
    TaskInfo,
    TaskSpec,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="t", instruction="i"),
            context=Context(background="", extend_props={}),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="a1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
        execution_config={},
    )


def test_summary_exposes_bbs_mode_flag():
    svc = TaskGraphService()
    svc.initialize_graph(_task_info())
    svc.update_task_graph_info(
        "t1",
        TaskGraphPatch(extend_props_patch={"bbs_mode": True}),
    )
    summaries = svc.list_task_summaries()
    assert summaries[0].bbs_mode is True


def test_summary_bbs_mode_default_false():
    svc = TaskGraphService()
    svc.initialize_graph(_task_info("t2"))
    assert svc.list_task_summaries()[0].bbs_mode is False
