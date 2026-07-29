"""BCS collaboration httpx client (Phase 4.2a, plan §2.4).

Implements :class:`BcsCollaborationProtocol` against the local open-source BCS
(state-machine runs). Read-only query face — performs NO writes, holds NO state,
so the cooperative group's self-loop invariant (no per-child tracking) stays
intact. The task graph stores only the ``SubDagRef`` pointer; this client feeds
the live snapshot to :class:`SmGraphAdapter` at drill-down render time.

Endpoints (local open-source BCS):
- ``GET /state-machine-runs/{run_id}/graph`` → ``StateMachineRunGraphView``
- ``GET /state-machine-runs/{run_id}/nodes/{node_id}`` → ``NodeRunView`` detail

A real BCS base URL is wired via ``BcsCollaborationHttpxModule`` (overrides the
Noop default) when the deployment front-ends a local BCS. Prod BCS wiring is a
corp/transport task (TODO Phase 6).
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from agentclaw.community.core.task.protocols import BcsCollaborationProtocol
from agentclaw.community.log import get_logger

logger = get_logger()

_DEFAULT_TIMEOUT = 5.0


class BcsCollaborationClient(BcsCollaborationProtocol):
    """httpx-backed read-only BCS state-machine run graph query client."""

    def __init__(
        self,
        base_url: str,
        timeout: float = _DEFAULT_TIMEOUT,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client if client is not None else httpx.Client(
            base_url=self._base_url, timeout=timeout
        )

    def fetch_state_machine_run_graph(self, bcs_run_id: str) -> Any:
        return self._get(f"/state-machine-runs/{bcs_run_id}/graph")

    def fetch_node_detail(self, bcs_run_id: str, node_id: str) -> Any:
        return self._get(f"/state-machine-runs/{bcs_run_id}/nodes/{node_id}")

    def _get(self, path: str) -> Any:
        resp = self._client.get(path)
        if resp.status_code == 404:
            logger.info("[BcsCollaborationClient] 404 path=%s", path)
            return {}
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()


class BcsCollaborationHttpxModule:
    """Optional override: bind :class:`BcsCollaborationProtocol` to the httpx
    client when a local BCS base URL is configured. Installed alongside
    :class:`CommunityTaskModule` in profiles that front a real BCS.

    Kept as a plain class with a ``configure`` helper (not an injector Module)
    so the default community profile stays Noop-free of httpx at import time.
    """

    def __init__(self, base_url: str, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._base_url = base_url
        self._timeout = timeout

    def make_client(self) -> BcsCollaborationClient:
        return BcsCollaborationClient(self._base_url, self._timeout)


__all__ = ["BcsCollaborationClient", "BcsCollaborationHttpxModule"]