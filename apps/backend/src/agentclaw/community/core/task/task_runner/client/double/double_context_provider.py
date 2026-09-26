"""singlebox double:静态凭据 + canned context + 收集 sink。"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.models import TaskCallbackData


class _DoubleApiKeyProvider:
    api_key = "ak_double_1234"
    api_key_prefix = "ak_doubl"
    base_url = "http://localhost:8890"
    cookie = ""
    referer = ""


class _DoubleContextProvider:
    def build(self, task_id: str, node_id: str) -> dict[str, Any]:
        return {"mode": "execute"}


class _DoubleSink:
    def __init__(self) -> None:
        self.reports: list[TaskCallbackData] = []

    async def report_result(self, data: TaskCallbackData) -> None:
        self.reports.append(data)
