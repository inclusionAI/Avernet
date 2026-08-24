# Migrate manifest, assets & state-machine-runs to the gateway — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `GET /manifest`, `GET /assets/{bundle}/{file}`, and the six `state-machine-runs` endpoints (incl. `pending-human-nodes`) from the legacy `bcs-http` adapter to the `bcs-api-http` v1 internal router under `/api/v1/collaboration/**`, and make the frontend panel tolerate both raw and enveloped response shapes.

**Architecture:** Pure-additive (legacy `bcs-http` routes stay untouched). New route modules under `crates/adapters/http/bcs-api-http/src/v1/internal/routes/` reuse the existing application services (`CollaborationRuntimeService`, `ManifestConfig`) directly, implement auth in the HTTP layer by mapping the gateway-signed `AuthenticatedCaller` to `AuthenticatedHumanCaller`, and wrap JSON responses in the v1 `Envelope`. New paths sit under `/api/v1/collaboration/**`, covered by the gateway's existing `collaboration-internal` domain — no new upstream domain, only `route_security` additions.

**Tech Stack:** Rust (axum 0.8, async-trait, serde), `bcs-service-api` (re-exports `CollaborationRuntimeService` + view types at crate root), `bcs-config-api` (`ManifestConfig`), TypeScript/React + Vite (panel), YAML OpenAPI fragments.

## Global Constraints

- Do NOT run `cargo fmt` or any global formatter. Keep whitespace edits to lines that must change.
- Do NOT modify any legacy `bcs-http` route in `crates/adapters/http/bcs-http/src/router.rs` (dual-exposure required).
- New paths are prefixed `/api/v1/collaboration/...`; legacy top-level paths (`/manifest`, `/assets/...`, `/state-machine-runs/...`) stay — they never collide because the literals differ.
- Response envelope for JSON is `{ code, message, data, request_id }` via `Envelope::success(20_000, "OK", data, request_id)`. `/assets` stays raw file bytes (not enveloped).
- Commits go on the current feature branch (`feat/state-machine-runs-manifest-gateway-migration`), never on `dev`.
- Multi-byte strings (Chinese) must never be sliced by byte index; this plan does not slice user strings.

## File Structure

**Created:**
- `crates/adapters/http/bcs-api-http/src/v1/internal/routes/collaboration_run.rs` — the six state-machine-run route handlers + HTTP-layer auth + error mapping.
- `crates/adapters/http/bcs-api-http/src/v1/internal/routes/manifest.rs` — enveloped `GET /manifest` + raw-bytes `GET /assets/{bundle}/{file}` (public boundary).
- `crates/adapters/http/bcs-api-http/tests/collaboration_run_routes.rs` — route tests + `FakeCollaborationRuntimeService`.
- `crates/adapters/http/bcs-api-http/tests/manifest_routes.rs` — manifest/assets contract tests.
- `api-contracts/v1/openapi/state-machine-runs.yaml` — six path-item fragments.
- `api-contracts/v1/openapi/manifest.yaml` — manifest + asset path-item fragments.

**Modified:**
- `crates/adapters/http/bcs-api-http/Cargo.toml` — add `bcs-config-api` dep.
- `crates/adapters/http/bcs-api-http/src/v1/common/state.rs` — add `collaboration_runtime_service`, `manifest`, `manifest_env` fields + builders.
- `crates/adapters/http/bcs-api-http/src/v1/internal/routes/mod.rs` — register new modules.
- `crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs` — merge new routers into protected/public nests.
- `crates/bootstrap/bcs/src/server.rs` — pass `collaboration_runtime` + manifest config into `ApiState` builders.
- `gateway/configs/application.yaml` — `route_security` additions.
- `api-contracts/v1/internal.yaml` — `$ref` entries for new paths.
- `src/bcs/assets/panel/src/StateMachineRunView.tsx` — `unwrapEnvelope` normalizer at all JSON parse sites.

**Not committed (gitignored build output, regenerated locally):**
- `src/bcs/assets/panel/dist/index.umd.js` — rebuilt via `npm run build`.

---

## Task 1: `GET /state-machine-runs/{run_id}` route + ApiState wiring + test harness

Establishes the new route module, the HTTP-layer auth helper, the error-mapping helper, the `ApiState` field/builder, the bootstrap wiring, and the `FakeCollaborationRuntimeService` test harness that all later state-machine-run tasks reuse.

**Files:**
- Create: `crates/adapters/http/bcs-api-http/src/v1/internal/routes/collaboration_run.rs`
- Create: `crates/adapters/http/bcs-api-http/tests/collaboration_run_routes.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/internal/routes/mod.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/common/state.rs`
- Modify: `crates/bootstrap/bcs/src/server.rs` (ApiState assembly ~line 1601 + function body)

**Interfaces:**
- Produces:
  - `pub fn protected_router() -> Router<ApiState>` in `collaboration_run.rs` (extended in Tasks 2–3).
  - `fn authenticated_human(caller: &AuthenticatedCaller) -> Option<AuthenticatedHumanCaller>` — HTTP-layer auth mapping (Tasks 2–3 reuse).
  - `fn collaboration_runtime_error_to_application_error(CollaborationRuntimeError) -> ApplicationError` (Tasks 2–3 reuse).
  - `ApiState::with_collaboration_runtime_service(Arc<dyn CollaborationRuntimeService>) -> Self` (Task 5 also relies on ApiState builder pattern).

- [ ] **Step 1: Add the `collaboration_runtime_service` field + builder to `ApiState`**

Edit `crates/adapters/http/bcs-api-http/src/v1/common/state.rs`.

In the `use bcs_service_api::application::v1::{...}` import block, the trait is NOT there — `CollaborationRuntimeService` lives at the crate root. Add a new import line after the existing `use bcs_service_api::application::v1::...;`:

```rust
use bcs_service_api::CollaborationRuntimeService;
```

Add the field to the `ApiState` struct (after `collaboration_definition_service`):

```rust
    pub collaboration_runtime_service: Option<Arc<dyn CollaborationRuntimeService>>,
```

In `ApiState::new`, add to the `Self { ... }` initializer (after `collaboration_definition_service: None,`):

```rust
            collaboration_runtime_service: None,
```

Add the builder method (place it after `with_collaboration_definition_service`):

```rust
    /// Add the legacy CollaborationRuntime service for the v1 state-machine-run
    /// endpoints. Auth is performed in the HTTP layer; this service is reused
    /// verbatim. Fail-closed (handler returns `internal` if None) until
    /// bootstrap mounts it.
    pub fn with_collaboration_runtime_service(
        mut self,
        service: Arc<dyn CollaborationRuntimeService>,
    ) -> Self {
        self.collaboration_runtime_service = Some(service);
        self
    }
```

- [ ] **Step 2: Write the failing test harness + `get_run` test**

Create `crates/adapters/http/bcs-api-http/tests/collaboration_run_routes.rs`:

```rust
#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::*;
use bcs_service_api::{
    AuthenticatedHumanCaller, CollaborationRuntimeError, CollaborationRuntimeService,
    StateMachineRunAccessCommand, StateMachineRunView,
};
use serde_json::{Value, json};
use tower::ServiceExt;

const RUN_ID: &str = "run-1";

struct HeaderVerifier;

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(AuthenticatedCaller {
                tenant: Some("tenant-1".into()),
                user: Some(AuthenticatedUserIdentity {
                    id: "staff-1".to_string(),
                    username: "staff-1".to_string(),
                    display_name: Some("Staff One".to_string()),
                    full_name: None,
                }),
                bot: None,
                app: None,
                access_key: None,
            })
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

#[derive(Default)]
struct FakeRuntimeService {
    next_run: Mutex<Option<StateMachineRunView>>,
    last_access: Mutex<Option<StateMachineRunAccessCommand>>,
}

// The `CollaborationRuntimeService` trait has 9 methods WITHOUT default
// implementations. Two are given real behavior here; the override adds canned
// data for the `_with_access` path the handler calls.
//
// To fill in the remaining 7 required stubs reliably, first write the empty
// `impl CollaborationRuntimeService for FakeRuntimeService {}`, then run
// `cargo check -p bcs-api-http --test collaboration_run_routes`. The compiler
// prints the exact list of required methods. For each, copy the signature,
// prefix every unused parameter with `_`, and return the stub below matching
// the return type:
//
//   Result<Option<_>, _>  -> Err(CollaborationRuntimeError::InvalidRequest("not used in fake".to_string()))
//   Result<_, _>          -> Err(CollaborationRuntimeError::InvalidRequest("not used in fake".to_string()))
//   Result<(), _>         -> Err(CollaborationRuntimeError::InvalidRequest("not used in fake".to_string()))
//
// The 7 required stubs (signatures verbatim from
// `crates/service-api/bcs-service-api/src/application/collaboration_runtime.rs`,
// trait body lines ~429–773):
//   - get_state_machine_session_history(&self, _session_id: &str, _limit: u64, _before: Option<u64>) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError>
//   - cancel_state_machine_run(&self, _cmd: CancelStateMachineRunCommand) -> Result<StateMachineRunView, CollaborationRuntimeError>
//   - lookup_delivery_correlation(&self, _run_id: &str) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError>
//   - register_delivery_alias(&self, _delivery_request_id: &str, _bot_delivery_run_id: String) -> Result<(), CollaborationRuntimeError>
//   - handle_bot_terminal_event(&self, _cmd: HandleBotTerminalEventCommand) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError>
//   - upsert_definition(&self, _definition: CollaborationDefinition) -> Result<(), CollaborationRuntimeError>
//   - configure_group_runtime(&self, _cmd: ConfigureGroupRuntimeCommand) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError>
//
// Import the referenced command/outcome types from `bcs_service_api` (they are
// all re-exported at the crate root from `application::collaboration_runtime`).
// If the compiler reports a type is not re-exported, import it from
// `bcs_service_api::application::collaboration_runtime` instead. Tasks 2–3 add
// overrides for the `_with_access` / `respond_human_node` / `list_pending_human_nodes`
// / `cancel_state_machine_run_with_access` methods (these have default impls;
// override only the ones a test exercises).
#[async_trait]
impl CollaborationRuntimeService for FakeRuntimeService {
    async fn start_state_machine_run(
        &self,
        _cmd: bcs_service_api::StartStateMachineRunCommand,
    ) -> Result<bcs_service_api::StartStateMachineRunOutcome, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::Internal("not used".to_string()))
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(self.next_run.lock().expect("run lock").clone())
    }

    async fn get_state_machine_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        *self.last_access.lock().expect("access lock") = Some(cmd.clone());
        Ok(self.next_run.lock().expect("run lock").clone())
    }

    // ... the 7 required stubs from the compiler-driven step above ...
}

fn sample_run() -> StateMachineRunView {
    serde_json::from_value(json!({
        "run": {
            "run_id": RUN_ID,
            "definition_id": "def-1",
            "definition_version": 1,
            "group_id": "group-1",
            "session_id": "session-1",
            "status": "running",
            "input": {"query": "example"},
            "created_at": 0,
            "updated_at": 0
        },
        "nodes": [],
        "judge_outputs": []
    }))
    .expect("sample StateMachineRunView")
}

fn test_router(service: Arc<FakeRuntimeService>) -> axum::Router {
    router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier),
        )
        .with_collaboration_runtime_service(service),
    )
}

async fn response_json(response: axum::http::Response<Body>) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX).await.expect("body");
    serde_json::from_slice(&bytes).expect("json body")
}

#[tokio::test]
async fn get_run_returns_enveloped_view_and_records_access() {
    let service = Arc::new(FakeRuntimeService {
        next_run: Mutex::new(Some(sample_run())),
        ..Default::default()
    });
    let app = test_router(service.clone());

    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1")
        .header("x-test-auth", "yes")
        .header("x-request-id", "req-get-run")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["data"]["run"]["run_id"], RUN_ID);
    assert_eq!(body["request_id"], "req-get-run");

    let recorded = service
        .last_access
        .lock()
        .expect("access lock")
        .clone()
        .expect("access command recorded");
    assert_eq!(recorded.run_id, RUN_ID);
    let human = recorded.authenticated_human.expect("human mapped");
    assert_eq!(human.actor_id, "human_staff-1");
    assert_eq!(human.display_name.as_deref(), Some("Staff One"));
}

#[tokio::test]
async fn get_run_returns_enveloped_404_when_missing() {
    let service = Arc::new(FakeRuntimeService::default());
    let app = test_router(service);

    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1")
        .header("x-test-auth", "yes")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = response_json(response).await;
    assert_eq!(body["code"], 40400);
    // The v1 error envelope puts `error_code` under `data`:
    // { code, message, data: { error_code }, request_id }.
    assert_eq!(body["data"]["error_code"], "not_found");
}
```

Note: `NoopGroupService`, `NoopSessionService`, `NoopMessageService`, `NoopInvitationService`, `NoopFriendshipService` are the noop service stubs. Copy them verbatim from `tests/collaboration_template_routes.rs` (lines ~114–283) — same file already contains all five with `Err(ApplicationError::internal("not configured"))` bodies. Reuse the exact definitions; do not abbreviate.

`StateMachineRunView` is `{ run: StateMachineRun, nodes: Vec<_>, judge_outputs: Vec<_> }`. `StateMachineRun` requires `run_id, definition_id, definition_version, group_id, session_id, status, input (Value — no default), created_at, updated_at` (see `crates/contracts/bcs-domain/src/collaboration.rs:423`). If `from_value` fails at runtime, re-read those structs and add the missing required field rather than relaxing the assertion.

- [ ] **Step 3: Run the test to verify it fails**

Run: `cargo test -p bcs-api-http --test collaboration_run_routes`
Expected: compile failure — `collaboration_run.rs` does not exist / route not registered.

- [ ] **Step 4: Create `collaboration_run.rs` with helpers + the `get_run` handler + `protected_router`**

Create `crates/adapters/http/bcs-api-http/src/v1/internal/routes/collaboration_run.rs`:

```rust
use std::sync::Arc;

use axum::Json;
use axum::extract::rejection::PathRejection;
use axum::extract::{Extension, Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use bcs_service_api::application::v1::{ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity};
use bcs_service_api::{
    AuthenticatedHumanCaller, CollaborationRuntimeError, CollaborationRuntimeService,
    StateMachineRunAccessCommand,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};

pub fn protected_router() -> Router<ApiState> {
    Router::new()
        .route(
            "/state-machine-runs/{run_id}",
            get(get_run),
        )
}

fn service(state: &ApiState, request_id: &RequestId) -> Result<Arc<dyn CollaborationRuntimeService>, ErrorResponse> {
    state
        .collaboration_runtime_service
        .clone()
        .ok_or_else(|| {
            application_error_response(
                request_id,
                ApplicationError::internal("V1 Collaboration Runtime service is not configured"),
            )
        })
}

/// HTTP-layer auth: project the gateway-signed User into BCS's Human Actor
/// model. `actor_id` follows the legacy `human_{staff_no}` convention. Returns
/// `None` when no User principal is present (callers decide whether that is
/// allowed).
fn authenticated_human(caller: &AuthenticatedCaller) -> Option<AuthenticatedHumanCaller> {
    caller.user.as_ref().map(
        |AuthenticatedUserIdentity { id, display_name, .. }| AuthenticatedHumanCaller {
            actor_id: format!("human_{id}"),
            display_name: display_name.clone(),
        },
    )
}

fn runtime_error(request_id: &RequestId, error: CollaborationRuntimeError) -> ErrorResponse {
    application_error_response(request_id, collaboration_runtime_error_to_application_error(error))
}

fn collaboration_runtime_error_to_application_error(
    error: CollaborationRuntimeError,
) -> ApplicationError {
    match error {
        CollaborationRuntimeError::RunNotFound(_)
        | CollaborationRuntimeError::NodeNotFound { .. }
        | CollaborationRuntimeError::DefinitionNotFound(_, _) => {
            ApplicationError::not_found("not_found", error.to_string())
        }
        CollaborationRuntimeError::InvalidDefinition(_)
        | CollaborationRuntimeError::InvalidParticipantBinding(_)
        | CollaborationRuntimeError::InvalidRequest(_) => {
            ApplicationError::invalid("invalid_request", error.to_string())
        }
        CollaborationRuntimeError::Unauthenticated => ApplicationError::Unauthenticated,
        CollaborationRuntimeError::Forbidden(_) => {
            ApplicationError::forbidden_code("forbidden", error.to_string())
        }
        CollaborationRuntimeError::JudgeUnavailable(_) => {
            ApplicationError::bad_gateway("judge_unavailable", error.to_string())
        }
        CollaborationRuntimeError::Conflict(_) => {
            ApplicationError::conflict("conflict", error.to_string())
        }
        CollaborationRuntimeError::Internal(_) => ApplicationError::internal(error.to_string()),
    }
}

async fn get_run(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(run_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let human = authenticated_human(&caller);
    let view = service(&state, &request_id)?
        .get_state_machine_run_with_access(StateMachineRunAccessCommand {
            run_id,
            authenticated_human: human,
        })
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    match view {
        Some(view) => Ok((
            StatusCode::OK,
            Json(Envelope::success(20_000, "OK", view, request_id.0)),
        )
            .into_response()),
        None => Err(application_error_response(
            &request_id,
            ApplicationError::not_found("not_found", "state machine run not found"),
        )),
    }
}

// Task 3 adds `JsonRejection`, `Deserialize`, and `axum::routing::post` when
// wiring the POST routes, plus the request body structs.
```

- [ ] **Step 5: Register the module + merge into the protected nest**

Edit `crates/adapters/http/bcs-api-http/src/v1/internal/routes/mod.rs` — add:

```rust
pub mod collaboration_run;
```

Edit `crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs` — in `protected_router()`, append `.merge(routes::collaboration_run::protected_router())` so the function reads:

```rust
pub fn protected_router() -> Router<ApiState> {
    Router::new().nest(
        "/api/v1/collaboration",
        routes::bot::router()
            .merge(routes::collaboration_template::router())
            .merge(routes::collaboration_definition::router())
            .merge(routes::session_file::protected_router())
            .merge(routes::collaboration_run::protected_router()),
    )
}
```

- [ ] **Step 6: Wire `collaboration_runtime` into `ApiState` in the bootstrap**

Edit `crates/bootstrap/bcs/src/server.rs`. The function that builds `ApiState` (~line 1480–1618) already receives `collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService>` as a parameter. In the `ApiState::new(...)` assembly (~line 1601–1615), add `.with_collaboration_runtime_service(collaboration_runtime.clone())` to the builder chain:

```rust
        .with_collaboration_template_service(collaboration_template_service)
        .with_collaboration_definition_service(collaboration_definition_service)
        .with_collaboration_runtime_service(collaboration_runtime.clone()),
```

(The trailing parameter-comma before the closing of the tuple becomes the last builder call.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cargo test -p bcs-api-http --test collaboration_run_routes`
Expected: PASS (both `get_run_returns_enveloped_view_and_records_access` and `get_run_returns_enveloped_404_when_missing`).

Also run: `cargo build -p bcs-api-http -p bcs`
Expected: builds cleanly.

- [ ] **Step 8: Commit**

```bash
git add crates/adapters/http/bcs-api-http/Cargo.toml \
  crates/adapters/http/bcs-api-http/src/v1/common/state.rs \
  crates/adapters/http/bcs-api-http/src/v1/internal/routes/mod.rs \
  crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs \
  crates/adapters/http/bcs-api-http/src/v1/internal/routes/collaboration_run.rs \
  crates/adapters/http/bcs-api-http/tests/collaboration_run_routes.rs \
  crates/bootstrap/bcs/src/server.rs
git commit -m "feat(bcs-api-http): migrate GET state-machine-runs/{run_id} to v1 internal router"
```

---

## Task 2: `GET` reads — graph, node-run, pending-human-nodes

Adds the remaining three read endpoints. `pending-human-nodes` requires a User principal (401 otherwise), matching the legacy `authenticated_human`.

**Files:**
- Modify: `crates/adapters/http/bcs-api-http/src/v1/internal/routes/collaboration_run.rs`
- Modify: `crates/adapters/http/bcs-api-http/tests/collaboration_run_routes.rs`

**Interfaces:**
- Consumes: `authenticated_human`, `service`, `runtime_error` from Task 1.
- Produces: `protected_router()` now also serves `/state-machine-runs/{run_id}/graph`, `/.../nodes/{node_id}`, `/.../pending-human-nodes`.

- [ ] **Step 1: Write the failing tests**

Append to `crates/adapters/http/bcs-api-http/tests/collaboration_run_routes.rs` (add imports for the view/command types at the top of the file as needed):

```rust
use bcs_service_api::{
    ListPendingHumanNodesCommand, PendingHumanNodeView, StateMachineNodeRunView,
    StateMachineRunGraphView,
};
```

Extend `FakeRuntimeService` with override state and trait overrides (add fields + impls):

```rust
#[derive(Default)]
struct FakeRuntimeService {
    next_run: Mutex<Option<StateMachineRunView>>,
    next_graph: Mutex<Option<StateMachineRunGraphView>>,
    next_node: Mutex<Option<StateMachineNodeRunView>>,
    next_pending: Mutex<Vec<PendingHumanNodeView>>,
    last_access: Mutex<Option<StateMachineRunAccessCommand>>,
    last_pending_cmd: Mutex<Option<ListPendingHumanNodesCommand>>,
}
```

(Replace the `#[derive(Default)] struct FakeRuntimeService { ... }` from Task 1 with this fuller struct; `Default` still works because all fields are `Mutex<Option/Vec>` which derive `Default`.)

Add trait overrides inside the existing `impl CollaborationRuntimeService for FakeRuntimeService`:

```rust
    async fn get_state_machine_run_graph_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
    ) -> Result<Option<StateMachineRunGraphView>, CollaborationRuntimeError> {
        *self.last_access.lock().expect("access lock") = Some(cmd.clone());
        Ok(self.next_graph.lock().expect("graph lock").clone())
    }

    async fn get_state_machine_node_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        *self.last_access.lock().expect("access lock") = Some(cmd.clone());
        assert_eq!(node_id, "node-1");
        Ok(self.next_node.lock().expect("node lock").clone())
    }

    async fn list_pending_human_nodes(
        &self,
        cmd: ListPendingHumanNodesCommand,
    ) -> Result<Vec<PendingHumanNodeView>, CollaborationRuntimeError> {
        *self.last_pending_cmd.lock().expect("pending lock") = Some(cmd.clone());
        Ok(self.next_pending.lock().expect("pending lock").clone())
    }
```

Add tests:

```rust
#[tokio::test]
async fn get_graph_returns_enveloped_view() {
    let service = Arc::new(FakeRuntimeService {
        next_graph: Mutex::new(Some(serde_json::from_value(json!({
            "run": { "run_id": RUN_ID, "definition_id": "def-1", "definition_version": 1,
                     "group_id": "group-1", "session_id": "session-1", "status": "running",
                     "input": {"query": "example"}, "created_at": 0, "updated_at": 0 },
            "definition": { "id": "def-1", "version": 1, "name": "d",
                            "graph_mode": "acyclic", "initial_nodes": [] },
            "nodes": [],
            "edges": []
        })).expect("graph"))),
        ..Default::default()
    });
    let app = test_router(service);
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/graph")
        .header("x-test-auth", "yes")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20000);
    assert_eq!(body["data"]["run"]["run_id"], RUN_ID);
    assert_eq!(body["data"]["definition"]["name"], "d");
}

#[tokio::test]
async fn get_node_returns_enveloped_view() {
    let service = Arc::new(FakeRuntimeService {
        next_node: Mutex::new(Some(serde_json::from_value(json!({
            // StateMachineNodeRun requires run_id, node_id, status, attempt.
            "node": { "run_id": RUN_ID, "node_id": "node-1", "status": "running", "attempt": 1 },
        })).expect("node"))),
        ..Default::default()
    });
    let app = test_router(service);
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/nodes/node-1")
        .header("x-test-auth", "yes")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["node"]["node_id"], "node-1");
}

#[tokio::test]
async fn pending_human_nodes_returns_enveloped_array() {
    let pending = vec![PendingHumanNodeView {
        node_id: "node-1".to_string(),
        display_name: "Review".to_string(),
        instruction: "please review".to_string(),
        response_ref: "ref-1".to_string(),
        judge_outcomes: vec![],
        timeout_deadline_ms: None,
        upstream_artifacts: vec![],
    }];
    let service = Arc::new(FakeRuntimeService {
        next_pending: Mutex::new(pending),
        ..Default::default()
    });
    let app = test_router(service.clone());
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/pending-human-nodes")
        .header("x-test-auth", "yes")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"][0]["node_id"], "node-1");

    let cmd = service.last_pending_cmd.lock().expect("pending lock").clone().expect("cmd");
    assert_eq!(cmd.caller_actor_id, "human_staff-1");
}

#[tokio::test]
async fn pending_human_nodes_requires_user_principal() {
    let app = test_router(Arc::new(FakeRuntimeService::default()));
    // No x-test-auth header -> HeaderVerifier returns Missing -> 401 from the
    // verify_principal boundary before the handler runs.
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/pending-human-nodes")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}
```

If `StateMachineRunGraphView` / `StateMachineNodeRunView` / `PendingHumanNodeView` do not derive `Deserialize`, construct them field-by-field from the struct definitions in `application/collaboration_runtime.rs` instead of `serde_json::from_value`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cargo test -p bcs-api-http --test collaboration_run_routes`
Expected: FAIL / compile error — the new routes are not yet registered.

- [ ] **Step 3: Implement the three handlers**

In `crates/adapters/http/bcs-api-http/src/v1/internal/routes/collaboration_run.rs`, add imports:

```rust
use bcs_service_api::{
    ListPendingHumanNodesCommand, PendingHumanNodeView, StateMachineNodeRunView,
    StateMachineRunGraphView,
};
```

Add the three handler functions:

```rust
async fn get_graph(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(run_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let view = service(&state, &request_id)?
        .get_state_machine_run_graph_with_access(StateMachineRunAccessCommand {
            run_id,
            authenticated_human: authenticated_human(&caller),
        })
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    view_view_or_404(view, &request_id)
}

async fn get_node(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((run_id, node_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let view = service(&state, &request_id)?
        .get_state_machine_node_run_with_access(
            StateMachineRunAccessCommand {
                run_id,
                authenticated_human: authenticated_human(&caller),
            },
            &node_id,
        )
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    view_view_or_404(view, &request_id)
}

async fn list_pending_human_nodes(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(run_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let human = authenticated_human(&caller).ok_or_else(|| {
        application_error_response(&request_id, ApplicationError::Unauthenticated)
    })?;
    let nodes: Vec<PendingHumanNodeView> = service(&state, &request_id)?
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id,
            caller_actor_id: human.actor_id,
        })
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", nodes, request_id.0)),
    )
        .into_response())
}

fn view_view_or_404<T: serde::Serialize>(
    view: Option<T>,
    request_id: &RequestId,
) -> Result<Response, ErrorResponse> {
    match view {
        Some(view) => Ok((
            StatusCode::OK,
            Json(Envelope::success(20_000, "OK", view, request_id.0)),
        )
            .into_response()),
        None => Err(application_error_response(
            request_id,
            ApplicationError::not_found("not_found", "state machine run not found"),
        )),
    }
}
```

Update `protected_router()` to register all three reads:

```rust
pub fn protected_router() -> Router<ApiState> {
    Router::new()
        .route("/state-machine-runs/{run_id}", get(get_run))
        .route("/state-machine-runs/{run_id}/graph", get(get_graph))
        .route(
            "/state-machine-runs/{run_id}/nodes/{node_id}",
            get(get_node),
        )
        .route(
            "/state-machine-runs/{run_id}/pending-human-nodes",
            get(list_pending_human_nodes),
        )
}
```

(Axum matches the static segment `pending-human-nodes` before the `{node_id}` param; `nodes/{node_id}` and `pending-human-nodes` are distinct literals so ordering is safe.)

Refactor `get_run` to use the new `view_view_or_404` helper (replace its `match view { ... }` body with `view_view_or_404(view, &request_id)`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cargo test -p bcs-api-http --test collaboration_run_routes`
Expected: PASS (all six tests so far).

- [ ] **Step 5: Commit**

```bash
git add crates/adapters/http/bcs-api-http/src/v1/internal/routes/collaboration_run.rs \
        crates/adapters/http/bcs-api-http/tests/collaboration_run_routes.rs
git commit -m "feat(bcs-api-http): migrate state-machine-runs graph, node & pending-human-nodes reads"
```

---

## Task 3: `POST` writes — respond (human required), cancel (optional human)

**Files:**
- Modify: `crates/adapters/http/bcs-api-http/src/v1/internal/routes/collaboration_run.rs`
- Modify: `crates/adapters/http/bcs-api-http/tests/collaboration_run_routes.rs`

**Interfaces:**
- Consumes: `authenticated_human`, `service`, `runtime_error` from Task 1.
- Produces: `protected_router()` now also serves `POST /.../respond` and `POST /.../cancel`. Adds the `JsonRejection`, `Deserialize`, and `axum::routing::post` imports plus the two request-body structs.

- [ ] **Step 1: Write the failing tests**

Append the following imports, fields/overrides, and tests to the test file:

```rust
use bcs_service_api::{HumanResponseSource, RespondHumanNodeCommand, RespondHumanNodeOutcome};
```

Extend `FakeRuntimeService` with respond + cancel state and overrides (the default `respond_human_node` / `cancel_state_machine_run_with_access` return `InvalidRequest`, so override them to return canned data):

```rust
// add fields:
    next_respond: Mutex<Option<RespondHumanNodeOutcome>>,
    last_respond: Mutex<Option<RespondHumanNodeCommand>>,
    next_cancel: Mutex<Option<StateMachineRunView>>,
    last_cancel_reason: Mutex<Option<String>>,

// add impl overrides (inside the existing impl block):
    async fn respond_human_node(
        &self,
        cmd: RespondHumanNodeCommand,
    ) -> Result<RespondHumanNodeOutcome, CollaborationRuntimeError> {
        *self.last_respond.lock().expect("respond lock") = Some(cmd.clone());
        self.next_respond
            .lock()
            .expect("respond lock")
            .clone()
            .ok_or_else(|| CollaborationRuntimeError::Internal("no canned respond".to_string()))
    }

    async fn cancel_state_machine_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
        reason: Option<String>,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        *self.last_access.lock().expect("access lock") = Some(cmd.clone());
        *self.last_cancel_reason.lock().expect("reason lock") = reason;
        self.next_cancel
            .lock()
            .expect("cancel lock")
            .clone()
            .ok_or_else(|| CollaborationRuntimeError::Internal("no canned cancel".to_string()))
    }
```

Add the respond test:

```rust
#[tokio::test]
async fn respond_requires_user_and_returns_enveloped_outcome() {
    // RespondHumanNodeOutcome = { node: StateMachineNodeRun, run: StateMachineRun }.
    // StateMachineNodeRun requires run_id, node_id, status, attempt.
    // StateMachineNodeStatus variants (snake_case): pending/ready/running/completed/
    // failed/retry_scheduled/skipped — "succeeded" is NOT valid, use "completed".
    let service = Arc::new(FakeRuntimeService {
        next_respond: Mutex::new(Some(serde_json::from_value(json!({
            "node": { "run_id": RUN_ID, "node_id": "node-1", "status": "completed", "attempt": 1 },
            "run": { "run_id": RUN_ID, "definition_id": "def-1", "definition_version": 1,
                     "group_id": "group-1", "session_id": "session-1", "status": "running",
                     "input": {"query": "example"}, "created_at": 0, "updated_at": 0 },
        })).expect("respond outcome"))),
        ..Default::default()
    });
    let app = test_router(service.clone());

    let request = Request::builder()
        .method("POST")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/nodes/node-1/respond")
        .header("x-test-auth", "yes")
        .header("content-type", "application/json")
        .body(Body::from(json!({"content": "approved"}).to_string()))
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["run"]["run_id"], RUN_ID);

    let cmd = service.last_respond.lock().expect("lock").clone().expect("cmd");
    assert_eq!(cmd.run_id, "run-1");
    assert_eq!(cmd.node_id, "node-1");
    assert_eq!(cmd.caller_actor_id, "human_staff-1");
    assert_eq!(cmd.content, "approved");
    assert!(matches!(cmd.source, HumanResponseSource::Http));
}

#[tokio::test]
async fn respond_rejects_missing_user_principal() {
    // No x-test-auth header -> HeaderVerifier returns Missing -> 401 at the
    // verify_principal boundary before the handler runs.
    let app = test_router(Arc::new(FakeRuntimeService::default()));
    let request = Request::builder()
        .method("POST")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/nodes/node-1/respond")
        .header("content-type", "application/json")
        .body(Body::from(json!({"content": "approved"}).to_string()))
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}
```

Add the cancel test:

```rust
#[tokio::test]
async fn cancel_returns_enveloped_view_with_optional_human() {
    // cancel returns a StateMachineRunView = { run, nodes, judge_outputs };
    // the run_id lives at data.run.run_id, not data.run_id.
    let service = Arc::new(FakeRuntimeService {
        next_cancel: Mutex::new(Some(sample_run())),
        ..Default::default()
    });
    let app = test_router(service.clone());
    let request = Request::builder()
        .method("POST")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/cancel")
        .header("x-test-auth", "yes")
        .header("content-type", "application/json")
        .body(Body::from(json!({"reason": "done"}).to_string()))
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["run"]["run_id"], RUN_ID);
    assert_eq!(*service.last_cancel_reason.lock().unwrap(), Some("done".to_string()));
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cargo test -p bcs-api-http --test collaboration_run_routes`
Expected: FAIL — `respond` / `cancel` routes not registered.

- [ ] **Step 3: Implement the two POST handlers**

In `collaboration_run.rs`, add the imports needed by the POST routes:

```rust
use axum::extract::rejection::JsonRejection;
use axum::routing::post;
use bcs_service_api::{HumanResponseSource, RespondHumanNodeCommand};
use serde::Deserialize;
```

Add request body structs:

```rust
#[derive(Debug, Deserialize)]
struct RespondHumanNodeRequest {
    pub content: String,
}

#[derive(Debug, Deserialize)]
struct CancelStateMachineRunRequest {
    #[serde(default)]
    pub reason: Option<String>,
}
```

Add handlers:

```rust
async fn respond_human_node(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
    body: Result<Json<RespondHumanNodeRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((run_id, node_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Json(body) = body.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let human = authenticated_human(&caller).ok_or_else(|| {
        application_error_response(&request_id, ApplicationError::Unauthenticated)
    })?;
    let outcome = service(&state, &request_id)?
        .respond_human_node(RespondHumanNodeCommand {
            run_id,
            node_id,
            caller_actor_id: human.actor_id,
            content: body.content,
            source: HumanResponseSource::Http,
        })
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", outcome, request_id.0)),
    )
        .into_response())
}

async fn cancel_state_machine_run(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<CancelStateMachineRunRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(run_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Json(body) = body.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let view = service(&state, &request_id)?
        .cancel_state_machine_run_with_access(
            StateMachineRunAccessCommand {
                run_id,
                authenticated_human: authenticated_human(&caller),
            },
            body.reason,
        )
        .await
        .map_err(|e| runtime_error(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", view, request_id.0)),
    )
        .into_response())
}
```

Update `protected_router()` to register both POSTs:

```rust
pub fn protected_router() -> Router<ApiState> {
    Router::new()
        .route("/state-machine-runs/{run_id}", get(get_run))
        .route("/state-machine-runs/{run_id}/graph", get(get_graph))
        .route("/state-machine-runs/{run_id}/nodes/{node_id}", get(get_node))
        .route(
            "/state-machine-runs/{run_id}/nodes/{node_id}/respond",
            post(respond_human_node),
        )
        .route(
            "/state-machine-runs/{run_id}/pending-human-nodes",
            get(list_pending_human_nodes),
        )
        .route(
            "/state-machine-runs/{run_id}/cancel",
            post(cancel_state_machine_run),
        )
}
```

Remove the `Task 3 adds ...` comment note at the bottom of the file (the POST routes now use `JsonRejection` and `Deserialize`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cargo test -p bcs-api-http --test collaboration_run_routes`
Expected: PASS (all eight tests).

Run: `cargo build -p bcs-api-http`
Expected: no warnings about unused imports.

- [ ] **Step 5: Commit**

```bash
git add crates/adapters/http/bcs-api-http/src/v1/internal/routes/collaboration_run.rs \
        crates/adapters/http/bcs-api-http/tests/collaboration_run_routes.rs
git commit -m "feat(bcs-api-http): migrate state-machine-runs respond & cancel writes"
```

---

## Task 4: `manifest.rs` — enveloped `GET /manifest` + raw-bytes `GET /assets/{bundle}/{file}`

Public boundary (no `verify_principal`).

**Files:**
- Modify: `crates/adapters/http/bcs-api-http/Cargo.toml` — add `bcs-config-api` dep.
- Create: `crates/adapters/http/bcs-api-http/src/v1/internal/routes/manifest.rs`
- Create: `crates/adapters/http/bcs-api-http/tests/manifest_routes.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/common/state.rs` — add `manifest` + `manifest_env` fields/builder.
- Modify: `crates/adapters/http/bcs-api-http/src/v1/internal/routes/mod.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs`
- Modify: `crates/bootstrap/bcs/src/server.rs` — pass manifest config into `ApiState`.

**Interfaces:**
- Consumes: `bcs_config_api::{ManifestBundleConfig, ManifestBundleSourceType, ManifestConfig}`.
- Produces: `pub fn public_router() -> Router<ApiState>` in `manifest.rs`; `ApiState::with_manifest_config(env, manifest)`.

- [ ] **Step 1: Add the `bcs-config-api` dependency**

Edit `crates/adapters/http/bcs-api-http/Cargo.toml`. In the `[dependencies]` section, add:

```toml
bcs-config-api = { workspace = true }
```

(Confirm the workspace exports it as `bcs-config-api` — check `src/bcs/Cargo.toml` for the workspace dep name and add a matching line; the legacy `bcs-http` crate already depends on `bcs-config-api` the same way, so mirror its declaration verbatim.)

- [ ] **Step 2: Add `manifest` + `manifest_env` to `ApiState`**

Edit `crates/adapters/http/bcs-api-http/src/v1/common/state.rs`. Add the import:

```rust
use bcs_config_api::ManifestConfig;
```

Add fields to the `ApiState` struct (after `collaboration_runtime_service`):

```rust
    pub manifest: ManifestConfig,
    pub manifest_env: String,
```

In `ApiState::new`, add to the `Self { ... }` initializer:

```rust
            collaboration_runtime_service: None,
            manifest: ManifestConfig::default(),
            manifest_env: "local".to_string(),
```

Add the builder (after `with_collaboration_runtime_service`):

```rust
    /// Provide the bundle manifest config served by the public v1
    /// `/api/v1/collaboration/manifest` route.
    pub fn with_manifest_config(mut self, env: String, manifest: ManifestConfig) -> Self {
        self.manifest_env = env;
        self.manifest = manifest;
        self
    }
```

- [ ] **Step 3: Write the failing tests**

Create `crates/adapters/http/bcs-api-http/tests/manifest_routes.rs`:

```rust
#![allow(clippy::expect_used, reason = "test assertions intentionally fail fast")]

use std::sync::Arc;

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_config_api::{ManifestBundleConfig, ManifestConfig};
use bcs_service_api::application::v1::*;
use serde_json::Value;
use tower::ServiceExt;

struct AcceptAllVerifier;

#[async_trait]
impl PrincipalVerifier for AcceptAllVerifier {
    async fn verify(
        &self,
        _headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        // Public routes do not pass the verify_principal boundary; this is
        // only here to satisfy ApiState::new.
        Err(PrincipalVerificationError::Missing)
    }
}

fn file_bundle_config() -> ManifestConfig {
    ManifestConfig {
        schema_version: 1,
        bundles: vec![ManifestBundleConfig {
            name: "bcsPanel".to_string(),
            source_type: None,
            url: None,
            file: Some("assets/panel/dist/index.umd.js".to_string()),
        }],
    }
}

fn test_router(manifest: ManifestConfig) -> axum::Router {
    router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopFriendshipService),
            Arc::new(AcceptAllVerifier),
        )
        .with_manifest_config("prod".to_string(), manifest),
    )
}

async fn body_bytes(response: axum::http::Response<Body>) -> Vec<u8> {
    to_bytes(response.into_body(), usize::MAX).await.expect("body").to_vec()
}

#[tokio::test]
async fn manifest_returns_enveloped_bundles_with_gateway_asset_url() {
    let app = test_router(file_bundle_config());
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/manifest")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let bytes = body_bytes(response).await;
    let body: Value = serde_json::from_slice(&bytes).expect("json");
    assert_eq!(body["code"], 20000);
    assert_eq!(body["data"]["schema_version"], 1);
    assert_eq!(body["data"]["env"], "prod");
    assert_eq!(body["data"]["bundles"][0]["name"], "bcsPanel");
    assert_eq!(body["data"]["bundles"][0]["url"], "/api/v1/collaboration/assets/bcsPanel/index.umd.js");
}
```

Copy the five noop services (`NoopGroupService`, `NoopSessionService`, `NoopMessageService`, `NoopInvitationService`, `NoopFriendshipService`) verbatim from `tests/collaboration_template_routes.rs`.

For the asset route test, create a temp dir with a fake `index.umd.js`, set the bundle `file` to that absolute path, then request `/api/v1/collaboration/assets/bcsPanel/index.umd.js` and assert the raw bytes + `content-type: application/javascript`. Use `tempfile::tempdir()` if the crate already depends on `tempfile` (check `bcs-api-http/Cargo.toml [dev-dependencies]`; if not present, use `std::env::temp_dir()` + a unique suffix and clean up with `std::fs::remove_dir_all` in a `Drop` guard):

```rust
#[tokio::test]
async fn assets_serves_raw_file_bytes_not_enveloped() {
    let dir = std::env::temp_dir().join(format!("bcs-api-http-manifest-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("mkdir");
    let file_path = dir.join("index.umd.js");
    std::fs::write(&file_path, b"console.log('panel');").expect("write");
    let manifest = ManifestConfig {
        schema_version: 1,
        bundles: vec![ManifestBundleConfig {
            name: "bcsPanel".to_string(),
            source_type: None,
            url: None,
            file: Some(file_path.to_string_lossy().to_string()),
        }],
    };
    let app = test_router(manifest);
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/assets/bcsPanel/index.umd.js")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers().get("content-type").unwrap(), "application/javascript; charset=utf-8");
    let bytes = body_bytes(response).await;
    assert_eq!(&bytes[..], b"console.log('panel');");
    let _ = std::fs::remove_dir_all(&dir);
}
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cargo test -p bcs-api-http --test manifest_routes`
Expected: compile failure — `manifest.rs` does not exist / routes not registered.

- [ ] **Step 5: Create `manifest.rs`**

Create `crates/adapters/http/bcs-api-http/src/v1/internal/routes/manifest.rs`:

```rust
use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use bcs_config_api::{ManifestBundleConfig, ManifestBundleSourceType};
use serde::Serialize;

use crate::v1::common::{ApiState, Envelope, RequestId};

const ASSETS_PREFIX: &str = "/api/v1/collaboration/assets";

pub fn public_router() -> Router<ApiState> {
    Router::new()
        .route("/manifest", get(get_manifest))
        .route(
            "/assets/{bundle_name}/{file_name}",
            get(manifest_asset),
        )
}

#[derive(Debug, Serialize)]
struct ManifestResponse {
    pub schema_version: u32,
    pub env: String,
    pub bundles: Vec<ManifestBundleResponse>,
}

#[derive(Debug, Serialize)]
struct ManifestBundleResponse {
    pub name: String,
    pub url: String,
}

async fn get_manifest(State(state): State<ApiState>, headers: HeaderMap) -> Response {
    let request_id = RequestId::from_headers(&headers);
    let bundles = state
        .manifest
        .bundles
        .iter()
        .filter_map(|bundle| {
            Some(ManifestBundleResponse {
                name: bundle.name.clone(),
                url: manifest_bundle_url(bundle)?,
            })
        })
        .collect();
    let data = ManifestResponse {
        schema_version: state.manifest.schema_version,
        env: state.manifest_env.clone(),
        bundles,
    };
    (
        StatusCode::OK,
        axum::Json(Envelope::success(20_000, "OK", data, request_id.0)),
    )
        .into_response()
}

async fn manifest_asset(
    State(state): State<ApiState>,
    Path((bundle_name, file_name)): Path<(String, String)>,
) -> Response {
    let Some(file_path) = state.manifest.bundles.iter().find_map(|bundle| {
        if bundle.name != bundle_name {
            return None;
        }
        let asset_file_name = local_asset_file_name(bundle)?;
        if asset_file_name == file_name {
            return bundle.file.clone();
        }
        None
    }) else {
        return (StatusCode::NOT_FOUND, "asset not found").into_response();
    };

    let Ok(bytes) = tokio::fs::read(&file_path).await else {
        return (StatusCode::NOT_FOUND, "asset not found").into_response();
    };

    let mut headers = HeaderMap::new();
    headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static(content_type_for(&file_path)),
    );
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    (headers, bytes).into_response()
}

fn manifest_bundle_url(bundle: &ManifestBundleConfig) -> Option<String> {
    if is_file_bundle(bundle) {
        let file_name = local_asset_file_name(bundle)?;
        return Some(format!(
            "{ASSETS_PREFIX}/{}/{}",
            urlencoding::encode(&bundle.name),
            urlencoding::encode(&file_name)
        ));
    }
    bundle.url.clone()
}

fn is_file_bundle(bundle: &ManifestBundleConfig) -> bool {
    match bundle.source_type {
        Some(ManifestBundleSourceType::File) => true,
        Some(ManifestBundleSourceType::Url) => false,
        None => bundle.file.as_deref().is_some() && bundle.url.as_deref().is_none(),
    }
}

fn local_asset_file_name(bundle: &ManifestBundleConfig) -> Option<String> {
    let file = bundle.file.as_deref()?;
    let file_name = std::path::Path::new(file).file_name()?.to_str()?;
    Some(file_name.to_string())
}

fn content_type_for(path: &str) -> &'static str {
    match std::path::Path::new(path).extension().and_then(|ext| ext.to_str()) {
        Some("css") => "text/css; charset=utf-8",
        Some("html") => "text/html; charset=utf-8",
        Some("js") | Some("mjs") => "application/javascript; charset=utf-8",
        Some("json") => "application/json; charset=utf-8",
        Some("map") => "application/json; charset=utf-8",
        Some("svg") => "image/svg+xml",
        _ => "application/octet-stream",
    }
}
```

`urlencoding` is already a transitive dependency used by `bcs-http/assets.rs`; if `bcs-api-http` does not directly depend on it, add `urlencoding = { workspace = true }` to `[dependencies]` (mirror the workspace declaration from `bcs-http/Cargo.toml`; if there is no workspace alias, add `urlencoding = "2"`).

- [ ] **Step 6: Register the module + merge into the public nest**

Edit `crates/adapters/http/bcs-api-http/src/v1/internal/routes/mod.rs` — add:

```rust
pub mod manifest;
```

Edit `crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs` — merge into `public_router()`:

```rust
pub fn public_router() -> Router<ApiState> {
    Router::new().nest(
        "/api/v1/collaboration",
        routes::session_file::public_router().merge(routes::manifest::public_router()),
    )
}
```

- [ ] **Step 7: Wire manifest config into `ApiState` in the bootstrap**

Edit `crates/bootstrap/bcs/src/server.rs`. Note `config` is in scope in the `ApiState`-building function (used at `config.eventing.enabled`, `config.invite...`). The env string must match what legacy `/manifest` returns: `crate::config_loader::Environment::resolve().as_str().to_string()`. Add `.with_manifest_config(...)` to the builder chain (after `with_collaboration_runtime_service`):

```rust
        .with_collaboration_runtime_service(collaboration_runtime.clone())
        .with_manifest_config(
            crate::config_loader::Environment::resolve().as_str().to_string(),
            config.manifest.clone(),
        ),
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cargo test -p bcs-api-http --test manifest_routes`
Expected: PASS (all manifest/assets tests).

Run: `cargo build -p bcs-api-http -p bcs`
Expected: builds cleanly.

- [ ] **Step 9: Commit**

```bash
git add crates/adapters/http/bcs-api-http/Cargo.toml \
        crates/adapters/http/bcs-api-http/src/v1/common/state.rs \
        crates/adapters/http/bcs-api-http/src/v1/internal/routes/mod.rs \
        crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs \
        crates/adapters/http/bcs-api-http/src/v1/internal/routes/manifest.rs \
        crates/adapters/http/bcs-api-http/tests/manifest_routes.rs \
        crates/bootstrap/bcs/src/server.rs
git commit -m "feat(bcs-api-http): migrate manifest & assets to v1 internal router"
```

---

## Task 5: Gateway `route_security` config

**Files:**
- Modify: `gateway/configs/application.yaml`

**Interfaces:** None (config-only).

- [ ] **Step 1: Add the `route_security` overrides**

Edit `gateway/configs/application.yaml`. Locate the `route_security:` block (around line 348, the `"/api/v1/collaboration/**"` rule) and add three more-specific rules immediately after the session-file rules (after the `"GET /api/v1/collaboration/sessions/shared-file/content": {}` entry, ~line 370). Insert:

```yaml
    # State-machine-run operations require a gateway-signed User Principal so
    # BCN's verify_principal boundary admits the request and the HTTP-layer
    # auth can map caller.user -> AuthenticatedHumanCaller. Legacy
    # direct-to-BCS routes still allow anonymous reads (dual-exposure).
    "/api/v1/collaboration/state-machine-runs/**":
      user: required

    # Public static resources served by the v1 internal public boundary.
    "GET /api/v1/collaboration/manifest": {}
    "/api/v1/collaboration/assets/**": {}
```

- [ ] **Step 2: Validate the config parses**

Run: `cd src/gateway && uv run python -c "import yaml; yaml.safe_load(open('configs/application.yaml'))" && echo OK`
Expected: prints `OK` (no YAML syntax errors).

- [ ] **Step 3: Commit**

```bash
git add gateway/configs/application.yaml
git commit -m "feat(gateway): route_security for state-machine-runs, manifest & assets"
```

---

## Task 6: API contract — `internal.yaml` + fragments + regenerate

**Files:**
- Create: `api-contracts/v1/openapi/state-machine-runs.yaml`
- Create: `api-contracts/v1/openapi/manifest.yaml`
- Modify: `api-contracts/v1/internal.yaml`

**Interfaces:** None (contract documentation).

- [ ] **Step 1: Study an existing fragment**

Read `api-contracts/v1/openapi/session-files.yaml` in full to mirror its `$ref` component style (named top-level path components like `SessionFilesPath`, envelope-wrapped response schemas). Read `api-contracts/v1/internal.yaml` to see how each `paths:` entry `$ref`s a component.

- [ ] **Step 2: Create `state-machine-runs.yaml`**

Create `api-contracts/v1/openapi/state-machine-runs.yaml` with six path-item components: `StateMachineRunPath`, `StateMachineRunGraphPath`, `StateMachineNodeRunPath`, `RespondHumanNodePath`, `CancelStateMachineRunPath`, `PendingHumanNodesPath`. Each `get`/`post` operation declares the enveloped `200` response (`{code, message, data, request_id}` schema — reuse the same envelope schema shape used in `session-files.yaml`) and the `404`/`401` envelope error responses. Define the runtime view schemas (`StateMachineRunView`, `StateMachineRunGraphView`, `StateMachineNodeRunView`, `PendingHumanNode`, `RespondHumanNodeOutcome`) as the `data` payload types, mirroring the Rust struct fields in `application/collaboration_runtime.rs`. Request bodies: `RespondHumanNodeRequest { content: string }`, `CancelStateMachineRunRequest { reason?: string }`.

- [ ] **Step 3: Create `manifest.yaml`**

Create `api-contracts/v1/openapi/manifest.yaml` with `ManifestPath` (enveloped `200`, `data` = `{ schema_version: integer, env: string, bundles: [{ name, url }] }`) and `ManifestAssetPath` (raw binary `200` with `content-type` variants, no envelope).

- [ ] **Step 4: Reference the new paths from `internal.yaml`**

Edit `api-contracts/v1/internal.yaml`. Under `paths:`, add (mirroring the existing session-file references):

```yaml
  /api/v1/collaboration/state-machine-runs/{run_id}:
    $ref: ./openapi/state-machine-runs.yaml#/StateMachineRunPath
  /api/v1/collaboration/state-machine-runs/{run_id}/graph:
    $ref: ./openapi/state-machine-runs.yaml#/StateMachineRunGraphPath
  /api/v1/collaboration/state-machine-runs/{run_id}/nodes/{node_id}:
    $ref: ./openapi/state-machine-runs.yaml#/StateMachineNodeRunPath
  /api/v1/collaboration/state-machine-runs/{run_id}/nodes/{node_id}/respond:
    $ref: ./openapi/state-machine-runs.yaml#/RespondHumanNodePath
  /api/v1/collaboration/state-machine-runs/{run_id}/pending-human-nodes:
    $ref: ./openapi/state-machine-runs.yaml#/PendingHumanNodesPath
  /api/v1/collaboration/state-machine-runs/{run_id}/cancel:
    $ref: ./openapi/state-machine-runs.yaml#/CancelStateMachineRunPath
  /api/v1/collaboration/manifest:
    $ref: ./openapi/manifest.yaml#/ManifestPath
  /api/v1/collaboration/assets/{bundle_name}/{file_name}:
    $ref: ./openapi/manifest.yaml#/ManifestAssetPath
```

- [ ] **Step 5: Regenerate & validate**

Run the existing contract tooling (check `api-contracts/README.md` and `src/bcs/scripts/` for exact invocations). Typical commands:

```bash
cd src/bcs
uv run python scripts/validate_openapi_contract.py
uv run python scripts/dump_openapi.py
uv run python scripts/bundle_openapi_contract.py
```

If the gateway publish step is part of the flow, also run `src/gateway/scripts/dump_and_publish.sh` so `gateway/configs/schemas/bcn.internal.openapi.json` reflects the new paths.

Expected: validators pass; the regenerated `bcn.internal.openapi.json` contains the eight new paths.

- [ ] **Step 6: Commit**

```bash
git add api-contracts/v1/internal.yaml \
        api-contracts/v1/openapi/state-machine-runs.yaml \
        api-contracts/v1/openapi/manifest.yaml \
        gateway/configs/schemas/bcn.internal.openapi.json
git commit -m "docs(api-contracts): document state-machine-runs, manifest & assets internal paths"
```

(Only `git add` the generated schema if the scripts write it to a tracked path and the repo commits generated schemas — check whether `bcn.internal.openapi.json` is tracked via `git ls-files` before staging; if it is gitignored, omit it.)

---

## Task 7: Frontend — envelope-tolerant `unwrapEnvelope` + rebuild dist

The panel source is git-tracked; `dist/index.umd.js` is gitignored (a `vite build` output regenerated locally).

**Files:**
- Modify: `src/bcs/assets/panel/src/StateMachineRunView.tsx`
- Regenerate (not committed): `src/bcs/assets/panel/dist/index.umd.js`

**Interfaces:** None.

- [ ] **Step 1: Write the failing test**

Create `src/bcs/assets/panel/test/unwrap-envelope.mjs`:

```js
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// Re-implement unwrapEnvelope here for the test (the production copy is
// inlined into the TSX). Keep them in sync.
function unwrapEnvelope(body) {
  if (
    body &&
    typeof body === 'object' &&
    !Array.isArray(body) &&
    ('code' in body || 'request_id' in body) &&
    'data' in body
  ) {
    return body.data;
  }
  return body;
}

const tests = [
  { name: 'raw object passthrough', input: { nodes: [], edges: [] }, expected: { nodes: [], edges: [] } },
  { name: 'raw array passthrough', input: [1, 2, 3], expected: [1, 2, 3] },
  { name: 'enveloped object unwrapped', input: { code: 20000, message: 'OK', data: { nodes: ['x'] }, request_id: 'r' }, expected: { nodes: ['x'] } },
  { name: 'object with data field but no envelope markers passes through', input: { data: { a: 1 } }, expected: { data: { a: 1 } } },
];

let failed = 0;
for (const t of tests) {
  const got = unwrapEnvelope(t.input);
  if (JSON.stringify(got) !== JSON.stringify(t.expected)) {
    failed++;
    console.error(`FAIL ${t.name}: got ${JSON.stringify(got)}`);
  }
}
if (failed) {
  process.exit(1);
}
console.log('unwrapEnvelope tests OK');
```

Run: `node src/bcs/assets/panel/test/unwrap-envelope.mjs`
Expected: PASS (prints `unwrapEnvelope tests OK`) — this validates the algorithm before wiring it into the TSX.

- [ ] **Step 2: Add `unwrapEnvelope` to the panel source and apply it at the three JSON parse sites**

Edit `src/bcs/assets/panel/src/StateMachineRunView.tsx`.

Add the helper near the other top-level helpers (after `const DEFAULT_POLLING_INTERVAL = 3000;`, ~line 184):

```ts
/**
 * Accept either the raw legacy BCS payload or the v1 gateway envelope
 * `{ code, message, data, request_id }` and return the payload. The
 * envelope-marker guard (`code`/`request_id`) prevents misinterpreting a raw
 * payload that happens to carry a `data` field.
 */
function unwrapEnvelope<T>(body: any): T {
  if (
    body &&
    typeof body === 'object' &&
    !Array.isArray(body) &&
    ('code' in body || 'request_id' in body) &&
    'data' in body
  ) {
    return body.data as T;
  }
  return body as T;
}
```

Apply at the graph parse site (~line 2709):

```ts
        const data = unwrapEnvelope<StateMachineRunGraph>(await response.json());
```

Apply at the pending-human-nodes parse site (~line 2627):

```ts
        const data = unwrapEnvelope<PendingHumanNode[]>(await response.json());
```

Apply at the node-detail parse site (~line 2807):

```ts
        const data = unwrapEnvelope<StateMachineNodeDetailResponse>(await response.json());
```

For the `respond` POST (~lines 2855–2875): the success body is unused, but the error path (`createRequestError` → `parseErrorBody`) reads `body.message` / `body.error`. If `parseErrorBody` is a local function (~lines 2017–2046), route its parsed JSON through `unwrapEnvelope` first so an enveloped error body (`{ code, message, data: { ... } }`) still surfaces `message`. Concretely, in `parseErrorBody`, change the line that does `const body = await response.json();` to:

```ts
        const raw = await response.json();
        const body = unwrapEnvelope<any>(raw);
```

If `parseErrorBody` cannot be safely modified because it lacks access to the typed envelope shape, instead leave `parseErrorBody` as-is and note that enveloped errors will fall back to the HTTP status text — acceptable since the panel surfaces a generic error either way. Prefer the in-place unwrap if the function is local.

- [ ] **Step 3: Typecheck the panel**

Run: `cd src/bcs/assets/panel && npm install && npm run typecheck`
Expected: `tsc --noEmit` passes.

- [ ] **Step 4: Rebuild the dist bundle**

Run: `cd src/bcs/assets/panel && npm run build`
Expected: `dist/index.umd.js` is regenerated (gitignored; not committed). The vite config writes it to `dist/index.umd.js`.

- [ ] **Step 5: Run the panel UMD contract test**

Run: `cd src/bcs/assets/panel && npm run test:umd`
Expected: PASS (the existing `test/umd-contract.mjs` still loads and exports the component).

- [ ] **Step 6: Commit (source only — dist is gitignored)**

```bash
git add src/bcs/assets/panel/src/StateMachineRunView.tsx \
        src/bcs/assets/panel/test/unwrap-envelope.mjs
git commit -m "feat(panel): tolerate both raw and v1 envelope response shapes"
```

Before committing, confirm `dist/` is actually gitignored (repo-root `.gitignore` line `dist/` excludes it). Run:

```bash
git check-ignore -v src/bcs/assets/panel/dist/index.umd.js
```

Expected: prints a `.gitignore` rule line (exit 0). If it prints nothing (exit 1 = not ignored), do **not** `git add` `dist/` — leave the build output local and stop to confirm the ignore rule with the user. `git status` after commit should show a clean tree (no untracked `dist/`).

---

## Final Verification

- [ ] **Step 1: Workspace build & targeted tests**

```bash
cd src/bcs
cargo build -p bcs-api-http -p bcs
cargo test -p bcs-api-http
```
Expected: no compile errors; all bcs-api-http tests pass (existing + the new `collaboration_run_routes` and `manifest_routes`).

- [ ] **Step 2: Legacy routes still mounted (dual-exposure sanity)**

Confirm `crates/adapters/http/bcs-http/src/router.rs` still contains the `/manifest`, `/assets/{...}`, and `/state-machine-runs/{...}` routes untouched (it should — no task modified it).

- [ ] **Step 3: Gateway config still valid**

```bash
cd src/gateway
uv run python -c "import yaml; yaml.safe_load(open('configs/application.yaml'))" && echo OK
```

- [ ] **Step 4: Contract validation**

```bash
cd src/bcs
uv run python scripts/validate_openapi_contract.py
```
Expected: passes.

- [ ] **Step 5: Branch hygiene**

Confirm all commits are on `feat/state-machine-runs-manifest-gateway-migration`, none on `dev`:

```bash
git log --oneline dev..HEAD
git status
```

Expected: clean working tree; the feature branch contains the seven task commits above `dev`.

---

## Out of Scope (per spec)

- `POST /groups/{id}/state-machine-runs`, `POST /sessions/{sid}/state-machine-runs`, `GET /sessions/{sid}/state-machine-permission` — stay on legacy.
- Removing any legacy `bcs-http` route.
- Building a new v1 application facade for collaboration runtime.
- New gateway upstream domains (only `route_security` additions).