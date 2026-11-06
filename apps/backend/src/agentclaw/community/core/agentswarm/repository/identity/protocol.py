"""A run's AgentPass, persisted so a restart resumes instead of re-minting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class SwarmIdentityRecord:
    """One run's identity, as stored."""

    run_id: str
    swarm_bot_id: str
    agent_code: str
    status: str
    mcp_codes: tuple[str, ...]
    cli_items: tuple[Mapping[str, Any], ...]
    worker_count: int
    owner_id: str


@runtime_checkable
class SwarmIdentityRepositoryProtocol(Protocol):
    """Read and write the run -> identity mapping.

    ``get`` is the resume path: a restarted process asks for the run's identity
    before applying for one, so an already-issued AgentPass is reused rather than
    re-applied. It answers ``None`` for an unknown run, never an error.
    """

    def get(self, run_id: str) -> SwarmIdentityRecord | None: ...
    def put(self, record: SwarmIdentityRecord) -> SwarmIdentityRecord: ...
    def update_scope(
        self,
        run_id: str,
        *,
        mcp_codes: Sequence[str],
        cli_items: Sequence[Mapping[str, Any]],
    ) -> bool: ...
    def increment_workers(self, run_id: str) -> bool: ...
    def list_stale(
        self, *, cutoff: Any, limit: int | None = None
    ) -> list[SwarmIdentityRecord]: ...
    def mark_destroyed(self, run_id: str) -> bool: ...