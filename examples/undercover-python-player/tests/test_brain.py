from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.fixtures import EULOGY_TEXT, speech_instruction, state_machine_text, vote_instruction
from undercover_player.brain import HybridBrain, LlmBrain, RulesBrain, build_brain, validate_answer
from undercover_player.config import PlayerConfig
from undercover_player.tasks import parse_task


class FakeOpenAI:
    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = json.loads(self.rfile.read(length)) if length else {}
                owner.requests.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
                status, content = owner.responses.pop(0) if owner.responses else (500, "{}")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if status == 200:
                    self.wfile.write(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
                else:
                    self.wfile.write(b'{"error":"boom"}')

            def log_message(self, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class BrainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _config(self, **overrides: object) -> PlayerConfig:
        base = PlayerConfig(
            token_file=Path(self.temp.name) / "session.json",
            log_dir=Path(self.temp.name) / "logs",
            llm_timeout_seconds=5.0,
        )
        return base.with_overrides(**overrides)

    def test_rules_brain_produces_valid_speech(self) -> None:
        task = parse_task(state_machine_text("speak_3", speech_instruction()))
        answer = asyncio.run(RulesBrain().answer(task, "run-1"))
        self.assertIsNone(validate_answer(task, answer.text))
        self.assertEqual(answer.source, "rules")

    def test_rules_brain_vote_is_legal_and_pk_never_abstains(self) -> None:
        normal = parse_task(state_machine_text("vote_3", vote_instruction()))
        answer = asyncio.run(RulesBrain().answer(normal, "run-2"))
        self.assertIsNone(validate_answer(normal, answer.text))
        pk = parse_task(state_machine_text("vote_3", vote_instruction(pk=True, targets=(1, 2))))
        pk_answer = asyncio.run(RulesBrain().answer(pk, "run-3"))
        self.assertIsNone(validate_answer(pk, pk_answer.text))
        self.assertNotEqual(pk_answer.text, "我弃权")

    def test_rules_brain_eulogy_and_vote_start(self) -> None:
        eulogy = parse_task(EULOGY_TEXT, known_word="苹果", known_seat=3)
        answer = asyncio.run(RulesBrain().answer(eulogy, "run-4"))
        self.assertIsNone(validate_answer(eulogy, answer.text))

    def test_no_llm_config_uses_rules(self) -> None:
        brain = build_brain(self._config())
        self.assertIsInstance(brain, HybridBrain)
        task = parse_task(state_machine_text("speak_3", speech_instruction()))
        answer = asyncio.run(brain.answer(task, "run-5"))
        self.assertEqual(answer.source, "rules")

    def test_llm_repairs_invalid_output(self) -> None:
        fake = FakeOpenAI([(200, "苹果很好吃"), (200, "平时不太动它，占地方。")])
        try:
            config = self._config(
                llm_base_url=f"http://127.0.0.1:{fake.port}/v1",
                llm_api_key="test-key",
                llm_model="test-model",
            )
            brain = LlmBrain(config)
            task = parse_task(state_machine_text("speak_3", speech_instruction()))
            answer = asyncio.run(brain.answer(task, "run-6"))
            self.assertEqual(answer.source, "llm")
            self.assertIsNone(validate_answer(task, answer.text))
            self.assertEqual(len(fake.requests), 2)
            self.assertEqual(fake.requests[0]["path"], "/v1/chat/completions")
            self.assertEqual(fake.requests[0]["authorization"], "Bearer test-key")
        finally:
            fake.close()

    def test_llm_accepts_valid_output_with_one_call(self) -> None:
        fake = FakeOpenAI([(200, "平时不太动它，占地方。")])
        try:
            config = self._config(
                llm_base_url=f"http://127.0.0.1:{fake.port}/v1",
                llm_api_key="test-key",
                llm_model="test-model",
            )
            brain = LlmBrain(config)
            task = parse_task(state_machine_text("speak_3", speech_instruction()))
            answer = asyncio.run(brain.answer(task, "run-valid"))
            self.assertEqual(answer.source, "llm")
            self.assertIsNone(validate_answer(task, answer.text))
            self.assertEqual(len(fake.requests), 1)
        finally:
            fake.close()

    def test_llm_failure_falls_back_to_rules(self) -> None:
        fake = FakeOpenAI([(500, "{}")])
        try:
            config = self._config(
                llm_base_url=f"http://127.0.0.1:{fake.port}/v1",
                llm_api_key="test-key",
                llm_model="test-model",
            )
            brain = HybridBrain(config)
            task = parse_task(state_machine_text("speak_3", speech_instruction()))
            answer = asyncio.run(brain.answer(task, "run-7"))
            self.assertEqual(answer.source, "rules")
            self.assertIsNone(validate_answer(task, answer.text))
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
