"""回投适配层:TaskCallbackData → TaskNodePatch → ExecutionEngine.on_report。

对齐 plan.md §3.5.2。TaskLoopCallback 实现类(实现 api/task/task_loop_callback.py Protocol)并入此模块。
协程化:report_result/start_run 为 async(on_report 链路 async),await 不阻塞回投调用方。
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.task.repository.types import TaskCallbackRecord
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    Status,
    TaskCallbackData,
    TaskNodePatch,
)

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.task import (
        TaskCallbackRepositoryProtocol,
    )

logger = logging.getLogger("task.callback")


def _split_loop_task_id(loop_task_id: Any) -> tuple[str, str]:
    """拆 ``"task_id::node_id"`` → (task_id, node_id);非法/空 → ("", "")。"""
    s = loop_task_id if isinstance(loop_task_id, str) else ""
    parts = s.split("::", 1)
    return parts[0], (parts[1] if len(parts) == 2 else "")


def _to_callback_record(payload: dict[str, Any]) -> TaskCallbackRecord:
    """由回投 ``data`` dict 组装回投记录:run_id/node_id 取自 loop_task_id 拆分;
    NOT NULL 的 invoker/main_session_id 缺省 ``""``;可空列缺省 ``None``(空保持空)。
    """
    run_id, node_id = _split_loop_task_id(payload.get("loop_task_id"))
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    success = result.get("success")
    ext_info = result.get("_ext_info")
    raw_body = payload.get("_raw_callback_body")
    orig = (json.dumps(raw_body, ensure_ascii=False, default=str)
            if isinstance(raw_body, dict) else json.dumps(payload, ensure_ascii=False, default=str))
    return TaskCallbackRecord(
        id=0,
        invoker=(payload.get("workflow_source") or ""),
        run_id=run_id,
        node_id=node_id,
        main_session_id=(payload.get("workflow_instance_id") or ""),
        status=payload.get("status"),
        orig_callback_data=orig,
        execution_graph=payload.get("execution_graph"),
        result=result or None,
        result_success=success if isinstance(success, bool) else None,
        exec_error=(result.get("exec_error") or None),
        extend_props=(ext_info if isinstance(ext_info, dict) else None),
        gmt_create=None,
        gmt_modified=None,
    )


class CallbackAdapter:
    """把执行实体回投的 TaskCallbackData 组装成 TaskNodePatch。

    Avernet stub:loop_task_id 格式 = f"{task_id}::{node_id}"(start_run 时记录的格式);
    真实 workflow 的 loop_task_id 映射需 corp adapter 落地。
    result.success/data/gaps→acceptance_result(PASS/FAIL)+output;旧 fail_detail→单 gap。
    """

    def adapt(self, data: TaskCallbackData) -> TaskNodePatch:
        """组装 TaskNodePatch。三路互斥(对齐 on_report 分流):
        ① result["exec_error"] 非空 → 执行报错(bot 没跑通)→ patch.exec_error(无 acceptance,→ on_harness 重投);
        ② success=True → 验收 PASS → acceptance_result=PASS;
        ③ success=False + 非空 gaps → 验收不过 → acceptance_result=FAIL(→ harness 重派)。
        非法/空终态 → exec_error=terminal_result_invalid。"""
        d = data.data if isinstance(data.data, dict) else {}
        task_id, node_id = _split_loop_task_id(d.get("loop_task_id"))
        result = d.get("result") if isinstance(d.get("result"), dict) else {}
        out = result.get("data")
        fail_detail = result.get("fail_detail")
        raw_gaps = result.get("gaps")
        gaps = [str(g).strip() for g in raw_gaps if str(g).strip()] \
            if isinstance(raw_gaps, list) else []
        if fail_detail and not gaps:
            gaps = [str(fail_detail)]
        exec_error = result.get("exec_error")
        ext = result.get("_ext_info") or {}
        ep_patch: dict[str, Any] = dict(ext)
        if fail_detail:
            ep_patch["fail_detail"] = fail_detail
        if exec_error:
            # 执行报错:不设 acceptance(与验收不过区分);on_report 据 exec_error 走 harness
            return TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                exec_error=str(exec_error),
                output_patch={"data": out} if out is not None else None,
                extend_props_patch=ep_patch if ep_patch else None,
            )
        success = result.get("success")
        if type(success) is not bool:
            return TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                exec_error="terminal_result_invalid: success must be bool",
                output_patch={"data": out} if out is not None else None,
                extend_props_patch=ep_patch if ep_patch else None,
            )
        if success:
            acceptance = AcceptanceResult(verdict=AcceptanceVerdict.PASS, acceptances_metric=["exec_ok"])
        else:
            if not gaps:
                return TaskNodePatch(
                    task_id=task_id,
                    node_id=node_id,
                    exec_error="terminal_result_invalid: failed result requires gaps",
                    output_patch={"data": out} if out is not None else None,
                    extend_props_patch=ep_patch if ep_patch else None,
                )
            acceptance = AcceptanceResult(
                verdict=AcceptanceVerdict.FAIL,
                gaps=gaps,
            )
        return TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            output_patch={"data": out} if out is not None else None,
            acceptance_result=acceptance,
            extend_props_patch=ep_patch if ep_patch else None,
        )

    def adapt_start(self, data: TaskCallbackData) -> TaskNodePatch:
        """start 回调:loop_task_id split + status=RUNNING(无 acceptance);折 _ext_info→extend_props。"""
        d = data.data if isinstance(data.data, dict) else {}
        task_id, node_id = _split_loop_task_id(d.get("loop_task_id"))
        result = d.get("result") if isinstance(d.get("result"), dict) else {}
        ext = result.get("_ext_info") or {}
        return TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            status=Status.RUNNING,
            extend_props_patch=dict(ext) if ext else None,
        )


class TaskLoopCallback:
    """供执行实体(bot workflow / bcn 协作群)PUSH 回投,对接框架 update_task_node_info(经编排核 on_report)。
    实现 api/task/task_loop_callback.py 的 TaskLoopCallbackProtocol。

    协程化:report_result/start_run 为 async,经 await engine.on_report 驱动编排核(async 链路),
    不阻塞回投调用方(HTTP 适配层/外部 bot workflow)。"""

    def __init__(
        self,
        adapter: CallbackAdapter,
        engine,
        callback_repo: "TaskCallbackRepositoryProtocol | None" = None,
    ) -> None:
        """adapter: CallbackAdapter;engine: ExecutionEngine(on_report async 入口)。
        callback_repo: 回投落库协议(DI 在 prod 注入真实实现;``None`` 时跳过落库,纯内核/单测路径用)。"""
        self._adapter = adapter
        self._engine = engine
        self._callback_repo = callback_repo

    async def start_run(self, data: TaskCallbackData) -> None:
        """任务开始执行:适配层 adapt_start → 编排核 on_start(await)→ PENDING→RUNNING(幂等)。
        协程化:on_start async,await 不阻塞回投调用方。"""
        self._persist(data)
        patch = self._adapter.adapt_start(data)
        await self._engine.on_start(patch)

    async def report_result(self, data: TaskCallbackData) -> None:
        """任务完成或失败:适配层组装 TaskNodePatch → 编排核 on_report(await) → graph.update_task_node_info → 翻态/传播/补救。
        协程化:on_report 是 async,await 不阻塞回投调用方。"""
        self._persist(data)
        patch = self._adapter.adapt(data)
        await self._engine.on_report(patch)

    async def ingest(self, data: TaskCallbackData) -> None:
        """仅落回投审计(``task_callback``),不推进编排核。供 ClawMind/BCN 等事件/工作流级回投用:
        其 run_id/workflow_id 不对应框架节点,``start_run``/``report_result`` 推进会 NodeNotFoundError。"""
        self._persist(data)

    def _persist(self, data: TaskCallbackData) -> None:
        """``data`` 为 dict → 解析回调记录字段,落 ``task_callback``(按 (run_id,node_id) upsert);
        非 dict 或无 repo → 不落库。best-effort:落库异常仅记日志,不阻断回投→编排核推进。"""
        if self._callback_repo is None:
            return
        payload = data.data
        if not isinstance(payload, dict):
            return
        try:
            self._callback_repo.upsert(_to_callback_record(payload))
        except Exception as exc:  # noqa: BLE001 落库失败不影响编排核推进
            logger.warning("[task-callback] persist task_callback failed: %s", exc)
