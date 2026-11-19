"""Authenticated worker completion ingress; evidence is mandatory, not telemetry.

The implementation validates allocation/lease/actor and artifact acceptance before
advancing the authoritative Stage and its L4 child Task through G4 machines.
A report from another worker or a repeated report is rejected without advancement.
"""

from dataclasses import dataclass
from typing import Protocol

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)


@dataclass(frozen=True)
class StageExecutionReport:
    collet_id: str
    binding_id: str
    stage_ref: str
    allocation_ref: str
    artifact_refs: tuple[str, ...]


class StageReportProtocol(Protocol):
    def report(self, report: StageExecutionReport, actor: ActorContext) -> None: ...
