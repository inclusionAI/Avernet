"""Deterministic Context executor fakes shared by ContextCollab tests."""

from __future__ import annotations

import itertools

from agentclaw.community.core.task.context_collab.executor import (
    ContextCollabExecutorError,
    ContextCollabResumeRequest,
    ContextCollabRunRequest,
    ContextCollabRunResult,
    ContextCollabRunStatus,
)


class RecordingContextCollabExecutor:
    """Record requests and expose deterministic direct-run handles."""

    def __init__(self) -> None:
        self.requests: list[ContextCollabRunRequest] = []
        self.get_run_requests: list[str] = []
        self._runs: dict[str, ContextCollabRunStatus] = {}
        self.resumed_requests: list[ContextCollabResumeRequest] = []
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

    async def resume(
        self, request: ContextCollabResumeRequest
    ) -> ContextCollabRunResult:
        self.resumed_requests.append(request)
        sequence = next(self._sequence)
        run_id = f"run_{sequence}"
        self._runs[run_id] = ContextCollabRunStatus(
            run_id=run_id,
            status="RUNNING",
            result=None,
            error=None,
            completed_at=None,
            session_id=request.session_id,
        )
        return ContextCollabRunResult(
            run_id=run_id,
            session_id=request.session_id,
        )

    async def get_run(
        self,
        run_id: str | None = None,
        *,
        group_id: str | None = None,
        session_id: str | None = None,
    ) -> ContextCollabRunStatus:
        del session_id
        if group_id is not None or run_id is None:
            raise ContextCollabExecutorError(
                "recording Context executor supports direct run_id lookup only"
            )
        self.get_run_requests.append(run_id)
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise ContextCollabExecutorError(
                f"unknown recording Context executor run: {run_id!r}"
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
        previous = self._runs.get(run_id)
        if previous is None:
            raise ContextCollabExecutorError(
                f"unknown recording Context executor run: {run_id!r}"
            )
        self._runs[run_id] = ContextCollabRunStatus(
            run_id=run_id,
            status=status.strip().upper(),
            result=result,
            error=error,
            completed_at=completed_at,
            session_id=(previous.session_id if session_id is None else session_id),
        )
