# Session File Upload Materialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development task-by-task. Do not commit or push.

**Goal:** Implement the OpenOCB Backend/Engine file materialization control plane and rewrite Chat file placeholders to canonical absolute paths inside the active Bot container.

**Architecture:** Backend owns resource authorization and the durable state machine. Engine owns BaaS pull, atomic workspace materialization, manifest validation, and Chat rewriting. Transport-specific routers are thin; core services depend on Service/Plugin Protocols. Real Engine-to-BaaS pull remains fail-closed until its external contract is published.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy, injector, pytest/pytest-asyncio, existing OpenOCB `HttpClient`, `DeviceAdapterTransport`, and task queue.

## Global Constraints

- Product code changes are limited to `src/backend/src/agentclaw/community` and `src/engine/src/engine/community` in OpenOCB.
- No Frontend, BaaS server, Relay, or `corp` source changes.
- Never persist or log signed URLs, URL query strings, tokens, file bodies, raw session keys, full prompts, or sensitive absolute paths.
- Client-provided paths are untrusted. Only Engine may derive a Bot absolute path from `workspace_root_strict()` plus a manifest-relative path and `Path.resolve()` containment checks.
- Backend polling never probes Engine and never writes `ready`; only a matching task-version callback may write `ready` or `device_sync_failed`.
- Use ORM/parameter binding only. External HTTP targets come from qualified fixed clients or existing device transport, never a caller URL.
- Every changed server branch has diagnostic logs. Add `# COSEC` comments around custom path-boundary checks.
- Changed-file line coverage must exceed 90%; run ruff/pyflakes and E203/E265 style checks.

---

### Task 1: Backend Domain and Repository

**Files:**
- Create: `src/backend/src/agentclaw/community/core/session_resources/README.md`
- Create: `src/backend/src/agentclaw/community/core/session_resources/types.py`
- Create: `src/backend/src/agentclaw/community/core/session_resources/repository/protocol.py`
- Create: `src/backend/src/agentclaw/community/core/session_resources/repository/models.py`
- Create: `src/backend/src/agentclaw/community/plugins/session_resource_repository.py`
- Modify: `src/backend/src/agentclaw/community/plugins/local/database.py`
- Test: `src/backend/tests/community/core/session_resources/test_repository.py`

**Interfaces:**
- `SessionResourceStatus`: `upload_url_issued`, `device_syncing`, `ready`, `device_sync_failed`, `deleted`.
- `SessionResourceRecord`: immutable record containing opaque resource id, owner/bot/scope/session hash, engine/tenant/bot UUID, safe filename, device/workspace paths, transfer id, task id/version, status, hashes, materialized refs, error code, timestamps.
- `SessionResourceRepositoryProtocol.create/get_owned/list_owned/cas_start_materialization/cas_finish_materialization/soft_delete`.
- Every read/write predicate includes `resource_id + owner_id + bot_id + session_key_hash`; callback CAS additionally includes transfer id, task id/version, and current `device_syncing` state.

- [ ] Write repository tests first for create/read isolation, start CAS, success/failure callback CAS, stale callback no-op, and delete-terminal protection.
- [ ] Run the tests and confirm missing-module failures.
- [ ] Implement SQLAlchemy model and unified repository with ORM expressions only.
- [ ] Register the model in SQLite bootstrap and rerun tests green.

### Task 2: Backend Service, BaaS Contract, and Durable Dispatch

**Files:**
- Create: `src/backend/src/agentclaw/community/api/session_resource_service.py`
- Create: `src/backend/src/agentclaw/community/core/session_resources/service.py`
- Create: `src/backend/src/agentclaw/community/core/session_resources/baas_client.py`
- Create: `src/backend/src/agentclaw/community/core/session_resources/materialization.py`
- Create: `src/backend/src/agentclaw/community/di/modules/session_resources_module.py`
- Modify: `src/backend/src/agentclaw/community/di/container.py`
- Test: `src/backend/tests/community/core/session_resources/test_service.py`
- Test: `src/backend/tests/community/contracts/test_session_resource_baas_client.py`
- Test: `src/backend/tests/community/core/session_resources/test_materialization.py`

**Interfaces:**
- `SessionResourceServiceProtocol` exposes upload intent, complete, status, list/referable/reference, download grant, preview grant, delete, and materialized callback methods.
- `SessionResourceBaasClient` injects `Annotated[HttpClient, QUALIFIER_BAAS]`; it calls only `/api/v1/bots/{tenant}/{bot_uuid}/files/upload-url` and `/files/download-url`, validates the response envelope, and returns typed grants without logging URLs.
- `SessionResourceMaterializeHandler.task_type == "session_resource.materialize"`; payload includes resource/task/transfer ids, task version, owner/bot/session/scope hashes, engine and canonical device path. It resolves the Bot via `DeviceContextResolver` and invokes `/api/resource-materializations` through `DeviceAdapterTransport`.
- `upload_complete` first performs the repository CAS, then enqueues the durable task. On enqueue failure it CAS-finishes the same version as `device_sync_failed/dispatch_failed` and raises; no row remains falsely ready.

- [ ] Write failing tests for known BaaS wire shapes, URL redaction, complete idempotency, queue payload, enqueue failure compensation, and stale callback.
- [ ] Implement the minimal service/client/handler and DI bindings.
- [ ] Verify task handler registration occurs before an enabled worker starts and does not require device transport in profiles where the worker is disabled.
- [ ] Run contract and service tests green.

### Task 3: Backend HTTP Adapters

**Files:**
- Create: `src/backend/src/agentclaw/community/adapters/http/session_resources/__init__.py`
- Create: `src/backend/src/agentclaw/community/adapters/http/session_resources/schemas.py`
- Create: `src/backend/src/agentclaw/community/adapters/http/session_resources/router.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/app.py`
- Test: `src/backend/tests/community/adapters/http/session_resources/test_router.py`

**Interfaces:**
- Public router prefix: `/api/session-resources` and `Depends(get_current_user)`; owner id always comes from authenticated `staffId`, never request JSON.
- Internal callback: `POST /internal/session-resources/{resource_id}/materialized`; auth uses the existing internal/device-token pattern and delegates all task-version checks to the service.
- Responses expose status/display metadata and short-lived grants only; no Bot absolute path is returned.

- [ ] Write failing FastAPI tests for upload intent, complete, polling-before/after callback, cross-session denial, download-before-auth denial, and soft delete.
- [ ] Implement thin schemas/router and register it in the app.
- [ ] Run router tests green and verify adapters do not import concrete plugins.

### Task 4: Engine Materialization Core and API

**Files:**
- Create: `src/engine/src/engine/community/plugin_api/resource_materialization.py`
- Create: `src/engine/src/engine/community/core/resource_materialization/models.py`
- Create: `src/engine/src/engine/community/core/resource_materialization/service.py`
- Create: `src/engine/src/engine/community/api/resource_materialization/schemas.py`
- Create: `src/engine/src/engine/community/api/resource_materialization/router.py`
- Create: `src/engine/src/engine/community/di/modules/resource_materialization_module.py`
- Modify: `src/engine/src/engine/community/di/container.py`
- Modify: `src/engine/src/engine/community/api/app.py`
- Test: `src/engine/src/engine/community/core/resource_materialization/tests/test_service.py`
- Test: `src/engine/src/engine/community/api/resource_materialization/tests/test_router.py`

**Interfaces:**
- `BaasMaterializationClient.pull(request, destination) -> PullResult` and `BackendMaterializationCallbackClient.report(result)` are Plugin Protocols with fail-closed `not_configured` base implementations and fake test implementations.
- `ResourceMaterializationService.materialize(request)` validates ids/path segments, resolves the strict Bot workspace, writes to a same-directory temporary file, validates size/hash, atomically replaces the target, atomically writes a JSON manifest, and reports callback.
- Idempotency key is `resource_id + transfer_id + task_version`; a matching ready manifest returns success without downloading again; older versions cannot overwrite newer versions.
- `POST /api/resource-materializations` validates the internal auth gate, schedules the service, and returns accepted/task identity. Real BaaS pull endpoint is not guessed.

- [ ] Write failing tests for success, missing configuration, traversal/absolute/symlink escape, size/hash mismatch cleanup, idempotent replay, stale version, atomic manifest, and callback retry semantics.
- [ ] Implement protocols, core service, fail-closed bindings, router, and DI.
- [ ] Run core/API tests green and add protocol contract tests.

### Task 5: Engine Chat Reference Validation and Absolute-Path Rewrite

**Files:**
- Create: `src/engine/src/engine/community/core/resource_references/models.py`
- Create: `src/engine/src/engine/community/core/resource_references/service.py`
- Modify: `src/engine/src/engine/community/api/transport/ws_server.py`
- Test: `src/engine/src/engine/community/core/resource_references/tests/test_service.py`
- Test: `src/engine/src/engine/community/api/tests/transport/test_ws_server_resource_references.py`

**Interfaces:**
- `ResourceReferenceService.rewrite(prompt, session_key, resource_references, prompt_file_refs) -> ResolvedResourceContext`.
- Each `<file-ref insert_id="X"></file-ref>` maps exactly once to a structured `insert_id -> resource_id`, then to a ready manifest whose session hash matches the original `ChatRequest.sessionId`.
- Rewritten text preserves placeholder position and uses `<file-ref name="..." path="/bot/canonical/absolute/path"></file-ref>`; the absolute path is derived internally with `workspace_root_strict()` and never logged.
- `EngineWebSocketServer._stream_chat_events()` performs rewrite after `ChatRequest` creation and before `chat_plugin.stream()`. Since WS ACK is already sent, validation errors are emitted as asynchronous error events. Requests without references are byte-for-byte unchanged.

- [ ] Write failing pure-service tests for one/multiple replacements, missing/duplicate/mismatched refs, cross-session manifest, non-ready/missing/hash-changed files, caller path injection, and no-reference pass-through.
- [ ] Implement the parser using a structured XML/HTML-safe token parser or a narrowly anchored parser with exact full-match validation; do not evaluate templates.
- [ ] Add WS tests proving the adapter receives rewritten Bot absolute paths and invalid references never start the adapter stream.
- [ ] Run existing file/WS tests plus new tests green.

### Task 6: Verification and Reports

**Files:**
- Create: `/Users/helloworld/Desktop/codes/teamclaw/spec/pipeline/session-file-upload-materialization/002-code-report.md`

- [ ] Run focused Backend and Engine suites and cross-contract tests.
- [ ] Run changed modules with pytest-cov and verify changed-file line coverage above 90%.
- [ ] Run ruff/pyflakes and E203/E265 checks on every changed Python file.
- [ ] Run backend/engine architecture and import-boundary tests.
- [ ] Confirm `git diff` contains no Frontend, BaaS server, Relay, `corp`, secrets, or unrelated `src/bcsfuse/**` files.
- [ ] Record exact commands, pass counts, residual external BaaS pull limitation, and parent/OpenOCB branch state in `002-code-report.md`.
