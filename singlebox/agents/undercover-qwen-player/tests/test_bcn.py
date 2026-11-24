import asyncio
import time
import unittest

from bcn import BcnSession

SESSION = "game:1234abcd"


def chat(request_id: str, timestamp: int, timeout: int = 1000) -> dict:
    return {
        "type": "req",
        "id": request_id,
        "method": "chat.send",
        "params": {
            "session_key": SESSION,
            "bcs_session_id": SESSION,
            "timeout_ms": timeout,
            "message": {
                "timestamp": timestamp,
                "content": [{"type": "text", "text": "任务输入"}],
            },
        },
    }


class BcnSessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.out = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.once = 0
        self.inputs = []

        async def send(frame: dict) -> None:
            self.out.append(frame)

        async def reply(text: str) -> str:
            self.once += 1
            self.inputs.append(text)
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                return "迟到的结果"
            return "这是正常回复"

        self.session = BcnSession(send, reply)
        self.addAsyncCleanup(self.session.close)

    async def test_send_ack_and_final_use_same_run_and_canonical_session(self) -> None:
        await self.session.handle(chat("task", time.time_ns() // 1_000_000))
        await self.started.wait()
        run = self.out[0]["payload"]["run_id"]
        self.release.set()
        await self.session.wait_idle()
        self.assertEqual(self.out[1]["payload"]["runId"], run)
        self.assertEqual(self.out[1]["payload"]["sessionId"], SESSION)
        self.assertEqual(self.out[1]["payload"]["state"], "final")

    async def test_abort_discards_even_a_result_returned_after_cancellation(
        self,
    ) -> None:
        await self.session.handle(chat("task", time.time_ns() // 1_000_000))
        await self.started.wait()
        run = self.out[0]["payload"]["run_id"]
        await self.session.handle(
            {
                "type": "req",
                "method": "chat.abort",
                "id": "abort",
                "params": {"session_key": SESSION, "run_id": run},
            }
        )
        await self.session.wait_idle()
        self.assertEqual(self.out[1]["payload"]["aborted_run_ids"], [run])
        self.assertEqual(
            [f["payload"]["state"] for f in self.out if f["type"] == "event"],
            ["aborted"],
        )

    async def test_abort_different_session_does_not_cancel_active_run(self) -> None:
        await self.session.handle(chat("task", time.time_ns() // 1_000_000))
        await self.started.wait()
        run_id = self.out[0]["payload"]["run_id"]
        await self.session.handle(
            {
                "type": "req",
                "method": "chat.abort",
                "id": "wrong-session",
                "params": {"session_key": "other:abcd1234", "run_id": run_id},
            }
        )
        self.release.set()
        await self.session.wait_idle()
        self.assertEqual(self.out[1]["error"]["code"], "not_found")
        self.assertEqual(self.out[2]["payload"]["state"], "final")

    async def test_execution_cannot_publish_a_final_after_deadline(self) -> None:
        await self.session.handle(
            chat("task", time.time_ns() // 1_000_000, timeout=100)
        )
        await self.started.wait()
        await asyncio.wait_for(self.session.wait_idle(), timeout=2)
        self.assertEqual(self.out[1]["payload"]["state"], "error")
        self.assertEqual(self.out[1]["payload"]["errorCode"], "TASK_TIMEOUT")

    async def test_group_id_does_not_replace_missing_canonical_session(self) -> None:
        frame = chat("missing-session", time.time_ns() // 1_000_000)
        del frame["params"]["bcs_session_id"]
        frame["params"]["bcs_group_id"] = "game"
        await self.session.handle(frame)
        self.assertEqual(self.out[0]["error"]["code"], "invalid_params")
        self.assertEqual(self.once, 0)

    async def test_run_reuses_upstream_canonical_delivery_id(self) -> None:
        frame = chat("request", time.time_ns() // 1_000_000)
        frame["params"]["idempotency_key"] = "upstream-node-run"
        await self.session.handle(frame)
        self.release.set()
        await self.session.wait_idle()
        self.assertEqual(self.out[0]["payload"]["run_id"], "upstream-node-run")
        self.assertEqual(self.out[1]["payload"]["runId"], "upstream-node-run")

    async def test_run_uses_structured_raw_message_without_group_header(self) -> None:
        frame = chat("task", time.time_ns() // 1_000_000)
        frame["params"]["session_context"] = {"message": "raw node text"}
        frame["params"]["message"]["content"][0]["text"] = (
            "[GroupContext] ... wrapped text"
        )
        await self.session.handle(frame)
        self.release.set()
        await self.session.wait_idle()
        self.assertEqual(self.inputs, ["raw node text"])

    async def test_late_task_is_rejected_and_duplicate_run_reuses_ack(self) -> None:
        now = time.time_ns() // 1_000_000
        await self.session.handle(chat("stale", now - 2000))
        self.assertFalse(self.out[0]["ok"])
        frame = chat("task", now)
        frame["params"]["idempotency_key"] = "node-attempt"
        await self.session.handle(frame)
        await self.session.handle({**frame, "id": "retry"})
        self.release.set()
        await self.session.wait_idle()
        self.assertEqual(self.out[2]["id"], "retry")
        self.assertEqual(self.out[1]["payload"], self.out[2]["payload"])
        self.assertEqual(self.once, 1)
