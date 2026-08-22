# BaaS to BCN Interaction SSE Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert Engine `interaction.requested` and `interaction.resolved` envelopes into flat BCN Provider 2.0 `event: interaction` SSE frames without allowing a malformed interaction to terminate the chat stream.

**Architecture:** Keep the complete Engine envelope unchanged in the interaction persistence path, and normalize only at the BaaS `DefaultStreamConverter` delivery boundary. Use explicit kind-specific allowlist functions for `ask_user`, `exec`, and `mode_switch`; the generic SSE builder remains the sole owner of BCN run ID, sequence, event ID, and timestamp. BCS already preserves kind-specific fields in `InteractionEvent.raw`, so its production parser remains unchanged while its contract test and protocol documentation are extended.

**Tech Stack:** Python 3.12, pytest, BaaS `StreamChunk`/`SseEvent`, Rust 2024, serde_json, Cargo tests, Markdown Provider 2.0 protocol.

---

Implementation must follow @superpowers:test-driven-development. Before claiming completion, use @superpowers:verification-before-completion. Do not run `cargo fmt`; `src/bcs/CLAUDE.md` explicitly forbids global formatting.

Reference design: `src/baas/docs/2026-08-19-baas-bcn-interaction-sse-design.md`.

### Task 1: Replace passthrough with the BCN interaction envelope and exec mapping

**Files:**

- Modify: `src/baas/tests/unit/core/service/sse/test_default_converter.py:419`
- Modify: `src/baas/src/secbaas/community/core/service/sse/_default_converter.py:1-168`

**Step 1: Add a test helper that builds the real BaaS chunk shape**

Add below `_data` in the test module:

```python
def _interaction_chunk(event: str, payload: dict) -> StreamChunk:
    envelope = {
        "type": "event",
        "event": event,
        "payload": payload,
    }
    if "seq" in payload:
        envelope["seq"] = payload["seq"]
    return StreamChunk(
        type="interaction",
        metadata={"event": event, "payload": envelope},
    )
```

Replace the two passthrough assertions in `TestInteractionBranch` with focused tests:

```python
def test_exec_requested_is_flat_bcn_interaction(self):
    converter = DefaultStreamConverter()
    event = converter.convert(
        _interaction_chunk(
            "interaction.requested",
            {
                "interactionId": "int-1",
                "runId": "engine-run-7",
                "seq": 7279,
                "ts": 1787043219471,
                "kind": "exec",
                "phase": "requested",
                "title": "Command approval required",
                "description": "Deploy current build",
                "toolCallId": "tc-1",
                "cwd": "/workspace",
                "command": "npm run deploy",
                "options": [
                    {"label": "Proceed", "decision": "proceed", "value": "legacy"},
                    {"label": "Deny", "value": "deny"},
                ],
                "status": "pending",
            },
        ),
        run_id="bcn-run-1",
    )

    assert event is not None
    assert event.event == "interaction"
    assert event.id == "1"
    assert _data(event) == {
        "runId": "bcn-run-1",
        "seq": 1,
        "interactionId": "int-1",
        "kind": "exec",
        "phase": "requested",
        "title": "Command approval required",
        "description": "Deploy current build",
        "toolCallId": "tc-1",
        "cwd": "/workspace",
        "command": "npm run deploy",
        "options": [
            {"label": "Proceed", "decision": "proceed"},
            {"label": "Deny", "decision": "deny"},
        ],
    }

def test_resolved_is_flat_bcn_interaction(self):
    converter = DefaultStreamConverter()
    event = converter.convert(
        _interaction_chunk(
            "interaction.resolved",
            {
                "interactionId": "int-1",
                "kind": "exec",
                "phase": "resolved",
                "decision": "proceed",
                "idempotencyKey": "idem-1",
            },
        ),
        run_id="bcn-run-1",
    )

    assert event is not None
    assert event.event == "interaction"
    assert _data(event) == {
        "runId": "bcn-run-1",
        "seq": 1,
        "interactionId": "int-1",
        "kind": "exec",
        "phase": "resolved",
        "decision": "proceed",
        "idempotencyKey": "idem-1",
    }
```

The first test intentionally proves that Engine `runId/seq/ts/status/value` do not leak and that `decision` wins over `value`.

Add one resolved ask-user test asserting `action` and `answers` are copied by the
resolved allowlist, and one exec test without source `options` asserting the target
synthesizes the BCN-compatible `allow-once`, `allow-always`, and `deny` options.
These protect the existing resolved capability and the old Engine exec shape.

**Step 2: Run the tests and verify the old passthrough fails**

Run from `src/baas`:

```bash
uv run pytest tests/unit/core/service/sse/test_default_converter.py::TestInteractionBranch -q
```

Expected: FAIL because the current event is `interaction.requested` or `interaction.resolved`, and data still contains the Engine envelope.

**Step 3: Add safe dispatch, common fields, resolved fields, and exec conversion**

In `_default_converter.py`:

- Change the module description to say interaction chunks produce `event: interaction`.
- Import `get_logger` and define `logger = get_logger("core-service")`.
- Pass `run_id` into `_transform_chunk`, so warnings can identify the BCN run without logging business content.
- Replace `_transform_interaction` with explicit envelope unwrapping and allowlist construction.

The dispatch signature change is limited to this private call chain:

```python
def convert(self, chunk: StreamChunk, *, run_id: str) -> SseEvent | None:
    converted = _transform_chunk(chunk, _engine_name(chunk), run_id=run_id)
    # Keep the existing heartbeat and _build_event logic unchanged below.


def _transform_chunk(
    chunk: StreamChunk,
    engine: str,
    *,
    run_id: str,
) -> dict[str, Any] | None:
    # Keep all existing chat/agent branches unchanged.
    if chunk.type == "interaction":
        return _transform_interaction(chunk, run_id=run_id)
```

Use these function responsibilities and shapes:

```python
_INTERACTION_PHASES = {
    "interaction.requested": "requested",
    "interaction.resolved": "resolved",
}


def _transform_interaction(
    chunk: StreamChunk,
    *,
    run_id: str,
) -> dict[str, Any] | None:
    metadata = chunk.metadata or {}
    source_event = metadata.get("event")
    expected_phase = _INTERACTION_PHASES.get(source_event)
    envelope = metadata.get("payload")
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    if expected_phase is None or not isinstance(payload, dict):
        _warn_interaction(run_id=run_id, error_type="invalid_envelope")
        return None

    interaction_id = _non_empty_str(payload.get("interactionId"))
    if interaction_id is None:
        interaction_id = _non_empty_str(payload.get("id"))
    kind = _non_empty_str(payload.get("kind"))
    if interaction_id is None or kind is None:
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path="interactionId" if interaction_id is None else "kind",
            error_type="missing_required_field",
        )
        return None

    data = _common_interaction_data(
        payload,
        interaction_id=interaction_id,
        kind=kind,
        expected_phase=expected_phase,
        run_id=run_id,
    )
    if expected_phase == "resolved":
        _copy_present(payload, data, ("decision", "action", "answers", "idempotencyKey"))
        return {"event": "interaction", "data": data}

    if kind == "exec":
        converter = _transform_exec_requested
    elif kind == "ask_user":
        converter = _transform_ask_user_requested
    elif kind == "mode_switch":
        converter = _transform_mode_switch_requested
    else:
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path="kind",
            error_type="unknown_kind",
        )
        return None

    try:
        converter(payload, data, run_id=run_id)
    except Exception as exc:
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            error_type=type(exc).__name__,
        )
        return None
    return {"event": "interaction", "data": data}
```

`_common_interaction_data` must:

- always set `interactionId`, `kind`, and the phase derived from the source event;
- warn when payload phase is present and differs from the derived phase;
- copy only `title` and `description` when present;
- copy `toolCallId` from payload first, otherwise from a dict-valued `subject`;
- never copy Engine `runId`, `seq`, `ts`, or the envelope.

Implement those rules explicitly:

```python
def _common_interaction_data(
    payload: dict[str, Any],
    *,
    interaction_id: str,
    kind: str,
    expected_phase: str,
    run_id: str,
) -> dict[str, Any]:
    payload_phase = payload.get("phase")
    if payload_phase is not None and payload_phase != expected_phase:
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path="phase",
            error_type="phase_conflict",
        )
    data: dict[str, Any] = {
        "interactionId": interaction_id,
        "kind": kind,
        "phase": expected_phase,
    }
    _copy_present(payload, data, ("title", "description"))
    subject = payload.get("subject")
    subject = subject if isinstance(subject, dict) else {}
    _copy_first_present(payload, subject, data, "toolCallId", "toolCallId")
    return data


def _copy_first_present(
    source: dict[str, Any],
    fallback: dict[str, Any],
    target: dict[str, Any],
    source_key: str,
    target_key: str,
) -> None:
    value = source.get(source_key)
    if value is None:
        value = fallback.get(source_key)
    if value is not None:
        target[target_key] = value


def _non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _warn_interaction(
    *,
    run_id: str,
    interaction_id: str | None = None,
    kind: str | None = None,
    field_path: str | None = None,
    error_type: str,
) -> None:
    logger.warning(
        "interaction SSE conversion warning: run_id=%s interaction_id=%s "
        "kind=%s field_path=%s error_type=%s",
        run_id,
        interaction_id or "",
        kind or "",
        field_path or "",
        error_type,
    )
```

Add this exec-specific implementation. BCN requires a non-empty command and a
non-empty, unique decision option list. The old Engine shape without an
`options` key is normalized to the three standard decisions; an explicitly
present invalid/empty/null `options` value is rejected rather than defaulted:

```python
def _transform_exec_requested(
    payload: dict[str, Any],
    data: dict[str, Any],
    *,
    run_id: str,
) -> bool:
    command = _non_empty_str(payload.get("command"))
    if command is None:
        _warn_interaction(
            run_id=run_id,
            interaction_id=data["interactionId"],
            kind=data["kind"],
            field_path="payload.command",
            error_type="missing_required_field",
        )
        return False
    data["command"] = command
    _copy_present(payload, data, ("cwd",))

    if "options" not in payload:
        data["options"] = [dict(option) for option in _DEFAULT_EXEC_OPTIONS]
        return True

    options = _convert_decision_options(
        payload.get("options"),
        run_id=run_id,
        interaction_id=data["interactionId"],
        kind=data["kind"],
    )
    if not options:
        _warn_interaction(
            run_id=run_id,
            interaction_id=data["interactionId"],
            kind=data["kind"],
            field_path="payload.options",
            error_type="no_valid_options",
        )
        return False
    data["options"] = options
    return True
```

`_convert_decision_options` always returns a list. It contains only valid
`label` and `decision` fields, prefers a non-empty `decision`, falls back to a
non-empty `value`, skips malformed children, and applies stable last-write-wins
deduplication by decision. The exec caller rejects an empty result.

```python
def _convert_decision_options(
    value: Any,
    *,
    run_id: str,
    interaction_id: str,
    kind: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        _warn_interaction(
            run_id=run_id,
            interaction_id=interaction_id,
            kind=kind,
            field_path="payload.options",
            error_type="invalid_type",
        )
        return []

    options: list[dict[str, str]] = []
    decision_positions: dict[str, int] = {}
    for index, source in enumerate(value):
        field_path = f"payload.options[{index}]"
        if not isinstance(source, dict):
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=field_path,
                error_type="invalid_type",
            )
            continue
        label = _non_empty_str(source.get("label"))
        decision = _non_empty_str(source.get("decision")) or _non_empty_str(
            source.get("value")
        )
        if label is None or decision is None:
            _warn_interaction(
                run_id=run_id,
                interaction_id=interaction_id,
                kind=kind,
                field_path=field_path,
                error_type="missing_required_field",
            )
            continue
        option = {"label": label, "decision": decision}
        existing_position = decision_positions.get(decision)
        if existing_position is not None:
            options[existing_position] = option
            continue
        decision_positions[decision] = len(options)
        options.append(option)
    return options
```

The producer-side `_allowed_decisions` is kind-aware. `ask_user` always records
`("submit", "cancel")` and ignores unsupported top-level options. `exec` and
`mode_switch` accept only options whose label and decision/value are non-empty
strings, skip malformed children, and deduplicate without mutating the Engine
envelope. Missing exec options yield the same three defaults; missing or wholly
invalid mode-switch options, plus unknown/missing kinds, yield an explicit empty
tuple.

Persisted allowed decisions use three states so the stricter validation remains
compatible with historical pending rows:

- `None` means a legacy JSON payload had no `allowedDecisions` field. Only this
  state retains unrestricted resolve behavior.
- `()` means a new record explicitly has no valid decisions and is fail-closed.
- A non-empty tuple is the exact resolve whitelist.

`BotRunInteractionPayload.from_dict` maps a missing field to `None` and an
explicit `[]` to `()`. `to_dict` omits `None` but must preserve `()` as
`allowedDecisions: []`. `BotInteractionService.resolve` performs membership
validation for every tuple, including the empty tuple, and bypasses it only for
legacy `None`; it never reconstructs policy from the opaque Engine envelope.

`_warn_interaction` must use parameterized logging and only these values: `run_id`, `interaction_id`, `kind`, `field_path`, and `error_type`. Do not log payloads, exception messages, question text, command, description, or answers.

**Step 4: Run the focused tests**

```bash
uv run pytest tests/unit/core/service/sse/test_default_converter.py::TestInteractionBranch -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/baas/src/secbaas/community/core/service/sse/_default_converter.py src/baas/tests/unit/core/service/sse/test_default_converter.py
git commit -m "feat(baas): normalize exec interaction SSE events"
```

### Task 2: Add ask_user field mapping and tolerant question IDs

**Files:**

- Modify: `src/baas/tests/unit/core/service/sse/test_default_converter.py`
- Modify: `src/baas/src/secbaas/community/core/service/sse/_default_converter.py`

**Step 1: Write failing ask_user mapping tests**

Add a `TestAskUserInteraction` class covering one complete event:

```python
def test_maps_questions_and_derives_question_ids(self):
    converter = DefaultStreamConverter()
    event = converter.convert(
        _interaction_chunk(
            "interaction.requested",
            {
                "interactionId": "int-ask-1",
                "kind": "ask_user",
                "phase": "requested",
                "title": "Deployment settings",
                "questions": [
                    {
                        "header": "Environment",
                        "question": "Where should this be deployed?",
                        "allowOther": True,
                        "multiSelect": False,
                        "options": [
                            {
                                "decision": "staging",
                                "value": "legacy-staging",
                                "label": "Staging",
                                "description": "Staging environment",
                            },
                            {"value": "production", "label": "Production"},
                        ],
                    }
                ],
            },
        ),
        run_id="bcn-run-1",
    )

    assert event is not None
    assert _data(event)["questions"] == [
        {
            "questionId": "Environment",
            "header": "Environment",
            "question": "Where should this be deployed?",
            "allowOther": True,
            "multiSelect": False,
            "options": [
                {
                    "value": "staging",
                    "label": "Staging",
                    "description": "Staging environment",
                },
                {"value": "production", "label": "Production"},
            ],
        }
    ]
```

Add separate tests for:

- absent optional `allowOther`, `multiSelect`, `description`, and `options` are omitted;
- missing/empty header produces `question_1`, `question_2` and emits a warning;
- duplicate header/fallback IDs use last-write-wins, keep the key's first position, and emit a warning;
- missing/non-array/all-invalid questions drop only the interaction without consuming sequence;
- explicit options that are non-array, empty, or all-invalid drop their question;
- questions and per-question options enforce BCN's 1..4 and unique-key constraints;
- non-object children and missing required fields are skipped locally when a valid parent remains;
- real Engine label/description-only options use label as the legacy value fallback and emit a sanitized warning;
- mixed options preserve the priority `decision` > `value` > legacy `label`, with duplicate handling based on the final value;
- `allowOther`/`multiSelect` accept only bool, and free-text questions omit `allowOther`.

Use `caplog` to assert the warning contains the field path/error type but not the question text or option label.

**Step 2: Run and verify failure**

```bash
uv run pytest tests/unit/core/service/sse/test_default_converter.py::TestAskUserInteraction -q
```

Expected: FAIL because `_transform_ask_user_requested` has not populated questions.

**Step 3: Implement ask_user conversion**

Implement `_transform_ask_user_requested` and helpers with these exact output rules:

- The kind helper returns `True` only after producing 1..4 valid questions. The caller
  returns `None` when it returns `False`, so the rejected interaction consumes no SSE seq.
- Treat whitespace-only required strings as missing while preserving valid source strings.
- Derive `questionId` from non-empty `header`, otherwise from the source array's 1-based
  index. Store converted questions by `questionId`: a duplicate replaces the prior value
  in its original position and emits a warning. Limit unique questions to four.
- A missing/non-array questions value or a list with no valid question rejects the current
  interaction with a warning, without terminating the stream.
- Only a source question with no `options` key means free text. If the key exists, including
  with a `None` value, it must be an array yielding 1..4 valid, unique values or the question
  is skipped. Duplicate values replace the prior option in its original position; excess
  unique options are skipped.
- Copy the first non-empty `decision`, `value`, or legacy `label` to option
  `value`, plus required `label` and optional `description`. The label fallback
  exists for the real Engine `InteractionQuestion` shape whose options contain
  only label/description; emit `legacy_label_fallback` with structural context
  only. Do not emit Engine-only option fields.
- Apply duplicate last-write-wins and the 1..4 limit to the final option value,
  including label-only legacy options.
- Copy `multiSelect` only when it is bool. Copy `allowOther` only when it is bool and the
  question has valid options; warn and omit it for free-text questions.
- Requested helpers return a success bool: `exec` requires a valid command and
  valid/default options, `ask_user` requires valid questions, and `mode_switch`
  requires valid options. Keep the existing kind-converter exception boundary.

Do not use question text, header, option label/description, payload, or exception messages as
log context. Malformed children may be skipped locally, but never emit a BCN-invalid empty
questions/options collection.

**Step 4: Run ask_user and existing converter tests**

```bash
uv run pytest tests/unit/core/service/sse/test_default_converter.py::TestAskUserInteraction tests/unit/core/service/sse/test_default_converter.py::TestInteractionBranch -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/baas/src/secbaas/community/core/service/sse/_default_converter.py src/baas/tests/unit/core/service/sse/test_default_converter.py
git commit -m "feat(baas): map ask-user interaction questions"
```

### Task 3: Add actual Engine mode_switch compatibility

**Files:**

- Modify: `src/baas/tests/unit/core/service/sse/test_default_converter.py`
- Modify: `src/baas/src/secbaas/community/core/service/sse/_default_converter.py`

**Step 1: Write the failing captured-shape test**

Add a pytest fixture or module constant representing the captured Engine payload. It must include:

```python
{
    "interactionId": "int:14136f21-01a0-42ed-8000-14136f2102ed",
    "id": "int:14136f21-01a0-42ed-8000-14136f2102ed",
    "runId": "engine-run-1",
    "sessionKey": "engine-session-1",
    "kind": "mode_switch",
    "interactionType": "mode_switch",
    "phase": "requested",
    "status": "pending",
    "title": "Plan mode transition",
    "description": "Transition from plan to execute",
    "subject": {
        "type": "mode",
        "toolCallId": "subject-tool-call",
        "fromMode": "subject-plan",
        "toMode": "subject-execute",
    },
    "toolCallId": "top-level-tool-call",
    "options": [
        {
            "value": "legacy-proceed",
            "decision": "proceed",
            "label": "Continue to execution",
            "recommended": True,
            "optionId": "opt-0",
        },
        {"value": "stay", "label": "Stay in planning", "targetMode": "plan"},
    ],
    "fromMode": "plan",
    "toMode": "execute",
    "inputSchema": {"type": "choices", "multiSelect": False},
    "uiHints": {"variant": "plan", "severity": "info"},
    "schemaVersion": 2,
    "seq": 7279,
    "ts": 1787043219471,
}
```

Assert the flat result uses top-level `toolCallId/fromMode/toMode`, renames `toMode` to `targetMode`, prefers option `decision`, falls back to option `value`, retains optional `recommended/targetMode`, and contains none of the Engine internal keys.

Add a second test with only `subject.toolCallId/fromMode/toMode` to verify the old Engine fallback.

**Step 2: Run and verify failure**

```bash
uv run pytest tests/unit/core/service/sse/test_default_converter.py::TestModeSwitchInteraction -q
```

Expected: FAIL because mode-switch fields are not yet populated.

**Step 3: Implement mode_switch conversion**

```python
def _transform_mode_switch_requested(
    payload: dict[str, Any],
    data: dict[str, Any],
    *,
    run_id: str,
) -> bool:
    subject = payload.get("subject")
    subject = subject if isinstance(subject, dict) else {}
    _copy_first_present(
        data,
        "fromMode",
        _non_empty_str(payload.get("fromMode")),
        _non_empty_str(subject.get("fromMode")),
    )
    _copy_first_present(
        data,
        "targetMode",
        _non_empty_str(payload.get("toMode")),
        _non_empty_str(subject.get("toMode")),
    )
    options = _convert_mode_switch_options(
        payload.get("options"),
        run_id=run_id,
        interaction_id=data["interactionId"],
        kind=data["kind"],
    )
    if not options:
        _warn_interaction(
            run_id=run_id,
            interaction_id=data["interactionId"],
            kind=data["kind"],
            field_path="payload.options",
            error_type="no_valid_options",
        )
        return False
    data["options"] = options
    return True
```

`_convert_mode_switch_options` requires a non-empty source array and at least one
valid option. It uses decision/value fallback and stable last-write-wins
deduplication, copies non-empty optional `targetMode`, and copies
`recommended` only when it is bool (preserving explicit `False`).

**Step 4: Run all interaction converter tests**

```bash
uv run pytest tests/unit/core/service/sse/test_default_converter.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/baas/src/secbaas/community/core/service/sse/_default_converter.py src/baas/tests/unit/core/service/sse/test_default_converter.py
git commit -m "feat(baas): map mode-switch interaction events"
```

### Task 4: Prove failure isolation, phase normalization, and shared sequencing

**Files:**

- Modify: `src/baas/tests/unit/core/service/sse/test_default_converter.py`
- Modify if tests expose a gap: `src/baas/src/secbaas/community/core/service/sse/_default_converter.py`

**Step 1: Add failure-isolation tests**

Add tests that assert:

```python
def test_invalid_interaction_does_not_consume_seq_or_stop_following_chat(caplog):
    converter = DefaultStreamConverter()
    first = converter.convert(StreamChunk(type="delta", content="a"), run_id="bcn-run-1")
    dropped = converter.convert(
        _interaction_chunk(
            "interaction.requested",
            {"interactionId": "int-secret", "kind": "unknown", "title": "secret-title"},
        ),
        run_id="bcn-run-1",
    )
    following = converter.convert(StreamChunk(type="delta", content="b"), run_id="bcn-run-1")

    assert first.id == "1"
    assert dropped is None
    assert following.id == "2"
    assert "secret-title" not in caplog.text
```

Also cover:

- missing `interactionId` and missing `kind` return `None` with warning;
- `interactionId` falls back to payload `id`;
- missing phase is derived from the source event;
- conflicting phase warns and the source event phase wins;
- malformed envelope returns `None`;
- monkeypatching a kind converter to raise proves unexpected errors return `None` and only log the exception class, not its message;
- optional `False` values survive, while absent optional fields are omitted;
- one valid interaction between an agent/chat event pair uses the same converter-wide sequence and has `event.id == str(data["seq"])`.

**Step 2: Run and verify any uncovered case fails**

```bash
uv run pytest tests/unit/core/service/sse/test_default_converter.py -q
```

Expected before the final hardening: at least the phase conflict or forced-exception test fails if Task 1 did not fully isolate it.

**Step 3: Make the minimum hardening changes**

Ensure all interaction-specific failures are caught inside `_transform_interaction`. Do not add a broad catch around chat/agent conversion, because unrelated converter bugs must retain their current behavior. Make warning values structural and parameterized; never include `repr(payload)` or `str(exc)`.

**Step 4: Run BaaS focused tests and lint**

From `src/baas`:

```bash
uv run pytest tests/unit/core/service/sse/test_default_converter.py -q
uv run ruff check src/secbaas/community/core/service/sse/_default_converter.py tests/unit/core/service/sse/test_default_converter.py
```

Expected: both commands PASS.

**Step 5: Commit**

```bash
git add src/baas/src/secbaas/community/core/service/sse/_default_converter.py src/baas/tests/unit/core/service/sse/test_default_converter.py
git commit -m "test(baas): cover interaction SSE failure isolation"
```

### Task 5: Extend the BCN Provider 2.0 mode-switch contract

**Files:**

- Modify: `src/bcs/crates/contracts/bcs-protocol/src/stream/parse.rs:266-290`
- Modify: `src/bcs/docs/bcs-provider-2.0-sse-protocol.md:480-518`

**Step 1: Add a parser compatibility test for the new optional fields**

Add beside the existing interaction parser tests:

```rust
#[test]
fn preserves_requested_mode_switch_target_and_recommendation() {
    let data = json!({
        "runId": "provider-run-1",
        "seq": 9,
        "phase": "requested",
        "interactionId": "interaction-3",
        "kind": "mode_switch",
        "fromMode": "plan",
        "targetMode": "execute",
        "options": [
            {
                "decision": "proceed",
                "label": "Continue to execution",
                "targetMode": "execute",
                "recommended": true
            },
            {"decision": "stay", "label": "Stay in planning"}
        ]
    });

    match parse_stream_event("interaction", data.clone()) {
        StreamEvent::Interaction(interaction) => {
            assert_eq!(interaction.kind, InteractionKind::ModeSwitch);
            assert_eq!(interaction.raw["targetMode"], json!("execute"));
            assert_eq!(interaction.raw["options"][0]["targetMode"], json!("execute"));
            assert_eq!(interaction.raw["options"][0]["recommended"], json!(true));
            assert_eq!(interaction.raw, data);
        }
        other => panic!("expected interaction, got {other:?}"),
    }
}
```

**Step 2: Run the contract test**

From `src/bcs`:

```bash
cargo test -p bcs-protocol stream::parse::tests::preserves_requested_mode_switch_target_and_recommendation -- --exact
```

Expected: PASS without production parser changes. This is a characterization test: the parser intentionally validates common interaction fields and preserves all kind-specific fields in `raw`.

**Step 3: Update the Provider 2.0 protocol**

In the mode-switch requested example:

- add top-level `"targetMode": "execute"` next to `fromMode`;
- keep option `targetMode` optional;
- mark the preferred option with `"recommended": true`.

Update the constraints to state explicitly:

```markdown
- `fromMode/targetMode` are optional Provider opaque strings; BCS does not maintain a mode enum.
- Option `targetMode` is optional. An option entering a known mode should provide it; stay/deny options may omit it.
- Option `recommended` is an optional boolean UI hint; omission means no recommendation.
```

Do not add typed Rust fields or validation for these extensions; doing so would duplicate the raw kind-specific protocol and broaden the code change unnecessarily.

**Step 4: Run the bcs-protocol tests**

```bash
cargo test -p bcs-protocol
```

Expected: PASS. Do not run `cargo fmt`.

**Step 5: Commit**

```bash
git add src/bcs/crates/contracts/bcs-protocol/src/stream/parse.rs src/bcs/docs/bcs-provider-2.0-sse-protocol.md
git commit -m "docs(bcs): extend mode-switch interaction SSE contract"
```

### Task 6: Run affected-module verification

**Files:**

- Verify only; no planned production edits.

**Step 1: Install this worktree's repository hooks if not already installed**

From the repository root:

```bash
scripts/install_git_hooks.sh
```

Expected: hooks are installed for this worktree.

**Step 2: Run the complete BaaS unit suite**

From `src/baas`:

```bash
just test-ut
```

Expected: PASS. If unrelated pre-existing failures occur, record the exact tests and rerun the focused converter suite to separate regression evidence.

**Step 3: Run the BaaS architecture suite**

```bash
just test-arch
```

Expected: PASS, including layer and exception-handling rules.

**Step 4: Re-run the affected BCS crate**

From `src/bcs`:

```bash
cargo test -p bcs-protocol
```

Expected: PASS.

**Step 5: Check the final diff**

From the repository root:

```bash
git diff --check origin/dev...HEAD
git status --short
```

Expected: no whitespace errors and no uncommitted implementation files. Review `git diff --stat origin/dev...HEAD` to confirm only the requested BaaS converter/tests and BCN protocol/test files were added beyond the pre-existing feature work.

**Step 6: Record validation evidence**

Use the exact command results in the final handoff or PR `## Validation` section. Do not claim the full BaaS or BCS suites passed unless those commands actually completed successfully.
