# V1 Internal API: Validate Collaboration Definition

**Date:** 2026-08-22
**Scope:** `src/bcs` (BCS Rust)
**Status:** Design — pending implementation plan

## Problem

BCS exposes YAML validation for collaboration-definition authoring only through
the legacy route `POST /collaboration/definitions/validate`. The versioned V1
internal contract (`api-contracts/v1/internal.yaml`) has no projection for this
operation, so it cannot be published under the BCN internal ownership prefix or
consumed through the V1 `bcs-api-http` adapter like the other migrated legacy
domains (collaboration templates, candidate search, session files).

The legacy route is unchanged and continues to serve existing clients; the V1
adapter is additive.

## Goal

Add a V1 internal API `POST /api/v1/collaboration/definitions/validate` whose
function and logic are identical to the legacy operation, while request and
response follow the V1 contract norm (standard envelope, identity policy,
error vocabulary). The legacy route is not modified.

## Non-Goals

- No new validation logic. The V1 facade delegates to the existing legacy
  `CollaborationRuntimeService::validate_definition_yaml`.
- No migration of other `CollaborationRuntimeService` methods to V1 in this
  change.
- No removal or alias deprecation of the legacy route or its `yaml` body alias.
- No change to `bcs-cli` (it keeps calling the legacy route).

## Reference Precedent

This change mirrors the collaboration-template V1 migration (commit `d7b6dc653`,
PR #1350). Its full layer-by-layer chain is the template to follow:

| Layer | Template precedent | This change |
| --- | --- | --- |
| Contract | `api-contracts/v1/openapi/collaboration-templates.yaml` | `api-contracts/v1/openapi/collaboration-definitions.yaml` |
| Contract entry | `api-contracts/v1/internal.yaml` | same file, new path registered |
| V1 facade trait | `application/v1/collaboration_template.rs` | `application/v1/collaboration_definition.rs` |
| Facade impl crate | `crates/application/v1/bcs-app-collaboration-template` | `crates/application/v1/bcs-app-collaboration-definition` |
| HTTP route | `v1/internal/routes/collaboration_template.rs` | `v1/internal/routes/collaboration_definition.rs` |
| OpenAPI DTO | `v1/openapi/dto/collaboration_template.rs` | `v1/openapi/dto/collaboration_definition.rs` |
| `ApiState` mount | `with_collaboration_template_service` | `with_collaboration_definition_service` |

## Approach Decisions

### HTTP method: POST (confirmed)

The legacy route is `POST /collaboration/definitions/validate`
(`crates/adapters/http/bcs-http/src/router.rs:250-253`; `src/bcs/CLAUDE.md` API
table also lists it as POST). It accepts a JSON body
`{ "definition_yaml": "..." }`. The V1 projection reuses POST; GET with a YAML
body would be non-standard and would diverge from legacy logic.

### Response shape: V1 standard envelope wrapping the outcome (confirmed)

The legacy handler returns the bare `CollaborationDefinitionValidationOutcome`
as `Json(outcome)`. The V1 norm requires the standard envelope
`{ code, message, data, request_id }`, so the V1 route returns
`Envelope::success(20_000, "OK", outcome, request_id)`. The `outcome` body itself
is byte-compatible with the legacy projection (reused verbatim from
`bcs_service_api::application::collaboration_runtime`).

### Request field: single `definition_yaml`, no legacy `yaml` alias (confirmed)

The legacy handler accepts `definition_yaml` with a serde alias `yaml` — and the
alias is actively used: the backend adapter
`src/backend/src/agentclaw/community/core/task/task_runner/integration/bcs_http_adapter.py:175`
posts `{"yaml": ...}` today, while `bcs-cli` posts the canonical
`{"definition_yaml": ...}`. The V1 contract does **not** retain the alias:
callers migrating to V1 must adopt the canonical field name `definition_yaml`.
This is an explicit migration contract — legacy callers (e.g. the backend
adapter if/when it moves to V1) change their body key on migration; callers that
stay on the legacy route keep the `yaml` alias there. The V1 OpenAPI schema
declares exactly one body field, `definition_yaml`, required, `minLength: 1`,
with `additionalProperties: false`, and the V1 serde type carries no alias.
Calling V1 with `{"yaml": ...}` returns `400 invalid_request` by design.

### Facade crate organization: independent crate, capability-scoped (confirmed)

Existing V1 facade crates are one-per-legacy-service/capability-domain
(`bcs-app-bot`, `bcs-app-group`, `bcs-app-session`, `bcs-app-invitation`,
`bcs-app-collaboration-template`). `definition validate` does **not** delegate to
the same legacy service as templates:

- template facade -> legacy `CollaborationTemplateService` (read-only catalog)
- definition facade -> legacy `CollaborationRuntimeService`
  (compile/validate + `judge_available` runtime config)

Different `Arc<dyn ...>` injection points and constructor parameters, so merging
into `bcs-app-collaboration-template` would mislabel the crate (a template
service crate carrying a runtime service dependency). An independent crate is
required.

Because `validate` is one method of
`CollaborationRuntimeService` rather than a dedicated legacy service, the crate
is named by capability domain to keep the contract-file == crate == V1-trait
symmetry that templates have, and to avoid colliding with the legacy
`CollaborationRuntimeService` trait name:

- contract file `collaboration-definitions.yaml`
- crate `bcs-app-collaboration-definition`
- V1 trait `CollaborationDefinitionService`

Rejected alternative: `bcs-app-collaboration-runtime` + V1 trait
`CollaborationRuntimeService` — collides with the existing legacy trait of the
same name and overstates scope (only `validate` is V1-projected here; YAGNI).

## Legacy Behavior To Preserve

`CollaborationRuntime::validate_definition_yaml`
(`crates/services/bcs-collaboration-runtime/src/runtime.rs:2353-2358`) is
`Ok(validate_authoring_definition_yaml(cmd))` — a synchronous pure validation
that **never returns `Err`**. Validation failures are expressed in the outcome
as `valid: false` plus `errors[]` diagnostics, **not** as HTTP errors. The V1
route must preserve this: an invalid YAML still returns `200` with the envelope
containing `valid: false`. HTTP error responses are reserved for failed request
binding, identity rejection, and service wiring failure, not for invalid YAML.

## Design

### 1. Contract (contract-first)

New file `src/bcs/api-contracts/v1/openapi/collaboration-definitions.yaml`:

- Operation `POST /api/v1/collaboration/definitions/validate`, root node key
  `CollaborationDefinitionValidatePath`, `operationId: validate_definition`
  (the operationId omits the `collaboration` token because
  `scripts/validate_openapi_contract.py` flags `collaboration`/`bcn`/`openapi`
  as routing/version-only naming and would reject it; the path
  `/api/v1/collaboration/definitions/validate` supplies the domain context,
  mirroring the template precedent `list_templates`/`get_template`).
- Tags `["Collaboration / Definitions"]`.
- Request body (required, `application/json`):
  `CollaborationDefinitionValidateRequest { definition_yaml: string, required, minLength: 1 }`.
  `additionalProperties: false`.
- Success `200`: `CollaborationDefinitionValidationEnvelope` — the standard
  envelope (`code: integer`, `message: string`, `data`, `request_id` ref
  `../shared.yaml#/RequestId`) where `data` is `CollaborationDefinitionValidationOutcome`.
- `CollaborationDefinitionValidationOutcome` schema mirrors the legacy serde
  projection exactly:
  - `valid: boolean` (required)
  - `errors: array<CollaborationDefinitionValidationDiagnostic>` (optional,
    omitted when empty, matching `skip_serializing_if = "Vec::is_empty"`)
  - `warnings: array<diagnostic>` (optional, same)
  - `summary: CollaborationDefinitionValidationSummary` (required):
    `participants: int`, `nodes: int`, `initial_nodes: array<string>`,
    `final_output_node: string?`
  - `participants: array<CollaborationDefinitionParticipantSlot>` (optional):
    `binding`, `display_name?`, `description?`, `required`, `assigned`
  - `graph: CollaborationDefinitionGraphPreview?` (optional):
    `graph_mode`, `nodes[]`, `edges[]`
  - **Not declared:** `definition` — legacy marks it `#[serde(skip_serializing)]`
    so it never appears on the wire.
- Error responses, with `x-error-codes`:
  - `400` `BadRequestResponse`: `invalid_request` (body binding failure or empty
    `definition_yaml`)
  - `401` `UnauthenticatedResponse`: `unauthenticated` (Gateway Principal
    missing/invalid)
  - `500` `../shared.yaml#/InternalErrorResponse`: `internal_error`
  - **Not declared:** `judge_unavailable`, `invalid_definition`, etc. — `validate`
    surfaces invalid YAML inside the outcome, not as an HTTP error.
- Security metadata:
  - `x-avernet-security: { user: optional, app: optional, bot: optional }`
    (read-only validation, no bot/session identity policy; same trust level as
    template catalog reads)
  - `x-avernet-behavior.legacy_equivalent: /collaboration/definitions/validate`

Register in `src/bcs/api-contracts/v1/internal.yaml`:

```yaml
  /api/v1/collaboration/definitions/validate:
    $ref: ./openapi/collaboration-definitions.yaml#/CollaborationDefinitionValidatePath
```

### 2. Service API facade

New file `src/bcs/crates/service-api/bcs-service-api/src/application/v1/collaboration_definition.rs`:

```rust
//! Transport-neutral OpenAPI V1 collaboration-definition application contract.
//!
//! Definition validation is a projection of the legacy
//! `CollaborationRuntimeService::validate_definition_yaml` use case. The V1
//! facade reuses the legacy `CollaborationDefinitionValidationOutcome` response
//! type verbatim (byte-compatible projection) and only translates the legacy
//! runtime error vocabulary into the V1 `ApplicationError`. The facade is not
//! caller-scoped: the delivery adapter authenticates the Gateway Principal on
//! the protected boundary, but no per-caller authorization is applied to the
//! validation result. `judge_available` is a server-side runtime configuration,
//! not a request field; it is injected when the facade implementation is built.

use async_trait::async_trait;
use super::ApplicationError;
pub use crate::application::collaboration_runtime::CollaborationDefinitionValidationOutcome;

#[derive(Debug, Clone)]
pub struct ValidateCollaborationDefinition {
    pub definition_yaml: String,
}

#[async_trait]
pub trait CollaborationDefinitionService: Send + Sync {
    async fn validate_definition_yaml(
        &self,
        command: ValidateCollaborationDefinition,
    ) -> Result<CollaborationDefinitionValidationOutcome, ApplicationError>;
}
```

- Re-export `CollaborationDefinitionService` and `ValidateCollaborationDefinition`
  from `application/v1/mod.rs`.
- `CollaborationDefinitionValidationOutcome` is already exported from
  `bcs_service_api` root (`lib.rs:81`); no new re-export needed there.

### 3. Facade implementation crate

New crate `src/bcs/crates/application/v1/bcs-app-collaboration-definition/`,
mirroring `bcs-app-collaboration-template`:

- `Cargo.toml`: deps `async-trait` and `bcs-service-api` (workspace), dev-dep
  `tokio`.
- `src/lib.rs`:

```rust
//! Versioned collaboration-definition application facade for the BCN V1 API.
//!
//! The V1 facade delegates validation to the legacy
//! `CollaborationRuntimeService::validate_definition_yaml` and translates the
//! legacy runtime error vocabulary into the V1 `ApplicationError`. It performs
//! no per-caller authorization. `judge_available` is fixed at construction from
//! the same configuration source as the legacy `state.judge_enabled`.

use std::sync::Arc;
use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, CollaborationDefinitionService as V1CollaborationDefinitionService,
    ValidateCollaborationDefinition,
};
use bcs_service_api::application::collaboration_runtime::{
    CollaborationRuntimeError, CollaborationRuntimeService as LegacyCollaborationRuntimeService,
    ValidateCollaborationDefinitionYamlCommand,
};
use bcs_service_api::CollaborationDefinitionValidationOutcome;

pub struct CollaborationDefinitionServiceImpl {
    legacy: Arc<dyn LegacyCollaborationRuntimeService>,
    judge_available: bool,
}

impl CollaborationDefinitionServiceImpl {
    pub fn new(legacy: Arc<dyn LegacyCollaborationRuntimeService>, judge_available: bool) -> Self {
        Self { legacy, judge_available }
    }
}

#[async_trait]
impl V1CollaborationDefinitionService for CollaborationDefinitionServiceImpl {
    async fn validate_definition_yaml(
        &self,
        command: ValidateCollaborationDefinition,
    ) -> Result<CollaborationDefinitionValidationOutcome, ApplicationError> {
        self.legacy
            .validate_definition_yaml(ValidateCollaborationDefinitionYamlCommand {
                definition_yaml: command.definition_yaml,
                judge_available: self.judge_available,
            })
            .await
            .map_err(map_runtime_error)
    }
}

fn map_runtime_error(error: CollaborationRuntimeError) -> ApplicationError {
    match error {
        CollaborationRuntimeError::InvalidDefinition(message) => {
            ApplicationError::invalid("invalid_definition", message)
        }
        CollaborationRuntimeError::InvalidRequest(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        CollaborationRuntimeError::InvalidParticipantBinding(message) => {
            ApplicationError::invalid("invalid_participant_binding", message)
        }
        CollaborationRuntimeError::RunNotFound(id) => {
            ApplicationError::not_found("not_found", format!("state machine run not found: {id}"))
        }
        CollaborationRuntimeError::NodeNotFound { run_id, node_id } => ApplicationError::not_found(
            "not_found",
            format!("state machine node not found: {run_id}/{node_id}"),
        ),
        CollaborationRuntimeError::DefinitionNotFound(id, version) => ApplicationError::not_found(
            "not_found",
            format!("collaboration definition not found: {id}@{version}"),
        ),
        CollaborationRuntimeError::Unauthenticated => ApplicationError::Unauthenticated,
        CollaborationRuntimeError::Forbidden(message) => {
            ApplicationError::forbidden_code("forbidden", message)
        }
        CollaborationRuntimeError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        // validate_definition_yaml never returns Err in the production
        // implementation; these branches preserve trait completeness.
        CollaborationRuntimeError::JudgeUnavailable(message) => {
            ApplicationError::internal(format!("judge unavailable: {message}"))
        }
        CollaborationRuntimeError::Internal(detail) => ApplicationError::internal(detail.to_string()),
    }
}
```

> The `ApplicationError::not_found`, `.invalid`, `.forbidden_code`, `.conflict`,
> `.Internal` factory method names follow the precedent usage in
> `bcs-app-collaboration-template`'s `map_template_error`. If
> `forbidden_code` / `conflict` factory names differ in the actual
> `ApplicationError` definition, align to the exact existing constructors during
> implementation (see Validation below).

- Register the crate in the workspace `Cargo.toml`: add to `members` and add a
  `bcs-app-collaboration-definition = { path = ... }` dependency line beside
  `bcs-app-collaboration-template`.
- Unit tests (same pattern as template's `StubLegacyService`): stub legacy
  `CollaborationRuntimeService` that replays a canned error; assert
  `validate_definition_yaml` maps each error variant to the expected
  `ApplicationError` code and forwards `definition_yaml`/`judge_available`
  into the legacy command.

  Coverage: `InternalDefinition` -> `invalid_definition`; `InvalidRequest` ->
  `invalid_request`; `Internal` -> `internal_error`. These exercise the realistic
  mapping surface for `validate`; the remaining variants share the mapping
  table and are checked once for completeness.

### 4. HTTP adapter (`bcs-api-http`)

New file `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/collaboration_definition.rs`:

```rust
use serde::Deserialize;

#[derive(Debug, Deserialize)]
pub struct ValidateCollaborationDefinitionRequest {
    pub definition_yaml: String,
}
```

- Register module in `v1/openapi/dto/mod.rs`.

New file `src/bcs/crates/adapters/http/bcs-api-http/src/v1/internal/routes/collaboration_definition.rs`:

```rust
use axum::Router;
use axum::extract::{Extension, State};
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use axum::{Json, StatusCode};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, CollaborationDefinitionService,
    ValidateCollaborationDefinition,
};
use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::collaboration_definition::ValidateCollaborationDefinitionRequest;
use std::sync::Arc;

pub fn router() -> Router<ApiState> {
    Router::new().route("/definitions/validate", post(validate))
}

fn service(state: &ApiState, request_id: &RequestId) -> Result<Arc<dyn CollaborationDefinitionService>, ErrorResponse> {
    state.collaboration_definition_service.clone().ok_or_else(|| {
        application_error_response(request_id, ApplicationError::internal("V1 Collaboration Definition service is not configured"))
    })
}

pub async fn validate(
    State(state): State<ApiState>,
    Extension(_caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    body: Result<Json<ValidateCollaborationDefinitionRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let definition_yaml = body.definition_yaml;
    if definition_yaml.trim().is_empty() {
        return Err(invalid_request(&request_id, "definition_yaml must not be empty"));
    }
    let outcome = service(&state, &request_id)?
        .validate_definition_yaml(ValidateCollaborationDefinition { definition_yaml })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((StatusCode::OK, Json(Envelope::success(20_000, "OK", outcome, request_id.0))).into_response())
}
```

> Exact import names (e.g. whether `ErrorResponse` is exported from
  `v1::common`, whether body rejection uses `JsonRejection`/`body_text`) must be
  aligned to the actual `v1/internal/routes/collaboration_template.rs` pattern
  during implementation — the template route is the authoritative reference for
  these adapter idioms.

- Register module in `v1/internal/routes/mod.rs`.
- Merge into the protected boundary in `v1/internal/mod.rs`:

  ```rust
  // protected_router()
  routes::collaboration_template::router()
      .merge(routes::collaboration_definition::router())   // new
      .merge(routes::session_file::protected_router())
  ```

  The `/definitions/validate` route sits under the existing `nest("/api/v1/collaboration")`,
  so the full path is `/api/v1/collaboration/definitions/validate`. Static segment
  `/definitions/validate` does not collide with any `{template_id}` param segment
  on the templates router (different sibling path).

### 5. ApiState mount

In `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/state.rs`:

- Add field
  `pub collaboration_definition_service: Option<Arc<dyn CollaborationDefinitionService>>`
  to `ApiState`, initialize to `None` in `ApiState::new`.
- Add builder:
  ```rust
  pub fn with_collaboration_definition_service(
      mut self,
      service: Arc<dyn CollaborationDefinitionService>,
  ) -> Self {
      self.collaboration_definition_service = Some(service);
      self
  }
  ```
  Fail-closed semantics identical to `with_collaboration_template_service`
  (handler returns `internal` if unmounted).

### 6. Bootstrap composition

In `src/bcs/crates/bootstrap/bcs/src/server.rs` (or wherever the V1 `ApiState`
assembled, mirroring where
`with_collaboration_template_service` is wired today):

- Construct `CollaborationDefinitionServiceImpl::new(legacy_runtime.clone(), judge_available)`,
  where `legacy_runtime: Arc<dyn CollaborationRuntimeService>` is the same
  runtime instance backing the legacy `ServicesBuilder`, and `judge_available`
  comes from the same configuration source as the legacy
  `state.judge_enabled` (`HttpAppState::judge_enabled`).
- `.with_collaboration_definition_service(Arc::new(...))` when building the V1
  `ApiState`.

The legacy `POST /collaboration/definitions/validate` route and its
`ServicesBuilder` wiring are unchanged.

### 7. Tests (contract + runtime)

- **Facade unit tests** in `bcs-app-collaboration-definition` (stub legacy service
  replaying canned `CollaborationRuntimeError`s): error mapping + command
  forwarding.
- **Route tests** in `src/bcs/crates/adapters/http/bcs-api-http/tests/collaboration_definition_routes.rs`,
  modeled on `collaboration_template_routes.rs`:
  - valid YAML -> `200`, envelope, `data.valid == true`;
  - invalid YAML -> `200`, envelope, `data.valid == false`, non-empty
    `data.errors` (proves the V1 route keeps validation failures as success
    responses, not HTTP errors);
  - empty body / missing `definition_yaml` -> `400 invalid_request`;
  - service-not-configured -> `500 internal_error` (fail-closed).
- **OpenAPI contract validation**:
  ```
  uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
    --root src/bcs/api-contracts/v1 --entrypoint internal.yaml \
    --path-prefix /api/v1/collaboration/
  ```
  expects N+1 operations (inventory test updated from the current 12 to 13).
- **Rust gates**:
  ```
  cargo check -p bcs-service-api -p bcs-app-collaboration-definition -p bcs-api-http -p bcs --tests
  cargo test  -p bcs-app-collaboration-definition
  cargo test  -p bcs-api-http
  ```

## Validation Gaps To Resolve During Implementation

(Enumerated here so the spec has no silent assumptions.)

1. **`ApplicationError` factory method names.** The `map_runtime_error` table
   assumes `ApplicationError::invalid(code, message)`,
   `ApplicationError::not_found(code, message)`,
   `ApplicationError::forbidden_code(code, message)`,
   `ApplicationError::conflict(code, message)`, and
   `ApplicationError::internal(detail)` exist with those signatures. The template
   facade uses `invalid`/`not_found`/`internal`; confirm `forbidden_code` /
   `conflict` exist or substitute the real constructors. Since
   `validate_definition_yaml` never returns `Err` in production, any
   inconsistency here is contract-completeness, not a runtime path.
2. **Route-handler adapter idioms.** Body rejection extraction, `ErrorResponse`
   import path, and the exact `Query`/`Path` rejection helpers must match the
   authoritative `v1/internal/routes/collaboration_template.rs`. The code blocks
   above are structural, not copy-paste-verbatim.
3. **`judge_available` source.** Confirm the bootstrap reads
   `judge_enabled` from the same config path the legacy `HttpAppState` uses, so
   the V1 and legacy validation diverge only on transport, never on judge
   availability.

## Invariants & Compatibility

- Legacy route `/collaboration/definitions/validate` and its `yaml` body alias
  are untouched; `bcs-cli` keeps using it.
- Outcome semantics unchanged: invalid YAML returns `200` + `valid: false`
  (not an HTTP error).
- V1 success wraps the outcome in the standard envelope; the outcome body is
  byte-compatible with the legacy projection.
- No hardcoded external endpoints, tokens, or private paths introduced (AGENTS.md
  red-flag rule).
- Rust workspace; AGENTS.md Python `T | None` rule does not apply.
- No `cargo fmt`; edits are scoped to changed lines (BCS coding guideline).

## Open Questions

None blocking. The three validation gaps above are implementation-time
alignment tasks, not design decisions.
