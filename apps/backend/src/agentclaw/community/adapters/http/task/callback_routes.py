"""Internal task HTTP adapter; domain work is delegated to TaskServiceProtocol."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.task.auth import CallbackAuthenticator
from agentclaw.community.adapters.http.task.gating_relay_callback import reject_legacy_relay_result
from agentclaw.community.adapters.http.task.schemas import (
    TaskCallbackDataDTO, TaskCallbackRequest,
    TaskNodeCallbackRequest, callback_from_dto,
)
from agentclaw.community.adapters.http.task.translator import (
    is_bcn_event_payload,
    is_claw_mind_payload,
    is_common_task_payload,
    parse_manager_worker_bcn,
    translate,
    translate_bcn,
    translate_claw_mind,
    translate_common_task_callback
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import CallbackDataEnricher
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    Status,
    TaskCallbackData,
    TaskReportCommand,
    TaskReportResult,
)

from agentclaw.community.core.task.task_runner.callback_correlation import CallbackCorrelationRegistry
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

# ===== task_loop inbound PUSH callback router(单 bot workflow / bcn 协作群)=====
# 边缘:解析 raw body → Pydantic schema → auth.verify(source from body) → translate → disposition 分发 start_run/report_result。
# 领域异常→ ``@envelope_errors`` 映射(CallbackAuthError→401/CallbackCorrelationError→400/NotFound→404/TaskState→409);
# raw-body 非 JSON→ HTTPException(422);幂等:result 重投到已终态节点→200 ack(start stale→409)。无节点名字面量(零 case)。
task_callback_router = APIRouter(
    prefix="/api/v1/collaboration/tasks/callback", tags=["task-callback"]
)

_TERMINAL = {Status.DONE, Status.SUCCESS, Status.FAILED, Status.HUNG}


def _find_node_status(svc: TaskServiceProtocol, loop_task_id: str) -> Status | None:
    task_id, node_id = loop_task_id.split("::", 1)
    graph = svc.get_task_dashboard(task_id)
    node = next((n for n in graph.tasks if n.node_id == node_id), None)
    return node.status if node is not None else None


def _session_id_of(raw_obj: Any) -> str:
    """从原始回调 body 提取 session_id(落库 ``main_session_id`` 源),供入口/链路日志关联。
    BCN/manager_worker=``scope.session_id``;ClawMind=``ext_info.flow_runs.origin_session_id``;羽雀/兜底 DTO 无此形态 → 返 ``""``。"""
    if not isinstance(raw_obj, dict):
        return ""
    scope = raw_obj.get("scope")
    if isinstance(scope, dict) and scope.get("session_id"):
        return str(scope["session_id"])
    ext = raw_obj.get("ext_info")
    if isinstance(ext, dict):
        flow_runs = ext.get("flow_runs")
        if isinstance(flow_runs, dict) and flow_runs.get("origin_session_id"):
            return str(flow_runs["origin_session_id"])
    return ""


# ===== relay 统一上报审计留底(task_callback 原文落库;成功/失败皆落) =====


def _redact_relay_token(raw: Any) -> dict[str, Any]:
    """relay 原始报文的落库形态:浅拷贝并把 ``relay_token`` 值替换为掩码。"""
    redacted = dict(raw) if isinstance(raw, dict) else {}
    if "relay_token" in redacted:
        redacted["relay_token"] = "***"
    return redacted


def relay_report_audit_payload(
    raw: Any,
    *,
    command: TaskReportCommand | None = None,
    result: TaskReportResult | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """relay 上报 → ``task_callback`` 审计行 payload(对齐 ``_to_callback_record`` 消费约定)。

    * 身份/幂等:``loop_task_id = "{event_id}::{node_id}"`` → ``(run_id=event_id, node_id)``
      是表的唯一 upsert 键——同一 event 重放覆盖同一行,不同 event 各自成行。
    * ``invoker="relay"``(``workflow_source``);``status``=event_type。
    * 原文留底:``_raw_callback_body`` = relay_token 掩码后的请求体。
    * 成败:``result.success`` + ``exec_error``(失败原因);```result._ext_info``` 携带
      task_id/event_type/execution_decision 等路由外补充字段。
    * ``command`` 缺席(422 校验失败)时 best-effort 从 raw 提取三键。
    """
    task_id = str((command.task_id if command else _pick(raw, "task_id")) or "")
    node_id = str((command.node_id if command else _pick(raw, "node_id")) or "")
    event_id = str((command.event_id if command else _pick(raw, "event_id")) or "")
    if command is not None:
        event_type = str(command.event_type.value)
    else:
        event_type = str(_pick(raw, "event_type") or "")
    result_data: dict[str, Any] = {}
    if result is not None:
        result_data = {
            "status": result.status.value,
            "target_node_id": result.target_node_id,
            "task_completed": result.task_completed,
            "task_hung": result.task_hung,
            "bbs_publish_pending": result.bbs_publish_pending,
        }
    decision = getattr(command.event, "execution_decision", None) if command else None
    ext_info: dict[str, Any] = {
        "relay_report": True,
        "task_id": task_id,
        "event_type": event_type,
    }
    if decision:
        ext_info["execution_decision"] = str(decision)
    return {
        "loop_task_id": f"{event_id}::{node_id}",
        "node_id": node_id,
        "event_id": event_id,
        "workflow_source": "relay",
        "workflow_instance_id": "",
        "status": event_type,
        "_raw_callback_body": _redact_relay_token(raw),
        "result": {
            "success": error is None,
            "data": result_data,
            "exec_error": error,
            "_ext_info": ext_info,
        },
    }


def _pick(raw: Any, key: str) -> Any:
    return raw.get(key) if isinstance(raw, dict) else None


async def persist_relay_report_audit(
    svc: TaskServiceProtocol,
    *,
    raw: Any,
    command: TaskReportCommand | None = None,
    result: TaskReportResult | None = None,
    error: str | None = None,
) -> None:
    """relay 上报审计落库,``svc.callback.ingest`` 通道;best-effort:任何失败仅 WARNING,
    绝不改变上报本身的结果/异常(``ingest`` 自身为纯落库旁路,同样吞异常)。"""
    try:
        payload = relay_report_audit_payload(
            raw, command=command, result=result, error=error
        )
        await svc.callback.ingest(TaskCallbackData(data=payload))
    except Exception as exc:  # noqa: BLE001  观测旁路,吞而不抛
        logger.warning(
            "[task_callback] relay report 审计落库失败 node=%s: %s",
            getattr(command, "node_id", None) if command else None,
            exc,
        )


async def _dispatch(
    request: Request,
    disposition: str,
    schema_cls: type[TaskCallbackRequest],
    svc: TaskServiceProtocol,
    auth: CallbackAuthenticator,
    registry: CallbackCorrelationRegistry,
    enricher: CallbackDataEnricher,
) -> Envelope[dict[str, Any]]:
    """回调数据处理总入口(计时包装):实际分流/落库委派 ``_dispatch_impl``;全程计时到毫秒,
    ``finally`` 打 ``elapsed_ms``(覆盖正常 + 异常路径,便于定位慢回投)。"""
    _t0 = time.perf_counter()
    try:
        return await _dispatch_impl(
            request, disposition, schema_cls, svc, auth, registry, enricher,
        )
    finally:
        logger.info(
            "[task_callback] _dispatch 总耗时 elapsed_ms=%.0f disposition=%s",
            (time.perf_counter() - _t0) * 1000, disposition,
        )


async def _dispatch_impl(
    request: Request,
    disposition: str,
    schema_cls: type[TaskCallbackRequest],
    svc: TaskServiceProtocol,
    auth: CallbackAuthenticator,
    registry: CallbackCorrelationRegistry,
    enricher: CallbackDataEnricher,
) -> Envelope[dict[str, Any]]:
    raw = await request.body()
    # 回调 body 按调用者分流:ClawMind(HttpCallbackPayload 四字段)/ BCN(CloudEvent 信封)/ 羽雀(默认 schema)。
    try:
        _raw_obj = json.loads(raw)
    except Exception:
        _raw_obj = None
    if _raw_obj is None and raw:
        # raw-body 非 JSON → HTTPException(422)(对齐 _dispatch docstring:仅非 JSON 走 422;
        # 合法 JSON 但不匹配任一分流 → 200 ack,不推进)。各端点共享本分流。
        raise HTTPException(status_code=422, detail="callback raw body is not valid json")
    _sid = _session_id_of(_raw_obj)  # session_id(主回投键)→ 入口/链路各日志关联
    # ClawMind / BCN 是事件/工作流级回投(run_id/workflow_id 不对应框架节点):只落 task_callback 审计,
    # 不推进编排核(start_run/report_result 会 NodeNotFoundError),直接 ack。
    if is_claw_mind_payload(_raw_obj):
        logger.info("[task_callback] claw_mind callback received session_id=%s", _sid)
        auth.verify(
            source="claw_mind",
            headers=request.headers,
            raw_body=raw,
            method=request.method,
            path=request.url.path,
        )
        # 解析(translate+构图)+落库任一步出错(如内嵌 JSON 非法)→ 打 error 日志,兜底落错误记录
        # (exec_error=错误信息、extend_props=原始 body;经 ingest_parse_error→upsert_error 仅改这两列,
        # 其它已有字段不动),再 ack 返回——不跳过落库,也不全量覆盖污染已有 task_callback。
        try:
            _tc = translate_claw_mind(_raw_obj, disposition)
            enricher.enrich_claw_mind(_tc.data, _raw_obj)
            await svc.callback.ingest(_tc.data)
        except Exception as exc:  # noqa: BLE001 解析失败不阻断回投应答;兜底落错误记录而非全量覆盖
            logger.error(
                "[task_callback] claw_mind 回调解析失败,兜底落错误记录 session_id=%s: %s",
                _sid, exc, exc_info=True,
            )
            await svc.callback.ingest_parse_error(_raw_obj, str(exc))
        return envelope({"ok": True}, request)
    if is_bcn_event_payload(_raw_obj):
        logger.info("[task_callback] bcn_event_callback session_id=%s", _sid)
        auth.verify(
            source="bcn",
            headers=request.headers,
            raw_body=raw,
            method=request.method,
            path=request.url.path,
        )
        # manager_worker(任务协作群)事件:走 manager_worker 分流(parse+merge 进单 session 行 +
        # session.completed 收敛),不进 state_machine 的 translate_bcn/run_detail 路径。
        if parse_manager_worker_bcn(_raw_obj) is not None:
            logger.info("[task_callback] is_manager_worker_event, session_id=%s", _sid)
            #await svc.apply_manager_worker_event(_raw_obj)
            return envelope({"ok": True}, request)

        logger.info("[task_callback] is_state_machine_event, session_id=%s", _sid)
        _tc = translate_bcn(_raw_obj)
        if _tc is None:
            return envelope({"ok": True}, request, message="bcn event not handled")
        # 回调数据处理(execution_graph 构建 + run 明细 → extend_props)统一交 CallbackDataEnricher,
        # base_url 取自注入的 BcsTokenProvider(替代原 os.environ BCS_API_BASE_URL/httpx 内联)。
        _run_id = (
            ((_raw_obj.get("scope") or {}).get("run_id"))
            if isinstance(_raw_obj, dict)
            else None
        )
        logger.info("[task_callback] bcn_event_run_id=%s session_id=%s", _run_id, _sid)
        # 1) 先落原始回调数据(translate 后 minimal:orig=CloudEvent + main_session_id + run_id;
        #    execution_graph/extend_props 暂空)——回调到达即留底,后续解析/查 BCS 失败也不丢原始记录。
        await svc.callback.ingest(_tc.data)
        # 2) 解析/转换:经 enricher 查 BCS run 明细 + 构建 execution_graph + 落 extend_props(改写 _tc.data)。
        _run_detail = (
            await enricher.enrich_bcn(_tc.data, _raw_obj, _run_id)
            if _run_id
            else None
        )
        # 3) 更新这条落库数据(补 execution_graph + extend_props;同 run_id/node_id upsert 覆盖)。
        await svc.callback.ingest(_tc.data)
        # 终态收敛:优先用 BCS run 明细(run_detail.run.status);fetch 失败/非 200 时,若事件本身是
        # state_machine.run.completed(BCS 已表明 run 成功完成),用事件体兜底收敛,不让 BCS 瞬时
        # 抖动丢掉终态翻转(任务节点停在 RUNNING)。按 session_id 查 task_node_run_info → 框架
        # (task_id, node_id) → svc.converge_by_session → on_report → 翻态(验收+传播+根收敛)。
        _run_status = (
            ((_run_detail.get("run") or {}).get("status")) if _run_detail else None
        )
        _converge_output = (
            ((_run_detail.get("run") or {}).get("output")) if _run_detail else None
        )
        if (
            _run_status is None
            and isinstance(_raw_obj, dict)
            and _raw_obj.get("event_type") == "state_machine.run.completed"
        ):
            _run_status = "completed"
            _converge_output = _tc.data.data.get("result", {}).get("data")
        if _run_status in ("completed", "failed", "aborted"):
            _session_id = (
                ((_raw_obj.get("scope") or {}).get("session_id"))
                if isinstance(_raw_obj, dict)
                else None
            )
            if _session_id:
                _success = _run_status == "completed"
                try:
                    await svc.converge_by_session(
                        _session_id, success=_success, output=_converge_output
                    )
                    logger.info(
                        "[task_callback_report] 终态收敛已触发 session_id=%s success=%s",
                        _session_id,
                        _success,
                    )
                except Exception as exc:  # noqa: BLE001 收敛失败不阻断(回调查询/落库已完成)
                    logger.warning(
                        "[task_callback_report] 终态收敛失败 session_id=%s: %s",
                        _session_id,
                        exc,
                    )
        logger.info("[task_callback] finish_process_callback session_id=%s run_id=%s", _sid, _run_id)
        return envelope({"ok": True}, request)

    # Framework task callbacks support the rich callback schema, the common
    # task-loop payload, and the legacy loop_task_id/result DTO. Unknown JSON
    # must be rejected instead of being acknowledged as success.
    if not isinstance(_raw_obj, dict):
        raise HTTPException(status_code=422, detail="callback body must be a JSON object")

    reject_legacy_relay_result(disposition, _raw_obj, svc)

    if _raw_obj.get("event_type") is not None:
        raise TaskStateError("relay events require the authenticated two-event report contract")

    if is_common_task_payload(_raw_obj):
        logger.info("[task_callback] common_task_loop_callback session_id=%s, raw_obj=%s", _sid, _raw_obj)
        tc = translate_common_task_callback(_raw_obj)
        try:
            await svc.callback.report_result(tc.data)
        except TaskStateError:
            loop_task_id = tc.data.data.get("loop_task_id", "")
            try:
                if _find_node_status(svc, loop_task_id) in _TERMINAL:
                    return envelope({"ok": True}, request, message="idempotent")
            except (AttributeError, KeyError, ValueError):
                pass
            raise
        return envelope({"ok": True}, request)

    # Rich framework callback. Validation errors are intentionally allowed to
    # fall through to the legacy DTO so the old report contract remains valid.
    try:
        req = schema_cls.model_validate(_raw_obj)
    except Exception:
        req = None
    if req is not None:
        auth.verify(
            source=req.workflow_source,
            headers=request.headers,
            raw_body=raw,
            method=request.method,
            path=request.url.path,
        )
        tc = translate(req, disposition, registry)
        try:
            if tc.disposition == "start":
                await svc.callback.start_run(tc.data)
            else:
                await svc.callback.report_result(tc.data)
        except TaskStateError:
            # A result replay against an already terminal node is idempotent.
            if tc.disposition == "result":
                loop_task_id = tc.data.data.get("loop_task_id", "")
                try:
                    if _find_node_status(svc, loop_task_id) in _TERMINAL:
                        return envelope({"ok": True}, request, message="idempotent")
                except (AttributeError, KeyError, ValueError):
                    pass
            raise
        return envelope({"ok": True}, request)

    # Legacy callback/report contract: {loop_task_id, workflow_type, result}.
    try:
        dto = TaskCallbackDataDTO.model_validate(_raw_obj)
    except Exception as exc:
        # A syntactically valid JSON object that carries no callback-shaped
        # fields is an unrelated probe/notification. Acknowledge it without
        # invoking authentication or advancing the task graph. Objects that
        # claim to be callback payloads remain validation errors, preserving
        # the endpoint contract for malformed callback requests.
        callback_markers = {
            "loop_task_id",
            "workflow_type",
            "result",
            "task_id",
            "workflow_source",
            "workflow_id",
            "workflow_instance_id",
            "node_id",
            "status",
            "is_success",
            "output",
            "failed_info",
            "acceptance_result",
        }
        if _raw_obj and not any(key in _raw_obj for key in callback_markers):
            return envelope({"ok": True}, request, message="ignored")
        raise HTTPException(status_code=422, detail="invalid callback body") from exc
    auth.verify(
        source=(dto.workflow_type or "single_bot"),
        headers=request.headers,
        raw_body=raw,
        method=request.method,
        path=request.url.path,
    )
    try:
        await svc.callback.report_result(callback_from_dto(dto))
    except TaskStateError:
        try:
            if _find_node_status(svc, dto.loop_task_id) in _TERMINAL:
                return envelope({"ok": True}, request, message="idempotent")
        except (AttributeError, KeyError, ValueError):
            pass
        raise
    return envelope({"ok": True}, request)

@task_callback_router.post("/workflow_start", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def workflow_start(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
    enricher: CallbackDataEnricher = Injected(CallbackDataEnricher),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(request, "start", TaskCallbackRequest, svc, auth, registry, enricher)


@task_callback_router.post("/workflow_result", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def workflow_result(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
    enricher: CallbackDataEnricher = Injected(CallbackDataEnricher),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(request, "result", TaskCallbackRequest, svc, auth, registry, enricher)


@task_callback_router.post("/node_start", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def node_start(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
    enricher: CallbackDataEnricher = Injected(CallbackDataEnricher),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(
        request, "start", TaskNodeCallbackRequest, svc, auth, registry, enricher
    )


@task_callback_router.post("/node_result", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def node_result(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
    enricher: CallbackDataEnricher = Injected(CallbackDataEnricher),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(
        request, "result", TaskNodeCallbackRequest, svc, auth, registry, enricher
    )
