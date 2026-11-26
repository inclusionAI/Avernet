"""test async chat interactions; focused regression tests and fixtures."""

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.bot_interaction import (
    InteractionRequestedResult,
    InteractionResolution,
    InteractionResolvedResult,
)
from secbaas.community.core.service.bot_interaction import InteractionDispatch
from secbaas.community.core.service.bot_run._async_chat_events import (
    _public_interaction_envelope,
)
from secbaas.community.core.service.bot_run._interaction_protocol import (
    EngineInteractionResolveExchange,
)
from secbaas.community.core.service.sse import DefaultStreamConverter
from tests.utils.chat_transport import make_chat_client

from ._async_chat_coverage_support import (
    _setup_session_state,
)
from ._async_chat_coverage_support import (
    mock_bot_ws as mock_bot_ws,
)


class TestPublicInteractionEnvelope:
    def test_rejects_non_object_payload(self):
        with pytest.raises(
            ValueError, match="interaction event envelope payload must be an object"
        ):
            _public_interaction_envelope(
                {"type": "event", "payload": "not-an-object"},
                baas_interaction_id="BAAS-INTERACTION-public-1",
            )

    def test_rewrites_legacy_id_without_mutating_engine_envelope(self):
        engine_envelope = {
            "type": "event",
            "event": "interaction.resolved",
            "payload": {
                "sessionKey": "sk1",
                "interactionId": "engine-interaction-1",
                "id": "legacy-engine-interaction-1",
            },
        }

        public_envelope = _public_interaction_envelope(
            engine_envelope,
            baas_interaction_id="BAAS-INTERACTION-public-1",
        )

        assert public_envelope == {
            "type": "event",
            "event": "interaction.resolved",
            "payload": {
                "sessionKey": "sk1",
                "interactionId": "BAAS-INTERACTION-public-1",
                "id": "BAAS-INTERACTION-public-1",
            },
        }
        assert engine_envelope == {
            "type": "event",
            "event": "interaction.resolved",
            "payload": {
                "sessionKey": "sk1",
                "interactionId": "engine-interaction-1",
                "id": "legacy-engine-interaction-1",
            },
        }


class TestInteractionEvents:
    def test_requested_is_ignored_when_interaction_processing_is_disabled(
        self, mock_bot_ws
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_interaction_requested(
            {"sessionKey": "sk1", "interactionId": "int-1"}
        )

        assert state.stream_queue.empty()
        assert client._interaction_tasks == {}

    def test_resolved_is_ignored_when_interaction_processing_is_disabled(
        self, mock_bot_ws
    ):
        client = make_chat_client(uri="ws://host/ws", max_retries=0)
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_interaction_resolved({"sessionKey": "sk1", "interactionId": "int-1"})

        assert state.stream_queue.empty()

    @pytest.mark.asyncio
    async def test_requested_passes_validated_identity_and_emits_chunk(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.record_requested.return_value = InteractionRequestedResult(
            baas_interaction_id="BAAS-INTERACTION-public-1",
            created=True,
        )
        client = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        payload = {
            "sessionKey": "sk1",
            "interactionId": "int-1",
            "kind": "exec",
            "seq": 42,
            "expiresAtMs": 123456,
            "options": [{"label": "Once", "decision": "allow-once"}],
        }
        client._on_interaction_requested(payload)

        service.record_requested.assert_called_once_with(
            session_key="sk1",
            interaction_id="int-1",
            envelope={
                "type": "event",
                "event": "interaction.requested",
                "payload": payload,
                "seq": 42,
            },
            allowed_decisions=("allow-once",),
            expires_at_ms=123456,
        )
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "interaction"
        assert chunk.metadata["event"] == "interaction.requested"
        assert chunk.metadata["payload"]["event"] == "interaction.requested"
        assert chunk.metadata["payload"]["seq"] == 42
        assert (
            chunk.metadata["payload"]["payload"]["interactionId"]
            == "BAAS-INTERACTION-public-1"
        )
        assert payload["interactionId"] == "int-1"

    @pytest.mark.asyncio
    async def test_requested_mixed_options_reach_persistence_chunk_and_converter(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.record_requested.return_value = InteractionRequestedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mixed",
            created=True,
        )
        client = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()
        payload = {
            "sessionKey": "sk1",
            "interactionId": "int-mixed",
            "kind": "exec",
            "command": "make test",
            "options": [
                "not-an-object",
                {"label": "Once", "decision": "", "value": "allow-once"},
                {"label": "Deny", "decision": 42, "value": "deny"},
                {"label": "Once replacement", "decision": "allow-once"},
                {},
            ],
        }
        original = deepcopy(payload)

        client._on_interaction_requested(payload)

        persisted = service.record_requested.call_args.kwargs
        assert persisted["allowed_decisions"] == ("allow-once", "deny")
        assert persisted["envelope"]["payload"] is payload
        assert payload == original
        chunk = state.stream_queue.get_nowait()
        event = DefaultStreamConverter().convert(chunk, run_id="bcn-run")
        assert event is not None
        data = json.loads(event.data)
        assert data["options"] == [
            {"label": "Once replacement", "decision": "allow-once"},
            {"label": "Deny", "decision": "deny"},
        ]

    @pytest.mark.asyncio
    async def test_duplicate_requested_does_not_emit_duplicate_chunk(self, mock_bot_ws):
        service = MagicMock()
        service.record_requested.side_effect = [
            InteractionRequestedResult(
                baas_interaction_id="BAAS-INTERACTION-public-1",
                created=True,
            ),
            InteractionRequestedResult(
                baas_interaction_id="BAAS-INTERACTION-public-1",
                created=False,
            ),
        ]
        client = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()
        payload = {"sessionKey": "sk1", "interactionId": "int-1"}

        client._on_interaction_requested(payload)
        client._on_interaction_requested(payload)

        assert state.stream_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_requested_persistence_failure_is_not_silently_ignored(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.record_requested.side_effect = RuntimeError("db unavailable")
        client = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        with pytest.raises(RuntimeError, match="db unavailable"):
            client._on_interaction_requested(
                {"sessionKey": "sk1", "interactionId": "int-1"}
            )

        assert state.stream_queue.empty()

    @pytest.mark.asyncio
    async def test_resolved_uses_callback_identity_and_emits_public_chunk(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.mark_resolved.return_value = InteractionResolvedResult(
            baas_interaction_id="BAAS-INTERACTION-public-1",
            applied=True,
        )
        client = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        payload = {
            "sessionKey": "sk1",
            "interactionId": "int-1",
            "decision": "allow-once",
        }
        client._on_interaction_resolved(payload)

        service.mark_resolved.assert_called_once_with(
            session_key="sk1",
            interaction_id="int-1",
            envelope={
                "type": "event",
                "event": "interaction.resolved",
                "payload": payload,
            },
        )
        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "interaction"
        assert chunk.metadata["event"] == "interaction.resolved"
        assert chunk.metadata["payload"]["event"] == "interaction.resolved"
        assert (
            chunk.metadata["payload"]["payload"]["interactionId"]
            == "BAAS-INTERACTION-public-1"
        )
        assert payload["interactionId"] == "int-1"

    @pytest.mark.asyncio
    async def test_duplicate_resolved_does_not_emit_duplicate_chunk(self, mock_bot_ws):
        service = MagicMock()
        service.mark_resolved.side_effect = [
            InteractionResolvedResult(
                baas_interaction_id="BAAS-INTERACTION-public-1",
                applied=True,
            ),
            InteractionResolvedResult(
                baas_interaction_id="BAAS-INTERACTION-public-1",
                applied=False,
            ),
        ]
        client = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()
        payload = {"sessionKey": "sk1", "interactionId": "int-1"}

        client._on_interaction_resolved(payload)
        client._on_interaction_resolved(payload)

        assert state.stream_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_mode_transition_resolved_emits_once_after_rpc_terminalized_record(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.record_requested.return_value = InteractionRequestedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mode",
            created=True,
        )
        service.mark_resolved.return_value = InteractionResolvedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mode",
            applied=False,
        )
        client = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()
        requested = {
            "sessionKey": "sk1",
            "interactionId": "int-mode",
            "kind": "mode_switch",
            "options": [
                {"label": "Continue to execution", "decision": "proceed"},
                {"label": "Stay in planning", "decision": "stay"},
            ],
        }
        client._on_interaction_requested(requested)
        requested_chunk = state.stream_queue.get_nowait()
        assert requested_chunk.metadata["event"] == "interaction.requested"
        resolved = {
            "sessionKey": "sk1",
            "interactionId": "int-mode",
            "transitionId": "int-mode",
            "kind": "mode_switch",
            "phase": "proceeded",
            "decision": "proceed",
            "status": "resolved",
            "options": [
                {"label": "Continue to execution", "decision": "proceed"},
                {"label": "Stay in planning", "decision": "stay"},
            ],
            "seq": 131,
        }

        client._on_mode_transition_resolved(resolved)
        client._on_mode_transition_resolved(resolved)

        assert state.stream_queue.qsize() == 1
        chunk = state.stream_queue.get_nowait()
        assert chunk.metadata["event"] == "mode_transition.resolved"
        assert chunk.metadata["payload"] == {
            "type": "event",
            "event": "mode_transition.resolved",
            "payload": {
                **resolved,
                "interactionId": "BAAS-INTERACTION-public-mode",
                "transitionId": "BAAS-INTERACTION-public-mode",
            },
            "seq": 131,
        }
        assert resolved["interactionId"] == "int-mode"
        assert resolved["transitionId"] == "int-mode"
        event = DefaultStreamConverter().convert(chunk, run_id="bcn-run")
        assert event is not None
        data = json.loads(event.data)
        assert isinstance(data.pop("ts"), int)
        assert data == {
            "runId": "bcn-run",
            "seq": 1,
            "interactionId": "BAAS-INTERACTION-public-mode",
            "kind": "mode_switch",
            "phase": "resolved",
            "decision": "proceed",
        }

    def test_mode_transition_resolved_without_exposed_request_does_not_emit(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.mark_resolved.return_value = InteractionResolvedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mode",
            applied=True,
        )
        client = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        client._on_mode_transition_resolved(
            {
                "sessionKey": "sk1",
                "interactionId": "int-mode",
                "kind": "mode_switch",
                "decision": "stay",
            }
        )

        assert state.stream_queue.empty()

    def test_mode_transition_requested_without_stream_queue_is_not_exposed(
        self, mock_bot_ws
    ):
        service = MagicMock()
        service.record_requested.return_value = InteractionRequestedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mode",
            created=True,
        )
        service.mark_resolved.return_value = InteractionResolvedResult(
            baas_interaction_id="BAAS-INTERACTION-public-mode",
            applied=False,
        )
        client = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")

        client._on_interaction_requested(
            {
                "sessionKey": "sk1",
                "interactionId": "int-mode",
                "kind": "mode_switch",
                "options": [{"label": "Continue", "decision": "proceed"}],
            }
        )
        state.stream_queue = asyncio.Queue()
        client._on_mode_transition_resolved(
            {
                "sessionKey": "sk1",
                "interactionId": "int-mode",
                "kind": "mode_switch",
                "decision": "proceed",
            }
        )

        assert state.stream_queue.empty()

    def test_mode_transition_resolved_rejects_non_mode_kind_before_delivery(
        self, mock_bot_ws
    ):
        service = MagicMock()
        client = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        state = _setup_session_state(client, "sk1")
        state.stream_queue = asyncio.Queue()

        with pytest.raises(ValueError, match="kind must be mode_switch"):
            client._on_mode_transition_resolved(
                {
                    "sessionKey": "sk1",
                    "interactionId": "int-exec",
                    "kind": "exec",
                    "decision": "allow-once",
                }
            )

        assert state.stream_queue.empty()
        service.mark_resolved.assert_not_called()


@pytest.mark.asyncio
async def test_interaction_dispatch_uses_typed_claim_command(mock_bot_ws):
    service = MagicMock()
    resolution = InteractionResolution(
        kind="ask_user",
        decision="submit",
        answer="target: staging",
        message="target: staging",
        values={"target": "staging"},
        answers={"Where?": "staging"},
        selected_options=(("staging",),),
    )
    service.claim_for_dispatch.return_value = InteractionDispatch(
        session_key="sk1",
        interaction_id="int-1",
        resolution=resolution,
    )
    engine_client = MagicMock()
    engine_request = {
        "type": "req",
        "id": "engine-1",
        "method": "interaction.resolve",
        "params": {"interactionId": "int-1", "decision": "allow-once"},
    }
    engine_response = {"type": "res", "id": "engine-1", "ok": True}
    engine_client.interaction_resolve = AsyncMock(
        return_value=EngineInteractionResolveExchange.from_frames(
            request=engine_request,
            response=engine_response,
        )
    )
    client = make_chat_client(
        uri="ws://host/ws", max_retries=0, interaction_service=service
    )
    client._chat_transport._client = engine_client

    await client._dispatch_interaction_answer(
        session_key="sk1",
        interaction_id="int-1",
        deadline_ms=None,
    )

    engine_client.interaction_resolve.assert_awaited_once_with(
        interaction_id="int-1",
        resolution=resolution,
    )
    service.record_engine_exchange.assert_called_once_with(
        session_key="sk1",
        interaction_id="int-1",
        engine_req=engine_request,
        engine_res=engine_response,
    )


@pytest.mark.asyncio
async def test_mode_switch_dispatch_marks_accepted_rpc_resolved(mock_bot_ws):
    service = MagicMock()
    resolution = InteractionResolution(kind="mode_switch", decision="stay")
    service.claim_for_dispatch.return_value = InteractionDispatch(
        session_key="sk1",
        interaction_id="int-mode-1",
        resolution=resolution,
    )
    engine_client = MagicMock()
    engine_request = {
        "type": "req",
        "id": "engine-mode-1",
        "method": "mode_transition.resolve",
        "params": {"transitionId": "int-mode-1", "decision": "stay"},
    }
    engine_response = {
        "type": "res",
        "id": "engine-mode-1",
        "ok": True,
        "payload": {
            "accepted": True,
            "transitionId": "int-mode-1",
            "decision": "stay",
        },
    }
    engine_client.interaction_resolve = AsyncMock(
        return_value=EngineInteractionResolveExchange.from_frames(
            request=engine_request,
            response=engine_response,
        )
    )
    client = make_chat_client(
        uri="ws://host/ws", max_retries=0, interaction_service=service
    )
    client._chat_transport._client = engine_client

    await client._dispatch_interaction_answer(
        session_key="sk1",
        interaction_id="int-mode-1",
        deadline_ms=None,
    )

    service.mark_resolved.assert_called_once_with(
        session_key="sk1",
        interaction_id="int-mode-1",
        envelope=engine_response,
    )
