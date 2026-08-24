# Migrate manifest, assets & state-machine-runs endpoints to the gateway (bcs-api-http v1)

Date: 2026-08-24
Status: Approved (pending spec review)

## Problem

Several BCS endpoints currently live only on the legacy `bcs-http` delivery
adapter and are not reachable through the gateway (`bcs-api-http` v1 internal
router under `/api/v1/collaboration/**`). They need to be exposed on the
gateway the same way `GET /sessions/{session_id}/files` was migrated, so that
gateway-authenticated callers (the frontend panel via cookie SSO, the
workbench host) can reach them through the standard signed-Principal boundary
and the v1 response envelope.

Endpoints to migrate:

- `GET /manifest`
- `GET /state-machine-runs/{run_id}`
- `GET /state-machine-runs/{run_id}/graph`
- `GET /state-machine-runs/{run_id}/nodes/{node_id}`
- `POST /state-machine-runs/{run_id}/nodes/{node_id}/respond`
- `POST /state-machine-runs/{run_id}/cancel`
- `GET /state-machine-runs/{run_id}/pending-human-nodes` (added because the
  panel depends on it and it shares the identical pattern)

The frontend panel (`src/bcs/assets/panel/src/StateMachineRunView.tsx`) reads
these endpoints' response fields directly. After migration the gateway
responses are wrapped in the v1 envelope `{code, message, data,
request_id}`, so the panel must tolerate **both** the raw legacy shape and the
enveloped gateway shape.

## Guiding rule

Follow the `session_file` reference migration exactly:

1. **Pure-additive** — the legacy `bcs-http` routes stay mounted. No route
   collisions because every new gateway path is prefixed with
   `/api/v1/collaboration` while the legacy paths are top-level.
2. **Reuse the service layer** — call the existing application services
   (`CollaborationRuntimeService`, `ManifestConfig`) directly; do not build a
   new v1 application facade.
3. **Auth in the HTTP layer** — map the gateway-signed `AuthenticatedCaller`
   to the legacy `AuthenticatedHumanCaller` inside the route handlers; do not
   rely on the `IdentityPolicy` / `select_principal` mechanism (the
   collaboration-runtime access model is "optional human", which no existing
   `IdentityPolicy` variant expresses).
4. **Envelope** — wrap JSON successes in `Envelope::success(20_000, "OK",
   data, request_id)`.
5. **Reuse the existing gateway upstream domain** — all new paths live under
   `/api/v1/collaboration/**`, which the existing `collaboration-internal`
   domain already forwards to the BCS server. No new upstream domain is
   required.

## Path mapping

| Endpoint | Legacy path (kept, raw) | New gateway path (enveloped unless noted) |
|---|---|---|
| `GET /manifest` | `/manifest` | `/api/v1/collaboration/manifest` |
| `GET /assets/{bundle_name}/{file_name}` | `/assets/{bundle_name}/{file_name}` | `/api/v1/collaboration/assets/{bundle_name}/{file_name}` (raw bytes, **not** enveloped) |
| `GET /state-machine-runs/{run_id}` | `/state-machine-runs/{run_id}` | `/api/v1/collaboration/state-machine-runs/{run_id}` |
| `GET /state-machine-runs/{run_id}/graph` | same | `/api/v1/collaboration/state-machine-runs/{run_id}/graph` |
| `GET /state-machine-runs/{run_id}/nodes/{node_id}` | same | `/api/v1/collaboration/state-machine-runs/{run_id}/nodes/{node_id}` |
| `POST .../nodes/{node_id}/respond` | same | `/api/v1/collaboration/state-machine-runs/{run_id}/nodes/{node_id}/respond` |
| `POST .../cancel` | same | `/api/v1/collaboration/state-machine-runs/{run_id}/cancel` |
| `GET .../pending-human-nodes` | same | `/api/v1/collaboration/state-machine-runs/{run_id}/pending-human-nodes` |

The new `manifest` response projects bundle URLs onto the new
`/api/v1/collaboration/assets/{name}/{file}` path so gateway consumers fetch
static assets through the gateway. The legacy `/manifest` keeps `/assets/...`
URLs.

## Components

### 1. `v1/internal/routes/collaboration_run.rs` (new)

`pub fn protected_router() -> Router<ApiState>` registering the six
state-machine-run routes under relative paths (the
`/api/v1/collaboration` prefix is supplied by the existing `nest` in
`v1/internal/mod.rs`):

- `GET /state-machine-runs/{run_id}` → `get_run`
- `GET /state-machine-runs/{run_id}/graph` → `get_graph`
- `GET /state-machine-runs/{run_id}/nodes/{node_id}` → `get_node`
- `POST /state-machine-runs/{run_id}/nodes/{node_id}/respond` → `respond`
  (JSON body `{ content: string }`)
- `POST /state-machine-runs/{run_id}/cancel` → `cancel`
  (JSON body `{ reason?: string }`)
- `GET /state-machine-runs/{run_id}/pending-human-nodes` → `list_pending`

Handler shape (mirrors `session_file.rs`): each takes `State(state)`,
`Extension(caller): AuthenticatedCaller`, `Extension(request_id):
RequestId`, `Path<...>` (and `Json<...>` for the two POSTs). No
`.identity_policy()` is attached; the handler performs auth:

- A helper `authenticated_human(caller: &AuthenticatedCaller) ->
  Option<AuthenticatedHumanCaller>` maps `caller.user` to
  `AuthenticatedHumanCaller { actor_id: format!("human_{}", user.id),
  display_name: user.display_name.clone() }`, matching the legacy
  `format!("human_{staff_no}")` convention.
- Reads (`get_run`, `get_graph`, `get_node`) pass
  `authenticated_human: Option<_>` into `StateMachineRunAccessCommand`,
  preserving the legacy optional-human semantics.
- `pending-human-nodes` and `respond` **require** a human (matching the
  legacy `authenticated_human`, which 401s when absent): if `caller.user` is
  `None`, return `application_error_response` with
  `ApplicationError::Unauthenticated` (401). `respond` uses
  `source: HumanResponseSource::Http`.
- `cancel` passes optional human (matches legacy `optional_authenticated_human`).

Each handler calls `state.collaboration_runtime_service` (reused legacy
`CollaborationRuntimeService`, fail-closed `internal` error if `None`) and
wraps the result:

- `Ok(Some(view))` → `Envelope::success(20_000, "OK", view, request_id.0)`
  (200 OK).
- `Ok(None)` → `ApplicationError::not_found(...)` →
  `application_error_response` (404), matching legacy.
- `Err(CollaborationRuntimeError)` → mapped to `ApplicationError` via a
  `collaboration_runtime_error_to_application_error` function preserving the
  legacy `collaboration_error_to_response` status intent, using the actual
  `ApplicationError` constructors:
  - `RunNotFound` / `NodeNotFound` / `DefinitionNotFound` →
    `ApplicationError::not_found("not_found", msg)` (404)
  - `InvalidDefinition` / `InvalidParticipantBinding` / `InvalidRequest` →
    `ApplicationError::invalid(<code>, msg)` (400)
  - `Unauthenticated` → `ApplicationError::Unauthenticated` (401)
  - `Forbidden(_)` → `ApplicationError::forbidden_code("forbidden", msg)`
    (403)
  - `JudgeUnavailable(_)` → `ApplicationError::bad_gateway("judge_unavailable", msg)`
    (502 — `ApplicationError` has no 503 variant; `BadGateway` is the closest
    available status)
  - `Conflict(_)` → `ApplicationError::conflict("conflict", msg)` (409)
  - `Internal(_)` → `ApplicationError::internal(msg)` (500)

### 2. `v1/internal/routes/manifest.rs` (new)

`pub fn public_router() -> Router<ApiState>` (public — **no**
`verify_principal`; static config resources carry no identity):

- `GET /manifest` → `get_manifest`, enveloped:
  `Envelope::success(20_000, "OK", { schema_version, env, bundles:
  [{ name, url }] }, request_id)`. `request_id` is built with
  `RequestId::from_headers` (the public boundary does not insert it via
  `verify_principal`). `url` is projected to
  `/api/v1/collaboration/assets/{encoded name}/{encoded file_name}` for
  file bundles, else the configured `url`.
- `GET /assets/{bundle_name}/{file_name}` → `manifest_asset`, serving raw
  file bytes with content-type sniffed by extension and `cache-control:
  no-cache`. **Not** enveloped (it is a JS/CSS/JSON file body, not an
  envelope). 404 when the bundle/file is not found.

Logic is moved from `bcs-http/src/routes/{manifest,assets}.rs` (the
`manifest_bundle_url` / `local_asset_url` / content-type helpers are reused
but project onto the new `/api/v1/collaboration/assets/...` prefix).

### 3. `v1/common/state.rs` (ApiState additions)

Add fields + builders, defaulting to `None`/empty in `ApiState::new`:

```rust
pub collaboration_runtime_service: Option<Arc<dyn CollaborationRuntimeService>>,
pub manifest: ManifestConfig,
pub manifest_env: String,
```

with `with_collaboration_runtime_service(Arc<dyn
CollaborationRuntimeService>)` and `with_manifest_config(env: String,
manifest: ManifestConfig)`.

Add `bcs-config-api = { workspace = true }` to bcs-api-http `Cargo.toml`
(for `ManifestConfig`).

### 4. `v1/internal/mod.rs` and `v1/internal/routes/mod.rs`

- Register `pub mod collaboration_run;` and `pub mod manifest;` in
  `routes/mod.rs`.
- Merge `routes::collaboration_run::protected_router()` into
  `protected_router()` (inside the `/api/v1/collaboration` nest; gains the
  `verify_principal` boundary).
- Merge `routes::manifest::public_router()` into `public_router()` (same
  nest; no `verify_principal`).

The `v1/mod.rs` `router()` composition and the server's
`.merge(bcs_api_http::router(...))` need **no** change.

### 5. Bootstrap composition root (`bootstrap/bcs/src/server.rs`)

When assembling `ApiState` (currently around the session_file wiring, ~line
1601):

- Reuse the existing `services.collaboration_runtime` `Arc` (the same
  instance the legacy `HttpAppState.services.collaboration_runtime` holds)
  and call `.with_collaboration_runtime_service(...)`.
- Pass the existing manifest config: `.with_manifest_config(config
  .manifest_env.clone(), config.manifest.clone())` (or from the
  `BcsServerState` fields that already feed the legacy `with_manifest_config`).

The legacy `HttpAppState` wiring is unchanged (it still serves `/manifest`,
`/assets/...`, and `/state-machine-runs/...` directly on the BCS port).

### 6. Gateway config (`gateway/configs/application.yaml`)

No new upstream domain (all new paths are under the existing
`collaboration-internal` domain, `match: /api/v1/collaboration/**`). Add
`route_security` overrides:

- `"/api/v1/collaboration/state-machine-runs/**": user: required` — the
  `verify_principal` boundary rejects a missing Principal; requiring a user
  guarantees the gateway signs a user Principal so `caller.user` is populated
  for the panel's cookie SSO. (Legacy direct-to-BCS routes still allow
  anonymous reads — dual-exposure.)
- `"GET /api/v1/collaboration/manifest": {}` and
  `"/api/v1/collaboration/assets/**": {}` — public; forward without
  identity (same pattern as the shared-file content route).

These more-specific rules outrank the broad
`"/api/v1/collaboration/**": user/app/bot: optional` default.

### 7. API contract (`src/bcs/api-contracts/v1/internal.yaml` and fragments)

The internal contract is the source of truth for the versioned internal API.
Add path items for the new operations:

- Add `api-contracts/v1/openapi/state-machine-runs.yaml` defining the six
  path components (`StateMachineRunPath`, `StateMachineRunGraphPath`,
  `StateMachineNodeRunPath`, `RespondHumanNodePath`,
  `CancelStateMachineRunPath`, `PendingHumanNodesPath`) plus the request/
  response schemas (envelope wrapped, reusing the runtime view shapes).
- Add `api-contracts/v1/openapi/manifest.yaml` defining `ManifestPath`
  (enveloped) and `ManifestAssetPath` (raw bytes).
- Add the corresponding `$ref` entries to `api-contracts/v1/internal.yaml`
  under `paths:`, mirroring the existing session-files references.
- Regenerate/validate via the existing scripts:
  `src/bcs/scripts/dump_openapi.py`,
  `src/bcs/scripts/bundle_openapi_contract.py`,
  `src/bcs/scripts/validate_openapi_contract.py`, and the gateway
  `src/gateway/scripts/dump_and_publish.sh` so the published
  `configs/schemas/bcn.internal.openapi.json` reflects the new paths.

### 8. Frontend (`src/bcs/assets/panel/src/StateMachineRunView.tsx`)

Add a normalizer and apply it at every JSON parse site so the panel tolerates
both the raw legacy payload and the enveloped gateway payload:

```ts
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

The envelope-marker guard (`code`/`request_id` presence) prevents
misinterpreting a raw payload that happens to have a `data` field. Apply at:

- graph: `const data = unwrapEnvelope<StateMachineRunGraph>(await response.json());`
- pending-human-nodes: `const data = unwrapEnvelope<PendingHumanNode[]>(await response.json());`
- node detail: `const data = unwrapEnvelope<StateMachineNodeDetailResponse>(await response.json());`
- respond: success body is unused. Do **not** route the error parser
  (`createRequestError` → `parseErrorBody`) through `unwrapEnvelope`: the v1
  error envelope is `{ code, message, data: { error_code }, request_id }`,
  where `message` lives at the top level, so the existing `parseErrorBody`
  (which reads `body.message` / `body.error`) already surfaces it correctly.
  Unwrapping to `body.data` would actually hide the top-level `message`.

`unwrapEnvelope` is for **success** bodies only (where the payload is in
`data`). Then rebuild the panel `dist/index.umd.js` via the panel build (the
file is gitignored — see the plan's verification step).

## Behavior changes

1. Gateway-exposed state-machine-runs require a User Principal (the
   `verify_principal` boundary + `user: required` route_security). Legacy
   direct-to-BCS routes keep anonymous-read support (dual-exposure preserved).
2. The new `/api/v1/collaboration/manifest` is enveloped and its bundle URLs
   point to `/api/v1/collaboration/assets/{name}/{file}`. Legacy `/manifest`
   stays raw with `/assets/{name}/{file}` URLs.
3. `pending-human-nodes` is migrated in addition to the originally listed six
   endpoints (the panel depends on it).
4. `/assets/{bundle}/{file}` served from bcs-api-http is **not** enveloped
   (raw file bytes); only `/manifest` is enveloped.

## Tests

- New bcs-api-http route tests (mirror `tests/session_file_*`): envelope
  shape for each state-machine-run route, optional-human for reads,
  required-human for `respond`, `None`-→-404 mapping, and the
  `CollaborationRuntimeError` → envelope status/code mapping.
- New bcs-api-http manifest/assets contract tests at the prefixed paths
  (enveloped manifest, raw asset bytes, bundle URL projection to
  `/api/v1/collaboration/assets/...`). The legacy bcs-http manifest contract
  tests at `/manifest` remain green.
- A small `unwrapEnvelope` unit test in the panel test harness
  (`panel/test/umd-contract.mjs` or equivalent) covering raw, enveloped,
  array, and envelope-marker-absent cases.
- Run the OpenAPI contract validation scripts after editing
  `internal.yaml` + fragments.

## Out of scope

- `POST /groups/{id}/state-machine-runs`, `POST /sessions/{sid}/state-machine-runs`,
  `GET /sessions/{sid}/state-machine-permission` — not in the requested set;
  stay on the legacy adapter.
- Removing any legacy `bcs-http` route (dual-exposure is required).
- Building a new v1 application facade for collaboration runtime (service
  layer is reused).
- Changing the gateway's upstream domain topology (only `route_security`
  additions).