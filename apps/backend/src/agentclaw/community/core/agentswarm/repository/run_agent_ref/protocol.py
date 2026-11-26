from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentswarm.domain.models import (
    RunAgentRef, RunId, TenantId,
)


@runtime_checkable
class RunAgentRefRepositoryProtocol(Protocol):

    def replace_for_run(self, run_id: RunId, tenant: TenantId,
                        refs: list[RunAgentRef]) -> None: ...

    def get_for_run(self, run_id: RunId,
                    tenant: TenantId) -> dict[str, RunAgentRef]: ...
