"""_DoubleOpenApiBot:进程内模拟 grant/send/poll(不经网络)。"""
from __future__ import annotations

import uuid
from typing import Any

from agentclaw.community.core.task.task_runner.client.ports import BotSendResult


class _DoubleOpenApiBot:
    """进程内模拟 grant/send/poll(不经网络)。

    支持 ``poll_once_then_terminal``(send_message 返 RUNNING,首次 get_run 仍 RUNNING,第 N 次翻 final_status)
    以便 TaskExecutorResultPoller 真实轮询路径端到端可验;默认 ``poll_once_then_terminal=False``
    送(立即终态,兼容旧行为)。
    """

    def __init__(self, *, final_status: str = "COMPLETED", content: Any = None,
                 error: str | None = None, poll_once_then_terminal: bool = False,
                 terminal_after: int = 1) -> None:
        self._final = final_status
        self._content = content
        self._error = error
        self._poll_mode = poll_once_then_terminal
        self._terminal_after = terminal_after
        self._runs: dict[str, dict] = {}
        self._poll_counts: dict[str, int] = {}
        self.cancelled: list[str] = []

    async def ensure_grant(self, bot_id: str) -> None:
        return None

    async def send_message(self, *, bot_id: str, message: str, metadata: dict) -> BotSendResult:
        rid = f"mid_{uuid.uuid4().hex[:8]}"
        init_status = "RUNNING" if self._poll_mode else self._final
        self._runs[rid] = {"status": init_status, "result": {"content": self._content}, "error": self._error}
        if self._poll_mode:
            self._poll_counts[rid] = 0
        return BotSendResult(run_id=rid, session_id=None)

    async def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id, {"status": "RUNNING"})
        if not self._poll_mode:
            return run
        # poll-to-terminal:累计查询次数达阈值后翻终态
        self._poll_counts[run_id] = self._poll_counts.get(run_id, 0) + 1
        if self._poll_counts[run_id] >= self._terminal_after:
            run["status"] = self._final
            run["result"] = {"content": self._content}
            run["error"] = self._error
        return run

    async def cancel_run(self, run_id: str) -> None:
        self.cancelled.append(run_id)
        self._runs[run_id] = {"status": "FAILED", "error": "cancelled"}
