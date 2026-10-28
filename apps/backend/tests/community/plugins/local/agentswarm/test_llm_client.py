"""Tests for the OpenAI-compatible LLM client and StubLLMProvider (task 2.6).

Covers: success parsing, exact retryable/non-retryable status and transport
error classification, retry exhaustion, backoff growth, orchestrator-model
override, request body shape, simulated stall timeouts, and StubLLMProvider
scripting. All HTTP is served by an in-file httpx.MockTransport gateway — no
network, no credentials.

Conventions follow test_orchestrator.py: sync test defs driving async code
through asyncio.run, hand-written fakes (no MagicMock).
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agentclaw.community.core.agentswarm.domain.models import (
    CompletionRequest, CompletionResponse, FinishReason, LlmCallKind,
    ModelId, TokenUsage,
)
from agentclaw.community.plugins.local.agentswarm import llm_openai
from agentclaw.community.plugins.local.agentswarm.llm_openai import OpenAICompatibleClient
from agentclaw.community.plugins.local.agentswarm.llm_stub import StubLLMProvider


def _ok_body(text="Hello world", finish="stop", usage=(1, 2, 3)) -> dict:
    return {
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": usage[0],
            "completion_tokens": usage[1],
            "total_tokens": usage[2],
        },
    }


class FakeOpenAIServer:
    """Sequenced httpx transport: scripted responses or transport errors."""

    def __init__(self, responses=None) -> None:
        self.responses = list(responses or [])
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responses:
            item = self.responses.pop(0)
        else:
            item = httpx.Response(200, json=_ok_body())
        if isinstance(item, Exception):
            raise item
        return item


def _make_client(server: FakeOpenAIServer, **kwargs) -> OpenAICompatibleClient:
    kwargs.setdefault("base_url", "http://fake-llm.local/v1")
    kwargs.setdefault("api_key", "test-api-key")
    kwargs.setdefault("default_model", "default-model-x")
    client = OpenAICompatibleClient(**kwargs)
    # standard httpx testing seam: swap the internal client for the same
    # configuration riding the in-file MockTransport
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(server),
        base_url=client._base_url,
        headers={"Authorization": f"Bearer {kwargs['api_key']}"},
        timeout=client._timeout,
    )
    return client


def _req(kind=LlmCallKind.SUBAGENT, model=None, system_prompt="",
         user_prompt="do the thing", max_tokens=None, temperature=0.0):
    return CompletionRequest(
        kind=kind, model=model, system_prompt=system_prompt,
        user_prompt=user_prompt, max_tokens=max_tokens,
        temperature=temperature,
    )



async def _instant_sleep(seconds: float) -> None:
    return None


def _skip_retry_waits(monkeypatch) -> None:
    """Remove real client backoff sleeps; counters stay meaningful."""
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)


class TestSuccessPath:
    def test_success_returns_parsed_completion(self):
        async def main():
            server = FakeOpenAIServer(
                [httpx.Response(200, json=_ok_body("Hello world", "stop", (1, 2, 3)))]
            )
            client = _make_client(server)
            resp = await client.complete(_req())
            await client._client.aclose()

            assert isinstance(resp, CompletionResponse)
            assert resp.text == "Hello world"
            assert resp.model == "default-model-x"
            assert resp.token_usage == TokenUsage(1, 2, 3)
            assert resp.finish_reason == FinishReason.STOP
            assert len(server.requests) == 1
            assert server.requests[0].headers["authorization"] == "Bearer test-api-key"

        asyncio.run(main())


class TestRetryableFailures:
    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_server_error_retried_then_succeeds(self, status, monkeypatch):
        _skip_retry_waits(monkeypatch)

        async def main():
            server = FakeOpenAIServer([
                httpx.Response(status, json={"error": "transient"}),
                httpx.Response(200, json=_ok_body("after retry")),
            ])
            client = _make_client(server, max_retries=3)
            resp = await client.complete(_req())
            await client._client.aclose()

            assert resp.text == "after retry"
            assert len(server.requests) == 2

        asyncio.run(main())

    @pytest.mark.parametrize("transport_error", [
        httpx.ConnectError("connection reset"),
        httpx.ReadTimeout("read timed out"),
        httpx.ReadError("stream broke mid-read"),
    ], ids=["connect-error", "read-timeout", "read-error"])
    def test_transport_error_retried_then_succeeds(self, transport_error, monkeypatch):
        _skip_retry_waits(monkeypatch)

        async def main():
            server = FakeOpenAIServer([
                transport_error,
                httpx.Response(200, json=_ok_body("after transport blip")),
            ])
            client = _make_client(server, max_retries=3)
            resp = await client.complete(_req())
            await client._client.aclose()

            assert resp.text == "after transport blip"
            assert len(server.requests) == 2

        asyncio.run(main())

    def test_retry_exhaustion_raises_after_max_attempts(self, monkeypatch):
        _skip_retry_waits(monkeypatch)

        async def main():
            failures = [httpx.Response(500, json={}) for _ in range(3)]
            server = FakeOpenAIServer(failures)
            client = _make_client(server, max_retries=2)
            with pytest.raises(RuntimeError, match="failed after 3 attempts"):
                await client.complete(_req())
            await client._client.aclose()

            assert len(server.requests) == 3

        asyncio.run(main())

    def test_backoff_durations_grow_between_attempts(self, monkeypatch):
        async def main():
            sleeps = []

            async def fake_sleep(seconds):
                sleeps.append(seconds)

            monkeypatch.setattr(asyncio, "sleep", fake_sleep)
            failures = [httpx.Response(500, json={}) for _ in range(3)]
            server = FakeOpenAIServer(failures)
            client = _make_client(server, max_retries=2)
            with pytest.raises(RuntimeError):
                await client.complete(_req())
            await client._client.aclose()

            # min(2**attempt + U(0, 0.4*2**attempt), 30): attempt 0 in
            # [1, 1.4], attempt 1 in [2, 2.8], strictly growing
            assert len(sleeps) == 2
            assert 1.0 <= sleeps[0] <= 1.4
            assert 2.0 <= sleeps[1] <= 2.8
            assert sleeps[1] > sleeps[0]

        asyncio.run(main())


class TestNonRetryableFailures:
    @pytest.mark.parametrize("status", [400, 401, 422])
    def test_client_error_raises_immediately_no_retry(self, status):
        async def main():
            server = FakeOpenAIServer([
                httpx.Response(status, json={"error": "bad request"}),
                httpx.Response(200, json=_ok_body("never reached")),
            ])
            client = _make_client(server, max_retries=3)
            with pytest.raises(RuntimeError, match=rf"failed \(client error\): {status}"):
                await client.complete(_req())
            await client._client.aclose()

            assert len(server.requests) == 1

        asyncio.run(main())

    @pytest.mark.parametrize("status", [408, 425])
    def test_transient_4xx_request_timeout_and_too_early_are_retried(
            self, status, monkeypatch):
        _skip_retry_waits(monkeypatch)

        async def main():
            server = FakeOpenAIServer([
                httpx.Response(status, json={"error": "transient 4xx"}),
                httpx.Response(200, json=_ok_body("after retry")),
            ])
            client = _make_client(server, max_retries=3)
            resp = await client.complete(_req())
            await client._client.aclose()

            assert resp.text == "after retry"
            assert len(server.requests) == 2

        asyncio.run(main())

    def test_rate_limited_429_is_retried(self, monkeypatch):
        # 429 is a transient 4xx (server throttle), so it is retried like a
        # 5xx instead of failing immediately.
        _skip_retry_waits(monkeypatch)

        async def main():
            server = FakeOpenAIServer([
                httpx.Response(429, json={"error": "rate limited"}),
                httpx.Response(200, json=_ok_body("after retry")),
            ])
            client = _make_client(server, max_retries=3)
            resp = await client.complete(_req())
            await client._client.aclose()

            assert resp.text == "after retry"
            assert len(server.requests) == 2

        asyncio.run(main())

    def test_forbidden_403_is_client_error_no_retry(self):
        async def main():
            server = FakeOpenAIServer([
                httpx.Response(403, json={"error": "forbidden"}),
                httpx.Response(200, json=_ok_body("never reached")),
            ])
            client = _make_client(server, max_retries=3)
            with pytest.raises(RuntimeError, match=r"failed \(client error\): 403"):
                await client.complete(_req())
            await client._client.aclose()

            assert len(server.requests) == 1

        asyncio.run(main())


class _RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class TestThrottlePolicy:
    def test_429_honors_retry_after_header(self, monkeypatch):
        sleep = _RecordingSleep()
        monkeypatch.setattr(asyncio, "sleep", sleep)

        async def main():
            server = FakeOpenAIServer([
                httpx.Response(429, headers={"Retry-After": "30"},
                              json={"error": "slow down"}),
                httpx.Response(200, json=_ok_body("after window")),
            ])
            client = _make_client(server, max_retries=3)
            resp = await client.complete(_req())
            await client._client.aclose()

            assert resp.text == "after window"
            assert sleep.calls == [30.0]

        asyncio.run(main())

    def test_429_retry_after_capped_at_max_sleep(self, monkeypatch):
        # A hostile Retry-After must not park a worker slice for minutes.
        sleep = _RecordingSleep()
        monkeypatch.setattr(asyncio, "sleep", sleep)

        async def main():
            server = FakeOpenAIServer([
                httpx.Response(429, headers={"Retry-After": "9999"},
                              json={"error": "hostile"}),
                httpx.Response(200, json=_ok_body("after cap")),
            ])
            client = _make_client(server, max_retries=3)
            resp = await client.complete(_req())
            await client._client.aclose()

            assert resp.text == "after cap"
            assert sleep.calls == [llm_openai._THROTTLE_MAX_SLEEP]

        asyncio.run(main())

    def test_429_without_header_backs_off_in_steps_then_exhausts_own_budget(
            self, monkeypatch):
        # No header → ~20s steps (quota windows are tens of seconds); the
        # throttle budget is its own — generic max_retries does not cap it.
        sleep = _RecordingSleep()
        monkeypatch.setattr(asyncio, "sleep", sleep)

        async def main():
            server = FakeOpenAIServer([
                httpx.Response(429, json={"error": "rate limited"})
                for _ in range(llm_openai._THROTTLE_MAX_ATTEMPTS + 1)
            ])
            client = _make_client(server, max_retries=1)
            with pytest.raises(RuntimeError, match="throttled \\(429\\)"):
                await client.complete(_req())
            await client._client.aclose()

            attempted = len(server.requests)
            # wire calls = budget + 1 (the generic ladder's max_retries+1
            # convention; the 9th arrival is the exhausting one)
            assert attempted == llm_openai._THROTTLE_MAX_ATTEMPTS + 1
            assert sleep.calls[:3] == [20.0, 40.0, 60.0]

        asyncio.run(main())

    def test_transport_wrap_exception_retries_then_succeeds(self, monkeypatch):
        # Corp deployments wrap every transport failure in the ant_baselib
        # tracer's HttpxCallingException; the retry ladder must survive the
        # wrapper, not just the bare httpx exception classes.
        class Wrapped(Exception):
            pass

        monkeypatch.setattr(llm_openai, "_TRANSPORT_WRAP_EXCS", (Wrapped,))
        _skip_retry_waits(monkeypatch)

        async def main():
            server = FakeOpenAIServer([
                Wrapped("Error in httpx send hook"),
                httpx.Response(200, json=_ok_body("after wrapper")),
            ])
            client = _make_client(server, max_retries=3)
            resp = await client.complete(_req())
            await client._client.aclose()

            assert resp.text == "after wrapper"
            assert len(server.requests) == 2

        asyncio.run(main())


class TestModelResolution:
    @pytest.mark.parametrize("model,kind,expected", [
        (None, LlmCallKind.ORCHESTRATOR, "orch-model-x"),
        (None, LlmCallKind.SUBAGENT, "default-model-x"),
        (ModelId("explicit-model"), LlmCallKind.ORCHESTRATOR, "explicit-model"),
    ], ids=["orchestrator-override", "subagent-default", "explicit-wins"])
    def test_request_model_field_matches_kind_rules(self, model, kind, expected):
        async def main():
            server = FakeOpenAIServer()
            client = _make_client(server, orchestrator_model="orch-model-x")
            await client.complete(_req(kind=kind, model=model))
            await client._client.aclose()

            sent = server.requests[0]
            assert sent.url.path == "/v1/chat/completions"
            assert json.loads(sent.content)["model"] == expected

        asyncio.run(main())

    def test_orchestrator_kind_without_override_uses_default_model(self):
        async def main():
            server = FakeOpenAIServer()
            client = _make_client(server)
            await client.complete(_req(kind=LlmCallKind.ORCHESTRATOR))
            await client._client.aclose()

            body = json.loads(server.requests[0].content)
            assert body["model"] == "default-model-x"

        asyncio.run(main())


class TestRequestBodyShape:
    def test_body_carries_prompts_max_tokens_and_temperature(self):
        async def main():
            server = FakeOpenAIServer()
            client = _make_client(server)
            request = _req(
                system_prompt="be terse",
                user_prompt="summarize this",
                max_tokens=128,
                temperature=0.7,
            )
            await client.complete(request)
            await client._client.aclose()

            body = json.loads(server.requests[0].content)
            assert body == {
                "model": "default-model-x",
                "messages": [
                    {"role": "system", "content": "be terse"},
                    {"role": "user", "content": "summarize this"},
                ],
                "max_tokens": 128,
                "temperature": 0.7,
            }

        asyncio.run(main())

    def test_body_defaults_when_optionals_omitted(self):
        async def main():
            server = FakeOpenAIServer()
            client = _make_client(server, max_tokens=2048)
            request = CompletionRequest(
                kind=LlmCallKind.SUBAGENT, user_prompt="hi"
            )
            await client.complete(request)
            await client._client.aclose()

            body = json.loads(server.requests[0].content)
            assert body["messages"] == [{"role": "user", "content": "hi"}]
            assert body["max_tokens"] == 2048
            assert body["temperature"] == 0.0

        asyncio.run(main())


class TestResponseParsing:
    def test_unknown_finish_reason_falls_back_to_error(self):
        async def main():
            server = FakeOpenAIServer([
                httpx.Response(200, json=_ok_body("text", finish="obfuscated")),
            ])
            client = _make_client(server)
            resp = await client.complete(_req())
            await client._client.aclose()

            assert resp.finish_reason == FinishReason.ERROR

        asyncio.run(main())

    def test_missing_usage_defaults_to_zero_tokens(self):
        async def main():
            body = {"choices": [{"message": {"content": "no usage"},
                                 "finish_reason": "stop"}]}
            server = FakeOpenAIServer([httpx.Response(200, json=body)])
            client = _make_client(server)
            resp = await client.complete(_req())
            await client._client.aclose()

            assert resp.token_usage == TokenUsage(0, 0, 0)
            assert resp.finish_reason == FinishReason.STOP

        asyncio.run(main())

    @pytest.mark.parametrize("bad_body", [{}, {"choices": []}],
                             ids=["missing-choices", "empty-choices"])
    def test_parse_response_without_choices_raises_clear_error(self, bad_body):
        with pytest.raises(ValueError, match="no choices"):
            OpenAICompatibleClient._parse_response(bad_body, "default-model-x")

    def test_parse_response_none_content_coerces_to_empty_text(self):
        body = {"choices": [{"message": {"content": None},
                             "finish_reason": "stop"}]}
        resp = OpenAICompatibleClient._parse_response(body, "default-model-x")

        assert resp.text == ""
        assert resp.finish_reason == FinishReason.STOP
        assert resp.token_usage == TokenUsage(0, 0, 0)

    def test_parse_response_null_usage_fields_default_to_zero_tokens(self):
        body = {"choices": [{"message": {"content": None},
                             "finish_reason": "stop"}],
                "usage": None}
        resp = OpenAICompatibleClient._parse_response(body, "default-model-x")

        assert resp.text == ""
        assert resp.token_usage == TokenUsage(0, 0, 0)

    def test_complete_with_empty_choices_body_fails_fast_without_retry(self):
        async def main():
            server = FakeOpenAIServer([
                httpx.Response(200, json={"choices": []}),
                httpx.Response(200, json=_ok_body("never parsed")),
            ])
            client = _make_client(server, max_retries=3)
            with pytest.raises(ValueError, match="no choices"):
                await client.complete(_req())
            await client._client.aclose()

            assert len(server.requests) == 1, \
                "a 200 body without choices is malformed, not transient"

        asyncio.run(main())


class TestTimeoutBehavior:
    def test_transport_timeout_raises_typed_error(self):
        # NOTE: found-vs-expected — a MockTransport handler that merely stalls
        # is not subject to the client timeout (httpx timeout enforcement
        # lives in the httpcore transport), so the server stall is simulated
        # as the ReadTimeout the transport would surface.
        async def main():
            server = FakeOpenAIServer([httpx.ReadTimeout("server stalled")])
            client = _make_client(server, timeout_seconds=0.2, max_retries=0)
            with pytest.raises(RuntimeError) as excinfo:
                await client.complete(_req())
            await client._client.aclose()

            assert len(server.requests) == 1
            assert isinstance(excinfo.value.__cause__, httpx.TimeoutException)

        asyncio.run(main())


class TestStubLLMProvider:
    def test_returns_scripted_texts_in_order_then_default(self):
        async def main():
            stub = StubLLMProvider(script=["first", "second"])
            first = await stub.complete(_req())
            second = await stub.complete(_req())
            third = await stub.complete(_req())
            fourth = await stub.complete(_req())

            assert first.text == "first"
            assert second.text == "second"
            assert third.text == '{"kind": "finish", "answer": "stub done"}'
            assert fourth.text == third.text
            assert stub.call_count == 4

        asyncio.run(main())

    def test_unscripted_stub_serves_default_finish_decision(self):
        async def main():
            stub = StubLLMProvider()
            resp = await stub.complete(_req())
            assert resp.text == '{"kind": "finish", "answer": "stub done"}'

        asyncio.run(main())

    def test_set_script_resets_call_count(self):
        async def main():
            stub = StubLLMProvider(script=["a"])
            await stub.complete(_req())
            assert stub.call_count == 1
            stub.set_script(["b"])
            assert stub.call_count == 0
            resp = await stub.complete(_req())
            assert resp.text == "b"

        asyncio.run(main())

    def test_model_passthrough_and_fixed_usage(self):
        async def main():
            stub = StubLLMProvider()
            echo = await stub.complete(_req(model=ModelId("echo-model")))
            default = await stub.complete(_req())
            for resp in (echo, default):
                assert resp.token_usage == TokenUsage(10, 10, 20)
                assert resp.finish_reason == FinishReason.STOP
            assert echo.model == "echo-model"
            assert default.model == "stub"

        asyncio.run(main())


def _sse(data_events: list[dict], done: bool = True) -> bytes:
    lines = [f"data: {json.dumps(event)}" for event in data_events]
    if done:
        lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


class TestStreamingPath:
    def test_openai_stream_yields_deltas_then_usage_chunk(self):
        async def main():
            payload = _sse([
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {
                    "prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7,
                }},
            ])
            server = FakeOpenAIServer([httpx.Response(200, content=payload)])
            client = _make_client(server)
            chunks = [chunk async for chunk in client.stream_complete(_req())]
            await client._client.aclose()

            assert "".join(c.text for c in chunks) == "Hello"
            deltas = [c for c in chunks if c.text]
            assert all(c.finish_reason is None for c in deltas)
            final = chunks[-1]
            assert final.text == ""
            assert final.finish_reason == FinishReason.STOP
            assert final.model == "default-model-x"
            assert final.token_usage == TokenUsage(3, 4, 7)

            sent = json.loads(server.requests[0].read())
            assert sent["stream"] is True
            assert sent["stream_options"] == {"include_usage": True}

        asyncio.run(main())

    def test_openai_stream_rejects_unknown_finish_reason_to_error(self):
        async def main():
            payload = _sse([
                {"choices": [{"delta": {"content": "x"}, "finish_reason": "weird"}]},
                {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ])
            server = FakeOpenAIServer([httpx.Response(200, content=payload)])
            client = _make_client(server)
            chunks = [chunk async for chunk in client.stream_complete(_req())]
            await client._client.aclose()
            assert chunks[-1].finish_reason == FinishReason.ERROR

        asyncio.run(main())

    def test_openai_stream_fail_fast_has_no_retry(self):
        async def main():
            server = FakeOpenAIServer([
                httpx.Response(500, json={"error": "boom"}),
                httpx.Response(200, content=_sse([
                    {"choices": [{"delta": {"content": "never"}}]},
                ])),
            ])
            client = _make_client(server, max_retries=3)
            with pytest.raises(RuntimeError, match="stream failed \\(500\\)"):
                async for _ in client.stream_complete(_req()):
                    pass
            await client._client.aclose()
            assert len(server.requests) == 1, "streaming must not retry"

        asyncio.run(main())

    def test_openai_stream_uses_orchestrator_model_override(self):
        async def main():
            server = FakeOpenAIServer([httpx.Response(200, content=_sse([
                {"choices": [{"delta": {"content": "hi"}}]},
            ]))])
            client = _make_client(server, orchestrator_model="scout-commander")
            async for _ in client.stream_complete(_req(kind=LlmCallKind.ORCHESTRATOR)):
                pass
            await client._client.aclose()
            sent = json.loads(server.requests[0].read())
            assert sent["model"] == "scout-commander"

        asyncio.run(main())

    def test_openai_stream_truncated_without_done_raises_not_fake_stop(self):
        # Clean EOF without [DONE] and without any finish_reason means the
        # server truncated the stream — it must surface as an error, never
        # as a synthesized successful STOP.
        async def main():
            payload = _sse([
                {"choices": [{"delta": {"content": "partial"}}]},
            ], done=False)
            server = FakeOpenAIServer([httpx.Response(200, content=payload)])
            client = _make_client(server)
            seen = []
            with pytest.raises(RuntimeError, match="ended without completion"):
                async for chunk in client.stream_complete(_req()):
                    seen.append(chunk)
            await client._client.aclose()

            assert [c.text for c in seen] == ["partial"], \
                "deltas before truncation stay the caller's to discard"

        asyncio.run(main())

    def test_openai_stream_empty_body_without_done_raises_truncation(self):
        async def main():
            server = FakeOpenAIServer([httpx.Response(200, content=b"")])
            client = _make_client(server)
            with pytest.raises(RuntimeError, match="ended without completion"):
                async for _ in client.stream_complete(_req()):
                    pass
            await client._client.aclose()

        asyncio.run(main())

    def test_openai_stream_usage_without_done_or_finish_raises_truncation(self):
        # A final usage event alone is not a completion signal; truncation
        # after usage must still raise.
        async def main():
            payload = _sse([
                {"choices": [{"delta": {"content": "x"}}]},
                {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ], done=False)
            server = FakeOpenAIServer([httpx.Response(200, content=payload)])
            client = _make_client(server)
            with pytest.raises(RuntimeError, match="ended without completion"):
                async for _ in client.stream_complete(_req()):
                    pass
            await client._client.aclose()

        asyncio.run(main())

    def test_openai_stream_done_without_finish_reason_synthesizes_stop(self):
        async def main():
            payload = _sse([
                {"choices": [{"delta": {"content": "hi"}}]},
            ])
            server = FakeOpenAIServer([httpx.Response(200, content=payload)])
            client = _make_client(server)
            chunks = [chunk async for chunk in client.stream_complete(_req())]
            await client._client.aclose()

            final = chunks[-1]
            assert final.text == ""
            assert final.finish_reason == FinishReason.STOP
            assert final.token_usage == TokenUsage(0, 0, 0)

        asyncio.run(main())

    def test_openai_stream_finish_reason_without_done_still_completes(self):
        # A received finish_reason is a valid completion signal on its own;
        # a missing [DONE] after it does not turn the stream into an error.
        async def main():
            payload = _sse([
                {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
            ], done=False)
            server = FakeOpenAIServer([httpx.Response(200, content=payload)])
            client = _make_client(server)
            chunks = [chunk async for chunk in client.stream_complete(_req())]
            await client._client.aclose()

            final = chunks[-1]
            assert final.finish_reason == FinishReason.STOP
            assert final.token_usage == TokenUsage(0, 0, 0)

        asyncio.run(main())

    def test_stub_stream_mirrors_scripted_complete(self):
        async def main():
            stub = StubLLMProvider(script=["spot decision"])
            chunks = [c async for c in stub.stream_complete(_req(model=ModelId("echo-model")))]
            assert [c.text for c in chunks] == ["spot decision", ""]
            final = chunks[-1]
            assert final.finish_reason == FinishReason.STOP
            assert final.model == "echo-model"
            assert final.token_usage == TokenUsage(10, 10, 20)
            assert stub.call_count == 1, "stream shares the script counter with complete()"

        asyncio.run(main())