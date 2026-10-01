"""BBS_MAX_DEPTH 默认接入 _execution_config 测试(Task 2)。

校验 ``TaskGraphService._execution_config`` 默认填入 ``BBS_MAX_DEPTH=3`` 并允许
``execution_config`` 覆盖,供后续 Task 5 的 BBS attach 深度闸门读取。
纯加默认,不动状态机/签名/行为。
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


def _task_info(task_id: str = "c1") -> TaskInfo:
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


def test_bbs_max_depth_default():
    svc = TaskGraphService()
    svc.initialize_graph(_task_info())
    cfg = svc._execution_config("c1")
    assert cfg["BBS_MAX_DEPTH"] == 3


def test_bbs_max_depth_overridable():
    svc = TaskGraphService()
    svc.initialize_graph(_task_info("c2"))
    svc.update_task_graph_info(
        "c2",
        TaskGraphPatch(extend_props_patch={"execution_config": {"BBS_MAX_DEPTH": 5}}),
    )
    assert svc._execution_config("c2")["BBS_MAX_DEPTH"] == 5