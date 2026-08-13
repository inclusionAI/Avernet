"""_DoubleBcsClient:三态进程内模拟(create_group→session/run poll→终态)。"""
from __future__ import annotations

import uuid
from typing import Any

from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BcsCreateGroupRequest, BcsCreateGroupResult,
)


class _DoubleBcsClient:
    def __init__(self, *, session_status: str = "completed", session_output: Any = None,
                 sm_status: str = "completed", sm_output: Any = None) -> None:
        self._session_status = session_status
        self._session_output = session_output
        self._sm_status = sm_status
        self._sm_output = sm_output

    async def create_group(self, req: BcsCreateGroupRequest) -> BcsCreateGroupResult:
        gid = f"grp_{uuid.uuid4().hex[:8]}"
        dref = {"id": f"d_{gid[:4]}", "version": 1} if req.group_strategy == "state_machine" else None
        return BcsCreateGroupResult(group_id=gid, definition_ref=dref)

    async def create_session(self, group_id: str, *, bootstrap_prompt: str | None = None,
                             idempotency_key: str | None = None) -> str:
        return f"s_{uuid.uuid4().hex[:6]}"

    async def get_group(self, group_id: str) -> dict[str, Any]:
        return {"session": {"status": self._session_status, "output": self._session_output,
                            "error_message": None}}

    async def get_session_messages(self, session_id: str, *, limit: int = 50,
                                   since_msg_id: str | None = None) -> list[Any]:
        return []

    async def start_state_machine_run(self, group_id, *, definition_yaml, definition_ref,
                                      session_id, input) -> str:
        return f"run_{uuid.uuid4().hex[:6]}"

    async def get_state_machine_run(self, run_id: str) -> dict[str, Any]:
        return {"status": self._sm_status, "output": self._sm_output, "error": None}

    async def validate_definition(self, definition_yaml: str) -> None:
        return None
