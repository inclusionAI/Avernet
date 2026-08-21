# BCN Interaction Resolve Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Register `interaction.resolve` on BaaS `/bcn/downlink`, durably normalize BCS resolutions, and deliver the exact kind-specific request to the existing Engine WebSocket without changing Engine source.

**Architecture:** The BCN HTTP adapter validates the Provider webhook and converts it to a typed, transport-independent `InteractionResolution`. The existing bot-interaction state service persists that resolution and idempotency key, the owner worker claims it, and the BaaS Engine adapter selects `interaction.resolve` or `mode_transition.resolve` and serializes the exact wire params. Existing decision-only queued records remain compatible.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, dependency-injector, dataclasses, pytest, Ruff.

---

### Task 1: Add the typed interaction resolution contract

**Files:**
- Modify: `src/baas/src/secbaas/community/api/bot_interaction/_models.py`
- Modify: `src/baas/src/secbaas/community/api/bot_interaction/__init__.py`
- Test: `src/baas/tests/unit/core/service/test_bot_interaction_service.py`

**Step 1: Write the failing model test**

Add a test that constructs a normalized ask-user resolution with ordered
values, question-text answers, and two-dimensional selected options, then
asserts `to_dict()` and `from_dict()` preserve the exact JSON-safe shape.

The public model is:

```python
@dataclass(frozen=True, slots=True)
class InteractionResolution:
    decision: str
    kind: Literal["ask_user", "exec", "mode_switch"] | None = None
    answer: str | None = None
    message: str | None = None
    values: dict[str, str] | None = None
    answers: dict[str, str] | None = None
    selected_options: tuple[tuple[str, ...], ...] | None = None

    def to_dict(self) -> dict[str, object]: ...

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "InteractionResolution": ...
```

`kind=None` is intentional for real legacy queued records that predate the
normalized payload and must continue to use `interaction.resolve`.

**Step 2: Run the focused test and verify RED**

Run from `src/baas`:

```bash
.venv/bin/pytest tests/unit/core/service/test_bot_interaction_service.py -q
```

Expected: collection/import failure because `InteractionResolution` does not
exist.

**Step 3: Implement strict serialization**

Validate non-empty `decision`, the optional kind enum, string maps, and nested
string arrays. Serialize with Engine wire key `selectedOptions`, omitting every
`None` field. Export the model from `api.bot_interaction`.

**Step 4: Run the focused test and verify GREEN**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

```bash
git add src/baas/src/secbaas/community/api/bot_interaction \
  src/baas/tests/unit/core/service/test_bot_interaction_service.py
git commit -m "feat(baas): model normalized interaction resolutions"
```

### Task 2: Persist normalized resolutions and Provider idempotency

**Files:**
- Modify: `src/baas/src/secbaas/community/api/bot_interaction/_models.py`
- Modify: `src/baas/src/secbaas/community/api/bot_interaction/_protocols.py`
- Modify: `src/baas/src/secbaas/community/core/repository/bot_run_interaction/_record.py`
- Modify: `src/baas/src/secbaas/community/core/service/bot_interaction/_service.py`
- Test: `src/baas/tests/unit/core/service/test_bot_interaction_service.py`
- Test: `src/baas/tests/unit/core/repository/bot_run_interaction/test_orm_repository.py`

**Step 1: Write failing state-machine tests**

Cover:

- a requested record transitions to queued with `decision`, normalized
  `resolution`, original `clientReq`, and `idempotencyKey` persisted;
- `claim_for_dispatch` returns the complete `InteractionResolution`;
- the same idempotency key plus identical resolution deduplicates without a
  second transition, including after accepted terminal states;
- a lost `requested -> queued` transition race rereads and recognizes an
  identical concurrent winner;
- the same key with different content conflicts;
- a different key after the record left requested conflicts;
- a legacy queued record without `resolution` returns a decision-only
  resolution with `kind=None`.

Change the service contract to:

```python
def resolve(
    self,
    *,
    session_key: str,
    interaction_id: str,
    resolution: InteractionResolution,
    request_envelope: dict[str, Any],
    idempotency_key: str | None = None,
) -> InteractionResolveResult: ...
```

and make `InteractionDispatch` carry `resolution` instead of only `decision`.

**Step 2: Run tests and verify RED**

```bash
.venv/bin/pytest \
  tests/unit/core/service/test_bot_interaction_service.py \
  tests/unit/core/repository/bot_run_interaction/test_orm_repository.py -q
```

Expected: failures because payload and service do not persist or claim the new
fields.

**Step 3: Implement the minimal persistence changes**

Add optional JSON fields `resolution` and `idempotencyKey` to
`BotRunInteractionPayload` and its patch. The state service serializes the
typed resolution on transition and reconstructs it on claim. It compares the
persisted normalized resolution for idempotent retries and never compares or
logs raw answer text.

Update the existing OpenAPI resolve adapter to construct a decision-only
`InteractionResolution`; its external request and response remain unchanged.

**Step 4: Run tests and verify GREEN**

Run the command from Step 2 plus:

```bash
.venv/bin/pytest tests/unit/adapters/web/open_api -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/baas/src/secbaas/community/api/bot_interaction \
  src/baas/src/secbaas/community/core/repository/bot_run_interaction \
  src/baas/src/secbaas/community/core/service/bot_interaction \
  src/baas/src/secbaas/community/adapters/web/routers/open_api \
  src/baas/tests/unit/core/service/test_bot_interaction_service.py \
  src/baas/tests/unit/core/repository/bot_run_interaction/test_orm_repository.py \
  src/baas/tests/unit/adapters/web/open_api
git commit -m "feat(baas): persist interaction resolution payloads"
```

### Task 3: Build exact Engine resolve frames

**Files:**
- Modify: `src/baas/src/secbaas/community/core/service/bot_run/_interaction_protocol.py`
- Modify: `src/baas/src/secbaas/community/core/service/bot_run/_bot_websocket_client.py`
- Test: `src/baas/tests/unit/core/service/bot_run/test_interaction_protocol.py`
- Test: `src/baas/tests/unit/core/service/bot_run/test_bot_websocket_client.py`

**Step 1: Write failing protocol tests**

Assert exact frames for:

- ask-user submit with `answer`, `message`, `values`, question-text `answers`,
  and two-dimensional `selectedOptions`;
- ask-user cancel with only `interactionId` and `decision`;
- exec `allow-once` and `deny` using `interaction.resolve`;
- mode switch `stay` and `proceed` using `mode_transition.resolve` and
  `transitionId`;
- legacy `kind=None` using decision-only `interaction.resolve`.

**Step 2: Run tests and verify RED**

```bash
.venv/bin/pytest \
  tests/unit/core/service/bot_run/test_interaction_protocol.py \
  tests/unit/core/service/bot_run/test_bot_websocket_client.py -q
```

Expected: failures because the builder accepts only a decision and always
creates `interaction.resolve`.

**Step 3: Implement the builder and client handoff**

Change `build_interaction_resolve_request` and
`BotWebSocketClient.interaction_resolve` to accept `InteractionResolution`.
For `mode_switch`, emit `mode_transition.resolve` with `transitionId`; otherwise
emit `interaction.resolve` with `interactionId`. Copy only the normalized
optional fields produced by the BaaS adapter.

**Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

```bash
git add src/baas/src/secbaas/community/core/service/bot_run \
  src/baas/tests/unit/core/service/bot_run/test_interaction_protocol.py \
  src/baas/tests/unit/core/service/bot_run/test_bot_websocket_client.py
git commit -m "feat(baas): build kind-specific engine resolve frames"
```

### Task 4: Carry the resolution through the owner worker

**Files:**
- Modify: `src/baas/src/secbaas/community/core/service/bot_run/_async_chat_client.py`
- Test: `src/baas/tests/unit/core/service/bot_run/test_async_chat_client.py`
- Test: `src/baas/tests/unit/core/service/bot_run/test_async_chat_client_coverage.py`
- Test: `src/baas/tests/unit/core/service/bot_run/test_engine_dispatch_integration.py`

**Step 1: Write a failing persistence-to-Engine test**

Use the real interaction service with a fake repository, persist an ask-user
resolution, allow `AsyncChatClient` to claim it, and assert the fake Engine
client receives the complete typed resolution. Also retain an existing
decision-only case.

**Step 2: Run tests and verify RED**

```bash
.venv/bin/pytest \
  tests/unit/core/service/bot_run/test_async_chat_client.py \
  tests/unit/core/service/bot_run/test_async_chat_client_coverage.py \
  tests/unit/core/service/bot_run/test_engine_dispatch_integration.py -q
```

Expected: failures because the owner worker passes only `command.decision`.

**Step 3: Pass the typed resolution**

Replace the decision-only call with
`client.interaction_resolve(interaction_id=..., resolution=command.resolution)`.
Do not parse the stored BCN envelope in this layer.

**Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

```bash
git add src/baas/src/secbaas/community/core/service/bot_run/_async_chat_client.py \
  src/baas/tests/unit/core/service/bot_run
git commit -m "feat(baas): dispatch complete interaction resolutions"
```

### Task 5: Add the BCN request/domain contract and normalization

**Files:**
- Modify: `src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_model.py`
- Modify: `src/baas/src/secbaas/community/api/bcn/_models.py`
- Modify: `src/baas/src/secbaas/community/api/bcn/_protocols.py`
- Modify: `src/baas/src/secbaas/community/api/bcn/__init__.py`
- Modify: `src/baas/src/secbaas/community/core/service/bcn/_bcn_service.py`
- Test: `src/baas/tests/unit/core/service/bcn/test_bcn_service.py`
- Test: `src/baas/tests/unit/adapters/web/open_api/test_bcn_router.py`

**Step 1: Write failing request and normalization tests**

Add BCS-shaped fixtures for ask-user submit/cancel, exec allow/deny, and mode
switch proceed/stay. The ask-user assertion must exactly match:

```json
{
  "decision": "submit",
  "answer": "deploy_target: staging；components: web，worker",
  "message": "deploy_target: staging；components: web，worker",
  "values": {
    "deploy_target": "staging",
    "components": "web，worker"
  },
  "answers": {
    "what's your deploy target?": "staging",
    "whats' the components?": "web，worker"
  },
  "selectedOptions": [["staging"], ["web", "worker"]]
}
```

Also test empty values, missing question, invalid kind-specific decision/action,
and that ordinary values are never rewritten to `other`.

**Step 2: Run tests and verify RED**

```bash
.venv/bin/pytest \
  tests/unit/core/service/bcn/test_bcn_service.py \
  tests/unit/adapters/web/open_api/test_bcn_router.py -q
```

Expected: import and assertion failures because the BCN resolve models and
service method do not exist.

**Step 3: Implement models and normalization**

Add Pydantic transport models with aliases for `bcsRunId`, `runId`,
`interactionId`, and `idempotencyKey`. Add API dataclasses for the domain
input/result. `DefaultBcnDownlinkService.handle_interaction_resolve` performs
the ordinary-value conversion, builds `InteractionResolution`, and calls the
bot-interaction service with outer `session_id` and the original request
envelope.

Do not inspect requested options or create `other`.

**Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

```bash
git add src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_model.py \
  src/baas/src/secbaas/community/api/bcn \
  src/baas/src/secbaas/community/core/service/bcn/_bcn_service.py \
  src/baas/tests/unit/core/service/bcn/test_bcn_service.py \
  src/baas/tests/unit/adapters/web/open_api/test_bcn_router.py
git commit -m "feat(baas): normalize BCN interaction resolutions"
```

### Task 6: Register `/bcn/downlink` and wire composition

**Files:**
- Modify: `src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_router.py`
- Modify: `src/baas/src/secbaas/community/bootstrap/_core_services.py`
- Modify: `src/baas/tests/architecture/check_protocols/api/bcn/check_bcn_downlink_service.py`
- Test: `src/baas/tests/unit/adapters/web/open_api/test_bcn_router.py`
- Test: `src/baas/tests/e2e/asgi/baseline/test_bcn_downlink_extended.py`

**Step 1: Write a failing route test**

POST a full BCS Provider request with `method=interaction.resolve` and JSON
transport. Assert it is registered, calls the service once, and returns the
Provider acknowledgement `{"ok": true}`. Add a service-error case returning
`{"ok": false, "retryable": false, "error": ...}` without exposing answers.
Assert SSE transport continues to reject this finite method.

**Step 2: Run tests and verify RED**

```bash
.venv/bin/pytest \
  tests/unit/adapters/web/open_api/test_bcn_router.py \
  tests/e2e/asgi/baseline/test_bcn_downlink_extended.py -q
```

Expected: HTTP 501 / missing dispatcher.

**Step 3: Register and wire the service**

Register `interaction.resolve` in `_METHOD_DISPATCH`, update router docs and
response union, and inject `bot_interaction_service` into
`DefaultBcnDownlinkService` at the composition root. Update the architecture
protocol construction fixture.

**Step 4: Run tests and verify GREEN**

Run the command from Step 2 plus:

```bash
.venv/bin/pytest tests/architecture/check_protocols/api/bcn -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_router.py \
  src/baas/src/secbaas/community/bootstrap/_core_services.py \
  src/baas/tests/unit/adapters/web/open_api/test_bcn_router.py \
  src/baas/tests/e2e/asgi/baseline/test_bcn_downlink_extended.py \
  src/baas/tests/architecture/check_protocols/api/bcn/check_bcn_downlink_service.py
git commit -m "feat(baas): register BCN interaction resolve downlink"
```

### Task 7: Regression and boundary verification

**Files:**
- Modify only files required by failures caused by this feature.

**Step 1: Run focused interaction and BCN suites**

```bash
cd src/baas
.venv/bin/pytest \
  tests/unit/core/service/test_bot_interaction_service.py \
  tests/unit/core/repository/bot_run_interaction \
  tests/unit/core/service/bot_run/test_interaction_protocol.py \
  tests/unit/core/service/bot_run/test_bot_websocket_client.py \
  tests/unit/core/service/bot_run/test_async_chat_client.py \
  tests/unit/core/service/bot_run/test_async_chat_client_coverage.py \
  tests/unit/core/service/bot_run/test_engine_dispatch_integration.py \
  tests/unit/core/service/bcn \
  tests/unit/adapters/web/open_api/test_bcn_router.py \
  tests/e2e/asgi/baseline/test_bcn_downlink.py \
  tests/e2e/asgi/baseline/test_bcn_downlink_extended.py -q
```

Expected: PASS.

**Step 2: Run formatting and lint**

```bash
.venv/bin/ruff format --check \
  src/secbaas/community/api/bot_interaction \
  src/secbaas/community/api/bcn \
  src/secbaas/community/core/repository/bot_run_interaction \
  src/secbaas/community/core/service/bot_interaction \
  src/secbaas/community/core/service/bot_run \
  src/secbaas/community/core/service/bcn \
  src/secbaas/community/adapters/web/routers/bcn_downlink \
  tests/unit/core/service/test_bot_interaction_service.py \
  tests/unit/core/service/bot_run \
  tests/unit/core/service/bcn \
  tests/unit/adapters/web/open_api/test_bcn_router.py

.venv/bin/ruff check <the same changed Python paths>
```

Expected: PASS.

**Step 3: Check repository boundaries**

```bash
cd ../..
git diff --check origin/dev...HEAD
git diff --name-only 113cc1b04...HEAD | rg '^src/engine/'
git status --short
```

Expected: no whitespace errors, no Engine paths, and a clean worktree after
the final commit.

**Step 4: Commit any test-only cleanup**

```bash
git add <only files required by the verified feature>
git commit -m "test(baas): cover BCN interaction resolve delivery"
```

### Task 8: Harden validation, logging, terminalization, and retries

**Files:**
- Modify: `src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_router.py`
- Modify: `src/baas/src/secbaas/community/core/service/bot_interaction/_service.py`
- Modify: `src/baas/src/secbaas/community/core/service/bot_run/_async_chat_client.py`
- Modify: `src/baas/src/secbaas/community/core/service/bot_run/_bot_websocket_client.py`
- Test the corresponding router, state-service, and websocket paths.

**Step 1: Add RED regressions**

Cover four review findings: malformed Provider answers must receive a sanitized
finite ACK; interaction answer content must not appear in raw-frame or wildcard
logs; an accepted `mode_transition.resolve` response must terminalize its row;
and identical idempotent retries must survive terminal states and a concurrent
transition winner.

**Step 2: Implement the boundary fixes**

Catch `InteractionResolveRequest` validation errors inside `/bcn/downlink`, log
only structural interaction metadata, mark an accepted mode-switch RPC resolved
after recording its exchange, and reread a failed queue transition before
returning a conflict. Compare only the normalized resolution for idempotency;
never log raw answers.

**Step 3: Re-run the focused and complete verification from Task 7**
