"""ExecutionPort community httpx impl (Phase 4.2, plan §2.4/§4.2).

Effect layer — actually launches executors. Methods:
- ``dispatch_single_bot`` → open-source engine programmatic dispatch (R6, TODO:
  the engine programmatic-dispatch endpoint is not yet in the open-source repo;
  client + Mock land here, real wiring blocked on R6).
- ``coop_group`` → open-source BCS service-ified group creation (B5, TODO).
- ``redispatch_node`` → BCS same-group redispatch (P8/B5).
- ``bbs`` → bbs_executor (Phase 5 stub; real广场 wiring lands Phase 5).

All return :class:`DispatchResult` (proto + accept_token the owner-bot SKILL
echoes back). The default community profile keeps :class:`NoopExecutionPort`
(scheduler tick stays side-effect-free in tests/local); this client is bound
only when real engine/BCS base URLs are configured.

Avernet rules: no bare SQL; adapter holds no domain logic; httpx only.
"""
from __future__ import annotations

import uuid
from typing import Optional

import httpx

from agentclaw.community.core.task.domain.models import RunMode
from agentclaw.community.core.task.protocols import DispatchResult, ExecutionPort
from agentclaw.community.log import get_logger

logger = get_logger()

_DEFAULT_TIMEOUT = 10.0


def _new_token() -> str:
    return "tok-" + uuid.uuid4().hex[:12]


class ExecutionPortClient(ExecutionPort):
    """httpx-backed ExecutionPort. Real engine/BCS endpoints are TODO (R6/B5)."""

    def __init__(
        self,
        engine_base_url: str,
        bcs_base_url: str,
        timeout: float = _DEFAULT_TIMEOUT,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._engine = engine_base_url.rstrip("/")
        self._bcs = bcs_base_url.rstrip("/")
        self._timeout = timeout
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    # --- single bot (engine R6) -------------------------------------------

    def dispatch_single_bot(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        # TODO(R6): engine programmatic dispatch + 回投 endpoint.
        logger.info("[ExecutionPortClient] dispatch_single_bot task=%s node=%s bot=%s", task_id, node_id, bot_id)
        self._post(
            f"{self._engine}/api/tasks/dispatch",
            {"task_id": task_id, "node_id": node_id, "bot_id": bot_id, "mode": "single_bot"},
        )
        return DispatchResult(
            node_id=node_id,
            executor_id=bot_id,
            run_mode=RunMode.SINGLE_BOT,
            accept_token=_new_token(),
        )

    # --- coop group (BCS B5) ----------------------------------------------

    def coop_group(self, task_id: str, node_id: str, bot_ids: list[str]) -> DispatchResult:
        # TODO(B5): BCS service-ified group create + TaskDispatch + 回投.
        body = {"task_id": task_id, "node_id": node_id, "bot_ids": bot_ids, "mode": "coop_group"}
        resp = self._post(f"{self._bcs}/api/coop-groups", body)
        run_id = (resp or {}).get("run_id") or ""
        return DispatchResult(
            node_id=node_id,
            executor_id="",
            run_mode=RunMode.COOP_GROUP,
            accept_token=_new_token(),
            dispatched_at=run_id,
        )

    def redispatch_node(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        # TODO(P8/B5): same-group redispatch.
        self._post(
            f"{self._bcs}/api/coop-groups/redispatch",
            {"task_id": task_id, "node_id": node_id, "bot_id": bot_id},
        )
        return DispatchResult(
            node_id=node_id,
            executor_id=bot_id,
            run_mode=RunMode.SINGLE_BOT,
            accept_token=_new_token(),
        )

    # --- probe (watchdog 6.5) ---------------------------------------------

    def probe(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        # TODO(R6): engine probe/status-report-request endpoint — ask the bot
        # to actively report its status (the bot may be hung). Fire-and-forget;
        # the bot posts NODE_ACCEPTED/NODE_FAILED on receipt.
        logger.info("[ExecutionPortClient] probe task=%s node=%s bot=%s", task_id, node_id, bot_id)
        self._post(
            f"{self._engine}/api/tasks/probe",
            {"task_id": task_id, "node_id": node_id, "bot_id": bot_id},
        )
        return DispatchResult(
            node_id=node_id,
            executor_id=bot_id,
            run_mode=RunMode.SINGLE_BOT,
            accept_token=_new_token(),
        )

    def bbs(self, task_id: str, node_id: str, reason: str = "") -> DispatchResult:
        # Phase 5: bbs_executor 广场 wiring.
        logger.info("[ExecutionPortClient] bbs dispatch task=%s node=%s reason=%s", task_id, node_id, reason)
        return DispatchResult(
            node_id=node_id,
            executor_id="",
            run_mode=RunMode.BBS,
            accept_token=_new_token(),
        )

    # --- http seam --------------------------------------------------------

    def _post(self, url: str, body: dict) -> dict:
        resp = self._client.post(url, json=body)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {}

    def close(self) -> None:
        self._client.close()


__all__ = ["ExecutionPortClient"]