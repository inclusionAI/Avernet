from __future__ import annotations

import pytest

from agentclaw.community.core.task.forum.digest.service import BbsDigestTaskService
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_DIGEST_TASK_NAME,
    BbsSandboxTask,
)


class _FakeLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def summarize(self, prompt: str, *, max_tokens: int) -> str:
        self.calls.append((prompt, max_tokens))
        return "probe healthy"

    async def generate_tags(self, prompt: str, *, max_tokens: int) -> str:
        self.calls.append((prompt, max_tokens))
        return "[]"

@pytest.mark.asyncio
async def test_digest_task_runs_summary_probe_only():
    llm = _FakeLLM()
    service = BbsDigestTaskService(llm=llm)

    result = await service.execute_once(BbsSandboxTask(name=BBS_DIGEST_TASK_NAME))

    assert llm.calls[0][1] == BbsDigestTaskService._LLM_MAX_TOKENS
    assert result["summary"] == "probe healthy"
    assert result["success"] is True
