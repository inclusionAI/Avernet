"""Internal auto execute endpoint (silent auto-execution in an existing session)."""

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
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
    build_benchmark_task_launch_message,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/auto_execute", tags=["task"])


@router.post("", response_model=Envelope[TaskOpResultDTO])
@envelope_errors
async def auto_execute_task_internal(
    body: TaskInfoRequestDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    bot_port: OpenApiBotPort = Injected(OpenApiBotPort),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    """静默自动执行(已有会话,不发 prompt、不走 A2A)。

    评测/外部系统已准备好 bot 会话;端点跳过 ``benchmark_execute`` 的第一棒 prompt
    发送,直接 ``service.execute`` 创建根节点,取 Backend 生成的
    ``root_execution_instruction``,把副屏卡片 + 隐藏 Relay 根指令通过 OpenAPI
    ``bot_port`` 发回入参 ``main_session_id`` 指定的同一会话,使任务不开新会话即
    可自动启动。
    """
    main_session_id = str(body.execution_config.main_session_id or "").strip()
    extend_props: dict[str, Any] = {
        "auto_session_id": main_session_id,
    }
    if not main_session_id:
        extend_props["auto_launch_send"] = "error: missing session_id"
        logger.warning(
            "[task][auto_execute] reject: missing main_session_id "
            "owner_bot_id=%s owner_user_id=%s source_type=%s",
            body.owner_bot_id,
            body.owner_user_id,
            body.source_type,
        )
        return envelope(
            op_result_to_dto(
                TaskOpResult(
                    task_id="",
                    success=False,
                    run_id=0,
                    error="auto_execute requires main_session_id",
                    extend_props=extend_props,
                )
            ),
            request,
        )

    logger.info(
        "[task][auto_execute] start: owner_bot_id=%s owner_user_id=%s "
        "main_session_id=%s source_type=%s task_type=%s lifecycle_stage=%s",
        body.owner_bot_id,
        body.owner_user_id,
        main_session_id,
        body.source_type,
        body.execution_config.task_type,
        body.execution_config.model_dump(exclude_none=True).get("lifecycle_stage"),
    )

    task_request = task_info_request_from_dto(body)
    execution_config = dict(task_request.execution_config)
    execution_config["main_session_id"] = main_session_id
    execution_config.setdefault("lifecycle_stage", "draft")
    task_request = replace(task_request, execution_config=execution_config)

    result = await service.execute(task_request)
    result_props = result.extend_props or {}
    if not result.success:
        result.extend_props = {
            **result_props,
            **extend_props,
            "auto_launch_send": "skipped: execute failed",
        }
        logger.warning(
            "[task][auto_execute] execute failed: task_id=%s error=%s",
            result.task_id,
            result.error,
        )
        return envelope(op_result_to_dto(result), request)

    # ---- Step 1: 取 root_execution_instruction ----
    root_instruction = str(result_props.get("root_execution_instruction") or "")
    result_mode = str(result_props.get("orchestration_mode") or "")
    logger.info(
        "[task][auto_execute] execute ok: task_id=%s node_id=%s run_id=%s "
        "orchestration_mode=%s root_instruction_len=%s",
        result.task_id,
        result.node_id,
        result.run_id,
        result_mode,
        len(root_instruction),
    )
    if result_mode == "relay" and not root_instruction.strip():
        result = replace(
            result,
            success=False,
            error="relay task creation returned no root execution instruction",
        )
        result.extend_props = result_props = {
            **result_props,
            **extend_props,
            "auto_launch_send": "error: no root execution instruction",
        }
        logger.warning(
            "[task][auto_execute] reject relay instruction empty: task_id=%s",
            result.task_id,
        )
        return envelope(op_result_to_dto(result), request)

    if bot_port is None:
        result = replace(
            result,
            success=False,
            error="auto task launch send failed: bot port is unavailable",
        )
        result.extend_props = result_props = {
            **result_props,
            **extend_props,
            "auto_launch_send": "error: bot port unavailable",
        }
        logger.warning(
            "[task][auto_execute] reject bot_port unavailable: task_id=%s",
            result.task_id,
        )
        return envelope(op_result_to_dto(result), request)

    # ---- Step 2: 拼副屏卡片 + 隐藏 Relay 根指令 ----
    bot_identity = compose_bot_identity(body.owner_bot_id, body.owner_user_id)
    launch_message = build_benchmark_task_launch_message(
        task_id=result.task_id,
        owner_user_id=task_request.owner_user_id,
        task_spec=body.task_spec.model_dump(),
        root_execution_instruction=root_instruction,
    )
    logger.info(
        "[task][auto_execute] launch_message built: task_id=%s bot_identity=%s "
        "msg_len=%s has_hidden_instruction=%s",
        result.task_id,
        bot_identity,
        len(launch_message),
        bool(root_instruction.strip()),
    )

    # ---- Step 3: bot_port.send_message 发到入参 main_session_id 会话 ----
    bot_options = {
        "lifecycle_stage": task_request.execution_config.get("lifecycle_stage", "draft"),
    }
    metadata = {
        "benchmark": False,
        "auto": True,
        "session_id": main_session_id,
        "sender_options": {"from": task_request.owner_user_id},
        "bot_options": bot_options,
    }
    logger.info(
        "[task][auto_execute] send_message >>>: bot_id=%s session_id=%s "
        "sender_options_from=%s bot_options=%s",
        bot_identity,
        main_session_id,
        task_request.owner_user_id,
        bot_options,
    )
    try:
        launch_sent = await bot_port.send_message(
            bot_id=bot_identity,
            message=launch_message,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        result = replace(
            result,
            success=False,
            error=f"auto task launch send failed: {exc}",
        )
        result.extend_props = result_props = {
            **result_props,
            **extend_props,
            "auto_launch_send": f"error: {exc}",
        }
        logger.warning(
            "[task][auto_execute] send_message failed: task_id=%s bot_id=%s "
            "session_id=%s error=%s",
            result.task_id,
            bot_identity,
            main_session_id,
            exc,
        )
        return envelope(op_result_to_dto(result), request)

    logger.info(
        "[task][auto_execute] send_message <<<: task_id=%s run_id=%s "
        "runtime_session_id=%s",
        result.task_id,
        launch_sent.run_id,
        launch_sent.session_id,
    )

    # ---- Step 4: 把第二棒 run_id 回写到根节点 ----
    node_id = result.node_id or result.task_id
    await service.update_task_node_info(
        result.task_id,
        node_id,
        extend_props_patch={"run_id": launch_sent.run_id},
    )
    logger.info(
        "[task][auto_execute] update_task_node_info ok: task_id=%s node_id=%s "
        "run_id_patch=%s",
        result.task_id,
        node_id,
        launch_sent.run_id,
    )

    result.extend_props = {
        **result_props,
        **extend_props,
        "auto_run_id": launch_sent.run_id,
        "auto_launch_send": "ok",
    }
    logger.info(
        "[task][auto_execute] done: task_id=%s node_id=%s auto_run_id=%s "
        "auto_session_id=%s auto_launch_send=ok",
        result.task_id,
        node_id,
        launch_sent.run_id,
        main_session_id,
    )
    return envelope(op_result_to_dto(result), request)
