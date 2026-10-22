"""OpenClaw HostingEngine implementation. ACK, run result and chat events are distinct."""

import asyncio
import json
import math
from contextlib import aclosing
from uuid import uuid4

from engine.community.kernel.dws_execution import (
    AgentInvocation,
    EngineUpdate,
    ExecutionError,
    GatewayWitness,
    RunObservation,
)
from engine.community.plugins.dws_hosting.openclaw_transport import (
    HostingGatewayConfig,
    HostingGatewayTransport,
)


class OpenClawHostingEngine:
    def __init__(self, config: HostingGatewayConfig):
        self._transport = HostingGatewayTransport(config)
        self._config = config

    async def execute(self, request: AgentInvocation):
        try:
            async with aclosing(self._execute(request, raw_model=False)) as updates:
                async for update in updates:
                    yield update
        except asyncio.CancelledError:
            await self._abort(request)
            raise

    async def _abort(self, request: AgentInvocation) -> None:
        # COSEC: never abort a whole session or an interactive chat run.
        if not (
            request.session_key.startswith(f"agent:{request.agent_id}:dws:")
            and request.run_id.startswith("dws-turn-")
        ):
            return
        try:
            async with (
                asyncio.timeout(3),
                aclosing(
                    self._transport.responses(
                        uuid4().hex,
                        "chat.abort",
                        {"sessionKey": request.session_key, "runId": request.run_id},
                        timeout=3,
                    )
                ) as responses,
            ):
                async for response in responses:
                    if response.get("type") != "hosting.gateway":
                        return
        except (ExecutionError, TimeoutError):
            # Abort ACK is not a completion receipt. The durable ledger remains authoritative.
            pass

    async def repair(self, request: AgentInvocation) -> str:
        return await self._model_result(request, "format_repair_unavailable")

    async def review_reply(self, request: AgentInvocation) -> str:
        return await self._model_result(request, "reply_review_unavailable")

    async def _model_result(self, request: AgentInvocation, failure: str) -> str:
        try:
            async with aclosing(self._execute(request, raw_model=True)) as updates:
                async for update in updates:
                    if update.status == "completed":
                        return update.result_json
                    if update.status not in ("prepared", "accepted"):
                        raise ExecutionError(failure)
        except asyncio.CancelledError:
            await self._abort(request)
            raise
        raise ExecutionError(failure)

    async def _execute(self, request: AgentInvocation, *, raw_model: bool):
        params = {
            "agentId": request.agent_id,
            "sessionKey": request.session_key,
            "idempotencyKey": request.run_id,
            "message": request.message,
            "extraSystemPrompt": request.instructions,
            "deliver": False,
        }
        if raw_model:
            # COSEC: OpenClaw raw-model mode disables tools, not merely by prompt.
            # Unsupported gateways reject these fields; never retry as a normal agent.
            params.update(modelRun=True, promptMode="none")
        async with aclosing(
            self._transport.responses(
                request.rpc_id, "agent", params, timeout=self._config.run_timeout
            )
        ) as responses:
            async for response in responses:
                if response.get("type") == "hosting.gateway":
                    yield EngineUpdate(
                        run_id=request.run_id,
                        status="prepared",
                        gateway_witness=GatewayWitness.model_validate(
                            response["witness"]
                        ),
                    )
                    continue
                payload = response.get("payload")
                if (
                    isinstance(payload, dict)
                    and "runId" in payload
                    and payload["runId"] != request.run_id
                ):
                    raise ExecutionError("invalid_agent_response")
                if response.get("ok") is False:
                    yield EngineUpdate(
                        run_id=request.run_id, status="failed", reason="agent_rejected"
                    )
                    return
                if (
                    response.get("ok") is not True
                    or not isinstance(payload, dict)
                    or payload.get("runId") != request.run_id
                ):
                    raise ExecutionError("invalid_agent_response")
                status = payload.get("status")
                if status == "accepted":
                    yield EngineUpdate(run_id=request.run_id, status="accepted")
                    continue
                if status == "in_flight":
                    yield EngineUpdate(
                        run_id=request.run_id, status="unknown", reason="run_in_flight"
                    )
                    return
                if status in ("error", "timeout"):
                    yield EngineUpdate(
                        run_id=request.run_id, status="failed", reason="agent_failed"
                    )
                    return
                if (
                    status != "ok"
                    or payload.get("summary") != "completed"
                    or not isinstance(payload.get("result"), dict)
                ):
                    raise ExecutionError("invalid_agent_result")
                # Includes cached completed RPC results; never chat deltas or history[-1].
                try:
                    result = json.dumps(
                        payload["result"], ensure_ascii=False, allow_nan=False
                    )
                except (ValueError, RecursionError):
                    raise ExecutionError("invalid_agent_result") from None
                if len(result) > 262_144:
                    raise ExecutionError("result_too_large")
                yield EngineUpdate(
                    run_id=request.run_id, status="completed", result_json=result
                )
                return

    async def inspect_run(
        self, run_id: str, *, gateway_witness: GatewayWitness | None = None
    ) -> RunObservation:
        async with aclosing(
            self._transport.responses(
                uuid4().hex,
                "agent.wait",
                {"runId": run_id, "timeoutMs": 1},
                timeout=self._config.connection_timeout,
            )
        ) as responses:
            async for response in responses:
                if response.get("type") == "hosting.gateway":
                    current = GatewayWitness.model_validate(response["witness"])
                    if (
                        gateway_witness is not None
                        and current.host_boot_id == gateway_witness.host_boot_id
                        and current.started_lower_ms
                        > gateway_witness.started_upper_ms + 1000
                    ):
                        # Old local process cannot produce a result on this gateway.
                        # Do not redispatch the original request to recover it.
                        return RunObservation(run_id=run_id, ended=True)
                    continue
                payload = response.get("payload")
                if (
                    response.get("ok") is not True
                    or not isinstance(payload, dict)
                    or payload.get("runId") != run_id
                ):
                    raise ExecutionError("invalid_run_observation")
                ended_at = payload.get("endedAt")
                ended = (
                    type(ended_at) in (int, float)
                    and math.isfinite(ended_at)
                    and ended_at >= 0
                    and payload.get("status") in ("ok", "error", "timeout")
                )
                return RunObservation(run_id=run_id, ended=ended)
        raise ExecutionError("missing_run_observation")
