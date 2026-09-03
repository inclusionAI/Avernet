"""回投适配层:TaskCallbackData → TaskNodePatch → ExecutionEngine.on_report。

对齐 plan.md §3.5.2。TaskLoopCallback 实现类(实现 api/task/task_loop_callback.py Protocol)并入此模块。
协程化:report_result/start_run 为 async(on_report 链路 async),await 不阻塞回投调用方。
"""
from __future__ import annotations

import contextvars
import hashlib
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
from agentclaw.community.core.task.task_loop_callback_protocol import TaskLoopCallbackProtocol

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


def _unwrap_poller_content(data_field: Any) -> Any:
    """pull(poller)终态 ``{success, data, gaps}`` 的 ``data`` 归一为最终文本内容。

    bot 终态内容两种形态:
      1) 裸字符串(如 ``"行业全貌"``)——直接采用;
      2) 二次包裹 ``{"result": <str>}``(bot 把结论挂在 result key)——展平为 result 字符串,
         使 pull 与 push(callback/report ``output`` 裸字符串)在 run_info.output 中形态一致,
         dashboard 不再出现 ``{output: {result: ...}}`` 二次 json 嵌套。
    其它多键 dict(bcs/notify 检查点等)原样保留,避免误展平。"""
    if isinstance(data_field, dict) and isinstance(data_field.get("result"), str):
        return data_field["result"]
    return data_field


# Pending callback audit shared with the graph service so it can persist the
# inbound callback audit row in the SAME database transaction as the graph
# mutation it drives (spec §12). The callback boundary sets it; the graph
# service consumes it on the first successful graph mutation. Per-call scope:
# cleared in the ``finally`` of the callback handler.
_PENDING_CALLBACK_AUDIT: contextvars.ContextVar[TaskCallbackRecord | None] = contextvars.ContextVar(
    "task_pending_callback_audit", default=None
)


def _derive_event_id(payload: dict[str, Any], disposition: str) -> str | None:
    """Best-effort stable callback event id for replay idempotency.

    A payload-provided explicit id (``event_id`` / ``result._ext_info.event_id``)
    wins. Otherwise a deterministic digest over the routing key + disposition +
    canonical result is produced, so replays of the *same* inbound event collapse
    to one id while distinct events differ. Returns ``None`` only when there is
    no usable routing key (an ``ingest``-only event without loop_task_id).
    """
    run_id, node_id = _split_loop_task_id(payload.get("loop_task_id"))
    if not run_id:
        return None
    ext = payload.get("result", {}).get("_ext_info") if isinstance(payload.get("result"), dict) else None
    # Only a true per-event identifier may short-circuit the digest:
    # ``event_id`` on the payload, or the executor-supplied ``_ext_info.event_id``
    # (e.g. a BCN CloudEvent id). ``workflow_instance_id`` is per-workflow, not
    # per-event (start and result of one run share it), so it must NOT be used —
    # otherwise a result would be mistaken for its own start replay.
    explicit = (
        payload.get("event_id")
        or (ext.get("event_id") if isinstance(ext, dict) else None)
    )
    if explicit:
        return str(explicit)
    identity = json.dumps(
        {
            "run_id": run_id,
            "node_id": node_id,
            "disposition": disposition,
            "source": payload.get("workflow_source"),
            "status": payload.get("status"),
            "result": payload.get("result"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:32]
    return f"{run_id}:{node_id}:{disposition}:{digest}"


def _to_callback_record(payload: dict[str, Any], *, event_id: str | None = None,
                            process_status: str | None = None) -> TaskCallbackRecord:
    """由回投 ``data`` dict 组装回投记录:run_id/node_id 取自 loop_task_id 拆分;
    NOT NULL 的 invoker/main_session_id 缺省 ``""``;可空列缺省 ``None``(空保持空)。
    ``event_id`` 由调用方按 disposition 生成并传入(回放幂等键);``process_status`` 由调用方
    按同事务落库语义传入(callback 驱动路径=``PROCESSED``,``ingest`` 审计路径=``None``)。
    """
    run_id, node_id = _split_loop_task_id(payload.get("loop_task_id"))
    tmp_node_id = payload.get("node_id")
    if tmp_node_id:
        node_id = tmp_node_id
    logger.info("[task][task_callback] to_callback_record, payload=%s, node_id=%s", payload, node_id)

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
        event_id=event_id,
        process_status=process_status,
        processed_at=None,
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
        """组装 TaskNodePatch。两路互斥,按 data 形态分流:

        A) poller 形态(single_bot/BCN 翻译器产出的 ``result`` 是 dict 且含 ``success``):
           result.success/data/gaps/_ext_info/exec_error 组装;三段互斥(对齐 on_report 分流):
           ① exec_error 非空 → 执行报错(bot 没跑通)→ patch.exec_error(无 acceptance,→ on_harness 重投);
           ② success=True → 验收 SUCCESS → content=_unwrap_poller_content(data)(展平 {"result":<str>})
           + output_patch={"output": content};
           ③ success=False + 非空 gaps → 验收不过 → acceptance_result=FAILED,状态为 DONE(不重派);
           ④ success 非布尔/失败无 gaps → exec_error=terminal_result_invalid。

        B) common_task 形态(skill HTTP 上报 ``/callback/report`` 翻译后 ``result`` 非上述 dict,
           关键字段挂在 ``_raw_callback_body``):task_id/node_id/status/output/acceptance_result/extend_props
           原样映射(task_id 来自 body;status 来自 body 或顶层;verdict 来自 acceptance_result)。"""
        d = data.data if isinstance(data.data, dict) else {}
        result = d.get("result")
        if isinstance(result, dict) and ("success" in result or "exec_error" in result):
            return self._adapt_poller(d, result)
        return self._adapt_common_task(d)

    @staticmethod
    def _adapt_poller(d: dict, result: dict) -> TaskNodePatch:
        task_id, node_id = _split_loop_task_id(d.get("loop_task_id"))
        if d.get("node_id"):
            node_id = d["node_id"]
        ext = result.get("_ext_info")
        ext = ext if isinstance(ext, dict) else {}
        ext_patch = dict(ext) if ext else None
        exec_error = result.get("exec_error")
        # ① 执行报错(bot 没跑通):无验收,留 exec_error 走 harness 重投。
        if exec_error:
            return TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                status=Status.FAILED,
                exec_error=exec_error,
                extend_props_patch=ext_patch,
            )
        success = result.get("success")
        data_field = result.get("data")
        content = _unwrap_poller_content(data_field)  # 归一裸文本(展平 {result:<str>})
        # ④ success 非 boolean → 非法终态,无 acceptance。
        if success is None or not isinstance(success, bool):
            return TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                status=Status.FAILED,
                exec_error="terminal_result_invalid: success must be bool",
                extend_props_patch=ext_patch,
            )
        # ② success=True → 验收 SUCCESS。
        if success:
            return TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                status=Status.DONE,
                output_patch={"output": content} if content is not None else None,
                acceptance_result=AcceptanceResult(
                    verdict=AcceptanceVerdict.DONE,
                    acceptances_metric=[],
                    gaps=[],
                ),
                extend_props_patch=ext_patch,
            )
        # ③ success=False:必须有 gaps,否则 ④ 非法终态。
        gaps_raw = result.get("gaps")
        fail_detail = result.get("fail_detail")
        if isinstance(gaps_raw, list) and gaps_raw:
            gaps = list(gaps_raw)
        elif isinstance(fail_detail, str) and fail_detail:
            gaps = [fail_detail]
        else:
            return TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                status=Status.FAILED,
                exec_error="terminal_result_invalid: failed result requires gaps",
                extend_props_patch=ext_patch,
            )
        merged_ext = dict(ext)
        if isinstance(fail_detail, str) and fail_detail:
            merged_ext["fail_detail"] = fail_detail
        return TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            status=Status.FAILED,
            output_patch={"output": content} if content is not None else None,
            acceptance_result=AcceptanceResult(
                verdict=AcceptanceVerdict.FAILED,
                acceptances_metric=[],
                gaps=gaps,
            ),
            extend_props_patch=merged_ext if merged_ext else None,
        )

    @staticmethod
    def _adapt_common_task(d: dict) -> TaskNodePatch:
        body = d.get("_raw_callback_body")
        body = body if isinstance(body, dict) else {}
        accept = body.get("acceptance_result")
        accept = accept if isinstance(accept, dict) else {}
        # push(skill HTTP 上报)按协作群既定协议产 ``body["output"]``;保持该协议不变,
        # 产状按 ``"output"`` key 落 run_info.output(pull/poller 归一映射到同 key,见 _adapt_poller)。
        return TaskNodePatch(
            task_id=body.get("task_id"),
            node_id=d.get("node_id") or body.get("node_id"),
            status=Status(d.get("status") or body.get("status")),
            output_patch={"output": body.get("output")} if body.get("output") is not None else None,
            acceptance_result=AcceptanceResult(
                verdict=AcceptanceVerdict(accept.get("verdict")),
                acceptances_metric=accept.get("acceptances_metric", []),
                gaps=accept.get("gaps", []),
            ) if accept else None,
            extend_props_patch=body.get("extend_props"),
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


class TaskLoopCallback(TaskLoopCallbackProtocol):
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

    def _is_already_processed(self, event_id: str | None) -> bool:
        """event-idempotency guard (spec §12): a callback whose ``event_id`` is
        already recorded with ``process_status='PROCESSED'`` is acknowledged
        without replaying the graph mutation. Defensive against lightweight
        callback-repo fakes that do not implement ``find_by_event_id``."""
        if not event_id or self._callback_repo is None:
            return False
        finder = getattr(self._callback_repo, "find_by_event_id", None)
        if finder is None:
            return False
        try:
            prior = finder(event_id)
        except Exception as exc:  # noqa: BLE001 查幂等键失败不阻断回投(落库/推进仍进行)
            logger.warning("[task][task_callback] idempotency check failed event_id=%s: %s", event_id, exc)
            return False
        return prior is not None and prior.process_status == "PROCESSED"

    def _set_pending_audit(self, record: TaskCallbackRecord | None) -> None:
        _PENDING_CALLBACK_AUDIT.set(record)

    def _consume_pending_audit(self) -> TaskCallbackRecord | None:
        record = _PENDING_CALLBACK_AUDIT.get()
        if record is not None:
            _PENDING_CALLBACK_AUDIT.set(None)
        return record

    async def start_run(self, data: TaskCallbackData) -> None:
        """任务开始执行:适配层 adapt_start → 编排核 on_start(await)→ PENDING→RUNNING(幂等)。
        回放幂等:``event_id`` 已 PROCESSED → 直接 ack;否则把回调审计挂到图变同事务落库。"""
        payload = data.data if isinstance(data.data, dict) else None
        record = None
        if payload is not None:
            event_id = _derive_event_id(payload, "start")
            if self._is_already_processed(event_id):
                logger.info(
                    "[task][task_callback] idempotent start event_id=%s session_id=%s",
                    event_id,
                    payload.get("workflow_instance_id") or "",
                )
                return
            record = _to_callback_record(payload, event_id=event_id, process_status="PROCESSED")
        patch = self._adapter.adapt_start(data)
        if record is not None:
            self._set_pending_audit(record)
        try:
            await self._engine.on_start(patch)
        finally:
            if record is not None:
                self._fallback_persist_audit()
            else:
                self._set_pending_audit(None)

    async def report_result(self, data: TaskCallbackData) -> None:
        """任务完成或失败:适配层组装 TaskNodePatch → 编排核 on_report(await) → graph.update_task_node_info → 翻态/传播/补救。
        回放幂等:``event_id`` 已 PROCESSED → 直接 ack;否则把回调审计挂到图变同事务落库。"""

        logger.info("[task_callback] report_result, begin, data=%s", data)
        payload = data.data if isinstance(data.data, dict) else None
        event_id = _derive_event_id(payload, "result") if payload is not None else None

        # The same inbound result may be retried after the HTTP response is lost.
        # Use the stable event id before creating an audit record or replaying the
        # graph mutation, just like start_run does.
        if self._is_already_processed(event_id):
            logger.info(
                "[task][task_callback] idempotent result event_id=%s session_id=%s",
                event_id,
                payload.get("workflow_instance_id") if payload is not None else "",
            )
            return

        record = (
            _to_callback_record(
                payload,
                event_id=event_id,
                process_status="PROCESSED",
            )
            if payload is not None
            else None
        )
        logger.info("[task_callback] report_result, to_callback_record, %s", record)

        if record is not None:
            self._set_pending_audit(record)

        # 非 dict 回调无法组装 TaskNodePatch(无 loop_task_id/result/body):仅记录审计后返回,
        # 不推进图态(对齐 ``start_run`` 的非 dict 跳过语义;非 dict 不落库/不推进)。
        if payload is None:
            logger.warning(
                "[task_callback] report_result 非 dict data, 跳过 adapt/on_report: %s", data
            )
            self._set_pending_audit(None)
            return

        patch = self._adapter.adapt(data)
        logger.info("[task_callback] report_result, adapt patch, %s", patch)

        try:
            await self._engine.on_report(patch)
        finally:
            if record is not None:
                self._fallback_persist_audit()
            else:
                self._set_pending_audit(None)
        logger.info("[task_callback] report_result, finish")

    async def ingest(self, data: TaskCallbackData) -> None:
        """仅落回投审计(``task_callback``),不推进编排核。供 ClawMind/BCN 等事件/工作流级回投用:
        其 run_id/workflow_id 不对应框架节点,``start_run``/``report_result`` 推进会 NodeNotFoundError。"""
        self._persist(data, disposition="ingest")

    async def ingest_parse_error(self, raw: dict, error: str) -> None:
        """回调解析失败兜底:按 ``(run_id=flow_id, node_id="")`` 经 ``upsert_error`` 仅落
        ``exec_error``(错误信息)+ ``extend_props``(原始上报数据),其它已有字段不动;不推进编排核。
        无 callback_repo → 跳过(仅日志)。"""
        if self._callback_repo is None:
            logger.warning("[task][task_callback] 解析失败兜底落库跳过(无 callback_repo): %s", error)
            return
        ext = raw.get("ext_info") if isinstance(raw, dict) else None
        flow_runs = (ext.get("flow_runs") if isinstance(ext, dict) else None) or {}
        flow_runs = flow_runs if isinstance(flow_runs, dict) else {}
        rec = TaskCallbackRecord(
            id=0,
            invoker="claw_mind",
            run_id=(raw.get("flow_id") or "") if isinstance(raw, dict) else "",
            node_id="",
            main_session_id=(flow_runs.get("origin_session_key") or flow_runs.get("origin_session_id") or ""),
            status=None,
            orig_callback_data=(json.dumps(raw, ensure_ascii=False, default=str) if isinstance(raw, dict) else ""),
            execution_graph=None,
            result=None,
            result_success=None,
            exec_error=error,
            extend_props=raw if isinstance(raw, dict) else None,
        )
        self._callback_repo.upsert_error(rec)
        logger.info("[task][task_callback] 解析失败兜底已落库 run_id=%s exec_error=%s", rec.run_id, error[:120])

    def _fallback_persist_audit(self) -> None:
        """After a callback-driven graph mutation, if the graph service did not
        consume the pending audit (no shared repository, or an idempotent
        no-persist path like an already-RUNNING start), record it best-effort
        via the callback repository so the audit + idempotency key still land."""
        record = _PENDING_CALLBACK_AUDIT.get()
        self._set_pending_audit(None)
        if record is None or self._callback_repo is None:
            return
        try:
            self._callback_repo.upsert(record)
        except Exception as exc:  # noqa: BLE001 审计落库失败不阻断回投推进
            logger.warning(
                "[task][task_callback] fallback persist task_callback failed session_id=%s: %s",
                record.main_session_id or "",
                exc,
            )

    def _persist(self, data: TaskCallbackData, *, disposition: str = "ingest") -> None:
        """``data`` 为 dict → 解析回调记录字段,落 ``task_callback``(按 (run_id,node_id) upsert);
        非 dict 或无 repo → 不落库。best-effort:落库异常仅记日志,不阻断回投→编排核推进。

        仅 ``ingest`` 路径(事件/工作流级,不进编排核)用此直接落库;``start_run``/``report_result``
        经同事务审计路径(``_fallback_persist_audit`` / 图仓储 same-tx)。"""
        if self._callback_repo is None:
            return
        payload = data.data
        if not isinstance(payload, dict):
            return
        event_id = _derive_event_id(payload, disposition)
        try:
            self._callback_repo.upsert(_to_callback_record(payload, event_id=event_id))
        except Exception as exc:  # noqa: BLE001 落库失败不影响编排核推进
            logger.warning(
                "[task][task_callback] persist task_callback failed session_id=%s: %s",
                payload.get("workflow_instance_id") or "",
                exc,
            )
