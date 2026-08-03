# BCN-to-Gateway P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish and serve the existing 32-operation BCN V1 contract through Gateway and mount the matching authenticated BCS Router in production.

**Architecture:** Keep the BCS YAML contract authoritative, export deterministic self-contained JSON, and reuse Gateway's existing compatibility gate and configuration-driven forwarder. Compose concrete V1 Application facades only in the BCS bootstrap, inject the existing Gateway Principal verifier, and merge the existing Router without path rewriting.

**Tech Stack:** Rust/Axum, Python 3.12, FastAPI/HTTPX, OpenAPI 3.1, PyYAML, pytest, Cargo.

## Global Constraints

- Public paths are exactly `/openapi/v1/collaboration/**` on Gateway and BCS.
- Do not add Gateway path rewriting or handwritten per-operation proxy routes.
- Do not add PR #697's session WebSocket token endpoint in this branch.
- Keep the YAML contract authoritative; do not derive schemas from Axum runtime code.
- Do not run global `cargo fmt`; limit formatting to touched lines.
- Pre/gray/prod must fail startup without real Gateway Principal key material.
- Deferred conformance and route-inventory work remains in inclusionAI/Avernet#700.

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
