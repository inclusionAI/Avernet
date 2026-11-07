"""Internal benchmark execute endpoint (API-created evaluation sessions)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.task.schemas import (
    TaskInfoRequestDTO,
    TaskOpResultDTO,
    op_result_to_dto,
    task_info_request_from_dto,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.models import TaskOpResult
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
    BcsBotTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BcsClientPort,
    OpenApiBotPort,
)
from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
    build_benchmark_task_launch_message,
    compose_benchmark_a2a_session_id,
    format_benchmark_prompt,
)
from agentclaw.community.di import Injected

router = APIRouter(prefix="/benchmark_execute", tags=["task"])


@router.post("", response_model=Envelope[TaskOpResultDTO])
@envelope_errors
async def benchmark_execute_task_internal(
    body: TaskInfoRequestDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    bot_port: OpenApiBotPort = Injected(OpenApiBotPort),  # noqa: B008
    bcs_client: BcsClientPort = Injected(BcsClientPort),  # noqa: B008
    bot_tokens: BcsBotTokenProvider = Injected(BcsBotTokenProvider),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    """Benchmark 自动执行(内部副本,不经 spanner)。

    评测会话由 API 创建,不能依赖前端挂载 task_ready 卡片触发 execute。端点先发送原
    benchmark prompt 让 Bot 产出可回放的澄清卡片;随后创建正式任务,把副屏声明和 relay
    根指令作为第二条消息发回同一会话,使评测不开会话也能自动执行。

    A2A 分支第一个请求仍由 owner_bot(caller/proxy)发起;正式任务的 owner 绑定到
    target_bot_id/target_user_id,并复用服务端生成的 benchmark session。
    """
    task_request = task_info_request_from_dto(body)
    prompt = format_benchmark_prompt(body.task_spec.model_dump())
    bot_identity = compose_bot_identity(body.owner_bot_id, body.owner_user_id)

    a2a_target_bot = str(body.execution_config.target_bot_id or "").strip() or None
    a2a_target_user = str(body.execution_config.target_user_id or "").strip() or None
    a2a = bool(a2a_target_bot and a2a_target_user)

    extend_props: dict[str, Any] = {
        "benchmark_channel": "a2a" if a2a else "openapi",
    }
    caller_token: str | None = None
    session_id = ""

    def _send_failed(exc: Exception) -> TaskOpResult:
        extend_props["benchmark_send"] = f"error: {exc}"
        return TaskOpResult(
            task_id="",
            success=False,
            run_id=0,
            error=f"benchmark prompt send failed: {exc}",
            extend_props=extend_props,
        )

    if a2a:
        if bcs_client is None:
            return envelope(
                op_result_to_dto(
                    TaskOpResult(
                        task_id="",
                        success=False,
                        run_id=0,
                        error="benchmark prompt send failed: BCS client is unavailable",
                        extend_props={**extend_props, "benchmark_send": "error"},
                    )
                ),
                request,
            )
        session_id = compose_benchmark_a2a_session_id()
        extend_props["benchmark_session_id"] = session_id
        caller_token = bot_tokens.get_token(bot_identity)
        try:
            sent = await bcs_client.send_message_a2a(
                target_bot_id=str(a2a_target_bot),
                target_user_id=str(a2a_target_user),
                message=prompt,
                session_id=session_id,
                caller_bearer=caller_token,
            )
        except Exception as exc:  # noqa: BLE001
            return envelope(op_result_to_dto(_send_failed(exc)), request)
        extend_props["benchmark_run_id"] = sent.run_id
        extend_props["benchmark_send"] = "ok"
        task_request = replace(
            task_request,
            owner_bot_id=str(a2a_target_bot),
            owner_user_id=str(a2a_target_user),
        )
    else:
        if bot_port is None:
            return envelope(
                op_result_to_dto(
                    TaskOpResult(
                        task_id="",
                        success=False,
                        run_id=0,
                        error="benchmark prompt send failed: bot port is unavailable",
                        extend_props={**extend_props, "benchmark_send": "error"},
                    )
                ),
                request,
            )
        try:
            await bot_port.ensure_grant(bot_identity)
            sent = await bot_port.send_message(
                bot_id=bot_identity,
                message=prompt,
                metadata={
                    "benchmark": True,
                    "sender_options": {"from": body.owner_user_id},
                    "bot_options": {"lifecycle_stage": "online"},
                },
            )
        except Exception as exc:  # noqa: BLE001
            return envelope(op_result_to_dto(_send_failed(exc)), request)
        if not (sent.session_id or "").strip():
            return envelope(
                op_result_to_dto(
                    TaskOpResult(
                        task_id="",
                        success=False,
                        run_id=0,
                        error="benchmark prompt send failed: bot session id is unavailable",
                        extend_props={
                            **extend_props,
                            "benchmark_run_id": sent.run_id,
                            "benchmark_send": "error",
                        },
                    )
                ),
                request,
            )
        session_id = str(sent.session_id)
        extend_props["benchmark_run_id"] = sent.run_id
        extend_props["benchmark_session_id"] = session_id
        extend_props["benchmark_send"] = "ok"

    execution_config = dict(task_request.execution_config)
    runtime_profile = execution_config.get("runtime_profile")
    profile = runtime_profile if isinstance(runtime_profile, dict) else {}
    if not profile.get("allowed_run_modes") and not execution_config.get(
        "allowed_run_modes"
    ):
        profile = {
            **profile,
            "allowed_run_modes": ["single_bot", "coop_group"],
        }
        execution_config["runtime_profile"] = profile
    execution_config["main_session_id"] = session_id
    if not a2a:
        # Both benchmark messages target the online runtime binding. Persist that
        # fact with the root node so liveness probing and harness resume use the
        # same runtime history that the user is inspecting.
        execution_config["lifecycle_stage"] = "online"
    task_request = replace(task_request, execution_config=execution_config)

    result = await service.execute(task_request)
    result_props = result.extend_props or {}
    if not result.success:
        result.extend_props = {**result_props, **extend_props}
        return envelope(op_result_to_dto(result), request)
    root_instruction = str(result_props.get("root_execution_instruction") or "")
    result_mode = str(result_props.get("orchestration_mode") or "")
    if result_mode == "relay" and not root_instruction.strip():
        result = replace(
            result,
            success=False,
            error="relay task creation returned no root execution instruction",
        )
        result.extend_props = result_props = {**result_props, **extend_props}
        return envelope(op_result_to_dto(result), request)

    launch_message = build_benchmark_task_launch_message(
        task_id=result.task_id,
        owner_user_id=task_request.owner_user_id,
        task_spec=body.task_spec.model_dump(),
        root_execution_instruction=root_instruction,
    )
    try:
        if a2a:
            launch_sent = await bcs_client.send_message_a2a(
                target_bot_id=str(a2a_target_bot),
                target_user_id=str(a2a_target_user),
                message=launch_message,
                session_id=session_id,
                caller_bearer=caller_token,
            )
        else:
            launch_sent = await bot_port.send_message(
                bot_id=bot_identity,
                message=launch_message,
                metadata={
                    "benchmark": True,
                    "session_id": session_id,
                    "sender_options": {"from": task_request.owner_user_id},
                    "bot_options": {"lifecycle_stage": "online"},
                },
            )
    except Exception as exc:  # noqa: BLE001
        result = replace(
            result,
            success=False,
            error=f"benchmark task launch send failed: {exc}",
        )
        result.extend_props = {
            **result_props,
            **extend_props,
            "benchmark_launch_send": f"error: {exc}",
        }
        return envelope(op_result_to_dto(result), request)

    if not a2a:
        # The second OpenAPI message is the actual root-execution run. Persist its
        # handle on the root node so Harness can probe the exact engine run and
        # resume the same session once the engine reaches a recoverable terminal
        # or unresponsive state.
        await service.update_task_node_info(
            result.task_id,
            result.node_id or result.task_id,
            extend_props_patch={"run_id": launch_sent.run_id},
        )
    result.extend_props = {
        **result_props,
        **extend_props,
        "benchmark_launch_run_id": launch_sent.run_id,
        "benchmark_launch_send": "ok",
    }
    return envelope(op_result_to_dto(result), request)
