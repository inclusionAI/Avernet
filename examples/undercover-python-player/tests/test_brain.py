from __future__ import annotations

import asyncio
import json
import ssl
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.fixtures import EULOGY_TEXT, speech_instruction, state_machine_text, vote_instruction
from undercover_player.brain import HybridBrain, LlmBrain, RulesBrain, _tls_hint, build_brain, validate_answer
from undercover_player.config import PlayerConfig
from undercover_player.tasks import parse_task

FakeResponse = tuple[int, str] | dict[str, object]


class FakeOpenAI:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = json.loads(self.rfile.read(length)) if length else {}
                owner.requests.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
                entry = owner.responses.pop(0) if owner.responses else {"status": 500}
                if isinstance(entry, dict):
                    status = int(entry.get("status", 200))
                    content = str(entry.get("content", ""))
                    finish_reason = str(entry.get("finish_reason", "stop"))
                    reasoning = entry.get("reasoning_content")
                else:
                    status, content = entry
                    finish_reason = "stop"
                    reasoning = None
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if status == 200:
                    message: dict[str, object] = {"content": content}
                    if reasoning:
                        message["reasoning_content"] = reasoning
                    self.wfile.write(
                        json.dumps(
                            {"choices": [{"message": message, "finish_reason": finish_reason}]}
                        ).encode()
                    )
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
        self.assertEqual(answer.text, "我弃权")
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

    def test_llm_retries_empty_content_with_larger_budget(self) -> None:
        fake = FakeOpenAI([
            {"status": 200, "content": "", "finish_reason": "length", "reasoning_content": "内部推理"},
            {"status": 200, "content": "平时不太动它，占地方。", "finish_reason": "stop"},
        ])
        try:
            config = self._config(
                llm_base_url=f"http://127.0.0.1:{fake.port}/v1",
                llm_api_key="test-key",
                llm_model="deepseek-flash",
                llm_max_tokens=2048,
                llm_max_tokens_ceiling=8192,
                llm_max_attempts=3,
            )
            task = parse_task(state_machine_text("speak_3", speech_instruction()))
            answer = asyncio.run(LlmBrain(config).answer(task, "run-budget"))
            self.assertEqual(answer.source, "llm")
            self.assertIsNone(validate_answer(task, answer.text))
            budgets = [request["body"]["max_tokens"] for request in fake.requests]
            self.assertEqual(budgets, [2048, 4096])
        finally:
            fake.close()

    def test_llm_retries_truncated_content(self) -> None:
        fake = FakeOpenAI([
            {"status": 200, "content": "平时不太动它", "finish_reason": "length"},
            {"status": 200, "content": "平时不太动它，占地方。", "finish_reason": "stop"},
        ])
        try:
            config = self._config(
                llm_base_url=f"http://127.0.0.1:{fake.port}/v1",
                llm_api_key="test-key",
                llm_model="deepseek-flash",
            )
            task = parse_task(state_machine_text("speak_3", speech_instruction()))
            answer = asyncio.run(LlmBrain(config).answer(task, "run-truncated"))
            self.assertEqual(answer.source, "llm")
            self.assertEqual([request["body"]["max_tokens"] for request in fake.requests], [2048, 4096])
        finally:
            fake.close()

    def test_llm_exhausted_budget_falls_back_to_rules(self) -> None:
        fake = FakeOpenAI([
            {"status": 200, "content": "", "finish_reason": "length"},
            {"status": 200, "content": "", "finish_reason": "length"},
            {"status": 200, "content": "", "finish_reason": "length"},
        ])
        try:
            config = self._config(
                llm_base_url=f"http://127.0.0.1:{fake.port}/v1",
                llm_api_key="test-key",
                llm_model="deepseek-flash",
                llm_max_tokens=128,
                llm_max_tokens_ceiling=256,
                llm_max_attempts=3,
            )
            task = parse_task(state_machine_text("speak_3", speech_instruction()))
            answer = asyncio.run(HybridBrain(config).answer(task, "run-exhausted"))
            self.assertEqual(answer.source, "rules")
            self.assertIsNone(validate_answer(task, answer.text))
            self.assertIn("finish_reason=length", answer.detail)
            self.assertEqual([request["body"]["max_tokens"] for request in fake.requests], [128, 256, 256])
        finally:
            fake.close()

    def test_tls_failure_hint_points_to_ssl_cert_file(self) -> None:
        error = urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))
        hint = _tls_hint(error)
        self.assertIn("SSL_CERT_FILE=/etc/ssl/cert.pem", hint)
        self.assertIn("never disable verification", hint)

    def test_vote_llm_failure_degrades_to_explicit_abstention(self) -> None:
        fake = FakeOpenAI([(500, "{}")])
        try:
            config = self._config(
                llm_base_url=f"http://127.0.0.1:{fake.port}/v1",
                llm_api_key="test-key",
                llm_model="deepseek-flash",
            )
            task = parse_task(state_machine_text("vote_3", vote_instruction()))
            answer = asyncio.run(HybridBrain(config).answer(task, "run-vote-degraded"))
            self.assertEqual(answer.source, "rules")
            self.assertEqual(answer.text, "我弃权")
            self.assertIsNone(validate_answer(task, answer.text))
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
