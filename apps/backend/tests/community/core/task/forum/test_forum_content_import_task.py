from __future__ import annotations

import pytest

from agentclaw.community.core.task.forum.content_import.service import (
    BbsContentImportTaskService,
)
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_CONTENT_IMPORT_TASK_NAME,
    BbsSandboxTask,
)


class _FakeLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def summarize(self, prompt: str, *, max_tokens: int) -> str:
        self.calls.append((prompt, max_tokens))
        return "probe healthy"

    async def generate_tags(self, prompt: str, *, max_tokens: int) -> str:
        raise AssertionError("content import must not generate tags")


@pytest.mark.asyncio
async def test_content_import_task_uses_service_llm_budget():
    llm = _FakeLLM()
    service = BbsContentImportTaskService(llm=llm)

    result = await service.execute_once(
        BbsSandboxTask(name=BBS_CONTENT_IMPORT_TASK_NAME)
    )

    assert result == {
        "task_name": BBS_CONTENT_IMPORT_TASK_NAME,
        "success": True,
        "summary": "probe healthy",
    }
    assert len(llm.calls) == 1
    assert llm.calls[0][1] == BbsContentImportTaskService._LLM_MAX_TOKENS


@pytest.mark.asyncio
async def test_content_import_task_rejects_unknown_task():
    service = BbsContentImportTaskService(llm=_FakeLLM())

    with pytest.raises(ValueError, match="unknown BBS content import task"):
        await service.execute_once(BbsSandboxTask(name="unknown_task"))
