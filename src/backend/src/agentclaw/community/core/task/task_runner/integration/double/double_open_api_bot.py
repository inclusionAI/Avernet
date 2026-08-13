"""_DoubleOpenApiBot:进程内模拟 grant/send/poll(不经网络)。"""
from __future__ import annotations

import uuid
from typing import Any


class _DoubleOpenApiBot:
    def __init__(self, *, final_status: str = "COMPLETED", content: Any = None,
                 error: str | None = None) -> None:
        self._final = final_status
        self._content = content
        self._error = error
        self._runs: dict[str, dict] = {}

    async def ensure_grant(self, bot_id: str) -> None:
        return None

    async def send_message(self, *, bot_id: str, message: str, metadata: dict) -> str:
        rid = f"mid_{uuid.uuid4().hex[:8]}"
        self._runs[rid] = {"status": self._final, "result": {"content": self._content}, "error": self._error}
        return rid

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return self._runs.get(run_id, {"status": "RUNNING"})
