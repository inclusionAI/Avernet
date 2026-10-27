"""Same-session Relay harness recovery.

Distributed Relay retries do not own node preparation or dispatch.  This module
only continues the already-created Bot conversation so the executor can finish
its task-loop S1--S6 steps.  It intentionally keeps task id, node id, relay
token, runtime mode, assignee and session identity unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.models import TaskNode

logger = logging.getLogger("task.harness.relay")


RELAY_HARNESS_RESUME_PROMPT = (
    "继续执行当前未完成的 task-loop S1~S6。不要新建任务、不要重新澄清、"
    "不要重新提交 execute。如果当前步骤无法完成，请输出明确失败原因，"
    "并继续执行后续步骤。"
)


def relay_harness_resume_prompt(resume_step: str | None = None) -> str:
    """Build one step-aware same-session continuation prompt.

    ``resume_step`` is Harness-local recovery metadata.  It is not persisted and
    does not add a new task-loop protocol step or report event.
    """
    if resume_step == "S6":
        return (
            "继续执行当前未完成的 task-loop S6。"
            "不要新建任务、不要重新澄清、不要重新提交 execute、"
            "不要重做 S1~S5；请直接依据已接纳的 PLAN_RESULT 完成本棒收口、"
            "等待或交接。如果当前步骤无法完成，请输出明确失败原因，"
            "并继续执行后续步骤。"
        )
    if resume_step == "S5":
        return (
            "继续执行当前未完成的 task-loop S5。"
            "不要新建任务、不要重新澄清、不要重新提交 execute、"
            "不要重做 S1~S4；请基于已接纳的 EXECUTION_RESULT 刷新剩余缺口，"
            "并完成下一棒规划、搜索、执行者决策和 PLAN_RESULT 上报。"
            "如果当前步骤无法完成，请输出明确失败原因，"
            "并继续执行后续步骤。"
        )
    if resume_step == "S1-S4a":
        return (
            "继续执行当前未完成的 task-loop S1~S4a。"
            "不要新建任务、不要重新澄清、不要重新提交 execute；"
            "先重新读取 S1 权威上下文，再从第一个未完成步骤继续，"
            "直到完成 S4b 上报。如果当前步骤无法完成，"
            "请输出明确失败原因，并继续执行后续步骤。"
        )
    return RELAY_HARNESS_RESUME_PROMPT


class RelaySessionRecoveryError(RuntimeError):
    """Raised when an existing Relay execution session cannot be continued."""


@dataclass(frozen=True)
class RelayResumeResult:
    channel: str
    session_id: str
    dispatch_handle: str | None = None


class RelaySessionRecovery:
    """Continue one Relay baton in its original execution conversation.

    ``bot`` covers OpenAPI/BaaS sessions; ``bcs`` covers existing BCS group
    sessions through the participant-scoped session chat endpoint.  The optional
    BCS token provider is used only as the authenticated participant identity;
    it never authorizes a new group or dispatch.
    """

    def __init__(self, *, bot: Any = None, bcs: Any = None, bot_tokens: Any = None):
        self._bot = bot
        self._bcs = bcs
        self._bot_tokens = bot_tokens

    async def resume(
        self, node: TaskNode, *, resume_step: str | None = None
    ) -> RelayResumeResult:
        props = getattr(node.run_info, "extend_props", {}) or {}
        group_id = str(props.get("group_id") or "").strip()
        session_id = str(props.get("session_id") or "").strip()
        if group_id:
            if self._bcs is None:
                raise RelaySessionRecoveryError(
                    "relay resume has no BCS port for the existing group session"
                )
            if not session_id:
                raise RelaySessionRecoveryError(
                    "relay group node has no existing session_id to resume"
                )
            caller_bearer = await self._resolve_group_bearer(group_id, node)
            result = await self._bcs.send_session_chat(
                session_id,
                message=relay_harness_resume_prompt(resume_step),
                caller_bearer=caller_bearer,
            )
            handle = str(
                result.get("state_machine_run_id")
                or result.get("run_id")
                or result.get("session_id")
                or ""
            )
            logger.info(
                "[task][harness][relay-resume] session_chat sent task=%s node=%s "
                "group=%s session=%s delivered=%s",
                node.task_id,
                node.node_id,
                group_id,
                session_id,
                result.get("delivered"),
            )
            return RelayResumeResult(
                channel="bcs_session",
                session_id=session_id,
                dispatch_handle=handle or None,
            )

        if not session_id:
            raise RelaySessionRecoveryError(
                "relay node has no existing session_id to resume"
            )
        if self._bot is None:
            raise RelaySessionRecoveryError(
                "relay resume has no OpenAPI Bot port for the existing session"
            )
        assignee = str(getattr(node.run_info, "assignee", "") or "")
        if not assignee:
            raise RelaySessionRecoveryError(
                "relay node has no assignee for the existing session"
            )
        lifecycle_stage = str(props.get("lifecycle_stage") or "").strip()
        metadata: dict[str, Any] = {"session_id": session_id}
        if lifecycle_stage:
            metadata["bot_options"] = {"lifecycle_stage": lifecycle_stage}

        sent = await self._bot.send_message(
            bot_id=compose_bot_identity(assignee, props.get("assignee_owner_id")),
            message=relay_harness_resume_prompt(resume_step),
            metadata=metadata,
        )
        logger.info(
            "[task][harness][relay-resume] openapi message sent task=%s node=%s "
            "assignee=%s session=%s run=%s",
            node.task_id,
            node.node_id,
            assignee,
            session_id,
            sent.run_id,
        )
        return RelayResumeResult(
            channel="openapi_bot_session",
            session_id=session_id,
            dispatch_handle=sent.run_id,
        )

    async def _resolve_group_bearer(
        self, group_id: str, node: TaskNode
    ) -> str | None:
        """Resolve an existing participant's BCS token without mutating identity.

        Prefer the persisted driver/manager fact, then the authoritative group's
        current driver.  No value means the transport will fail closed; the
        harness surfaces the failure instead of creating a replacement session.
        """
        props = getattr(node.run_info, "extend_props", {}) or {}
        candidate = str(
            props.get("bcs_driver_bot")
            or props.get("assignee_bcs_uuid")
            or getattr(node.run_info, "assignee", "")
            or ""
        ).strip()
        if candidate in ("", group_id):
            # The group detail carries the authoritative driver/manager bot.
            getter = getattr(self._bcs, "get_group", None)
            if getter is not None:
                group = await getter(group_id)
                if isinstance(group, dict):
                    candidate = str(
                        group.get("driver_bot")
                        or group.get("driver_bot_id")
                        or ""
                    ).strip()
        if not candidate or self._bot_tokens is None:
            return None
        token = self._bot_tokens.get_token(candidate)
        return str(token or "") or None
