"""任务最终结果回写:按 root 节点 session 把结果消息送回发起会话。

供外部系统在任务跑完后调用:不改任务/节点状态(状态由 poller/回调链负责),仅经
``OpenApiBotPort.send_message``(BaaS ``POST /openapi/v1/messages``,api-key 鉴权面)
以 ``metadata.session_id`` 续用 root 节点(``node_id == task_id``)派发时落库的会话,
把最终结果文本送回该会话。目标 bot 取 root 节点的 ``assignee``(+``assignee_owner_id``
复合),与 single_bot 派发同口径。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.task.schemas_result_writeback import (
    TaskResultWritebackRequestDTO,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.errors import InternalError
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
    OpenApiError,
)
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter()


@router.post("/result_writeback", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def writeback_task_result(
    body: TaskResultWritebackRequestDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    bot_port: OpenApiBotPort = Injected(OpenApiBotPort),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """回写最终结果到 root 节点会话。

    前置:root 节点已派发且 ``run_info.extend_props.session_id`` 已落库(如未派发
    → ``TaskStateError`` 409;task 不存在 → ``TaskNotFoundError`` 404)。
    ``OpenApiError``(BaaS 拒绝/网关失败)→ ``InternalError`` 500 信封,不静默成功。"""
    graph = service.get_task_dashboard(body.task_id)
    root = next((n for n in graph.tasks if n.node_id == body.task_id), None)
    if root is None:
        raise TaskStateError(f"task root node not found: task_id={body.task_id}")
    session_id = str(root.run_info.extend_props.get("session_id") or "").strip()
    assignee = str(root.run_info.assignee or "").strip()
    if not session_id or not assignee:
        raise TaskStateError(
            "result writeback requires a dispatched root node with a session "
            f"(task_id={body.task_id}, session_id={'set' if session_id else 'missing'}, "
            f"assignee={'set' if assignee else 'missing'})"
        )
    bot_identity = compose_bot_identity(
        assignee, root.run_info.extend_props.get("assignee_owner_id")
    )
    logger.info(
        "[task][result_writeback] >>> task=%s root_session=%s bot=%s msg_len=%s",
        body.task_id,
        session_id,
        bot_identity,
        len(body.message),
    )
    try:
        sent = await bot_port.send_message(
            bot_id=bot_identity,
            message=body.message,
            metadata={"session_id": session_id, "biz_task_id": body.task_id},
        )
    except OpenApiError as exc:
        logger.error(
            "[task][result_writeback] send failed task=%s session=%s bot=%s: %s",
            body.task_id,
            session_id,
            bot_identity,
            exc,
        )
        raise InternalError(f"result writeback send failed: {exc}") from exc
    return envelope(
        {
            "task_id": body.task_id,
            "session_id": session_id,
            "run_id": sent.run_id,
        },
        request,
    )