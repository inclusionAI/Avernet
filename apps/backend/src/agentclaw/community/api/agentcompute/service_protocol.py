from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentcompute.domain.models import (
    RequestMetadata, Run, RunPage,
)


@dataclass(frozen=True)
class SubmitRunRequest:
    tenant: str
    goal: str
    agents: tuple
    provider: str = "default"
    model: str | None = None
    request_metadata: RequestMetadata = RequestMetadata()
    idempotency_key: str | None = None


@runtime_checkable
class AgentComputeServiceProtocol(Protocol):

    async def submit_async(self, req: SubmitRunRequest) -> str: ...
    async def submit_sync(self, req: SubmitRunRequest) -> Run: ...
    def get_run(self, run_id: str, tenant: str) -> Run | None: ...
    def list_runs(self, tenant: str, status: str | None = None,
                  *, page: int = 1, page_size: int = 20) -> RunPage: ...
    async def cancel(self, run_id: str, tenant: str) -> str: ...