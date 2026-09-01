"""乙':graph_to_dto 外部 DTO 剥离内部飞行态字段(在途去重/陈旧判定)。

内部 dispatching/dispatching_at 仍持久化 + 编排核读 extend_props 做跨实例/跨协程去重;
仅外部 dashboard 序列化(graph_to_dto)剥离——消除恒 null 噪音,且不泄漏内部在途闸实现。
"""
from __future__ import annotations

from agentclaw.community.adapters.http.task.schemas import graph_to_dto
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    Status,
    TaskNodePatch,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService

from tests.community.core.task.task_context.test_task_graph_service import (
    _node,
    _task_info,
)


def _svc_graph_with_node():
    svc = TaskGraphService()
    graph = svc.initialize_graph(_task_info("tdto"))
    svc.add_task_nodes([_node("c1", "tdto")], parent_node_id="tdto")
    svc.update_task_node_info(
        TaskNodePatch(
            task_id="tdto", node_id="c1", status=Status.RUNNING,
            run_mode="single_bot", assignee="b",
            acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE, acceptances_metric=["ac1"]),
        )
    )
    return svc, graph


def test_graph_to_dto_strips_internal_flight_flags():
    svc, _ = _svc_graph_with_node()
    # 节点 extend_props 含内部在途闸(dispatching/dispatching_at)+ 业务字段(hung_reason 等)
    svc.update_task_node_info(
        TaskNodePatch(
            task_id="tdto", node_id="c1",
            extend_props_patch={
                "dispatching": True,
                "dispatching_at": 1234567890000,
                "hung_reason": "acceptance_fail",
                "plan_round": 2,
            },
        )
    )
    dto = graph_to_dto(svc.query_task_dashboard("tdto"))
    node = next(n for n in dto.tasks if n.node_id == "c1")
    props = node.run_info.extend_props
    assert "dispatching" not in props  # 内部在途闸不外泄
    assert "dispatching_at" not in props
    # 持久化与编排核内部仍保留(未改存储,只剥外部 DTO)
    internal = svc.query_task_dashboard("tdto")
    inode = next(n for n in internal.tasks if n.node_id == "c1")
    assert inode.run_info.extend_props.get("dispatching") is True
    # 业务/诊断字段保留
    assert props.get("hung_reason") == "acceptance_fail"
    assert props.get("plan_round") == 2


def test_graph_to_dto_no_flight_flags_passes_through():
    svc, _ = _svc_graph_with_node()
    svc.update_task_node_info(
        TaskNodePatch(task_id="tdto", node_id="c1", extend_props_patch={"k": "v"})
    )
    dto = graph_to_dto(svc.query_task_dashboard("tdto"))
    node = next(n for n in dto.tasks if n.node_id == "c1")
    assert node.run_info.extend_props == {"k": "v"}


def test_graph_to_dto_projects_execution_config_once_at_top_level():
    svc = TaskGraphService()
    svc.initialize_graph(_task_info("tdto-config"))
    graph = svc.query_task_dashboard("tdto-config")
    graph.extend_props["execution_config"] = {"task_type": "dynamic", "MAX_LOOP": 3}
    graph.extend_props["custom"] = "kept"

    dto = graph_to_dto(graph)

    assert dto.execution_config == {"task_type": "dynamic", "MAX_LOOP": 3}
    assert dto.extend_props == {
        "source_type": "bot",
        "owner_bot_id": "b1",
        "owner_user_id": "",
        "custom": "kept",
    }
