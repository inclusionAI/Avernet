import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.task.schemas import (
    TaskCallbackRequest, TaskNodeCallbackRequest, _normalize_execution_config,
    execution_graph_to_product_status, op_result_to_dto,
    runtime_status_to_product_status,
)
from agentclaw.community.core.task.domain.models import TaskOpResult


def _base(**kw):
    d = dict(task_id="t1", workflow_source="bcn", workflow_id="w1",
             workflow_instance_id="i1", status="COMPLETED", is_success=True)
    d.update(kw)
    return d


def test_task_callback_request_defaults():
    r = TaskCallbackRequest(**_base())
    assert r.goal is None and r.output is None and r.failed_info is None
    assert r.ext_info is None and r.loop_task_id is None


def test_node_callback_request_requires_node_id():
    with pytest.raises(ValidationError):
        TaskNodeCallbackRequest(**_base())  # 缺 node_id
    r = TaskNodeCallbackRequest(**_base(node_id="n1"))
    assert r.node_id == "n1"


def test_workflow_source_literal():
    with pytest.raises(ValidationError):
        TaskCallbackRequest(**_base(workflow_source="bbs"))


def test_required_fields_enforced():
    with pytest.raises(ValidationError):
        TaskCallbackRequest(task_id="t1", workflow_source="bcn")  # 缺必填


def test_op_result_to_dto_returns_extend_props():
    dto = op_result_to_dto(TaskOpResult(
        task_id="t1",
        success=True,
        run_id=1,
        extend_props={"group_id": "bcs_grp_1"},
    ))

    assert dto.extend_props == {"group_id": "bcs_grp_1"}


@pytest.mark.parametrize(
    ("runtime", "product"),
    [
        ("PENDING", "DEFINED"),
        ("PLANNING", "EXECUTING"),
        ("RUNNING", "EXECUTING"),
        ("HUNG", "REVIEWING"),
        ("DONE", "DONE"),
        ("SUCCESS", "SUCCESS"),
        ("FAILED", "FAILED"),
        ("CANCELLED", "CANCELLED"),
    ],
)
def test_runtime_status_to_product_status(runtime, product):
    assert runtime_status_to_product_status(runtime) == product


import types as _types


def _node(node_id, tc=None):
    ext = {"teamclaw_context": tc} if tc is not None else {}
    spec = _types.SimpleNamespace(
        context=_types.SimpleNamespace(background="", extend_props=ext)
    )
    return _types.SimpleNamespace(node_id=node_id, task_spec=spec)


def _graph(task_id, nodes, extend_props=None):
    return _types.SimpleNamespace(
        task_id=task_id, tasks=nodes, extend_props=extend_props or {}
    )


def test_normalize_execution_config_backfills_historical_teamclaw_context():
    """历史记录:execution_config 缺 4 字段,根节点 teamclaw_context 有 → 只读回填(不改存储)。"""
    g = _graph(
        "T1",
        [_node("T1", tc={"main_session_id": "agent:main:session:x:user:1",
                        "main_session_name": "n1", "parent_task_id": None})],
        extend_props={"execution_config": {"task_type": "dynamic", "workflow_id": None}},
    )
    ec = _normalize_execution_config(g)
    assert ec["main_session_id"] == "agent:main:session:x:user:1"
    assert ec["main_session_name"] == "n1"
    assert ec["parent_task_id"] is None  # 显式 None 同样回填,繁键一致
    assert "source_group_id" not in ec  # tc 未持有该键则不注入
    # 输入 graph 域未被改写(只读归一)
    assert g.extend_props["execution_config"] == {"task_type": "dynamic", "workflow_id": None}


def test_normalize_execution_config_keeps_new_schema_passthrough():
    """新规范:execution_config 已扁平含 4 字段、无 teamclaw_context → 直接透出,不覆盖。"""
    g = _graph(
        "T2", [_node("T2", tc=None)],
        extend_props={"execution_config": {
            "task_type": "workflow", "workflow_id": "wf-1",
            "main_session_id": "s2", "main_session_name": "n2",
            "source_group_id": "g2", "parent_task_id": "P2"}},
    )
    ec = _normalize_execution_config(g)
    assert ec["main_session_id"] == "s2"
    assert ec["source_group_id"] == "g2"
    assert ec["workflow_id"] == "wf-1"


def test_normalize_execution_config_missing_ec_defaults_empty_and_backfills():
    """execution_config 缺失 → 默认 {} + 从 tc 回填;枚举 task_type 转 value。"""
    class FakeEnum:
        value = "yaml"
    g = _graph("T3", [_node("T3", tc={"main_session_id": "z", "main_session_name": "nz"})], {})
    assert _normalize_execution_config(g) == {"main_session_id": "z", "main_session_name": "nz"}
    g4 = _graph("T4", [_node("T4", tc=None)], {"execution_config": {"task_type": FakeEnum()}})
    assert _normalize_execution_config(g4)["task_type"] == "yaml"


def test_execution_graph_statuses_are_mapped_at_product_boundary():
    execution_graph = {
        "status": "PENDING",
        "tasks": [
            {"node_id": "n1", "status": "RUNNING"},
            {"node_id": "n2", "status": "HUNG"},
            {"node_id": "n3", "status": "DONE", "run_info": {"status": "completed"}},
        ],
        "extend_props": {"status": "completed"},
    }

    normalized = execution_graph_to_product_status(execution_graph)

    assert normalized["status"] == "DEFINED"
    assert [task["status"] for task in normalized["tasks"]] == [
        "EXECUTING",
        "REVIEWING",
        "DONE",
    ]
    # Only the graph and direct task statuses are projected; nested metadata is untouched.
    assert normalized["extend_props"]["status"] == "completed"
    assert execution_graph["status"] == "PENDING"
    assert execution_graph["tasks"][0]["status"] == "RUNNING"


def test_acceptance_verdict_backward_compat_pass_fail():
    """历史 PASS/FAIL 库数据/旧上报归一到新 DONE/FAILED,不抛 ValueError。"""
    from agentclaw.community.core.task.domain.models import AcceptanceVerdict

    assert AcceptanceVerdict("PASS") is AcceptanceVerdict.DONE
    assert AcceptanceVerdict("FAIL") is AcceptanceVerdict.FAILED
    assert AcceptanceVerdict("DONE") is AcceptanceVerdict.DONE
    assert AcceptanceVerdict("FAILED") is AcceptanceVerdict.FAILED
    import pytest

    with pytest.raises(ValueError):
        AcceptanceVerdict("UNKNOWN")


def test_acceptance_result_dto_accepts_object_arrays():
    """新协议 acceptances_metric/gaps 为对象数组,DTO(list[Any]) 应接受,不再 500。"""
    from agentclaw.community.adapters.http.task.schemas import AcceptanceResultDTO
    dto = AcceptanceResultDTO(
        verdict="DONE",
        acceptances_metric=[{"ac_1": "exec_ok"}],
        gaps=[{"sddss": "xxsdd"}],
    )
    assert dto.verdict == "DONE"
    assert dto.acceptances_metric == [{"ac_1": "exec_ok"}]
    assert dto.gaps == [{"sddss": "xxsdd"}]


def test_unwrap_node_output_collapses_single_output_key():
    """adapter 路径(pull/poller 与 push/callback-report)统一把产出挂在 output key,
    DTO 出口需把 ``{"output": <content>}`` 展平为标量,消除 dashboard 两层 output 嵌套。"""
    from agentclaw.community.adapters.http.task.schemas import _unwrap_node_output

    # 字符串内容 -> 标量(不再 {output:{output:...}})
    assert _unwrap_node_output({"output": "完整分析内容..."}) == "完整分析内容..."
    # dict 内容 -> 解出真实 dict 内容
    assert _unwrap_node_output({"output": {"answer": 42}}) == {"answer": 42}
    # 空 dict -> 空 dict
    assert _unwrap_node_output({}) == {}
    # 非 output 的单键/多键分支(static/bbs/notify)原样保留 dict
    assert _unwrap_node_output({"architects": [1, 2]}) == {"architects": [1, 2]}
    assert _unwrap_node_output({"result": "x", "extra": "y"}) == {"result": "x", "extra": "y"}
    assert _unwrap_node_output({"skipped": True}) == {"skipped": True}
    # None 直通
    assert _unwrap_node_output(None) is None


def test_runtime_info_dto_output_accepts_scalar_string():
    """RuntimeInfoDTO.output 放宽为 Any 后,adapter 展平的标量产出可直接落字段,
    与历史 dict 形态共存,不再因 ``output: dict[str, Any]`` 把字符串判为非法。"""
    from agentclaw.community.adapters.http.task.schemas import RuntimeInfoDTO

    scalar_dto = RuntimeInfoDTO(output="完整分析内容...")
    assert scalar_dto.output == "完整分析内容..."

    dict_dto = RuntimeInfoDTO(output={"architects": [1, 2]})
    assert dict_dto.output == {"architects": [1, 2]}

    empty_dto = RuntimeInfoDTO()
    assert empty_dto.output == {}
