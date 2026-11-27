"""Bot WebSocket V3, with one terminal event per accepted run."""

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import logging
import time
from typing import Protocol
from uuid import uuid4

from player import GameTask, InvalidOutput, InvalidTask, parse_task

LOG = logging.getLogger(__name__)


class Connection(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...


Decision = Callable[[GameTask, str, Callable[..., None]], Awaitable[str]]


class HandshakeError(RuntimeError):
    pass


def audit(event: str, **fields) -> None:
    LOG.info("%s", json.dumps({"event": event, "ts": int(time.time() * 1000), **fields}, ensure_ascii=False))


def request(method: str, params: dict, request_id: str) -> dict:
    return {"type": "req", "id": request_id, "method": method, "params": params}


def session_key(params: dict) -> str:
    canonical = params.get("bcs_session_id")
    if not isinstance(canonical, str) or not canonical.strip() or params.get("session_key") != canonical:
        raise InvalidTask("canonical_session_required")
    return canonical


def task_text(params: dict) -> str:
    context = params.get("session_context")
    original = context.get("message") if isinstance(context, dict) else None
    if isinstance(original, str) and original.strip():
        return original

    message = params.get("message")
    blocks = message.get("content") if isinstance(message, dict) else None
    if not isinstance(blocks, list):
        raise InvalidTask("missing_message")
    return "\n".join(
        item["text"] for item in blocks
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
    )


@dataclass
class Run:
    id: str
    session: str
    game_task: GameTask
    timeout: float
    started: float
    job: asyncio.Task | None = None
    outcome: str | None = None


class BotConnection:
    def __init__(
        self, socket: Connection, bot_id: str, decide: Decision, terminal_runs: OrderedDict[str, str],
    ) -> None:
        self.socket = socket
        self.bot_id = bot_id
        self.decide = decide
        self.pending: dict[str, Run] = {}
        self.terminal = terminal_runs
        self.sending = asyncio.Lock()

    async def send(self, frame: dict) -> None:
        async with self.sending:
            await self.socket.send(json.dumps(frame, ensure_ascii=False))

    async def handshake(self, token: str) -> None:
        request_id = str(uuid4())
        await self.send(request("bot.connect", {
            "protocol_version": 3, "client_kind": "custom-engine", "token": token,
        }, request_id))
        frame = json.loads(await asyncio.wait_for(self.socket.recv(), timeout=20))
        if not isinstance(frame, dict) or frame.get("type") != "res" or frame.get("id") != request_id:
            raise HandshakeError("invalid bot.connect response")
        if frame.get("ok") is not True:
            raise HandshakeError("already_connected" if (frame.get("error") or {}).get("code") == "already_connected"
                                 else "bot.connect rejected")
        payload = frame.get("payload") or {}
        if payload.get("is_new") or payload.get("bot_uuid") != self.bot_id:
            raise HandshakeError("BCN token did not restore the selected Bot identity")
        capabilities = payload.get("capabilities") or {}
        if (
            payload.get("protocol_version") != 3
            or capabilities.get("unified_run_events") is not True
            or capabilities.get("canonical_session_id") is not True
        ):
            raise HandshakeError("BCN did not negotiate the required V3 capabilities")
        audit("connected", protocol_version=3, bot_id=self.bot_id)

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(60)
            await self.send(request("bot.status", {}, str(uuid4())))

    async def response(self, request_id: str, payload: dict) -> None:
        await self.send({"type": "res", "id": request_id, "ok": True, "payload": payload})

    async def error(self, request_id: str, code: str, message: str) -> None:
        await self.send({
            "type": "res", "id": request_id, "ok": False,
            "error": {"code": code, "message": message, "retryable": False},
        })

    async def handle(self, frame: dict) -> None:
        if not isinstance(frame, dict) or frame.get("type") != "req" or not isinstance(frame.get("id"), str):
            return
        request_id = frame["id"]
        method = frame.get("method")
        params = frame.get("params")
        if not isinstance(params, dict):
            await self.error(request_id, "INVALID_REQUEST", "request params must be an object")
            return
        try:
            if method == "chat.send":
                await self.start(request_id, params)
            elif method == "chat.abort":
                await self.abort(request_id, params)
            elif method == "chat.inject":
                session_key(params)
                await self.response(request_id, {})
            elif method == "chat.history":
                key = params.get("session_key")
                if not isinstance(key, str) or not key:
                    raise InvalidTask("missing_session_key")
                await self.response(request_id, {"session_key": key, "messages": [], "has_more": False})
            else:
                await self.error(request_id, "METHOD_NOT_SUPPORTED", "method is not supported")
        except InvalidTask as error:
            audit("task_rejected", reason=str(error))
            await self.error(request_id, "INVALID_REQUEST", str(error))

    async def start(self, request_id: str, params: dict) -> None:
        session = session_key(params)
        context = params.get("session_context")
        recipient = context.get("recipient") if isinstance(context, dict) else None
        if recipient is not None and recipient != self.bot_id:
            await self.error(request_id, "SCOPE_MISMATCH", "message belongs to another Bot")
            return

        run_id = params.get("idempotency_key") or request_id
        if not isinstance(run_id, str) or not run_id.strip():
            raise InvalidTask("invalid_run_id")
        prior = self.pending.get(run_id)
        known_session = prior.session if prior else self.terminal.get(run_id)
        if known_session is not None:
            if known_session != session:
                await self.error(request_id, "SCOPE_MISMATCH", "run belongs to another session")
            else:
                await self.response(request_id, {"run_id": run_id})
            return

        timeout_ms = params.get("timeout_ms")
        if timeout_ms is None:
            timeout_ms = 180_000
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise InvalidTask("invalid_timeout")
        game_task = parse_task(task_text(params))
        run = Run(run_id, session, game_task, timeout_ms / 1000, time.monotonic())
        self.pending[run_id] = run
        await self.response(request_id, {"run_id": run_id})
        audit("accepted", run_id=run_id, session_id=session, node=game_task.node)
        run.job = asyncio.create_task(self.respond(run))
        run.job.add_done_callback(self.consume_job)

    def claim(self, run: Run, outcome: str) -> bool:
        # No suspension here. Abort and model completion cannot both claim the run.
        if self.pending.get(run.id) is not run:
            return False
        del self.pending[run.id]
        run.outcome = outcome
        self.terminal[run.id] = run.session
        if len(self.terminal) > 1024:
            self.terminal.popitem(last=False)
        return True

    async def run_event(self, run: Run, state: str, **fields) -> None:
        await self.send({
            "type": "event", "event": "chat", "seq": 1,
            "payload": {
                "runId": run.id, "sessionId": run.session, "seq": 1,
                "ts": int(time.time() * 1000), "state": state, **fields,
            },
        })
        audit(state, session_id=run.session, run_id=run.id, node=run.game_task.node,
              elapsed_ms=int((time.monotonic() - run.started) * 1000),
              **({"error_code": fields["errorCode"]} if "errorCode" in fields else {}))

    async def abort(self, request_id: str, params: dict) -> None:
        run_id, session = params.get("run_id"), params.get("session_key")
        if not all(isinstance(value, str) and value.strip() for value in (run_id, session)):
            raise InvalidTask("abort_requires_run_and_session")
        run = self.pending.get(run_id)
        known_session = run.session if run else self.terminal.get(run_id)
        if known_session is None:
            await self.error(request_id, "RUN_NOT_FOUND", "run is unknown")
        elif known_session != session:
            await self.error(request_id, "SCOPE_MISMATCH", "run belongs to another session")
        elif run is None:
            await self.response(request_id, {"aborted": False, "aborted_run_ids": []})
        else:
            self.claim(run, "aborted")
            if run.job is not None:
                run.job.cancel()
            await self.run_event(run, "aborted", stopReason="aborted")
            await self.response(request_id, {"aborted": True, "aborted_run_ids": [run_id]})

    async def respond(self, run: Run) -> None:
        def run_audit(event: str, **fields) -> None:
            audit(event, session_id=run.session, run_id=run.id, node=run.game_task.node, **fields)

        worker = asyncio.create_task(self.decide(run.game_task, run.session, run_audit))

        def consume_result(task: asyncio.Task) -> None:
            if not task.cancelled():
                task.exception()
                if run.outcome in {"aborted", "timeout", "disconnected"}:
                    run_audit("late_result_discarded")

        worker.add_done_callback(consume_result)
        try:
            remaining = max(0, run.timeout - (time.monotonic() - run.started))
            done, _ = await asyncio.wait({worker}, timeout=remaining)
            if not done or time.monotonic() - run.started >= run.timeout:
                if self.claim(run, "timeout"):
                    await self.run_event(run, "error", errorCode="TIMEOUT", errorMessage="player run timed out")
                return
            try:
                reply = worker.result()
            except (Exception, asyncio.CancelledError) as error:
                code = "INVALID_OUTPUT" if isinstance(error, InvalidOutput) else "MODEL_ERROR"
                if self.claim(run, "error"):
                    run_audit("decision_failed", error_type=type(error).__name__)
                    await self.run_event(run, "error", errorCode=code, errorMessage="player could not decide")
            else:
                if self.claim(run, "final"):
                    await self.run_event(run, "final", content=reply, stopReason="complete")
        finally:
            if not worker.done():
                worker.cancel()

    @staticmethod
    def consume_job(task: asyncio.Task) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            audit("run_delivery_failed", error_type=type(error).__name__)

    async def serve(self, token: str) -> None:
        await self.handshake(token)
        heartbeat = asyncio.create_task(self.heartbeat())
        try:
            while True:
                message = await self.socket.recv()
                try:
                    frame = json.loads(message)
                except (ValueError, UnicodeError):
                    audit("invalid_json_frame")
                    continue
                await self.handle(frame)
        finally:
            heartbeat.cancel()
            jobs = [heartbeat]
            for run in list(self.pending.values()):
                self.claim(run, "disconnected")
                if run.job is not None:
                    run.job.cancel()
                    jobs.append(run.job)
            await asyncio.gather(*jobs, return_exceptions=True)
