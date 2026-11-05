"""Incomplete evaluation inputs must not start or mutate a workflow."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.quality.errors import EvaluationInputIncompleteError
from agentclaw.community.core.quality.services.task_processor import TaskProcessor


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["init", "env_preparing", "env_ready"])
@pytest.mark.parametrize("ext", [{}, {"set_uuid": "suite"}, {"version": 1},
                                    {"set_uuid": [], "version": {}}])
async def test_incomplete_evaluation_has_no_side_effects(status, ext):
    repo, http, publish, tracer = (MagicMock() for _ in range(4))
    repo.get_by_id.return_value = SimpleNamespace(
        id=66, status=status, task_type="eval", biz_type="service_bot_single",
        bot_id="bot", ext=ext,
    )
    processor = TaskProcessor(repo, http, publish, tracer)
    for _ in range(2):
        with pytest.raises(EvaluationInputIncompleteError):
            await processor.process(66)
    repo.update_ext.assert_not_called()
    repo.update_status.assert_not_called()
    publish.assert_not_called()
    assert publish.mock_calls == []
    assert http.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_type", "biz_type", "status", "ext", "method"),
    [
        ("stress_test", "multi_bot", "init", {}, "to_env_preparing"),
        ("eval", "service_bot_single", "env_ready", {"set_task_uuid": "started"}, "to_task_created"),
        ("eval", "service_bot_single", "init", {"set_uuid": "set", "version": 1, "task_purpose": "environment_publish"}, "to_env_preparing"),
        ("eval", "service_bot_single", "task_executed", {}, "to_env_released"),
    ],
)
async def test_other_or_ready_workflows_keep_routing(task_type, biz_type, status, ext, method):
    repo = MagicMock()
    repo.get_by_id.return_value = SimpleNamespace(
        id=1, status=status, task_type=task_type, biz_type=biz_type, bot_id="bot", ext=ext,
    )
    processor = TaskProcessor(repo, MagicMock(), MagicMock(), MagicMock())
    replacement = AsyncMock(return_value="continued") if method == "to_env_preparing" else MagicMock(return_value="continued")
    setattr(processor, method, replacement)
    assert await processor.process(1) == "continued"
    replacement.assert_called_once()
