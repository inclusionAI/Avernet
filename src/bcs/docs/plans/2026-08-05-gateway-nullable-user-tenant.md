# Gateway Nullable User Tenant Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Align BCS with Gateway by accepting a null or absent User Principal tenant while retaining strict tenant requirements for Bot, App, and AccessKey identities and adding non-sensitive JWT diagnostics.

**Architecture:** The HTTP adapter remains responsible for validating Gateway's signed wire contract and projects an optional normalized tenant into the application Service API. Existing tenant-bearing callers and group-session tokens remain compatible; tenantless Human callers continue through every current use case because authorization is identity/ownership based, while the WebSocket connection token treats tenant as optional binding metadata. Diagnostic logs replace token previews with a SHA-256 fingerprint and report schema failures by exact claim path without claim values.

**Tech Stack:** Rust, Serde, `serde_path_to_error`, `jsonwebtoken`, SHA-256, `tracing`, Cargo tests.

---

### Task 1: Pin the Gateway Principal compatibility contract

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/tests.rs`
- Modify: `src/bcs/api-contracts/v1/gateway-principal/contract.md`
- Modify: `src/bcs/api-contracts/v1/gateway-principal/principal-set.json`

**Steps:**

1. Add verifier tests for a User-only Principal with `tenant: null` and with the field absent; assert verification succeeds with no normalized tenant.
2. Add a mixed User-null plus Bot/App test; assert required Principal tenants still normalize and must agree.
3. Run `cargo test -p bcs-api-http gateway_principal` and verify the new tests fail against the required `String` wire model.
4. Update the contract text and shared fixture to state that only User tenant is nullable/omissible.

### Task 2: Add safe verifier diagnostics

**Files:**
- Modify: `src/bcs/Cargo.toml`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/Cargo.toml`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/verifier.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/tests.rs`

**Steps:**

1. Add a test subscriber that captures a malformed signed claim failure.
2. Assert the log includes `claim_path=principals[0].tenant` and a deterministic short `token_fingerprint`.
3. Assert the log excludes the compact JWT, payload segment, signing key, and secret fixture markers.
4. Run the focused test and verify it fails because the existing verifier logs `token_prefix` and loses the Serde path.
5. Verify the compact JWT into `serde_json::Value`, deserialize the verified value through `serde_path_to_error`, and log only the error category, path, key id, and SHA-256 fingerprint.
6. Replace every existing `token_prefix` field with the fingerprint and rerun the focused verifier tests.

### Task 3: Project optional tenant through the Service API

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/wire.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/verifier.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/identity.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/principal.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/authorization.rs`
- Modify: affected `AuthenticatedCaller` contract and application tests.

**Steps:**

1. Change only the User wire tenant to `Option<String>`; leave Bot, App, and AccessKey wire tenants required.
2. Change the normalized caller and Human Principal tenant to `Option<String>` and preserve it without creating a default.
3. In projection, ignore an absent User tenant for normalization; validate a present User tenant and every required Principal tenant as nonblank and mutually equal.
4. Keep `subject.tenant_id` informational when the outer User tenant is absent; when both exist, require them to match.
5. Update constructors and assertions to use `Some(...)` for existing tenant-bearing callers and add a Service API contract test proving `require_human` accepts `None`.
6. Run `cargo test -p bcs-service-api` and the affected V1 application crate tests.

### Task 4: Preserve group-session token compatibility

**Files:**
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/group_session_connection.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/port/group_session_token.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/src/connection.rs`
- Modify: `src/bcs/crates/services/bcs-jwt/src/group_session.rs`
- Modify: affected HTTP/WS/session token tests.

**Steps:**

1. Add a failing token-service test that issues and verifies a tenantless scope.
2. Make the wire `tenant` claim optional with a Serde default and omit it for new tenantless tokens.
3. Continue accepting and returning existing string-valued tenant claims unchanged.
4. Remove tenant from the nonblank scope validation while retaining length validation when present.
5. Propagate `Option<String>` through connection bindings and reconstruct the authenticated caller without fabrication.
6. Run `cargo test -p bcs-jwt`, `cargo test -p bcs-app-session`, `cargo test -p bcs-ws`, and `cargo test -p bcs-api-http`.

### Task 5: Verify the affected boundary

**Files:**
- Review all changed files only; do not run global formatting.

**Steps:**

1. Run `cargo check -p bcs-api-http -p bcs-service-api -p bcs-app-session -p bcs-jwt -p bcs-ws`.
2. Run the focused contract and unit suites from Tasks 1–4.
3. Run `git diff --check` and inspect `git diff` for unrelated formatting or secret material.
4. Record compatibility: existing non-null User/Bot/App/AccessKey tokens and tenant-bearing group-session tokens remain accepted; only the previously rejected nullable User form is newly admitted.
