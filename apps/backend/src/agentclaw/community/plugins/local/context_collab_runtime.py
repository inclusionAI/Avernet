"""Deterministic local Context runtime used by tests and offline development."""

from __future__ import annotations

import itertools

from agentclaw.community.plugin_api.context_collab_runtime import (
    ContextCollabRuntimeError,
    ContextCollabRunRequest,
    ContextCollabRunResult,
    ContextCollabRunStatus,
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
        self.get_run_requests: list[str] = []
        self._runs: dict[str, ContextCollabRunStatus] = {}
        self._sequence = itertools.count(1)

    async def run(self, request: ContextCollabRunRequest) -> ContextCollabRunResult:
        self.requests.append(request)
        sequence = next(self._sequence)
        run_id = f"run_{sequence}"
        session_id = f"session_{sequence}"
        self._runs[run_id] = ContextCollabRunStatus(
            run_id=run_id,
            status="RUNNING",
            result=None,
            error=None,
            completed_at=None,
            session_id=session_id,
        )
        return ContextCollabRunResult(
            run_id=run_id,
            session_id=session_id,
        )

    async def get_run(self, run_id: str) -> ContextCollabRunStatus:
        self.get_run_requests.append(run_id)
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise ContextCollabRuntimeError(
                f"unknown local Context executor run: {run_id!r}"
            ) from exc

    def set_run_status_for_test(
        self,
        run_id: str,
        *,
        status: str,
        result: object | None = None,
        error: object | None = None,
        completed_at: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Control a recorded run deterministically in tests and local simulations."""
        previous = self._runs.get(run_id)
        if previous is None:
            raise ContextCollabRuntimeError(
                f"unknown local Context executor run: {run_id!r}"
            )
        self._runs[run_id] = ContextCollabRunStatus(
            run_id=run_id,
            status=status.strip().upper(),
            result=result,
            error=error,
            completed_at=completed_at,
            session_id=(previous.session_id if session_id is None else session_id),
        )
