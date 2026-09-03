"""_DoubleBcsClient:三态进程内模拟(create_group→session/run poll→终态)。"""
from __future__ import annotations

import uuid
from typing import Any

from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsCreateGroupRequest, BcsCreateGroupResult,
)


class _DoubleBcsClient:
    """三态进程内模拟(create_group→session/run poll→终态)。

    支持 ``poll_once_then_terminal``(get_group/start_..run 返 in-progress,首次仍 in-progress,
    第 N 次翻终态)以便 TaskExecutorResultPoller 真实轮询路径端到端可验;默认 False 立即终态(兼容旧行为)。
    """

    def __init__(self, *, session_status: str = "completed", session_output: Any = None,
                 sm_status: str = "completed", sm_output: Any = None,
                 poll_once_then_terminal: bool = False, terminal_after: int = 1) -> None:
        self._session_status = session_status
        self._session_output = session_output
        self._sm_status = sm_status
        self._sm_output = sm_output
        self._poll_mode = poll_once_then_terminal
        self._terminal_after = terminal_after
        self._grp_poll: dict[str, int] = {}
        self._sm_poll: dict[str, int] = {}

    async def create_group(self, req: BcsCreateGroupRequest) -> BcsCreateGroupResult:
        gid = f"grp_{uuid.uuid4().hex[:8]}"
        dref = {"id": f"d_{gid[:4]}", "version": 1} if req.group_strategy == "state_machine" else None
        return BcsCreateGroupResult(group_id=gid, definition_ref=dref)

    def task_callback_url(self) -> str:
        # Double 不接真 BCS,无 corp 注入;返空让 TaskExecutor 走 api_base_url 兜底。
        return ""

    async def create_session(self, group_id: str, *, bootstrap_prompt: str | None = None,
                             idempotency_key: str | None = None) -> str:
        return f"s_{uuid.uuid4().hex[:6]}"

    async def get_group(self, group_id: str) -> dict[str, Any]:
        status, output = self._resolve(self._session_status, self._session_output, self._grp_poll, group_id)
        return {"session": {"status": status, "output": output, "error_message": None}}

    async def get_session_messages(self, session_id: str, *, limit: int = 50,
                                   since_msg_id: str | None = None) -> list[Any]:
        return []

    async def start_state_machine_run(self, group_id, *, definition_yaml, definition_ref,
                                      session_id, input) -> str:
        return f"run_{uuid.uuid4().hex[:6]}"

    async def get_state_machine_run(self, run_id: str) -> dict[str, Any]:
        status, output = self._resolve(self._sm_status, self._sm_output, self._sm_poll, run_id)
        return {"status": status, "output": output, "error": None}

    async def validate_definition(self, definition_yaml: str) -> None:
        return None

    def _resolve(self, final_status: str, final_output: Any, counts: dict[str, int], key: str) -> tuple[str, Any]:
        if not self._poll_mode:
            return final_status, final_output
        counts[key] = counts.get(key, 0) + 1
        if counts[key] >= self._terminal_after:
            return final_status, final_output
        return ("in_progress" if final_status == "completed" else final_status), None
