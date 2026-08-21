# Mode Transition Resolved SSE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Convert the Engine's top-level `mode_transition.resolved` event into one BCN-compatible resolved interaction SSE while retaining the legacy accepted-RPC terminalization fallback.

**Architecture:** Extend the typed BaaS Engine boundary and SSE converter with an additive raw event alias. Track exposed mode-switch requests in the existing stream-local `SessionState`, so a late terminal Engine event is delivered once even when the database was already terminalized by the RPC fallback.

**Tech Stack:** Python 3.12, dataclasses, asyncio, pytest, unittest.mock, Ruff

**Spec:** `src/baas/docs/2026-08-20-mode-transition-resolved-sse-design.md`

## Global Constraints

- Change only BaaS; do not modify Engine behavior or Engine protocol frames.
- Preserve the raw Engine event name `mode_transition.resolved` in the typed envelope and stream chunk.
- Canonicalize the public BCN phase from the event name to `resolved`; never expose Engine `phase = proceeded` as the BCN phase.
- Deliver the terminal mode-switch SSE only for a mode-switch requested chunk exposed on the same active stream, and deliver it at most once.
- Keep the accepted `mode_transition.resolve` RPC response fallback for older Engine versions.
- Do not overwrite an already terminal database record with a late Engine event.
- Do not log raw interaction payloads, decision labels, questions, commands, answers, or selected options.

---

### Task 1: Type and convert the raw mode-transition event

**Files:**
- Modify: `src/baas/src/secbaas/community/core/service/bot_run/_interaction_protocol.py`
- Modify: `src/baas/src/secbaas/community/core/service/sse/_default_converter.py`
- Test: `src/baas/tests/unit/core/service/bot_run/test_interaction_protocol.py`
- Test: `src/baas/tests/unit/core/service/sse/test_default_converter.py`

**Interfaces:**
- Consumes: Engine payloads with `interactionId`, `kind = mode_switch`, `decision`, and raw lifecycle fields.
- Produces: `EngineInteractionResolvedEvent.from_mode_transition_payload(*, session_key: str, payload: JsonObject) -> EngineInteractionResolvedEvent` and a converter result with `event = interaction`, `phase = resolved`.

- [x] **Step 1: Write failing typed-boundary tests**

Add a real-payload characterization test and fail-closed kind validation:

```python
def test_mode_transition_resolved_preserves_raw_event_name() -> None:
    payload = {
        "sessionKey": "payload-session",
        "interactionId": "int-mode",
        "transitionId": "int-mode",
        "kind": "mode_switch",
        "phase": "proceeded",
        "decision": "proceed",
        "seq": 131,
    }

    event = EngineInteractionResolvedEvent.from_mode_transition_payload(
        session_key="trusted-session",
        payload=payload,
    )

    assert event.session_key == "trusted-session"
    assert event.interaction_id == "int-mode"
    assert event.envelope == {
        "type": "event",
        "event": "mode_transition.resolved",
        "payload": payload,
        "seq": 131,
    }


def test_mode_transition_resolved_rejects_non_mode_kind() -> None:
    with pytest.raises(ValueError, match="kind must be mode_switch"):
        EngineInteractionResolvedEvent.from_mode_transition_payload(
            session_key="s-1",
            payload={"interactionId": "int-1", "kind": "exec"},
        )
```

- [x] **Step 2: Write the failing converter test**

Use the observed Engine lifecycle phase and assert the public canonical data:

```python
def test_mode_transition_resolved_maps_actual_engine_event_to_common_path(self):
    converter = DefaultStreamConverter()
    event = converter.convert(
        _interaction_chunk(
            "mode_transition.resolved",
            {
                "interactionId": "int-mode",
                "kind": "mode_switch",
                "phase": "proceeded",
                "decision": "proceed",
                "status": "resolved",
                "options": [{"label": "Continue", "decision": "proceed"}],
            },
        ),
        run_id="bcn-run",
    )

    assert event is not None
    assert event.event == "interaction"
    assert _data(event) == {
        "runId": "bcn-run",
        "seq": 1,
        "interactionId": "int-mode",
        "kind": "mode_switch",
        "phase": "resolved",
        "decision": "proceed",
    }
```

- [x] **Step 3: Run the tests to verify RED**

Run:

```bash
cd src/baas
.venv/bin/pytest tests/unit/core/service/bot_run/test_interaction_protocol.py -k mode_transition_resolved -q
.venv/bin/pytest tests/unit/core/service/sse/test_default_converter.py -k mode_transition_resolved -q
```

Expected: failures because the typed constructor does not exist and the converter rejects the event name.

- [x] **Step 4: Implement the minimal typed boundary and converter alias**

Extend the event literal and add a dedicated constructor that validates the mode kind while retaining the original envelope:

```python
InteractionEventName = Literal[
    "interaction.requested",
    "interaction.resolved",
    "mode_transition.resolved",
]

@classmethod
def from_mode_transition_payload(
    cls,
    *,
    session_key: str,
    payload: JsonObject,
) -> EngineInteractionResolvedEvent:
    if payload.get("kind") != "mode_switch":
        raise ValueError("mode transition resolved kind must be mode_switch")
    return cls(
        session_key=_required_identity(session_key, "sessionKey"),
        interaction_id=_interaction_id(payload),
        envelope=_event_envelope("mode_transition.resolved", payload),
    )
```

Replace implicit phase inference with an explicit event-to-phase map:

```python
_INTERACTION_EVENT_PHASES = {
    "interaction.requested": "requested",
    "interaction.resolved": "resolved",
    "mode_transition.resolved": "resolved",
}

phase = _INTERACTION_EVENT_PHASES[event]
```

- [x] **Step 5: Run focused tests to verify GREEN**

Run the two commands from Step 3. Expected: all selected tests pass.

- [x] **Step 6: Commit the boundary mapping**

```bash
git add src/baas/src/secbaas/community/core/service/bot_run/_interaction_protocol.py src/baas/src/secbaas/community/core/service/sse/_default_converter.py src/baas/tests/unit/core/service/bot_run/test_interaction_protocol.py src/baas/tests/unit/core/service/sse/test_default_converter.py
git commit -m "feat(baas): map mode transition resolved events"
```

---

### Task 2: Subscribe and deliver the terminal mode-switch event once

**Files:**
- Modify: `src/baas/src/secbaas/community/core/service/bot_run/_session_state.py`
- Modify: `src/baas/src/secbaas/community/core/service/bot_run/_async_chat_client.py`
- Test: `src/baas/tests/unit/core/service/bot_run/test_async_chat_client_coverage.py`

**Interfaces:**
- Consumes: `EngineInteractionResolvedEvent.from_mode_transition_payload` from Task 1 and existing `BotInteractionService.mark_resolved`.
- Produces: `AsyncChatClient._on_mode_transition_resolved` and stream-local `SessionState.pending_mode_transition_ids: set[str]`.

- [x] **Step 1: Write failing connect and reconnect registration tests**

Extend the existing callback-registration assertions so both initial connect and reconstructed reconnect clients contain:

```python
mock_client.on_event.assert_any_call(
    "mode_transition.resolved",
    client._on_mode_transition_resolved,
)
```

Use the same mocked `BotWebSocketClient` instances already used by the connect and reconnect tests; do not introduce a second fake websocket stack.

- [x] **Step 2: Write failing stream-delivery tests**

Cover the RPC-won race, replay suppression, and exposure gate:

```python
def test_mode_transition_resolved_emits_after_rpc_terminalized_record(self, mock_bot_ws):
    service = MagicMock()
    service.record_requested.return_value = True
    service.mark_resolved.return_value = False
    client = AsyncChatClient(
        uri="ws://host/ws", max_retries=0, interaction_service=service
    )
    state = _setup_session_state(client, "sk1")
    state.stream_queue = asyncio.Queue()
    requested = {
        "sessionKey": "sk1",
        "interactionId": "int-mode",
        "kind": "mode_switch",
        "options": [{"label": "Continue", "decision": "proceed"}],
    }

    client._on_interaction_requested(requested)
    state.stream_queue.get_nowait()
    resolved = {
        "sessionKey": "sk1",
        "interactionId": "int-mode",
        "kind": "mode_switch",
        "phase": "proceeded",
        "decision": "proceed",
    }
    client._on_mode_transition_resolved(resolved)
    client._on_mode_transition_resolved(resolved)

    assert state.stream_queue.qsize() == 1
    chunk = state.stream_queue.get_nowait()
    assert chunk.metadata["event"] == "mode_transition.resolved"
    assert chunk.metadata["payload"]["event"] == "mode_transition.resolved"
```

Add separate tests proving a terminal event without a previously emitted mode
request does not emit, and that a non-mode kind raises before persistence or
delivery.

- [x] **Step 3: Write the failing sanitized-log test**

Call `_log_event("mode_transition.resolved", payload)` with an option label
sentinel and assert the log contains structural identity but not the sentinel.

- [x] **Step 4: Run the tests to verify RED**

Run:

```bash
cd src/baas
.venv/bin/pytest tests/unit/core/service/bot_run/test_async_chat_client_coverage.py -k 'mode_transition_resolved or callback_registration or reconnect' -q
```

Expected: failures for the missing callback, missing stream-local field, and unsanitized log alias.

- [x] **Step 5: Implement stream-local tracking and initial/reconnect subscriptions**

Add the bounded field:

```python
pending_mode_transition_ids: set[str] = field(default_factory=set)
```

When a newly created requested event emits a chunk, track only mode switches:

```python
if payload.get("kind") == "mode_switch":
    state.pending_mode_transition_ids.add(event.interaction_id)
```

Register the same dedicated handler in `connect` and `_reconnect_loop`:

```python
client.on_event(
    "mode_transition.resolved",
    self._on_mode_transition_resolved,
)
```

- [x] **Step 6: Implement the dedicated handler and log alias**

The handler always attempts persistence, but its SSE delivery is independently
gated by the active stream's pending set:

```python
@_with_session_trace("_on_mode_transition_resolved")
def _on_mode_transition_resolved(
    self,
    payload: JsonObject,
    *,
    session_key: str,
    state: SessionState | None,
) -> None:
    if self._interaction_service is None:
        return
    event = EngineInteractionResolvedEvent.from_mode_transition_payload(
        session_key=session_key,
        payload=payload,
    )
    self._interaction_service.mark_resolved(
        session_key=event.session_key,
        interaction_id=event.interaction_id,
        envelope=event.envelope,
    )
    if state is None:
        return
    if event.interaction_id not in state.pending_mode_transition_ids:
        return
    state.pending_mode_transition_ids.remove(event.interaction_id)
    self._emit_stream_chunk(
        state,
        StreamChunk(
            type="interaction",
            content="",
            metadata={
                "event": "mode_transition.resolved",
                "payload": event.envelope,
            },
        ),
    )
```

Add `mode_transition.resolved` to the interaction-event structured logging
allowlist. Do not add `options`, `decision` labels, or arbitrary payload fields
to the logged metadata.

- [x] **Step 7: Run focused tests to verify GREEN**

Run the command from Step 4 and the existing RPC fallback test:

```bash
cd src/baas
.venv/bin/pytest tests/unit/core/service/bot_run/test_async_chat_client_coverage.py -k 'mode_transition_resolved or mode_switch_dispatch or callback_registration or reconnect' -q
```

Expected: all selected tests pass, including the unchanged accepted-RPC fallback.

- [x] **Step 8: Commit the client delivery path**

```bash
git add src/baas/src/secbaas/community/core/service/bot_run/_session_state.py src/baas/src/secbaas/community/core/service/bot_run/_async_chat_client.py src/baas/tests/unit/core/service/bot_run/test_async_chat_client_coverage.py
git commit -m "feat(baas): deliver mode transition terminal SSE"
```

---

### Task 3: Align compatibility documentation and run affected gates

**Files:**
- Modify: `src/baas/docs/2026-08-19-baas-bcn-interaction-sse-design.md`
- Modify: `src/baas/docs/2026-08-20-bcn-interaction-resolve-design.md`
- Modify: `src/baas/docs/2026-08-20-mode-transition-resolved-sse-implementation.md`

**Interfaces:**
- Consumes: completed behavior from Tasks 1 and 2.
- Produces: design documentation that no longer states that mode transitions lack a top-level resolved event, plus fresh verification evidence.

- [x] **Step 1: Update stale compatibility statements**

Document that current Engines emit `mode_transition.resolved`, that BaaS
preserves that raw envelope and maps it to the common resolved SSE, and that
the accepted RPC remains the fallback for older Engines. Do not claim that the
RPC response itself emits a terminal SSE.

- [x] **Step 2: Run the focused affected suites**

```bash
cd src/baas
.venv/bin/pytest tests/unit/core/service/bot_run/test_interaction_protocol.py tests/unit/core/service/bot_run/test_async_chat_client_coverage.py tests/unit/core/service/sse/test_default_converter.py -q
```

Expected: all tests pass.

- [x] **Step 3: Run the broader SSE and bot-run regression suites**

```bash
cd src/baas
.venv/bin/pytest tests/unit/core/service/sse tests/unit/core/service/bot_run -q
```

Expected: all collected tests pass.

- [x] **Step 4: Run formatting, lint, and whitespace gates**

```bash
cd src/baas
.venv/bin/ruff format --check src/secbaas/community/core/service/bot_run/_interaction_protocol.py src/secbaas/community/core/service/bot_run/_session_state.py src/secbaas/community/core/service/bot_run/_async_chat_client.py src/secbaas/community/core/service/sse/_default_converter.py tests/unit/core/service/bot_run/test_interaction_protocol.py tests/unit/core/service/bot_run/test_async_chat_client_coverage.py tests/unit/core/service/sse/test_default_converter.py
.venv/bin/ruff check src/secbaas/community/core/service/bot_run/_interaction_protocol.py src/secbaas/community/core/service/bot_run/_session_state.py src/secbaas/community/core/service/bot_run/_async_chat_client.py src/secbaas/community/core/service/sse/_default_converter.py tests/unit/core/service/bot_run/test_interaction_protocol.py tests/unit/core/service/bot_run/test_async_chat_client_coverage.py tests/unit/core/service/sse/test_default_converter.py
cd ../..
git diff --check
```

Expected: every command exits zero.

- [x] **Step 5: Record actual validation results and commit documentation**

Replace this plan's checkbox states only with commands actually completed, then
commit the compatibility documentation:

```bash
git add src/baas/docs/2026-08-19-baas-bcn-interaction-sse-design.md src/baas/docs/2026-08-20-bcn-interaction-resolve-design.md src/baas/docs/2026-08-20-mode-transition-resolved-sse-implementation.md
git commit -m "docs(baas): document mode transition terminal events"
```

## Execution evidence

- Typed-boundary and converter RED: 3 expected failures for the missing raw
  event constructor and unsupported converter event.
- Async-client RED: 6 expected failures for missing initial/reconnect
  subscriptions, missing one-time delivery, missing exposure gating, and raw
  payload logging.
- Non-stream exposure RED: 1 expected failure proving that a `SessionState`
  without a stream queue must not qualify for terminal SSE delivery.
- Focused GREEN: 205 passed across the complete interaction protocol,
  AsyncChatClient coverage, and default converter test files.
- Broader regression: 1003 passed, 8 xpassed, and 0 failed across the complete
  BaaS `sse` and `bot_run` unit-test directories. The xpasses come from tests
  already marked expected-failure; they did not fail the suite.
- Ruff format check: 7 changed Python files already formatted.
- Ruff lint: all checks passed.
- `git diff --check`: passed.
