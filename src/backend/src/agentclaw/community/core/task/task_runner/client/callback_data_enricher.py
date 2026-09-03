"""回投数据 enricher —— 统一处理 BCN(state_machine)与 ClawMind 两路回调数据。

把「execution_graph 构建 + 明细 enrich」从 adapters/translator + router 内联抽到此处:
- ``CallbackDataEnricher.enrich_bcn``:BCN state_machine 回投,经 BCS(token provider.base_url)
  取 run 明细 + DAG,落 ``result._ext_info``(→ task_callback.extend_props)并构建
  ``graph_to_dict`` 形状 execution_graph;fetch 失败/非 200 不抛,用事件体兜底建极简图,返 run_detail 供收敛。
- ``CallbackDataEnricher.enrich_claw_mind``:ClawMind 回投,从事件体 ``ext_info`` 纯函数构建
  execution_graph(无 IO)。ClawMind 路径不查 BCS(事件体自带 flow_runs/node_executions)。

构建纯函数(``_build_bcn_execution_graph``/``_build_claw_mind_execution_graph`` + 状态映射 + 解析工具)
自 adapters/translator 搬入本模块(translator 拆 execution_graph 后仅做字段映射,反向 import 共享的
``_bcn_state_machine_status``/``_claw_mind_status_to_task``)。形状对齐
``core/task/repository/serializers.py::graph_to_dict``。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from agentclaw.community.core.task.domain.models import Status, TaskCallbackData
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    BcsTokenProvider,
)

logger = logging.getLogger("task.task_callback")


# ===== ClawMind 状态映射 + ext_info → graph_to_dict 执行图快照 =====

# 底层 status → callback execution snapshot:completed means execution DONE;
# acceptance pass is resolved by the task callback path to SUCCESS.
# cancelled/aborted→CANCELLED、running/started→RUNNING,余缺省 PENDING。
_CLAW_MIND_TO_TASK_STATUS: dict[str, Status] = {
    "succeeded": Status.DONE, "completed": Status.DONE, "done": Status.DONE,
    "node_succeeded": Status.DONE, "success": Status.DONE,
    "failed": Status.FAILED, "node_failed": Status.FAILED,
    "cancelled": Status.CANCELLED, "canceled": Status.CANCELLED, "aborted": Status.CANCELLED,
    "running": Status.RUNNING, "started": Status.RUNNING, "in_progress": Status.RUNNING,
    "active": Status.RUNNING,
    "pending": Status.PENDING, "queued": Status.PENDING, "waiting": Status.PENDING, "blocked": Status.PENDING,
    "planning": Status.PLANNING,
}


def _claw_mind_status_to_task(low_status: Any) -> Status:
    return _CLAW_MIND_TO_TASK_STATUS.get(str(low_status or "").lower(), Status.PENDING)


def _parse_json(value: Any, default: Any = None) -> Any:
    """容错解析 *_json 字段:dict 原样、str→ json.loads、None/异常 → default。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return default


def _parse_dict(value: Any) -> dict[str, Any]:
    parsed = _parse_json(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _parse_dict_strict(value: Any, *, field: str) -> dict[str, Any]:
    """严格版 _parse_dict(ClawMind 专用):非空字符串若非法 JSON → 抛 ``ValueError``,
    供 router claw_mind 分支 guard 捕获后打日志、不落库(避免脏 upsert 覆盖已有 task_callback 记录)。
    dict 原样返回;None/空串/解析为非 dict → ``{}``(类型宽松,只在 json.loads 失败时抛错)。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"claw_mind 内嵌 JSON 非法 field={field} raw={str(value)[:200]!r}"
            ) from exc
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _to_ms(value: Any) -> int | None:
    """ClawMind 秒级时间戳 → 毫秒(对齐 RuntimeInfo.start_time/end_time 约定)。
    探测值 < 1e12 视为秒(×1000)、已毫秒保持;非法/None → None。"""
    if value is None:
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v * 1000 if v < 1_000_000_000_000 else v


# 图级 extend_props 白名单(workflow 标识/运行指标);credentials_json/identity_key/
# plugin_version 等密钥·摘要·版本,以及图级 node_count/succeeded_count(节点为权威源)均不入。
_CLAW_MIND_GRAPH_KEEP = (
    "workflow_id", "workflow_title", "flow_id", "origin_session_id",
    "total_duration_ms", "total_token_usage", "triggered_by",
    "current_phase", "started_at", "completed_at",
)
_CLAW_MIND_NODE_KEEP = (
    "session_id", "session_key", "embedded_session_key",
    "branch_id", "progress_message", "triggered_by",
)


def _build_claw_mind_execution_graph(ext: dict, *, run_status: Any) -> dict[str, Any] | None:
    """ClawMind ext_info(flow_runs + node_executions)→ graph_to_dict 形状执行图快照。

    - ``run_id`` = int(flow_runs.id)(非法 → 0);图级 status 由底层 status 映射 7 态;
      ``output`` = 解析 flow_runs.result_json;
    - extend_props 白名单取 flow_runs 的 workflow 标识/运行指标;
    - nodes 取 node_executions:task_spec.metadata.title ← node_title(缺则 node_id),
      run_info.{start,end}_time 秒→毫秒;output = 解析 output_json;token_usage/input/
      system_context/timing/error 等富字段折叠进 run_info.extend_props;
    - relations 由各节点 input_json.nodeOutputKeys(params 的兄弟字段)派生(多父 DAG),
      两端须都在节点集内,过滤悬挂边(默认 DEPENDENCY)。
    无 flow_runs 且无 node_executions → None。
    """
    flow_runs = ext.get("flow_runs") if isinstance(ext, dict) else None
    flow_runs = flow_runs if isinstance(flow_runs, dict) else {}
    node_execs = ext.get("node_executions") if isinstance(ext, dict) else None
    node_execs = node_execs if isinstance(node_execs, list) else []
    if not flow_runs and not node_execs:
        return None

    node_ids = {ne.get("node_id") for ne in node_execs
                if isinstance(ne, dict) and ne.get("node_id")}

    tasks: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for ne in node_execs:
        if not isinstance(ne, dict) or not ne.get("node_id"):
            continue
        node_id = ne["node_id"]
        status = _claw_mind_status_to_task(ne.get("status") or run_status)
        input_doc = _parse_dict_strict(ne.get("input_json"), field="node_executions.input_json")
        ik_raw = input_doc.get("nodeOutputKeys")
        input_keys = ik_raw if isinstance(ik_raw, list) else []

        ep: dict[str, Any] = {}
        if ne.get("executor_type"):
            ep["executor_type"] = ne["executor_type"]
        if ne.get("attempt") is not None:
            ep["attempt"] = ne["attempt"]
        tok = _parse_dict_strict(ne.get("token_usage_json"), field="node_executions.token_usage_json")
        if tok:
            ep["token_usage"] = tok
        if input_doc:
            ep["input"] = input_doc
        sc = _parse_dict_strict(ne.get("system_context_json"), field="node_executions.system_context_json")
        if sc:
            ep["system_context"] = sc
        if ne.get("duration_ms") is not None:
            ep["duration_ms"] = ne["duration_ms"]
        if ne.get("started_at") is not None:
            ep["started_at"] = ne["started_at"]          # 原始秒
        if ne.get("completed_at") is not None:
            ep["completed_at"] = ne["completed_at"]
        if ne.get("error_text"):
            ep["error_text"] = ne["error_text"]
        for k in _CLAW_MIND_NODE_KEEP:
            if ne.get(k):
                ep[k] = ne[k]

        for src in input_keys:
            if isinstance(src, str) and src in node_ids and src != node_id:
                relations.append({"src_id": src, "dst_id": node_id,
                                  "type": "DEPENDENCY", "extend_props": {}})

        title = ne.get("node_title") or node_id
        tasks.append({
            "node_id": node_id,
            "task_id": "",
            "status": status.value,
            "task_spec": {
                "metadata": {"task_id": node_id, "title": title, "instruction": ""},
                "context": {"background": "", "extend_props": {}},
                "goal": {"objective": "", "acceptances": []},
            },
            "run_info": {
                "run_mode": None,
                "assignee": None,
                "start_time": _to_ms(ne.get("started_at")),
                "end_time": _to_ms(ne.get("completed_at")),
                "output": _parse_dict_strict(ne.get("output_json"), field="node_executions.output_json"),
                "acceptance_result": None,
                "extend_props": ep,
            },
        })

    graph_ep: dict[str, Any] = {}
    for k in _CLAW_MIND_GRAPH_KEEP:
        if flow_runs.get(k) is not None:
            graph_ep[k] = flow_runs[k]
    graph_params = _parse_dict_strict(flow_runs.get("params_json"), field="flow_runs.params_json")
    if graph_params:
        graph_ep["params"] = graph_params
    # 最终输出(flow_runs.result_json):图级 output 与 extend_props.output 两处都以 `output` 可取;
    # extend_props 仅在非空时落 key(对齐 graph_ep 白名单/params 只加非空的约定)。
    _flow_output = _parse_dict_strict(flow_runs.get("result_json"), field="flow_runs.result_json")
    if _flow_output:
        graph_ep["output"] = _flow_output

    try:
        run_id = int(flow_runs["id"]) if flow_runs.get("id") is not None else 0
    except (TypeError, ValueError):
        run_id = 0

    return {
        "run_id": run_id,
        "task_id": "",
        "loop_round": 0,
        "status": _claw_mind_status_to_task(run_status).value,
        "output": _flow_output,
        "extend_props": graph_ep,
        "tasks": tasks,
        "relations": relations,
    }


# ===== BCN state_machine run/node 状态 → Task Status 枚举 + graph_to_dict 执行图构建器 =====
# translate_bcn 兜底用事件 data 建极简图;router 取回 BCS run 明细/DAG 后调用同一构建器建全图。
# 形状对齐 serializers.graph_to_dict / ClawMind _build_claw_mind_execution_graph
# (run_id:int / task_id / loop_round / status / output / extend_props / tasks / relations)。

def _bcn_state_machine_status(event_type: str) -> Status:
    """state_machine 事件 → 回调行 status:``run.completed``→``DONE``，其余→``RUNNING``。

    对齐 req2:仅 ``state_machine.run.completed`` 视作完成;node.completed 等判运行中
    (节点/run 级终态由 BCS run 明细驱动收敛,回调行 status 仅做粗粒度审计投影)。"""
    return Status.DONE if event_type == "state_machine.run.completed" else Status.RUNNING


# manager_worker(任务协作群)事件 → 回调行 status 粗粒度审计投影(对齐 _bcn_state_machine_status req2):
# 终态事件 ``task.completed`` / ``session.completed`` → ``DONE``;其余事件(``group.created`` /
# ``session.created`` / ``task.assigned``)→ ``RUNNING``。真终态成功/失败由 ``converge_by_session``
# 按 ``session.completed`` 的 ``data.reason`` 收敛(对齐 state_machine 走 BCS run 明细的路径),
# 与本审计列正交。原 ``event_type`` 仍原样保留在 ``orig_callback_data`` 与 ``execution_graph.last_event_type``。
_BCN_MANAGER_WORKER_STATUS_DONE = frozenset({"task.completed", "session.completed"})


def _manager_worker_status(event_type: str) -> Status:
    """manager_worker 事件 → 回调行 status:终态事件 ``task.completed``/``session.completed``→``DONE``，其余→``RUNNING``。

    对齐 ``_bcn_state_machine_status`` 的粗粒度审计投影:回调行 status 仅标该回调是否抵达终态事件;
    真终态 ``DONE`` / ``FAILED`` 由 ``converge_by_session`` 按 ``session.completed`` 的 ``data.reason`` 收敛,
    非直接由本映射驱动(与 state_machine 的 run_detail.run.status 收敛口径一致)。"""
    return Status.DONE if event_type in _BCN_MANAGER_WORKER_STATUS_DONE else Status.RUNNING


_BCN_NODE_STATUS_DONE = frozenset({"completed", "succeeded", "done", "success"})
_BCN_NODE_STATUS_FAILED = frozenset({"failed", "error"})
_BCN_NODE_STATUS_CANCELLED = frozenset({"cancelled", "canceled", "aborted"})


def _bcn_node_status(exec_status: Any) -> Status:
    """BCS node 执行态 → Task Status:completed→DONE、failed→FAILED、aborted→CANCELLED、其余→RUNNING。"""
    s = str(exec_status or "").lower()
    if s in _BCN_NODE_STATUS_DONE:
        return Status.DONE
    if s in _BCN_NODE_STATUS_FAILED:
        return Status.FAILED
    if s in _BCN_NODE_STATUS_CANCELLED:
        return Status.CANCELLED
    return Status.RUNNING


def _bcn_run_id_as_int(run_id: Any) -> int:
    """run_id 整数化(对齐 graph_to_dict.run_id:int);非数字(BCS run_id 字符串)→0。

    真值字符串 run_id 仍保留在 task_callback.run_id 列与 extend_props 里,图内仅做 int 投影。"""
    try:
        return int(run_id) if run_id is not None else 0
    except (TypeError, ValueError):
        return 0


def _bcn_node_task(dag_node: dict, exec_node: dict) -> dict[str, Any]:
    """单节点:DAG 定义(display_name/assignee/final_output)+ 执行结果(status/attempt/outcome/...)→ graph_to_dict task。"""
    nid = dag_node.get("node_id") or exec_node.get("node_id") or ""
    ex_status = exec_node.get("status") or dag_node.get("status")
    title = dag_node.get("display_name") or nid
    final_out = exec_node.get("artifact_text")
    ep: dict[str, Any] = {}
    for k in ("attempt", "outcome", "assignee_bot_id", "error", "status"):
        if exec_node.get(k) is not None:
            ep[k] = exec_node[k]
    return {
        "node_id": nid,
        "task_id": "",
        "status": _bcn_node_status(ex_status).value,
        "task_spec": {
            "metadata": {"task_id": nid, "title": title, "instruction": ""},
            "context": {"background": "", "extend_props": {}},
            "goal": {"objective": "", "acceptances": []},
        },
        "run_info": {
            "run_mode": None,
            "assignee": dag_node.get("assignee"),
            "start_time": exec_node.get("started_at"),
            "end_time": exec_node.get("completed_at"),
            "output": {
                "artifact_text": final_out
            },
            "acceptance_result": None,
            "extend_props": ep,
        },
    }


def _build_bcn_execution_graph(
    *, event_type: str, run_id: Any, data: dict | None = None,
    run_detail: dict | None = None, graph_detail: dict | None = None,
) -> dict[str, Any] | None:
    """BCN state_machine → ``graph_to_dict`` 形状 TaskExecutionGraph 快照(对齐 ClawMind builder)。

    - 无 ``run_detail``/``graph_detail``(``translate_bcn`` 兜底):用事件 ``data`` 建极简图
      (``output=data.output``、空 tasks/relations)——保证落库 ``execution_graph`` 永不为原始事件体。
    - 有明细(router 取回 BCS run 与 DAG):``tasks`` 由 DAG nodes + 执行结果按 ``node_id`` 合并,
      ``relations`` 由 edges 派生(DEPENDENCY);图级 ``status`` 仍按 ``event_type`` 映射(req2),
      ``run_detail.run.status`` / DAG ``definition`` 放图级 ``extend_props``(结构化,非原始明细)。
      ``run_id`` 整数化(字符串 run_id→0,真值在 extend_props / run_id 列保留)。
    """
    data = data if isinstance(data, dict) else {}
    run_detail = run_detail if isinstance(run_detail, dict) else None
    graph_detail = graph_detail if isinstance(graph_detail, dict) else None
    # run_id 整数化:字符串 run_id(如 BCN "run-1")→0;真值保留在 extend_props/run_id 列(graph run_id 为 int)
    rid = 0 if isinstance(run_id, str) else run_id
    status = _bcn_state_machine_status(event_type)

    if not run_detail and not graph_detail:
        logger.error("[task_callback], run_detail and graph_detail should not be none")
        out = data.get("output")
        return {
            "run_id": rid,
            "task_id": "",
            "loop_round": 0,
            "status": status.value,
            "output": (out if isinstance(out, dict) else {}),
            "extend_props": {},
            "tasks": [],
            "relations": [],
        }

    run_obj = (run_detail or {}).get("run")
    run_obj = run_obj if isinstance(run_obj, dict) else {}
    run_output = run_obj.get("output")

    # 执行结果按 node_id 索引(来自 run_detail.nodes)
    exec_by_node: dict[str, dict] = {}
    for _n in run_detail.get("nodes") or []:
        if isinstance(_n, dict) and _n.get("node_id"):
            exec_by_node[_n["node_id"]] = _n

    tasks: list[dict[str, Any]] = []
    for _dn in graph_detail.get("nodes") or []:
        if isinstance(_dn, dict) and _dn.get("node_id"):
            tasks.append(_bcn_node_task(_dn, exec_by_node.get(_dn["node_id"], {})))

    # fallback:graph 无 nodes 但 run_detail 有执行 nodes → 直接用执行结果建 task
    if not tasks:
        for _ex in run_detail.get("nodes") or []:
            if isinstance(_ex, dict) and _ex.get("node_id"):
                tasks.append(_bcn_node_task({"node_id": _ex["node_id"]}, _ex))

    logger.info("[task_callback], edges = %s", str(graph_detail.get("edges")))
    relations: list[dict[str, Any]] = []
    for _e in graph_detail.get("edges") or []:
        if isinstance(_e, dict):
            # 兼容 src/dst 与 source/target 两种边键命名(BCN DAG)
            _src = _e.get("src") or _e.get("source")
            _dst = _e.get("dst") or _e.get("target")
            if _src and _dst:
                relations.append({"src_id": _src, "dst_id": _dst,
                                  "type": "DEPENDENCY", "extend_props": {}})

    graph_ep: dict[str, Any] = {}
    if run_obj.get("status") is not None:
        graph_ep["run_status"] = run_obj.get("status")
    if graph_detail.get("definition") is not None:
        graph_ep["definition"] = graph_detail.get("definition")

    return {
        "run_id": rid,
        "task_id": "",
        "loop_round": 0,
        "status": status.value,
        "output": run_output if isinstance(run_output, dict) else {},
        "extend_props": graph_ep,
        "tasks": tasks,
        "relations": relations,
    }


# ===== enricher =====

class CallbackDataEnricher:
    """统一处理 BCN(state_machine)与 ClawMind 回投数据:构建 execution_graph + 明细 enrich。

    ``base_url`` 取自动态注入的 ``BcsTokenProvider``(corp ``_RealToken`` / singlebox
    ``LocalBcsTokenProvider``),替代 router 原 ``os.environ BCS_API_BASE_URL`` 硬编码。

    - ``enrich_bcn``:经 BCS GET ``/state-machine-runs/{run_id}`` 与 ``/graph`` 取 run 明细 + DAG,
      落 ``result._ext_info``(→ extend_props)并构建 execution_graph;fetch 失败/非 200 不抛,
      用事件体兜底建极简图;返回 run_detail 供 router 终态收敛。
    - ``enrich_claw_mind``:从事件体 ``ext_info`` 纯函数构建 execution_graph(无 IO)。
    """

    def __init__(
        self,
        provider: BcsTokenProvider,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base = provider.base_url.rstrip("/")
        self._client = http_client            # 注入(测试 MockTransport);None → 每请求短连
        self._timeout = timeout

    async def enrich_bcn(self, cd: TaskCallbackData, raw: dict, run_id: str) -> dict | None:
        """BCN state_machine:查 BCS run 明细 + DAG → enrich(cd.data);返 run_detail(供收敛)。"""
        if not isinstance(cd.data, dict):
            return None
        _sid = (cd.data.get("workflow_instance_id") or "")
        if not _sid and isinstance(raw, dict):
            scope = raw.get("scope")
            if isinstance(scope, dict) and scope.get("session_id"):
                _sid = str(scope["session_id"])
        event_type = raw.get("event_type") if isinstance(raw, dict) else None

        logger.info("[task_callback] enrich_bcn, sid=%s, event_type=%s, run_id=%s, bcs_base_url=%s", _sid, event_type, run_id, self._base)

        run_detail: dict | None = None
        graph_detail: dict | None = None

        try:
            run_detail, graph_detail = await self._fetch_run_and_graph(run_id)
        except Exception as exc:  # noqa: BLE001 查 BCS 明细/DAG 失败不阻断(用事件体兜底建图)
            logger.error("[task_callback] 查 BCS run 明细/DAG 失败 run_id=%s session_id=%s: %s", run_id, _sid, exc)

        if run_detail:
            # 查询出来的原始 run 明细 → extend_props(result._ext_info);
            # orig_callback_data 保持原始 CloudEvent(callback 数据由 _raw_callback_body 承载,不在此覆盖)。
            cd.data.setdefault("result", {})["_ext_info"] = run_detail
            #logger.info("[task_callback] BCN run 明细已取回 run_id=%s session_id=%s → extend_props, run_detail=%s", run_id, _sid, run_detail)
            logger.info("[task_callback] BCN run 明细已取回 run_id=%s session_id=%s → extend_props, graph_detail=%s", run_id, _sid, graph_detail)
            eg = _build_bcn_execution_graph(
                event_type=event_type, run_id=run_id,
                run_detail=run_detail, graph_detail=graph_detail,
            )
        else:
            logger.error(
                "[task_callback] BCS run 明细非 200/未取到 run_id=%s session_id=%s → 用事件体兜底建图",
                run_id,
                _sid,
            )
            # 用事件体兜底建极简图(_build_bcn_execution_graph 的 data-only 路径),保证 execution_graph 永不为原始事件体
            _ev_data = raw.get("data") if isinstance(raw, dict) else None
            eg = _build_bcn_execution_graph(event_type=event_type, run_id=run_id, data=_ev_data)

        if eg is not None:
            cd.data["execution_graph"] = eg
        return run_detail

    def enrich_claw_mind(self, cd: TaskCallbackData, raw: dict) -> None:
        """ClawMind:event body ext_info → 纯函数构建 execution_graph(无 IO)。"""
        if not isinstance(cd.data, dict):
            return
        ext = raw.get("ext_info") if isinstance(raw, dict) else None
        ext = ext if isinstance(ext, dict) else {}
        flow_runs = ext.get("flow_runs")
        flow_runs = flow_runs if isinstance(flow_runs, dict) else {}
        node_execs = ext.get("node_executions")
        node_execs = node_execs if isinstance(node_execs, list) else []
        first_node = node_execs[0] if (node_execs and isinstance(node_execs[0], dict)) else {}
        low_status = (flow_runs.get("status") or first_node.get("status") or raw.get("status") or "")
        eg = _build_claw_mind_execution_graph(ext, run_status=low_status)
        if eg is not None:
            cd.data["execution_graph"] = eg

    async def _fetch_run_and_graph(self, run_id: str) -> tuple[dict | None, dict | None]:
        """GET {base}/state-machine-runs/{run_id} 与 /graph;各 200 才取 json,否则 None。"""
        if self._client is not None:
            return await self._gets(self._client, run_id)
        async with httpx.AsyncClient(base_url=self._base, timeout=self._timeout) as cli:
            return await self._gets(cli, run_id)

    async def _gets(self, cli: httpx.AsyncClient, run_id: str) -> tuple[dict | None, dict | None]:
        logger.info("[task_callback], http_get_state_machine_runs_detail, begin, run_id=%s", run_id)

        try:
            run_resp = await cli.get(f"/state-machine-runs/{run_id}")
            logger.info("[task_callback], http_get_state_machine_runs_detail, run_resp=%s", run_resp)

            graph_resp = await cli.get(f"/state-machine-runs/{run_id}/graph")
            logger.info("[task_callback], http_get_state_machine_runs_detail, graph_resp=%s", graph_resp)

            run_detail = run_resp.json() if run_resp.status_code == 200 else None
            graph_detail = graph_resp.json() if graph_resp.status_code == 200 else None
            return run_detail, graph_detail
        except Exception as e:
            logger.error("[task_callback], http_get_state_machine_runs_detail, meet_exception=%s", e)
            return None, None
