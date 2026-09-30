# BCS OpenAPI V1 Session Files Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose the existing BCS session-file workspace through OpenAPI V1 with Human/Bot identity policy, application-owned authorization, OpenAPI URL projection, and preserved legacy behavior.

**Architecture:** Add transport-neutral V1 contracts and identity selection in `bcs-service-api`, implement the session-file facade in `bcs-app-session`, keep HTTP and URL projection in `bcs-api-http`, and wire validated configuration and existing concrete services in bootstrap. Delegate lifecycle/storage to the existing `SessionFileService`; do not change legacy contracts.

**Tech Stack:** Rust 2024, Axum 0.8, async-trait, Serde, Tower, existing BCS service/plugin APIs, Cargo tests.

---

## Global Constraints

- Work only in the isolated `codex/bcs-openapi-session-files` worktree.
- Follow `docs/arch/arch.rules.md` and `src/bcs/CLAUDE.md`.
- Do not run workspace-wide `cargo fmt`; preserve existing formatting and touch
  only intended lines.
- Write each behavior test first, run it red for the intended reason, then make
  the smallest production change and rerun green.
- Do not change legacy `/sessions/...` response or URL behavior.
- Do not implement #978 or #979 in this branch.
- Do not add capabilities, shared metadata, schema migrations, or notification
  reliability changes.

### Task 1: Add IdentityPolicy Contracts and Selection

**Files:**
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/authorization.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/principal.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/tests/v1_authenticated_caller_contract.rs`

**Steps:**

1. Add failing contract tests for:
   - default policy is `HumanOnly`;
   - Human-only, Bot-only, Human-or-Bot selection;
   - Bot-first selection when User+owned Bot coexist;
   - `403` when User+Bot ownership mismatches;
   - App-only and AccessKey-only fail closed;
   - extra App does not override a valid actor.
2. Run:
   `cargo test -p bcs-service-api --test v1_authenticated_caller_contract`
   and confirm the missing-policy/selector failures.
3. Implement `IdentityPolicy` and a transport-neutral `select_principal` helper.
   Keep default `HumanOnly`; do not query repositories.
4. Rerun the contract test, then `cargo test -p bcs-service-api`.
5. Commit: `feat(bcs): add OpenAPI identity policy selection`

### Task 2: Define the V1 Session File Application Contract

**Files:**
- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/session_file.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/mod.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/tests/v1_session_file_application_contracts.rs`

**Contract:**

- Commands carry `AuthenticatedCaller` and resource ids, never HTTP headers,
  URIs, or client-supplied notification URLs.
- Define file metadata/list/prepare/share/download route views and
  `SessionFileApplicationService` methods for prepare, upload stream, complete,
  delete, get, list, protected download, share mint, public share consume, and
  stream fetch.
- Reuse the existing transport-neutral `ByteStream` and presign ticket types.
- Keep internal storage handles out of V1 results.

**Steps:**

1. Write failing compilation/serialization tests for object safety, command
   fields, snake_case status/actor values, and response shapes.
2. Run:
   `cargo test -p bcs-service-api --test v1_session_file_application_contracts`
   and confirm missing symbols.
3. Implement the minimal contracts and exports.
4. Rerun the new test and all `bcs-service-api` tests.
5. Commit: `feat(bcs): define V1 session file application contract`

### Task 3: Extend Typed V1 Error Categories

**Files:**
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/error.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/error.rs`

**Steps:**

1. Extend the existing HTTP mapping test with payload-too-large (`413`),
   unprocessable (`422`), and bad-gateway (`502`) application errors.
2. Run:
   `cargo test -p bcs-api-http v1::common::error::tests::maps_application_errors_to_the_v1_http_contract`
   and confirm non-exhaustive/missing-variant failure.
3. Add typed `ApplicationError` variants and constructors without string-based
   status inference.
4. Map them to stable V1 codes while preserving all existing mappings.
5. Rerun `cargo test -p bcs-api-http v1::common::error` and
   `cargo test -p bcs-service-api`.
6. Commit: `feat(bcs): add session file error categories to V1`

### Task 4: Implement the Session File Application Facade

**Files:**
- Create: `src/bcs/crates/application/v1/bcs-app-session/src/file.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/src/lib.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/Cargo.toml`
- Create: `src/bcs/crates/application/v1/bcs-app-session/tests/session_file_facade.rs`

**Dependencies and constructor inputs:**

- existing `SessionFileService`;
- `SessionManagementService` and `GroupCoreService`;
- `BotRegistryCoreService`;
- `SystemMessageService`;
- no HTTP or concrete repository/plugin type.

**Steps:**

1. Build fakes and write failing tests for:
   - every IdentityPolicy principal combination;
   - Bot and Human/owned-Bot session membership;
   - prepare records the effective actor as owner;
   - list/get/download/share reject non-members;
   - PUT/Complete allow owner and Human creator of Bot owner;
   - PUT/Complete reject a different Bot, including a sibling Bot;
   - authorization occurs before stream mutation;
   - delete enriches the legacy command with creator/driver/identities;
   - share enriches participant and identity inputs;
   - legacy use-case errors map to stable V1 error codes;
   - Complete notifies other Bot participants after success;
   - notification failure is logged/ignored and completion stays successful;
   - completion failure sends no notification;
   - share consume maps all token/file failures to `shared_file_not_found`.
2. Run:
   `cargo test -p bcs-app-session --test session_file_facade`
   and confirm missing implementation failures.
3. Implement one shared load/membership path and narrow mutation authorization.
4. Project domain files into V1 views and map every
   `SessionFileUseCaseError` explicitly.
5. Move reusable complete-notification composition into this facade. Accept
   only a server-constructed notification content URL in the internal command.
6. Rerun the focused test, then `cargo test -p bcs-app-session`.
7. Commit: `feat(bcs): add session file V1 application facade`

### Task 5: Add OpenAPI Base URL Configuration

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/src/config.rs`
- Modify: `src/bcs/configs/bcs-config-example.toml`
- Modify: `src/bcs/configs/bcs-config-local.toml`
- Test: existing inline tests in `src/bcs/crates/bootstrap/bcs/src/config.rs`

**Steps:**

1. Add failing config tests for:
   - valid absolute HTTP/HTTPS base;
   - trailing slash normalization or rejection contract;
   - relative, blank, query, fragment, and userinfo rejection;
   - default behavior compatible with existing test configs.
2. Run the focused bootstrap config tests and observe failures.
3. Add `OpenApiV1Config { public_collaboration_base_url }`, validation, and
   default/local/example values. Keep raw environment access out of adapters.
4. Rerun focused tests and `cargo test -p bcs --lib config`.
5. Commit: `feat(bcs): configure the OpenAPI public collaboration URL`

### Task 6: Add V1 DTOs and URL Projector

**Files:**
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session_file.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/mod.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/session_file_url.rs`

**Steps:**

1. Write failing unit tests for:
   - encoded session/file/token paths;
   - protected content and shared content URLs;
   - single and multipart proxy prepare URL replacement;
   - direct OSS/BaaS upload URLs remaining unchanged;
   - V1 request DTO unknown-field rejection and query defaults.
2. Run the focused `bcs-api-http` tests and confirm missing modules.
3. Implement typed request DTOs and an injected projector that never reads
   request Host headers.
4. Use structured URL parsing rather than ad hoc host comparison; retain the
   exact direct-storage targets from the existing prepare result.
5. Rerun focused tests and `cargo test -p bcs-api-http`.
6. Commit: `feat(bcs): project session file URLs for OpenAPI V1`

### Task 7: Implement Protected HTTP Routes

**Files:**
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/session_file.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/mod.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/state.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/Cargo.toml`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/tests/openapi_v1_session_files.rs`

**Steps:**

1. Add a fake V1 file facade and failing route tests for all protected methods,
   status/envelope codes, bad path/query/body handling, and app-only denial.
2. Add raw-body tests proving PUT forwards chunks without a `Bytes` extractor
   and accepts absent Content-Length.
3. Add download tests for `302` and raw `200` stream responses, including
   Content-Disposition `show` behavior and V1-envelope errors.
4. Run:
   `cargo test -p bcs-api-http --test openapi_v1_session_files`
   and confirm routes are absent.
5. Add the facade and URL projector to `ApiState` through explicit builder
   wiring, keeping unrelated V1 tests source-compatible.
6. Implement thin handlers and register routes with explicit
   `HumanOrOwnedBot` policy declarations.
7. Disable the Axum body limit only for the PUT content route.
8. Rerun focused and package tests.
9. Commit: `feat(bcs): expose protected session file OpenAPI routes`

### Task 8: Split Public Share Download from Principal Middleware

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/mod.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/session_file.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/openapi_v1_session_files.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/tests/openapi_v1_mount.rs`

**Steps:**

1. Add failing tests proving:
   - shared content is reachable with no Principal header;
   - protected file routes still require a verified Principal;
   - missing, invalid, expired, and not-found tokens all return the identical
     `404/shared_file_not_found` envelope;
   - `show` is forwarded and successful content is unwrapped.
2. Run the focused tests and observe the current global middleware returning
   `401` for the public route.
3. Split public and protected router construction. Preserve request-id support
   for both and apply Principal verification only to protected routes.
4. Implement share consume/download through the application facade.
5. Rerun focused, mount, and all `bcs-api-http` tests.
6. Commit: `feat(bcs): allow token-only OpenAPI shared file download`

### Task 9: Wire the Facade in Bootstrap

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/Cargo.toml`
- Modify: `src/bcs/crates/bootstrap/bcs/tests/openapi_v1_mount.rs`

**Steps:**

1. Extend mount tests to fail unless the real OpenAPI state exposes the file
   routes and the configured projector base.
2. Run the focused bootstrap tests and observe missing state wiring.
3. Pass the existing session-file service and system-message service into
   `build_openapi_v1_state`; construct the facade and URL projector there.
4. Update each bootstrap profile/call site consistently. Do not construct a
   second storage service.
5. Rerun `cargo test -p bcs --test openapi_v1_mount` and focused server/config
   tests.
6. Commit: `feat(bcs): wire OpenAPI session files in bootstrap`

### Task 10: Share Complete Notification Orchestration with Legacy

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-http/src/routes/session_files.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-http/src/state.rs` or
  `src/bcs/crates/services/bcs-services-container/src/lib.rs` only if required
  to inject the shared facade
- Modify: closest legacy session-file HTTP tests

**Steps:**

1. Add/adjust a regression test proving the legacy complete route still emits
   the same message, receiver set, legacy URL, and best-effort semantics.
2. Run the focused legacy test before modification.
3. Replace handler-owned notification logic with the shared application
   complete use case while retaining the legacy URL projector in the legacy
   adapter.
4. Remove only helpers made dead by this change.
5. Rerun all legacy session-file route tests and
   `cargo test -p bcs-session-file`.
6. Commit: `refactor(bcs): share file completion notification orchestration`

### Task 11: Contract Documentation and Coverage Inventory

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/CONTEXT.md`
- Modify: the canonical BCS HTTP/OpenAPI endpoint denominator or contract
  inventory discovered by `rg` during implementation
- Modify: applicable OpenAPI/Gateway route documentation if present

**Steps:**

1. Run `rg` to locate the canonical route and coverage denominators; do not
   invent a second inventory.
2. Add the nine operations and public/protected policy distinction.
3. Document the new application dependencies and why they comply with the
   boundary constitution.
4. Run relevant architecture/contract validation scripts described by the
   touched inventory.
5. Commit: `docs(bcs): document OpenAPI session file boundaries`

### Task 12: Full Verification and Review

**Commands:**

1. Focused packages:

   ```bash
   cd src/bcs
   cargo test -p bcs-service-api
   cargo test -p bcs-app-session
   cargo test -p bcs-api-http
   cargo test -p bcs-session-file
   cargo test -p bcs --test openapi_v1_mount
   ```

2. Compile affected composition:

   ```bash
   cargo check -p bcs
   ```

3. Architecture and hygiene from repository root:

   ```bash
   git diff --check
   scripts/ci/check_arch_rules.sh
   ```

   If the named architecture script has changed, use the command documented in
   `docs/arch/ci.enforce.md` and record the exact replacement.

4. Inspect `git status --short` and `git diff --stat`; verify no unrelated
   worktree changes, generated files, secrets, private endpoints, or global
   formatting noise.
5. Review the diff against the design acceptance criteria and run the
   requesting-code-review and verification-before-completion workflows.
6. Do not claim completion until every required command has fresh passing
   evidence. Record any unavailable gate with its exact reason.
7. Final commit if needed: `test(bcs): cover OpenAPI session file workflows`

## Expected Deferred Follow-up

- #978 changes Gateway forwarding to stream request bodies and must evaluate
  the effect on all forwarded request types.
- #979 removes `share_url` construction from the existing service and projects
  legacy, message, and OpenAPI URLs entirely in delivery adapters.
