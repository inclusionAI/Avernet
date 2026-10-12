from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.models import NodeAction
from agentclaw.community.core.task.task_center.task_service_relay import (
    TaskServiceRelayMixin,
)


class _Graph:
    def __init__(self) -> None:
        self.events = []

    def append_action_event(self, task_id, node_id, action, payload, **kwargs):
        self.events.append((task_id, node_id, action, payload, kwargs))


class _Search:
    async def search_catalog(self, query):
        return {
            "candidates": [
                {
                    "bot_uuid": "bot-1",
                    "bot_name": "Risk Bot",
                    "bot_desc": "risk analysis",
                    "recommend": {"score": "0.91"},
                }
            ],
            "raw_item_count": 3,
            "failed_keywords": ["legacy"],
        }


class _Service(TaskServiceRelayMixin):
    def __init__(self) -> None:
        self._graph = _Graph()
        self._relay_adapter = SimpleNamespace(search=_Search())


@pytest.mark.asyncio
async def test_search_bots_records_relay_search_trace() -> None:
    service = _Service()

    candidates = await service.search_bots(
        query="payment risk",
        task_id="task-1",
        node_id="node-1",
    )

    assert [candidate.bot_id for candidate in candidates] == ["bot-1"]
    task_id, node_id, action, payload, _ = service._graph.events[-1]
    assert (task_id, node_id, action) == ("task-1", "node-1", NodeAction.DISPATCH)
    assert payload["phase"] == "search"
    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["score"] == 0.91
