# BCN-to-Gateway P0 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish and serve the 34-operation BCN V1 HTTP/WebSocket contract through Gateway and mount both authenticated connection routes in production.

**Architecture:** Keep the BCS YAML contract authoritative, export deterministic self-contained JSON, and reuse Gateway's existing compatibility gate and configuration-driven HTTP/WebSocket forwarders. Compose concrete V1 Application facades and PR #697's session-token service only in BCS bootstrap, inject the approved verifiers, and mount both routes without rewriting. Reuse the existing Workbench handler; test the new WebSocket only through Upgrade, identity binding, and connect-time authorization.

**Tech Stack:** Rust/Axum, Python 3.12, FastAPI/HTTPX, OpenAPI 3.1, PyYAML, pytest, Cargo.

## Global Constraints

- Public paths are exactly `/openapi/v1/collaboration/**` on Gateway and BCS.
- Do not add Gateway path rewriting or handwritten per-operation proxy routes.
- Include PR #697's session-token POST and session-bound WebSocket Upgrade GET.
- Keep the YAML contract authoritative; do not derive schemas from Axum runtime code.
- Do not run global `cargo fmt`; limit formatting to touched lines.
- Pre/gray/prod must fail startup without real Gateway Principal key material.
- Deferred conformance and route-inventory work remains in inclusionAI/Avernet#700.

Tasks 1-5 record the completed 32-operation HTTP baseline. Tasks 6-11 extend
that baseline after PR #697 merged into `dev`.

---

### Task 1: Deterministic BCN OpenAPI JSON exporter

**Files:**
- Create: `src/bcs/scripts/dump_openapi.py`
- Create: `src/bcs/tests/openapi/test_dump_openapi.py`
- Modify: `src/bcs/api-contracts/README.md`

**Interfaces:**
- Consumes: `validate_openapi_contract.load_contract`, `validate_contract`, and discriminator rewriting from `bundle_openapi_contract`.
- Produces: `dump_contract(root: Path, output: Path) -> Path` and CLI `dump_openapi.py OUTPUT [--root ROOT]`.

- [ ] **Step 1: Write failing exporter tests**

  Test two output files for byte equality; parse the JSON and assert OpenAPI
  3.1, exactly 32 operations, every operation path starts with
  `/openapi/v1/collaboration/`, and no external/file `$ref` remains.

- [ ] **Step 2: Verify RED**

  Run `python3 -m unittest discover -s src/bcs/tests/openapi -p 'test_*.py' -v`.
  Expected: import/file failure because `dump_openapi.py` does not exist.

- [ ] **Step 3: Implement the minimal exporter**

  Reuse the existing resolver and discriminator mapping logic, validate the
  collaboration prefix, then write `json.dumps(..., ensure_ascii=False,
  sort_keys=True, separators=(",", ":")) + "\n"`.

- [ ] **Step 4: Verify GREEN**

  Run the unittest command and
  `python3 src/bcs/scripts/dump_openapi.py /tmp/bcn.openapi.json`.

### Task 2: Gateway publication and catalog configuration

**Files:**
- Modify: `src/gateway/scripts/dump_and_publish.sh`
- Modify: `src/gateway/configs/application.yaml`
- Create: `src/gateway/configs/schemas/bcn.openapi.json`
- Modify: `src/gateway/configs/schemas/README.md`
- Modify: `src/gateway/tests/test_domain_map.py`
- Modify: `src/gateway/tests/test_gate_and_publish.py`
- Modify: `src/gateway/tests/test_served_openapi.py`
- Modify: `src/gateway/tests/integration/test_forward_route.py`
- Modify: `src/gateway/tests/integration/test_forward_signs_principal.py`

**Interfaces:**
- Consumes: Task 1 CLI output and existing `gate_and_publish_openapi.py`.
- Produces: domain `collaboration`, server `bcs`, schema artifact `bcn.openapi.json`, and route-security rule requiring User.

- [ ] **Step 1: Write failing Gateway tests**

  Assert the shipped config resolves a collaboration path to server `bcs`,
  uses HTTP without rewrite, points to `schemas/bcn.openapi.json`, resolves
  `${bcs_server_url}`, and aggregates a representative BCN path alongside
  existing Backend and BaaS paths. Extend the ASGI forwarding stub with a
  collaboration GET and body-carrying PATCH/POST, assert paths and bodies are
  forwarded verbatim, and assert the signed Principal uses audience `bcs`
  after any forged inbound Principal is removed.

- [ ] **Step 2: Verify RED**

  Run `uv run pytest -q tests/test_domain_map.py tests/test_gate_and_publish.py tests/test_served_openapi.py tests/integration/test_forward_route.py tests/integration/test_forward_signs_principal.py` from `src/gateway` with proxy variables unset.
  Expected: failures for missing collaboration domain/artifact.

- [ ] **Step 3: Add publication and config**

  Register `bcn` in `dump_and_publish.sh`, dump with Task 1's CLI, gate to
  `configs/schemas/bcn.openapi.json`, and add the exact config entries from the
  approved design. Generate the initial artifact through the exporter and
  compatibility gate.

- [ ] **Step 4: Verify GREEN**

  Run the focused pytest command and
  `src/gateway/scripts/dump_and_publish.sh --skip backend --skip baas`.

### Task 3: BCS Gateway Principal trust composition

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/src/config.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify: `src/bcs/configs/bcs-config-example.toml`
- Modify: `src/bcs/configs/bcs-config-local.toml`
- Test: focused bootstrap config/trust unit tests beside the composition helper

**Interfaces:**
- Consumes: `GatewayPrincipalTrust::new` and `GatewayPrincipalTokenVerifier::new` from `bcs-api-http`.
- Produces: validated `GatewayPrincipalConfig` and one injected `Arc<dyn PrincipalVerifier>` using `iss=gateway`, `aud=bcs`, `kid=bare`.

- [ ] **Step 1: Write failing configuration/trust tests**

  Assert default local trust resolves the documented dev key, explicit secret
  material is preferred, and pre/gray/prod reject absent or empty key material.

- [ ] **Step 2: Verify RED**

  Run the focused bootstrap unit tests from `src/bcs`.
  Expected: compile/test failure because Gateway Principal bootstrap config and
  trust construction do not exist.

- [ ] **Step 3: Implement trust construction**

  Add non-secret issuer/audience/kid/secret lookup configuration. Resolve
  `AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE` in the bootstrap. Use
  `avernet-dev-signing-key-NOT-FOR-PROD` only for local/dev; return
  `BcsError::InvalidConfig` in pre/gray/prod when material is absent.

### Task 4: Compose V1 Application facades and mount the Router

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/Cargo.toml`
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/boundary_contract.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md`
- Modify: `src/bcs/crates/bootstrap/bcs/CONTEXT.md`
- Create: `src/bcs/crates/bootstrap/bcs/tests/openapi_v1_mount.rs`

**Interfaces:**
- Consumes: existing `BotServiceImpl`, `GroupServiceImpl`, `SessionServiceImpl`, `InvitationFriendshipServiceImpl`, stores, core services, and Task 3 verifier.
- Produces: one `bcs_api_http::ApiState` stored in bootstrap state and merged by `build_router()`.

- [ ] **Step 1: Write failing mount/auth tests and update the boundary contract**

  Replace the preparatory "must not depend" assertion with assertions that the
  bootstrap depends on `bcs-api-http` and all four V1 Application crates while
  the adapter itself still has no concrete-service dependency. Start a real
  in-memory `BcsServer`, assert a correctly signed `aud=bcs` Principal reaches
  a representative collaboration GET, and assert missing/invalid Principal is
  401.

- [ ] **Step 2: Compose V1 services in the composition root**

  Construct the Bot control plane, Group, Session/Message, and
  Invitation/Friendship V1 facades from the same core/store instances already
  used by Legacy services. Keep concrete imports in bootstrap only.

- [ ] **Step 3: Merge without nesting**

  Merge `bcs_api_http::router(state)` directly into the existing application
  Router. Do not add another prefix and do not modify `bcs_http::router`.

- [ ] **Step 4: Verify GREEN**

  Run `cargo test -p bcs-api-http`,
  `cargo test -p bcs --test openapi_v1_mount`, and `cargo check -p bcs --all-targets`.

### Task 5: Gateway-to-BCS forwarding evidence

**Files:**
- Modify: `src/gateway/tests/integration/test_forward_route.py`
- Modify: `src/gateway/tests/integration/test_forward_signs_principal.py`
- Modify: `src/gateway/tests/e2e/asgi/baseline/test_served_openapi.py`
- Modify if needed: `src/bcs/scripts/adapters_endpoint_coverage.py`

**Interfaces:**
- Consumes: configured Gateway domain map and mounted BCS Router.
- Produces: regression evidence for verbatim GET/body forwarding, `aud=bcs`, served documentation, old-path absence, and unaffected existing domains.

- [ ] **Step 1: Complete cross-component assertions not already added in Task 2**

  Task 2 must already extend the ASGI upstream stub with a collaboration GET
  and body-carrying PATCH/POST, assert verbatim paths/bodies, decode the signed
  Principal with audience `bcs`, and assert forged inbound Principal removal.
  Here, add only the live BCS-backed assertion or coverage inventory needed to
  prove those Gateway requests reach the mounted Router; do not duplicate
  lower-level Gateway tests.

- [ ] **Step 2: Verify RED then GREEN**

  Run the live BCS-backed test before and after its harness/wiring change. The
  lower-level Gateway tests retain their Task 2 RED/GREEN evidence.

- [ ] **Step 3: Run final focused regression**

  Run BCS contract/exporter tests, `bcs-api-http`, bootstrap mount/check,
  Gateway domain/publish/OpenAPI/forwarding tests, architecture checks for
  touched modules, and `git diff --check`. Record any known dev baseline failure
  separately rather than modifying unrelated files.

### Task 6: Merge latest dev and resolve the PR #697 context conflict

**Files:**
- Merge: `upstream/dev`
- Resolve: `src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md`

- [ ] **Step 1: Refresh and inspect the merge**

  Run `git fetch upstream dev` and
  `git merge-tree --write-tree --name-only HEAD upstream/dev`.
  Expected: only `bcs-api-http/CONTEXT.md` has a textual conflict; Cargo.lock
  and bootstrap server changes merge automatically.

- [ ] **Step 2: Merge without rewriting the published PR branch**

  Run `git merge --no-ff upstream/dev`. Preserve #697's session-token delivery
  description and no-store response ownership. Preserve this PR's production
  V1 Router mount and application/bootstrap ownership statements. State that
  the focused session-token and WebSocket routers are completed by Tasks 8-9.

- [ ] **Step 3: Inspect the merged foundation**

  Confirm the merge contains `GroupSessionConnectionService`,
  `GroupSessionJwtService`, `group_session_connection_router`,
  `WorkbenchConnectionAuth::SessionBound`, and the shared dispatcher changes.
  Confirm it does not yet mount either new public route.

- [ ] **Step 4: Commit the merge resolution**

  Complete the merge commit without changing unrelated upstream code.

### Task 7: Extend the authoritative contract from 32 to 34 operations

**Files:**
- Modify: `src/bcs/api-contracts/v1/openapi.yaml`
- Create: `src/bcs/api-contracts/v1/openapi/connections.yaml`
- Modify: `src/bcs/tests/openapi/test_contract.py`
- Create: `src/bcs/tests/openapi/test_group_session_connection_contract.py`
- Modify: `src/bcs/tests/openapi/test_dump_openapi.py`
- Modify: `src/bcs/api-contracts/README.md`

- [ ] **Step 1: Write failing contract tests**

  Require exactly these additional operations:

  ```text
  POST /openapi/v1/collaboration/sessions/{session_id}/token
  GET  /openapi/v1/collaboration/group/ws
  ```

  Assert 34 total operations. The POST must require a Gateway User, have no
  caller-controlled body, return the standard success envelope with only
  `token: string` and `expires_at: integer/int64`, document no-store headers,
  and declare 401/403/404/500 error envelopes. The GET must require a sensitive
  `token` query parameter, declare `x-avernet-protocol: websocket`, carry an
  explicit empty `x-avernet-security`, and document 101/401/503 responses.

- [ ] **Step 2: Verify RED**

  Run:

  ```bash
  uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
  ```

  Expected: failures for the two absent operations and the old 32-operation
  inventory.

- [ ] **Step 3: Add the minimal contract fragment**

  Define `SessionConnectionToken`, its standard success envelope, the token
  response headers, the HTTP path item, and the WebSocket Upgrade path item in
  `connections.yaml`. Reference both path items from `openapi.yaml`. Do not
  describe Workbench frames as HTTP request/response schemas.

- [ ] **Step 4: Verify GREEN and deterministic export**

  Run the contract tests, validator, and deterministic exporter. Expected:
  34 operations, collaboration-only paths, and no external `$ref` values.

- [ ] **Step 5: Commit**

  Commit as `feat(bcs): contract session-bound websocket access`.

### Task 8: Compose and mount session-token issuance

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/Cargo.toml`
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/src/http_adapter.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/CONTEXT.md`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md`
- Modify: `src/bcs/crates/bootstrap/bcs/tests/openapi_v1_mount.rs`
- Test: existing `src/bcs/crates/adapters/http/bcs-api-http/tests/group_session_connection_routes.rs`

- [ ] **Step 1: Write failing bootstrap tests**

  Extend the live mount test to prove the token POST is reachable through the
  production Router, a valid Gateway Human reaches the issuance application
  service, missing/invalid Principal returns 401, and successful responses
  contain only token/expiry plus both no-store headers. Add pure bootstrap tests
  that missing or empty `BCS_SECRET_BCN_GROUP_SESSION_WS_JWT` fails closed and
  explicit test material is accepted without appearing in Debug/errors.

- [ ] **Step 2: Verify RED**

  Run the focused bootstrap mount and group-session connection tests. Expected:
  the standalone route builder exists but production bootstrap does not compose
  or mount it.

- [ ] **Step 3: Compose one connection service in bootstrap**

  Resolve the fixed secret name `bcn-group-session-ws-jwt` through the existing
  secret boundary, construct `GroupSessionJwtService`, then construct one
  `GroupSessionConnectionServiceImpl` from the shared V1 Session service and
  token port. Store trait-object clones needed by both delivery adapters.

- [ ] **Step 4: Mount the HTTP slice without a second prefix**

  Merge `group_session_connection_router(service, principal_verifier)` next to
  the existing `bcs_api_http::router(ApiState)`. Do not add the token route to
  the generic `ApiState` Router and do not construct a second verifier.

- [ ] **Step 5: Verify GREEN and commit**

  Run `cargo test -p bcs-api-http`, the bootstrap mount test, focused secret
  tests, and `cargo check -p bcs --all-targets`. Commit as
  `feat(bcs): mount session connection token API`.

### Task 9: Add and mount the session-bound WebSocket Upgrade route

**Files:**
- Create: `src/bcs/crates/adapters/ws/bcs-ws/src/web/group_session.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/web/mod.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/CONTEXT.md`
- Create: `src/bcs/crates/adapters/ws/bcs-ws/tests/group_session_ws.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/CONTEXT.md`
- Modify: focused bootstrap WebSocket tests

- [ ] **Step 1: Write failing Upgrade/authentication tests**

  Cover only the new boundary: missing, malformed, forged, expired, or
  wrong-purpose token returns 401 before Upgrade; unavailable token service
  returns 503; a valid token upgrades with the exact immutable
  tenant/User/Group/Session binding; scope mismatch or revoked access during
  the existing connect frame is rejected and closes. Do not add chat, stream,
  attachment, abort, or event-delivery cases.

- [ ] **Step 2: Verify RED**

  Run `cargo test -p bcs-ws --test group_session_ws`. Expected: the public
  Upgrade Router does not exist.

- [ ] **Step 3: Implement the focused Router**

  Add `GET /openapi/v1/collaboration/group/ws`. Extract the query token before
  Upgrade, call only `GroupSessionConnectionService::verify_token`, map the
  approved 401/503 classes without exposing credential data, create
  `WorkbenchConnectionAuth::SessionBound`, and call the existing
  `handle_client_connection`. Reuse the existing dispatch state, registry,
  idle timer, frontend delivery, and instrumentation.

- [ ] **Step 4: Mount through bootstrap**

  Inject the same connection service and existing Workbench dispatch state,
  then merge the WebSocket Router beside `/ws` and `/ws/bot`. Keep both legacy
  endpoints unchanged.

- [ ] **Step 5: Verify GREEN and commit**

  Run the focused Upgrade tests, `cargo test -p bcs-ws`, the bootstrap
  WebSocket/mount tests, and `cargo check -p bcs --all-targets`. Commit as
  `feat(bcs): mount session-bound workbench websocket`.

### Task 10: Enable Gateway HTTP/WebSocket routing and security

**Files:**
- Modify: `src/gateway/configs/application.yaml`
- Modify: `src/gateway/tests/test_domain_map.py`
- Modify: `src/gateway/tests/test_route_security.py`
- Modify: `src/gateway/tests/test_log_redaction.py`
- Modify: `src/gateway/tests/integration/test_forward_signs_principal.py`
- Modify: `src/gateway/tests/integration/test_relay_ws_route.py`
- Modify: `src/gateway/tests/e2e/asgi/baseline/test_served_openapi.py`

- [ ] **Step 1: Write failing Gateway tests**

  Require `collaboration.protocols == [http, websocket]`; the token POST keeps
  the general required User and signed `aud=bcs` Principal; exact rule
  `GET /openapi/v1/collaboration/group/ws` resolves to `{}`; the WebSocket
  relay preserves path and query byte-for-byte and sends no forged identity;
  every `token` query value is redacted from normal and error logs.

- [ ] **Step 2: Verify RED**

  Run the focused domain/security/HTTP-signing/WebSocket-relay/redaction tests.
  Expected: collaboration is HTTP-only and the general User rule blocks the
  browser WebSocket handshake.

- [ ] **Step 3: Add minimal configuration**

  Add `protocols: [http, websocket]` to the collaboration domain and exact
  method/path security exception:

  ```yaml
  "GET /openapi/v1/collaboration/group/ws": {}
  ```

  Do not add rewrite logic or a handwritten Gateway operation. Reuse the
  existing relay and redactor.

- [ ] **Step 4: Verify GREEN and commit**

  Run the focused Gateway suite and narrow Ruff. Commit as
  `feat(gateway): relay BCN session websocket`.

### Task 11: Publish the 34-operation artifact and prove the integrated boundary

**Files:**
- Modify: `src/gateway/configs/schemas/bcn.openapi.json`
- Modify: `src/gateway/tests/test_dump_and_publish_script.py`
- Modify: `src/gateway/tests/test_gate_and_publish.py`
- Modify: `src/gateway/tests/test_served_openapi.py`
- Modify: `src/gateway/tests/integration/test_live_bcs_forwarding.py`
- Modify: `src/gateway/scripts/test_live_bcs_forwarding.sh`
- Modify: relevant BCS/Gateway context documentation

- [ ] **Step 1: Write failing publication and live-boundary tests**

  Assert the checked-in artifact has 34 operations and both new paths. Extend
  the live harness only through the requested boundary: issue a token through
  Gateway to a real BCS process, then prove the WebSocket path reaches the BCS
  Upgrade/authentication layer. Do not send chat or assert Workbench messages.

- [ ] **Step 2: Regenerate through the compatibility gate**

  Run `src/gateway/scripts/dump_and_publish.sh --skip backend --skip baas`.
  Because two operations are additive, the gate must publish without
  `--allow-breaking` and the artifact must byte-match a fresh exporter run.

- [ ] **Step 3: Run final focused verification**

  Run contract/exporter tests, `bcs-api-http`, `bcs-ws`, bootstrap mount and
  WebSocket tests, BCS all-target check, Gateway publication/domain/security/
  HTTP/WS relay/OpenAPI tests, the live HTTP/WS boundary proof, narrow Ruff,
  shell syntax, and `git diff --check`.

- [ ] **Step 4: Independent review and commit**

  Review the entire range against the approved design, fix actionable
  findings, then commit publication/evidence changes as
  `test(gateway): prove BCN session connection integration`.
