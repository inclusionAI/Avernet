"""Connection-scoped BCN V3 request dispatch and run lifecycle."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from player import DecisionError, ModelCallError

LOGGER = logging.getLogger(__name__)
DEFAULT_TIMEOUT_MS = 300_000
MAX_ACTIVE_RUNS = 8
MAX_RETAINED_RUNS = 512
Send = Callable[[dict], Awaitable[None]]
Reply = Callable[[str], Awaitable[str]]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def request(method: str, request_id: str, params: dict) -> dict:
    return {"type": "req", "id": request_id, "method": method, "params": params}


@dataclass
class Run:
    run_id: str
    session_id: str
    deadline: float
    task: asyncio.Task[None] | None = None
    terminal: bool = False


class BcnSession:
    def __init__(self, send: Send, reply: Reply) -> None:
        self.send = send
        self.reply = reply
        self._lock = asyncio.Lock()
        self._runs: dict[tuple[str, str], Run] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def handle(self, frame: dict) -> None:
        request_id = frame.get("id")
        if frame.get("type") != "req" or not isinstance(request_id, str):
            return
        params = frame.get("params")
        if not isinstance(params, dict):
            await self._error(
                request_id, "invalid_params", "Expected a parameter object"
            )
            return
        async with self._lock:
            method = frame.get("method")
            if method == "chat.send":
                await self._start(request_id, params)
            elif method == "chat.abort":
                await self._abort(request_id, params)
            elif method == "chat.inject":
                if self._session(params):
                    await self._ack(request_id, {})
                else:
                    await self._error(
                        request_id, "invalid_params", "Expected a canonical session"
                    )
            elif method == "chat.history" and isinstance(
                params.get("session_key"), str
            ):
                session = params["session_key"]
                await self._ack(
                    request_id,
                    {
                        "session_key": session,
                        "session_id": session,
                        "messages": [],
                    },
                )
            else:
                await self._error(
                    request_id, "unknown_method", "Unsupported request method"
                )

    async def _start(self, request_id: str, params: dict) -> None:
        session_id = self._session(params)
        message = params.get("message")
        timeout = params.get("timeout_ms", DEFAULT_TIMEOUT_MS)
        delivery_id = params.get("idempotency_key") or request_id
        if (
            not session_id
            or not isinstance(message, dict)
            or type(timeout) is not int
            or timeout <= 0
            or not isinstance(delivery_id, str)
        ):
            await self._error(
                request_id, "invalid_params", "Invalid V3 task parameters"
            )
            return
        key = (session_id, delivery_id)
        existing = self._runs.get(key)
        if existing:
            await self._ack(request_id, {"run_id": existing.run_id})
            return
        timestamp = message.get("timestamp")
        content = message.get("content")
        if type(timestamp) is not int or not isinstance(content, list):
            await self._error(request_id, "invalid_params", "Invalid task message")
            return
        text = "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ).strip()
        context = params.get("session_context")
        raw_text = context.get("message") if isinstance(context, dict) else None
        if isinstance(raw_text, str) and raw_text.strip():
            text = raw_text.strip()
        if not text:
            await self._error(request_id, "invalid_params", "Task has no text input")
            return
        remaining = min(timeout, timestamp + timeout - now_ms())
        if remaining <= 0:
            await self._error(
                request_id, "invalid_params", "Task deadline already elapsed"
            )
            return
        if len(self._tasks) >= MAX_ACTIVE_RUNS:
            await self._error(
                request_id, "unavailable", "Player reached the active run limit", True
            )
            return
        if len(self._runs) >= MAX_RETAINED_RUNS:
            oldest = next(run for run in self._runs.values() if run.terminal)
            del self._runs[(oldest.session_id, oldest.run_id)]
        run = Run(
            delivery_id,
            session_id,
            asyncio.get_running_loop().time() + remaining / 1000,
        )
        await self._ack(request_id, {"run_id": run.run_id})
        self._runs[key] = run
        run.task = asyncio.create_task(self._execute(run, text))
        self._tasks.add(run.task)
        run.task.add_done_callback(self._reap)

    async def _abort(self, request_id: str, params: dict) -> None:
        run_id = params.get("run_id")
        session = params.get("session_key")
        run = (
            self._runs.get((session, run_id))
            if isinstance(run_id, str) and isinstance(session, str)
            else None
        )
        if run is None:
            await self._error(request_id, "not_found", "Unknown run in this session")
            return
        aborted = [] if run.terminal else [run.run_id]
        if aborted:
            run.terminal = True
            if run.task:
                run.task.cancel()
        await self._ack(request_id, {"aborted": True, "aborted_run_ids": aborted})
        if aborted:
            await self._event(run, "aborted", stopReason="aborted")

    async def _execute(self, run: Run, text: str) -> None:
        try:
            async with asyncio.timeout_at(run.deadline):
                output = await self.reply(text)
        except TimeoutError:
            await self._finish(
                run,
                "error",
                errorCode="TASK_TIMEOUT",
                errorMessage="Task deadline elapsed",
            )
        except DecisionError:
            await self._finish(
                run,
                "error",
                errorCode="INVALID_OUTPUT",
                errorMessage="No legal player response",
            )
        except ModelCallError as error:
            LOGGER.warning("Model request failed (%s)", type(error.__cause__).__name__)
            await self._finish(
                run,
                "error",
                errorCode="MODEL_ERROR",
                errorMessage="Model request failed",
            )
        else:
            if asyncio.get_running_loop().time() >= run.deadline:
                await self._finish(
                    run,
                    "error",
                    errorCode="TASK_TIMEOUT",
                    errorMessage="Task deadline elapsed",
                )
            else:
                await self._finish(run, "final", content=output, stopReason="complete")

    async def _finish(self, run: Run, state: str, **parts: str) -> None:
        async with self._lock:
            if not run.terminal:
                run.terminal = True
                await self._event(run, state, **parts)

    async def _event(self, run: Run, state: str, **parts: str) -> None:
        await self.send(
            {
                "type": "event",
                "event": "chat",
                "seq": 1,
                "payload": {
                    "runId": run.run_id,
                    "sessionId": run.session_id,
                    "seq": 1,
                    "ts": now_ms(),
                    "state": state,
                    **parts,
                },
            }
        )

    async def _ack(self, request_id: str, payload: dict) -> None:
        await self.send(
            {"type": "res", "id": request_id, "ok": True, "payload": payload}
        )

    async def _error(
        self, request_id: str, code: str, message: str, retryable: bool = False
    ) -> None:
        await self.send(
            {
                "type": "res",
                "id": request_id,
                "ok": False,
                "error": {"code": code, "message": message, "retryable": retryable},
            }
        )

    def _reap(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            LOGGER.error("Run delivery failed (%s)", type(error).__name__)

    @staticmethod
    def _session(params: dict) -> str | None:
        session = params.get("bcs_session_id")
        return (
            session
            if isinstance(session, str)
            and session
            and session == params.get("session_key")
            else None
        )

    async def wait_idle(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def close(self) -> None:
        async with self._lock:
            for run in self._runs.values():
                run.terminal = True
            for task in self._tasks:
                task.cancel()
        await self.wait_idle()
