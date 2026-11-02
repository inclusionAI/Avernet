from __future__ import annotations

import asyncio
import hashlib
import json
import stat
import tempfile
import threading
import unittest
from contextlib import suppress
from pathlib import Path

from tests.fake_server import FakeBcsServer
from tests.fixtures import (
    EULOGY_TEXT,
    SESSION_ID,
    SESSION_KEY,
    VOTE_START_INSTRUCTION,
    chat_send_frame,
    speech_instruction,
    state_machine_text,
    vote_instruction,
)
from undercover_player import guard
from undercover_player.bcn_client import BcnClient
from undercover_player.brain import Answer, RulesBrain
from undercover_player.config import PlayerConfig
from undercover_player.store import EventLog
from undercover_player.tasks import vote_start_text


class ScriptedBrain:
    def __init__(self, text: str = "平时不太动它，占地方。", *, gate: asyncio.Event | None = None, delay: float = 0.0):
        self.text = text
        self.gate = gate
        self.delay = delay
        self.calls = 0

    async def answer(self, task, run_id: str) -> Answer:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        return Answer(self.text, "test")


class StubbornBrain:
    """故意吞掉取消信号，用于验证 abort 必须等生成真正退出才响应。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def answer(self, task, run_id: str) -> Answer:  # noqa: ANN001 - test double
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            # 模拟不守规矩的生成器：取消后继续等到测试放行。
            await asyncio.shield(self.release.wait())
        return Answer("平时不太动它，占地方。", "test")


class ThreadBlockingBrain:
    """用 to_thread 模拟同步 HTTP/线程：取消只停住等待，线程本身会继续跑完。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    async def answer(self, task, run_id: str) -> Answer:  # noqa: ANN001 - test double
        def worker() -> Answer:
            self.started.set()
            self.release.wait(timeout=5)
            return Answer("平时不太动它，占地方。", "thread-test")

        return await asyncio.to_thread(worker)


class BcnClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fake = await FakeBcsServer().start()
        self.client: BcnClient | None = None
        self.run_task: asyncio.Task[None] | None = None

    async def asyncTearDown(self) -> None:
        if self.client is not None:
            await self.client.stop()
        if self.run_task is not None:
            self.run_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.run_task
        await self.fake.close()
        self.temp.cleanup()

    def _config(self) -> PlayerConfig:
        return PlayerConfig(
            bcs_url=f"http://127.0.0.1:{self.fake.http_port}",
            ws_url=f"ws://127.0.0.1:{self.fake.ws_port}/ws/bot",
            token_file=Path(self.temp.name) / "session.json",
            log_dir=Path(self.temp.name) / "logs",
            rules_only=True,
            llm_timeout_seconds=2.0,
        )

    async def _start(self, brain=None):
        config = self._config()
        self.client = BcnClient(
            config,
            brain=brain if brain is not None else RulesBrain(),
            event_log=EventLog(config.log_dir),
        )
        self.run_task = asyncio.create_task(self.client.run())
        await asyncio.wait_for(self.client.connected.wait(), timeout=5)
        return self.client

    async def _send(self, text: str, run_id: str, *, timeout_ms: int | None = 420_000) -> None:
        await self.fake.send(chat_send_frame(text, run_id=run_id, timeout_ms=timeout_ms))
        await self.fake.wait_for_response(run_id)

    async def test_connect_onboards_and_saves_private_token(self) -> None:
        client = await self._start()
        paths = [(entry["method"], entry["path"]) for entry in self.fake.http_requests]
        self.assertIn(("POST", "/bots/onboard"), paths)
        self.assertIn(("PUT", "/bots/bot-fake/visibility"), paths)
        onboard = next(entry for entry in self.fake.http_requests if entry["path"] == "/bots/onboard")
        self.assertEqual(onboard["authorization"], "Bearer tok-fake")
        self.assertEqual(onboard["body"]["name"], "玩家稳健老陈")
        token_file = client.token_store._path
        self.assertTrue(token_file.is_file())
        mode = stat.S_IMODE(token_file.stat().st_mode)
        self.assertEqual(mode, 0o600)

    async def test_speech_task_returns_canonical_final(self) -> None:
        client = await self._start()
        run_id = "smnode-run-1-speak_3-0"
        await self._send(state_machine_text("speak_3", speech_instruction()), run_id)
        frame = await self.fake.wait_for_final(run_id)
        payload = frame["payload"]
        self.assertEqual(payload["runId"], run_id)
        self.assertEqual(payload["sessionId"], SESSION_ID)
        self.assertEqual(payload["state"], "final")
        self.assertEqual(payload["seq"], 1)
        self.assertEqual(frame["seq"], 1)
        self.assertIsNone(guard.speech_violation(payload["content"], "苹果", 25))
        self.assertIn("final_sent", client.event_log.path.read_text(encoding="utf-8"))

    async def test_vote_and_pk_vote(self) -> None:
        await self._start()
        run_id = "smnode-run-1-vote_3-0"
        await self._send(state_machine_text("vote_3", vote_instruction()), run_id)
        frame = await self.fake.wait_for_final(run_id)
        # 无模型/模型失败时常规票明确弃权，不再伪造确定性推理票。
        self.assertEqual(frame["payload"]["content"], "我弃权")

        pk_id = "smnode-run-2-vote_3-0"
        await self._send(
            state_machine_text("vote_3", vote_instruction(round_number=2, pk=True, targets=(1, 2))),
            pk_id,
        )
        pk_frame = await self.fake.wait_for_final(pk_id)
        self.assertIn(pk_frame["payload"]["content"], {"我投1号", "我投2号"})

    async def test_vote_start_phrase_and_eulogy(self) -> None:
        await self._start()
        start_id = "smnode-run-1-vote_start-0"
        await self._send(state_machine_text("vote_start", VOTE_START_INSTRUCTION), start_id)
        start_frame = await self.fake.wait_for_final(start_id)
        self.assertEqual(start_frame["payload"]["content"], vote_start_text())

        eulogy_id = "task-eulogy-1"
        await self._send(EULOGY_TEXT, eulogy_id, timeout_ms=None)
        eulogy_frame = await self.fake.wait_for_final(eulogy_id)
        self.assertIsNone(guard.eulogy_violation(eulogy_frame["payload"]["content"], "苹果", 35, (1, 2, 4)))

    async def test_chat_inject_is_acked_without_reply(self) -> None:
        client = await self._start()
        frame = {
            "type": "req",
            "id": "inject-1",
            "method": "chat.inject",
            "params": {
                "session_key": SESSION_KEY,
                "bcs_group_id": "grp-test",
                "bcs_session_id": SESSION_ID,
                "message": {"role": "user", "content": [{"type": "text", "text": "观察"}]},
            },
        }
        await self.fake.send(frame)
        response = await self.fake.wait_for_response("inject-1")
        self.assertTrue(response["ok"])
        await asyncio.sleep(0.1)
        finals = [
            item
            for item in client.sent_frames
            if item.get("type") == "event" and item.get("payload", {}).get("runId") == "inject-1"
        ]
        self.assertEqual(finals, [])

    async def test_abort_cancels_active_run_and_suppresses_late_final(self) -> None:
        gate = asyncio.Event()
        brain = ScriptedBrain(gate=gate)
        client = await self._start(brain)
        run_id = "smnode-run-1-speak_3-0"
        await self.fake.send(chat_send_frame(state_machine_text("speak_3", speech_instruction()), run_id=run_id))
        await self.fake.wait_for_response(run_id)
        await self.fake.send(
            {
                "type": "req",
                "id": "abort-1",
                "method": "chat.abort",
                "params": {"session_key": SESSION_KEY, "run_id": run_id},
            }
        )
        response = await self.fake.wait_for_response("abort-1")
        self.assertEqual(
            response["payload"],
            {"aborted": True, "aborted_run_ids": [run_id]},
        )
        gate.set()
        await asyncio.sleep(0.2)
        finals = [
            item
            for item in client.sent_frames
            if item.get("type") == "event" and item.get("payload", {}).get("runId") == run_id
        ]
        self.assertEqual(finals, [])

    async def test_abort_terminal_and_unknown(self) -> None:
        await self._start()
        run_id = "smnode-run-1-speak_3-0"
        await self._send(state_machine_text("speak_3", speech_instruction()), run_id)
        await self.fake.wait_for_final(run_id)
        await self.fake.send(
            {
                "type": "req",
                "id": "abort-1",
                "method": "chat.abort",
                "params": {"session_key": SESSION_KEY, "run_id": run_id},
            }
        )
        terminal = await self.fake.wait_for_response("abort-1")
        self.assertEqual(terminal["payload"], {"aborted": False, "aborted_run_ids": []})

        await self.fake.send(
            {
                "type": "req",
                "id": "abort-2",
                "method": "chat.abort",
                "params": {"session_key": SESSION_KEY, "run_id": "unknown-run"},
            }
        )
        unknown = await self.fake.wait_for_response("abort-2")
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["error"]["code"], "RUN_NOT_FOUND")

    async def test_abort_scope_mismatch_does_not_cancel(self) -> None:
        gate = asyncio.Event()
        brain = ScriptedBrain(gate=gate)
        await self._start(brain)
        run_id = "smnode-run-1-speak_3-0"
        await self.fake.send(chat_send_frame(state_machine_text("speak_3", speech_instruction()), run_id=run_id))
        await self.fake.wait_for_response(run_id)
        await self.fake.send(
            {
                "type": "req",
                "id": "abort-1",
                "method": "chat.abort",
                "params": {"session_key": "another-session", "run_id": run_id},
            }
        )
        response = await self.fake.wait_for_response("abort-1")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "SCOPE_MISMATCH")
        gate.set()
        frame = await self.fake.wait_for_final(run_id)
        self.assertEqual(frame["payload"]["runId"], run_id)

    async def test_expired_result_is_dropped(self) -> None:
        brain = ScriptedBrain(delay=0.3)
        client = await self._start(brain)
        run_id = "smnode-run-1-speak_3-0"
        await self._send(state_machine_text("speak_3", speech_instruction()), run_id, timeout_ms=100)
        await asyncio.sleep(0.7)
        finals = [
            item
            for item in client.sent_frames
            if item.get("type") == "event" and item.get("payload", {}).get("runId") == run_id
        ]
        self.assertEqual(finals, [])
        logs = client.event_log.path.read_text(encoding="utf-8")
        self.assertIn("result_dropped_expired", logs)

        next_id = "smnode-run-2-speak_3-0"
        await self._send(state_machine_text("speak_3", speech_instruction(round_number=2)), next_id, timeout_ms=5_000)
        next_frame = await self.fake.wait_for_final(next_id)
        self.assertEqual(next_frame["payload"]["runId"], next_id)

    async def test_duplicate_chat_send_replies_once(self) -> None:
        client = await self._start()
        run_id = "smnode-run-1-speak_3-0"
        frame = chat_send_frame(state_machine_text("speak_3", speech_instruction()), run_id=run_id)
        await self.fake.send(frame)
        await self.fake.send(frame)
        await self.fake.wait_for_final(run_id)
        await asyncio.sleep(0.2)
        finals = [
            item
            for item in client.sent_frames
            if item.get("type") == "event" and item.get("payload", {}).get("runId") == run_id
        ]
        self.assertEqual(len(finals), 1)

    async def test_chat_history_returns_local_replies(self) -> None:
        await self._start()
        run_id = "smnode-run-1-speak_3-0"
        await self._send(state_machine_text("speak_3", speech_instruction()), run_id)
        final = await self.fake.wait_for_final(run_id)
        await self.fake.send(
            {
                "type": "req",
                "id": "history-1",
                "method": "chat.history",
                "params": {"session_key": SESSION_KEY, "limit": 10},
            }
        )
        response = await self.fake.wait_for_response("history-1")
        messages = response["payload"]["messages"]
        self.assertEqual(response["payload"]["session_key"], SESSION_KEY)
        self.assertEqual(messages[-1]["content"], final["payload"]["content"])

    async def test_chat_send_without_bcs_session_id_is_rejected(self) -> None:
        client = await self._start()
        frame = chat_send_frame(state_machine_text("speak_3", speech_instruction()), run_id="bad-run")
        frame["params"].pop("bcs_session_id")
        await self.fake.send(frame)
        response = await self.fake.wait_for_response("bad-run")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_request")
        await asyncio.sleep(0.1)
        finals = [
            item
            for item in client.sent_frames
            if item.get("type") == "event" and item.get("payload", {}).get("runId") == "bad-run"
        ]
        self.assertEqual(finals, [])

    async def test_abort_without_exact_run_id_is_rejected(self) -> None:
        await self._start()
        await self.fake.send(
            {
                "type": "req",
                "id": "abort-1",
                "method": "chat.abort",
                "params": {"session_key": SESSION_KEY},
            }
        )
        response = await self.fake.wait_for_response("abort-1")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_request")

    async def test_ack_echoes_request_id_as_run_id(self) -> None:
        await self._start()
        run_id = "smnode-run-ack-1"
        await self.fake.send(chat_send_frame(state_machine_text("speak_3", speech_instruction()), run_id=run_id))
        response = await self.fake.wait_for_response(run_id)
        self.assertTrue(response["ok"])
        self.assertEqual(response["payload"], {"run_id": run_id})

    async def test_abort_waits_for_stubborn_generator_to_stop(self) -> None:
        brain = StubbornBrain()
        client = await self._start(brain)
        run_id = "smnode-run-stubborn-1"
        await self.fake.send(chat_send_frame(state_machine_text("speak_3", speech_instruction()), run_id=run_id))
        await self.fake.wait_for_response(run_id)
        await asyncio.wait_for(brain.started.wait(), timeout=2)

        await self.fake.send(
            {
                "type": "req",
                "id": "abort-stubborn",
                "method": "chat.abort",
                "params": {"session_key": SESSION_KEY, "run_id": run_id},
            }
        )
        await asyncio.sleep(0.2)
        # 生成器还没退出，不能先 ACK。
        with self.assertRaises(TimeoutError):
            await self.fake.wait_for_response("abort-stubborn", timeout=0.2)
        # 但接收循环不能被等待取消阻塞：其它请求仍应被处理。
        await self.fake.send(
            {
                "type": "req",
                "id": "inject-during-abort",
                "method": "chat.inject",
                "params": {"session_key": SESSION_KEY, "bcs_session_id": SESSION_ID, "message": {}},
            }
        )
        inject = await self.fake.wait_for_response("inject-during-abort", timeout=1)
        self.assertTrue(inject["ok"])
        brain.release.set()
        response = await self.fake.wait_for_response("abort-stubborn", timeout=3)
        self.assertEqual(response["payload"], {"aborted": True, "aborted_run_ids": [run_id]})
        finals = [
            item
            for item in client.sent_frames
            if item.get("type") == "event" and item.get("payload", {}).get("runId") == run_id
        ]
        self.assertEqual(finals, [])

    async def test_abort_before_final_send_suppresses_final(self) -> None:
        await self._start(RulesBrain())
        client = self.client
        assert client is not None
        original_send_final = client._send_final  # noqa: SLF001 - 注入 final 发送前窗口
        final_waiting = asyncio.Event()
        send_gate = asyncio.Event()

        async def delayed_send_final(run, text):  # noqa: ANN001
            final_waiting.set()
            await send_gate.wait()
            return await original_send_final(run, text)

        client._send_final = delayed_send_final  # type: ignore[method-assign]
        run_id = "smnode-run-final-window"
        await self.fake.send(chat_send_frame(state_machine_text("speak_3", speech_instruction()), run_id=run_id))
        await self.fake.wait_for_response(run_id)
        await asyncio.wait_for(final_waiting.wait(), timeout=2)

        await self.fake.send(
            {
                "type": "req",
                "id": "abort-final-window",
                "method": "chat.abort",
                "params": {"session_key": SESSION_KEY, "run_id": run_id},
            }
        )
        response = await self.fake.wait_for_response("abort-final-window", timeout=3)
        self.assertEqual(response["payload"], {"aborted": True, "aborted_run_ids": [run_id]})
        send_gate.set()
        await asyncio.sleep(0.2)
        finals = [
            item
            for item in client.sent_frames
            if item.get("type") == "event" and item.get("payload", {}).get("runId") == run_id
        ]
        self.assertEqual(finals, [])

    async def test_cross_session_duplicate_run_id_is_rejected(self) -> None:
        client = await self._start()
        run_id = "duplicate-across-sessions"
        await self.fake.send(chat_send_frame(state_machine_text("speak_3", speech_instruction()), run_id=run_id))
        await self.fake.wait_for_response(run_id)
        conflicting = chat_send_frame(
            state_machine_text("speak_3", speech_instruction()),
            run_id=run_id,
            session_key="other-group:ffffffff",
            session_id="other-group:ffffffff",
        )
        await self.fake.send(conflicting)
        response = await self.fake.wait_for(
            lambda frame: frame.get("type") == "res" and frame.get("id") == run_id and frame.get("ok") is False,
            timeout=2,
        )
        self.assertEqual(response["error"]["code"], "invalid_request")
        # 原 run 的 final 可能先于冲突响应到达；直接检查客户端已发送帧，避免把 final
        # 从 fake server 队列里误消费掉。
        finals: list[dict[str, object]] = []
        for _ in range(100):
            finals = [
                item
                for item in client.sent_frames
                if item.get("type") == "event" and item.get("payload", {}).get("runId") == run_id
            ]
            if finals:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(len(finals), 1)
        payload = finals[0]["payload"]
        self.assertEqual(payload["sessionId"], SESSION_ID)

    async def test_late_thread_result_after_abort_is_suppressed(self) -> None:
        brain = ThreadBlockingBrain()
        client = await self._start(brain)
        run_id = "smnode-run-thread-1"
        await self.fake.send(chat_send_frame(state_machine_text("speak_3", speech_instruction()), run_id=run_id))
        await self.fake.wait_for_response(run_id)
        self.assertTrue(await asyncio.to_thread(brain.started.wait, 2))

        await self.fake.send(
            {
                "type": "req",
                "id": "abort-thread",
                "method": "chat.abort",
                "params": {"session_key": SESSION_KEY, "run_id": run_id},
            }
        )
        response = await self.fake.wait_for_response("abort-thread", timeout=3)
        self.assertEqual(response["payload"], {"aborted": True, "aborted_run_ids": [run_id]})
        # 线程还在跑；放行后它也不能把 final 补发出去。
        brain.release.set()
        await asyncio.sleep(0.3)
        finals = [
            item
            for item in client.sent_frames
            if item.get("type") == "event" and item.get("payload", {}).get("runId") == run_id
        ]
        self.assertEqual(finals, [])

    async def test_unknown_state_machine_node_returns_explicit_error(self) -> None:
        client = await self._start(RulesBrain())
        run_id = "unrecognized-sm-node-1"
        text = state_machine_text("mystery_node", "这是一条无法识别的节点指令")
        await self.fake.send(chat_send_frame(text, run_id=run_id))
        await self.fake.wait_for_response(run_id)
        error = await self.fake.wait_for(
            lambda frame: (
                frame.get("type") == "event"
                and frame.get("event") == "chat"
                and isinstance(frame.get("payload"), dict)
                and frame["payload"].get("runId") == run_id
                and frame["payload"].get("state") == "error"
            ),
            timeout=2,
        )
        self.assertEqual(error["payload"].get("errorCode"), "UNRECOGNIZED_NODE")
        await asyncio.sleep(0.1)
        finals = [
            item for item in client.sent_frames
            if item.get("type") == "event" and item.get("payload", {}).get("runId") == run_id
            and item["payload"].get("state") == "final"
        ]
        self.assertEqual(finals, [])
        records = [
            json.loads(line) for line in client.event_log.path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        self.assertTrue(any(item.get("event") == "task_unrecognized" for item in records))
        self.assertTrue(any(item.get("event") == "terminal_error_sent" for item in records))

    async def test_unanswerable_pk_vote_returns_error_instead_of_silent_timeout(self) -> None:
        client = await self._start(RulesBrain())
        run_id = "pk-without-legal-targets"
        text = state_machine_text("vote_3", vote_instruction(pk=True, targets=()))
        await self.fake.send(chat_send_frame(text, run_id=run_id))
        await self.fake.wait_for_response(run_id)
        error = await self.fake.wait_for(
            lambda frame: (
                frame.get("type") == "event"
                and isinstance(frame.get("payload"), dict)
                and frame["payload"].get("runId") == run_id
                and frame["payload"].get("state") == "error"
            ),
            timeout=2,
        )
        self.assertEqual(error["payload"].get("errorCode"), "INVALID_ANSWER")
        self.assertFalse(
            any(
                frame.get("type") == "event"
                and isinstance(frame.get("payload"), dict)
                and frame["payload"].get("runId") == run_id
                and frame["payload"].get("state") == "final"
                for frame in client.sent_frames
            )
        )

    async def test_session_memory_does_not_mix_words_across_sessions(self) -> None:
        client = await self._start(RulesBrain())
        session_a = "bcs_grp_a:aaaaaaaa"
        session_b = "bcs_grp_b:bbbbbbbb"
        a_run = "smnode-a-speak_1-0"
        b_run = "smnode-b-speak_2-0"
        await self.fake.send(
            chat_send_frame(
                state_machine_text("speak_1", speech_instruction(word="苹果", seat=1)),
                run_id=a_run,
                session_key=session_a,
                session_id=session_a,
            )
        )
        await self.fake.wait_for_response(a_run)
        await self.fake.wait_for_final(a_run)
        await self.fake.send(
            chat_send_frame(
                state_machine_text("speak_2", speech_instruction(word="香蕉", seat=2)),
                run_id=b_run,
                session_key=session_b,
                session_id=session_b,
            )
        )
        await self.fake.wait_for_response(b_run)
        await self.fake.wait_for_final(b_run)

        eulogy_run = "eulogy-session-a"
        await self.fake.send(
            chat_send_frame(
                EULOGY_TEXT,
                run_id=eulogy_run,
                session_key=session_a,
                session_id=session_a,
                timeout_ms=None,
            )
        )
        await self.fake.wait_for_response(eulogy_run)
        await self.fake.wait_for_final(eulogy_run)
        records = [
            json.loads(line)
            for line in client.event_log.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        completed = next(item for item in records if item.get("event") == "task_completed" and item.get("run_id") == eulogy_run)
        expected = hashlib.sha256("苹果".encode()).hexdigest()[:12]
        self.assertEqual(completed.get("word_hash"), expected)

    async def test_unknown_task_is_logged_with_duration(self) -> None:
        client = await self._start(RulesBrain())
        run_id = "unrecognized-task-1"
        await self.fake.send(chat_send_frame("这是一条无法识别的节点任务", run_id=run_id))
        await self.fake.wait_for_response(run_id)
        final = await self.fake.wait_for_final(run_id)
        self.assertEqual(final["payload"]["content"], "我只处理谁是卧底节点任务。")
        await asyncio.sleep(0.1)
        records = [
            json.loads(line)
            for line in client.event_log.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        unrecognized = [item for item in records if item.get("event") == "task_unrecognized"]
        self.assertEqual(len(unrecognized), 1)
        self.assertEqual(unrecognized[0]["run_id"], run_id)
        completed = [item for item in records if item.get("event") == "task_completed"]
        self.assertEqual(len(completed), 1)
        self.assertIsInstance(completed[0].get("duration_ms"), int)
        self.assertGreaterEqual(completed[0]["duration_ms"], 0)

    async def test_rules_brain_works_without_model_credentials(self) -> None:
        config = self._config()
        self.assertFalse(config.llm_enabled())
        await self._start(RulesBrain())
        run_id = "smnode-run-1-vote_3-0"
        await self._send(state_machine_text("vote_3", vote_instruction()), run_id)
        frame = await self.fake.wait_for_final(run_id)
        self.assertEqual(frame["payload"]["content"], "我弃权")


if __name__ == "__main__":
    unittest.main()
