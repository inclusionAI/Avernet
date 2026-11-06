"""BCN V3 request adapter: exact-run cancellation and one terminal result per run."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .domain import Player
from .tasks import PrivateSeat, parse_task, text_content

Send = Callable[[dict], Awaitable[None]]


@dataclass
class Run:
    session: str
    work: asyncio.Task | None = None
    terminal: bool = False
    aborted: bool = False
    seq: int = 0


class Runtime:
    def __init__(
        self,
        player: Player,
        send: Send,
        *,
        private_seats: dict[str, PrivateSeat],
        max_records: int = 1024,
    ):
        self.player, self.send = player, send
        self.runs: OrderedDict[str, Run] = OrderedDict()
        self.private = private_seats
        self.max_records = max_records
        self.lock = asyncio.Lock()
        self.closed = False

    async def response(self, request_id: str, payload: dict) -> None:
        await self.send({"type": "res", "id": request_id, "ok": True, "payload": payload})

    async def error(self, request_id: str, code: str, message: str) -> None:
        await self.send(
            {
                "type": "res",
                "id": request_id,
                "ok": False,
                "error": {"code": code, "message": message, "retryable": False},
            }
        )

    @staticmethod
    def session(params: dict) -> str:
        session = params.get("bcs_session_id")
        if not isinstance(session, str) or not session or params.get("session_key") != session:
            raise ValueError("V3 requires matching canonical session_key and bcs_session_id")
        return session

    async def dispatch(self, frame: dict) -> None:
        if frame.get("type") != "req":
            return
        request_id, method = frame.get("id"), frame.get("method")
        params = frame.get("params", {})
        if not isinstance(request_id, str) or not isinstance(params, dict):
            raise ValueError("Invalid request frame")
        try:
            if method == "chat.send":
                await self.start(request_id, params)
            elif method == "chat.inject":
                self.session(params)
                # No broadcast accumulation: only the referee's explicit node input is used.
                await self.response(request_id, {})
            elif method == "chat.abort":
                await self.abort(request_id, params)
            elif method == "chat.history":
                key = params.get("session_key")
                if not isinstance(key, str) or not key:
                    raise ValueError("Missing session_key")
                # Deliberately no persistent transcript; private instructions are never exposed.
                await self.response(request_id, {"session_key": key, "messages": []})
            else:
                await self.error(request_id, "unknown_method", "Unsupported player method")
        except (ValueError, KeyError, TypeError):
            await self.error(request_id, "invalid_request", "Invalid or unsupported player task")

    async def start(self, request_id: str, params: dict) -> None:
        session = self.session(params)
        if self.closed:
            raise ValueError("Runtime closed")
        # Match the existing BCN plugin: retain BCS's unique delivery ID as run ID.
        # This keeps V3 terminal events and exact-run aborts on the registered scope.
        if request_id in self.runs:
            if self.runs[request_id].session != session:
                raise ValueError("Delivery ID reused across sessions")
            await self.response(request_id, {"run_id": request_id})
            return
        if len(self.runs) >= self.max_records:
            done = next((key for key, run in self.runs.items() if run.terminal), None)
            if done is None:
                await self.error(request_id, "unavailable", "Too many active player tasks")
                return
            self.runs.pop(done)
        task = parse_task(text_content(params.get("message")), self.private.get(session))
        if task.word:
            if session not in self.private and len(self.private) >= self.max_records:
                raise ValueError("Session capacity reached; restart with a new identity state")
            self.private[session] = PrivateSeat(task.seat, task.word)
        run_id, run = request_id, Run(session)
        self.runs[run_id] = run
        await self.response(request_id, {"run_id": run_id})
        timeout_ms = params.get("timeout_ms", 60000)
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, (int, float)):
            timeout_ms = 60000
        run.work = asyncio.create_task(self.execute(run_id, task, min(timeout_ms / 1000, 120)))

    async def emit(self, run_id: str, state: str, **fields) -> bool:
        # Serialize terminal publication against cancellation. Never publish model deltas.
        async with self.lock:
            run = self.runs[run_id]
            if self.closed or run.terminal or (run.aborted and state != "aborted"):
                return False
            run.seq += 1
            run.terminal = True
            await self.send(
                {
                    "type": "event",
                    "event": "chat",
                    "seq": run.seq,
                    "payload": {
                        "runId": run_id,
                        "sessionId": run.session,
                        "seq": run.seq,
                        "ts": int(time.time() * 1000),
                        "state": state,
                        **fields,
                    },
                }
            )
            return True

    async def execute(self, run_id: str, task, deadline_seconds: float) -> None:
        try:
            async with asyncio.timeout(max(0.001, deadline_seconds)):
                answer = await self.player.answer(task)
            await self.emit(
                run_id,
                "final",
                content=answer.text,
                stopReason="complete",
                usage={"input": answer.input_tokens, "output": answer.output_tokens},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Provider exceptions may include credentials or private prompts: don't serialize them.
            await self.emit(
                run_id,
                "error",
                errorCode="PLAYER_EXECUTION_FAILED",
                errorMessage="Player model execution or output validation failed",
            )

    async def abort(self, request_id: str, params: dict) -> None:
        run_id = params.get("run_id")
        if not isinstance(run_id, str) or run_id not in self.runs:
            await self.error(request_id, "not_found", "Unknown player run")
            return
        run = self.runs[run_id]
        if params.get("session_key") != run.session:
            await self.error(request_id, "invalid_request", "Run does not belong to session")
            return
        async with self.lock:
            if run.terminal:
                await self.response(request_id, {"aborted": True, "aborted_run_ids": []})
                return
            run.aborted = True
        if run.work:
            run.work.cancel()
            done, _ = await asyncio.wait({run.work}, timeout=2)
            if not done:
                raise RuntimeError("Model task did not confirm cancellation")
            # Retrieve a task exception; CancelledError is an expected cancellation outcome.
            await asyncio.gather(run.work, return_exceptions=True)
        await self.emit(run_id, "aborted", stopReason="aborted")
        await self.response(request_id, {"aborted": True, "aborted_run_ids": [run_id]})

    async def close(self) -> None:
        self.closed = True
        works = [r.work for r in self.runs.values() if r.work and not r.work.done()]
        for work in works:
            work.cancel()
        if works:
            done, pending = await asyncio.wait(works, timeout=2)
            await asyncio.gather(*done, return_exceptions=True)
            if pending:
                raise RuntimeError("Player tasks did not stop")
