"""Deterministic local Context runtime used by tests and offline development."""

from __future__ import annotations

import itertools

from agentclaw.community.plugin_api.context_collab_runtime import (
    ContextCollabRunRequest,
    ContextCollabRunResult,
    ContextCollabRuntimePlugin,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.SIMULATOR,
    rationale="record Context run requests without reaching an external Bot",
)
class LocalContextCollabRuntimePlugin(ContextCollabRuntimePlugin):
    """Recording runtime whose generated handles are stable and observable."""

    def __init__(self) -> None:
        self.requests: list[ContextCollabRunRequest] = []
        self._sequence = itertools.count(1)

    async def run(
        self, request: ContextCollabRunRequest
    ) -> ContextCollabRunResult:
        self.requests.append(request)
        sequence = next(self._sequence)
        return ContextCollabRunResult(
            run_id=f"run_{sequence}",
            session_id=f"session_{sequence}",
        )
