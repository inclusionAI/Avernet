import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from secbaas.community.adapters.web.routers.bcn_downlink.bcn_model import (
    ChatSendRequest,
    InteractionResolveRequest,
)
from secbaas.community.adapters.web.routers.bcn_downlink.bcn_router import (
    _METHOD_DISPATCH,
    _dispatch_chat_send,
    _dispatch_chat_send_stream,
    _dispatch_interaction_resolve,
    validate_bcn_token,
)
from secbaas.community.api.bcn import (
    Attachment as DomainAttachment,
)
from secbaas.community.api.bcn import (
    BcnInteractionResolveResult,
    BcnInvalidRequestError,
)
from secbaas.community.api.bot_interaction import InteractionBadRequestError
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.sse import DefaultStreamConverter


def _interaction_resolve_body(**param_overrides):
    params = {
        "bcsRunId": "bcs-run-1",
        "runId": "provider-run-1",
        "interactionId": "interaction-ask-1",
        "kind": "ask_user",
        "idempotencyKey": "idem-ask-1",
        "action": "submit",
        "answers": {
            "deploy_target": {
                "header": "Deployment environment",
                "values": ["staging"],
                "question": "what's your deploy target?",
            },
            "components": {
                "header": "Components",
                "values": ["web", "worker"],
                "question": "whats' the components?",
            },
        },
    }
    params.update(param_overrides)
    return {
        "type": "req",
        "id": "bcn-resolve-1",
        "method": "interaction.resolve",
        "session_id": "session-1",
        "bcn_group_id": "group-1",
        "to_bot": {"provider_id": "baas", "provider_bot_ref": "bot-1"},
        "params": params,
        "timeout_ms": 3_600_000,
    }


def test_interaction_resolve_request_accepts_bcs_ask_user_shape() -> None:
    body = _interaction_resolve_body()
    body["params"]["answers"]["components"]["customValues"] = ["scheduler"]

    request = InteractionResolveRequest.model_validate(body)

    assert request.params.interaction_id == "interaction-ask-1"
    assert request.params.action == "submit"
    assert request.params.answers is not None
    assert request.params.answers["components"].values == ["web", "worker"]
    assert request.params.answers["components"].custom_values == ["scheduler"]
    assert request.params.answers["deploy_target"].header == ("Deployment environment")


@pytest.mark.parametrize("custom_values", [[""], ["   "], ["", "   "]])
def test_interaction_resolve_request_preserves_blank_custom_values(
    custom_values: list[str],
) -> None:
    body = _interaction_resolve_body()
    body["params"]["answers"]["components"]["customValues"] = custom_values

    request = InteractionResolveRequest.model_validate(body)

    assert request.params.answers is not None
    assert request.params.answers["components"].custom_values == custom_values


@pytest.mark.parametrize("values", [[], [""], ["   "], ["custom raw value"]])
def test_interaction_resolve_request_accepts_custom_and_skipped_values(
    values: list[str],
) -> None:
    request = InteractionResolveRequest.model_validate(
        _interaction_resolve_body(
            answers={
                "question-1": {
                    "header": "Question",
                    "values": values,
                    "question": "Question?",
                }
            }
        )
    )

    assert request.params.answers is not None
    assert request.params.answers["question-1"].values == values


def test_interaction_resolve_is_registered_on_json_downlink() -> None:
    request_model, dispatcher = _METHOD_DISPATCH["interaction.resolve"]

    assert request_model is InteractionResolveRequest
    assert dispatcher is _dispatch_interaction_resolve


@pytest.mark.asyncio
async def test_dispatch_interaction_resolve_preserves_full_request() -> None:
    captured = {}

    class _CapturingService:
        async def handle_interaction_resolve(self, resolve_input):
            captured["input"] = resolve_input
            return BcnInteractionResolveResult(ok=True)

    body = _interaction_resolve_body()
    body["params"]["answers"]["components"]["customValues"] = ["scheduler"]
    request = InteractionResolveRequest.model_validate(body)

    response = await _dispatch_interaction_resolve(request, _CapturingService())

    assert response.model_dump(exclude_none=True) == {"ok": True}
    resolve_input = captured["input"]
    assert resolve_input.id == "bcn-resolve-1"
    assert resolve_input.session_id == "session-1"
    assert resolve_input.bcn_group_id == "group-1"
    assert resolve_input.interaction_id == "interaction-ask-1"
    assert resolve_input.kind == "ask_user"
    assert resolve_input.idempotency_key == "idem-ask-1"
    assert resolve_input.action == "submit"
    assert resolve_input.answers["components"].values == ("web", "worker")
    assert resolve_input.answers["components"].custom_values == ("scheduler",)
    assert resolve_input.answers["components"].question == "whats' the components?"
    assert resolve_input.answers["deploy_target"].header == "Deployment environment"
    assert resolve_input.request_envelope == body


@pytest.mark.asyncio
async def test_dispatch_interaction_resolve_returns_finite_domain_error() -> None:
    class _RejectingService:
        async def handle_interaction_resolve(self, _resolve_input):
            raise InteractionBadRequestError("resolution does not match interaction")

    request = InteractionResolveRequest.model_validate(_interaction_resolve_body())

    response = await _dispatch_interaction_resolve(request, _RejectingService())

    assert response.model_dump(exclude_none=True) == {
        "ok": False,
        "retryable": False,
        "error": "resolution does not match interaction",
    }
    assert "staging" not in response.error


@pytest.mark.parametrize(
    "params",
    [
        {"answers": {}},
        {
            "answers": {
                "question-1": {
                    "header": "Question",
                    "values": [7],
                    "question": "Question?",
                },
            }
        },
        {
            "answers": {
                "question-1": {
                    "header": "Question",
                    "values": ["value"],
                    "question": "   ",
                },
            }
        },
        {
            "answers": {
                "question-1": {
                    "values": ["value"],
                    "question": "Question?",
                },
            }
        },
        {
            "answers": {
                "question-1": {
                    "header": "",
                    "values": ["value"],
                    "question": "Question?",
                },
            }
        },
        {
            "answers": {
                "question-1": {
                    "header": "   ",
                    "values": ["value"],
                    "question": "Question?",
                },
            }
        },
        {
            "answers": {
                "question-1": {
                    "header": 7,
                    "values": ["value"],
                    "question": "Question?",
                },
            }
        },
    ],
)
def test_interaction_resolve_request_rejects_bad_ask_user_answers(params) -> None:
    with pytest.raises(ValidationError):
        InteractionResolveRequest.model_validate(_interaction_resolve_body(**params))


@pytest.mark.parametrize("kind", ["exec", "mode_switch"])
def test_interaction_resolve_request_requires_decision_for_non_ask(kind) -> None:
    with pytest.raises(ValidationError):
        InteractionResolveRequest.model_validate(
            _interaction_resolve_body(
                kind=kind,
                action=None,
                answers=None,
                decision=None,
            )
        )


def test_validate_bcn_token_uses_secret_store_when_env_is_unset(monkeypatch):
    monkeypatch.delenv("BCS_BAAS_DOWNLINK_TOKEN", raising=False)
    secret_plugin = MagicMock()
    secret_plugin.get_secret.return_value = "secret-token"

    assert validate_bcn_token("Bearer secret-token", secret_plugin) == "secret-token"
    secret_plugin.get_secret.assert_called_once_with(
        "other_manual_secbaas_bcn_to_provider_token"
    )


def test_validate_bcn_token_accepts_local_stub_when_secret_store_is_unavailable(
    monkeypatch,
):
    monkeypatch.delenv("BCS_BAAS_DOWNLINK_TOKEN", raising=False)
    secret_plugin = MagicMock()
    secret_plugin.get_secret.side_effect = RuntimeError("secret store unavailable")

    assert validate_bcn_token("Bearer local-token", secret_plugin) == "local-token"


class _StreamService:
    async def handle_chat_send_stream(self, _input):
        async def _chunks():
            # "assistant" stream is noise — converter drops it (returns None)
            yield StreamChunk(
                type="agent",
                metadata={
                    "engine_frame": {
                        "stream": "assistant",
                        "data": {"text": "mirror"},
                    }
                },
            )
            # delta chunk is kept — converter produces a chat event
            yield StreamChunk(type="delta", content="hello")

        return _chunks()


class _ConverterFactory:
    def create(self, name):
        assert name == "default"
        return DefaultStreamConverter()


def _chat_send_request() -> ChatSendRequest:
    return ChatSendRequest.model_validate(
        {
            "id": "run-1",
            "session_id": "session-1",
            "bcn_group_id": "group-1",
            "to_bot": {
                "provider_id": "baas",
                "provider_bot_ref": "bot-1",
            },
            "from": {"kind": "human", "id": "user-1"},
            "message": {"role": "user", "content": "hi"},
            "extensions": {"response_mode": "stream"},
        }
    )


@pytest.mark.asyncio
async def test_stream_dispatch_skips_dropped_converter_events():
    response = await _dispatch_chat_send_stream(
        _chat_send_request(),
        _StreamService(),
        _ConverterFactory(),
    )

    chunks = []
    async for item in response.body_iterator:
        chunks.append(item)

    assert len(chunks) == 1
    assert chunks[0].startswith("id: 1\nevent: chat\n")
    data_line = next(
        line for line in chunks[0].splitlines() if line.startswith("data: ")
    )
    data = json.loads(data_line.removeprefix("data: "))
    data.pop("ts", None)  # ts is stamped at conversion time, not asserted
    assert data == {
        "runId": "run-1",
        "seq": 1,
        "state": "delta",
        "deltaText": "hello",
    }


@pytest.mark.asyncio
async def test_stream_dispatch_error_yields_error_sse():
    """When chunk_iter raises, on_error produces an error SSE event."""

    class _ErrorStreamService:
        async def handle_chat_send_stream(self, _input):
            async def _chunks():
                yield StreamChunk(type="delta", content="hi")
                raise RuntimeError("chunk boom")

            return _chunks()

    response = await _dispatch_chat_send_stream(
        _chat_send_request(),
        _ErrorStreamService(),
        _ConverterFactory(),
    )

    items = []
    async for item in response.body_iterator:
        items.append(item)

    # delta + error
    assert len(items) == 2
    assert items[0].startswith("id: 1\nevent: chat\n")
    assert "error" in items[1]
    assert "INTERNAL_ERROR" in items[1]


# ── attachment passthrough tests ──


@pytest.mark.asyncio
async def test_dispatch_chat_send_passes_attachments():
    """_dispatch_chat_send constructs ChatSendInput with attachments list."""
    # Record the ChatSendInput that handle_chat_send receives
    captured = {}

    class _CapturingService:
        async def handle_chat_send(self, input_):
            captured["input"] = input_
            from secbaas.community.api.bcn import ChatSendResult

            return ChatSendResult(ok=True)

    req = ChatSendRequest.model_validate(
        {
            "id": "run-1",
            "session_id": "session-1",
            "bcn_group_id": "group-1",
            "to_bot": {"provider_id": "baas", "provider_bot_ref": "bot-1"},
            "from": {"kind": "human", "id": "user-1"},
            "message": {"role": "user", "content": "hi"},
            "attachments": [
                {
                    "attachment_id": "att_1",
                    "type": "file",
                    "file_name": "brief.pdf",
                    "url": "https://cdn.example.com/att_1",
                },
            ],
        }
    )

    await _dispatch_chat_send(req, _CapturingService())

    input_ = captured["input"]
    assert input_.attachments is not None, "ChatSendInput.attachments must not be None"
    assert len(input_.attachments) == 1
    assert input_.attachments[0].attachment_id == "att_1"
    assert input_.attachments[0].type == "file"
    assert isinstance(input_.attachments[0], DomainAttachment), (
        "attachments must be domain dataclass instances, not Pydantic models"
    )


@pytest.mark.asyncio
async def test_dispatch_chat_send_passes_attachments_none():
    """_dispatch_chat_send constructs ChatSendInput with attachments=None when absent."""
    captured = {}

    class _CapturingService:
        async def handle_chat_send(self, input_):
            captured["input"] = input_
            from secbaas.community.api.bcn import ChatSendResult

            return ChatSendResult(ok=True)

    req = ChatSendRequest.model_validate(
        {
            "id": "run-1",
            "session_id": "session-1",
            "bcn_group_id": "group-1",
            "to_bot": {"provider_id": "baas", "provider_bot_ref": "bot-1"},
            "from": {"kind": "human", "id": "user-1"},
            "message": {"role": "user", "content": "hi"},
            # no "attachments" key
        }
    )

    await _dispatch_chat_send(req, _CapturingService())

    input_ = captured["input"]
    assert input_.attachments is None, (
        "ChatSendInput.attachments must be None when absent"
    )


@pytest.mark.asyncio
async def test_dispatch_chat_send_value_error_wraps_to_bcn_invalid_request():
    """When handle_chat_send raises ValueError, it's wrapped in BcnInvalidRequestError."""

    class _ValueErrorService:
        async def handle_chat_send(self, input_):
            raise ValueError("invalid timeout value")

    with pytest.raises(BcnInvalidRequestError) as exc_info:
        await _dispatch_chat_send(_chat_send_request(), _ValueErrorService())
    assert "invalid timeout value" in str(exc_info.value)
