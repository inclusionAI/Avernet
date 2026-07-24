# Tasks: Config-Driven Gateway Forwarding

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Domain map config + resolver
- **Goal:** Load `upstreams.yaml` and resolve a request's leading path segment to a target server (unknown domain → no match).
- **Files:** `src/gateway/src/gateway/community/core/forwarding/_domains.py` (new), `src/gateway/configs/upstreams.yaml` (new)
- **Done when:**
  - [ ] `DomainMap.from_yaml` parses `domains` (server + schema source) and `servers` (base_url, env-expanded).
  - [ ] `resolve(path) -> Server | None` returns the server for a configured leading segment (after the `/openapi/v1` base) and `None` otherwise.
  - [ ] Unit tests cover match, no-match, and env-var base_url expansion.
- **Depends on:** —

## Task 2: Forwarder SPI + bare httpx plugin
- **Goal:** A streaming HTTP forwarder behind an SPI so flavors can swap.
- **Files:** `src/gateway/src/gateway/community/spi/forwarder/{_protocols,__init__}.py` (new), `src/gateway/src/gateway/community/plugins/forwarder/bare/_plugin.py` (new), `pyproject.toml`
- **Done when:**
  - [ ] `Forwarder.forward(request, target, principal_token) -> Response` protocol defined.
  - [ ] Bare plugin issues the upstream call with `httpx`, streaming request and response bodies, propagating method/headers/query (dropping hop-by-hop headers) and attaching the principal token header.
  - [ ] `httpx` promoted to a runtime dependency; `mypy --strict` clean.
  - [ ] Unit test against a mocked httpx transport (status, headers, streamed body).
- **Depends on:** —

## Task 3: PrincipalSigner seam + bare HMAC plugin
- **Goal:** Sign the established principal for the downstream call (the seam forwarding relies on; §7.1).
- **Files:** `src/gateway/src/gateway/community/spi/signer/{_protocols,__init__}.py` (new), `src/gateway/src/gateway/community/plugins/signer/bare/_plugin.py` (new)
- **Done when:**
  - [ ] `PrincipalSigner.sign(principal) -> str` protocol defined.
  - [ ] Bare plugin emits a short-TTL HMAC JWT carrying the serialized principal (claims per §7.1: `iss`/`aud`/`iat`/`exp`).
  - [ ] Unit test: sign → decode round-trip; TTL/`aud` present.
- **Depends on:** —

## Task 4: SchemaCatalog SPI + file loader + background refresh
- **Goal:** Provide the current published description per domain, refreshed in the background with last-known-good.
- **Files:** `src/gateway/src/gateway/community/spi/schema_catalog/{_protocols,__init__}.py` (new), `src/gateway/src/gateway/community/plugins/schema_catalog/bare/_plugin.py` (new)
- **Done when:**
  - [ ] `SchemaCatalog.current(domain) -> dict` protocol defined.
  - [ ] Bare plugin reads a local file (single-box); a refresher re-reads the source every `refresh_seconds` and swaps the in-memory copy.
  - [ ] On read failure or malformed/unparseable content, the previous copy is retained (last-known-good); no exception escapes.
  - [ ] Unit tests: adopts a changed source; keeps last-known-good on failure.
- **Depends on:** —

## Task 5: OpenAPI generator
- **Goal:** Produce the served doc from a published description — filter to the domain namespace and attach auth metadata.
- **Files:** `src/gateway/src/gateway/community/core/forwarding/_openapi.py` (new)
- **Done when:**
  - [ ] `generate_openapi(description, rules) -> dict` keeps only `/openapi/v1/<domain>` paths and their referenced `components`.
  - [ ] Each operation carries `x-avernet-security` resolved from the prefix auth rules.
  - [ ] Unit tests: namespace filter, component collection, security attach.
- **Depends on:** Task 4

## Task 6: Catch-all forwarding entrypoint
- **Goal:** One route that authenticates, signs, and forwards verbatim — the runtime request path.
- **Files:** `src/gateway/src/gateway/community/adapters/web/_forward.py` (new)
- **Done when:**
  - [ ] Resolves the domain (unknown → `404`, never open-proxy); authenticates via `require_principal` (fail-closed) before any forward.
  - [ ] Signs the principal and forwards the path **verbatim** to the resolved server via `Forwarder`; response returns through the standard envelope.
  - [ ] Integration tests (mocked upstream transport): auth reject-before-forward, unknown-domain 404, JWT attached, success/error envelope, one SSE path, one upload path.
- **Depends on:** Tasks 1, 2, 3

## Task 7: Wire gateway + retire #389 stub routers
- **Goal:** Compose the pieces and cut over the app to config-driven serving.
- **Files:** `src/gateway/src/gateway/community/bootstrap/` , `src/gateway/src/gateway/community/adapters/web/app.py`, `src/gateway/src/gateway/community/adapters/web/routers/**` (deleted)
- **Done when:**
  - [ ] Bootstrap composes forwarder, domain map, schema catalog (+ refresher), and signer; existing `Authenticator` wiring preserved.
  - [ ] `app.py` mounts the catch-all, overrides `app.openapi` to serve the generated doc, starts the refresher on lifespan, and drops `include_all`.
  - [ ] The seven `routers/<group>/` stub packages are deleted; `ruff` + `mypy --strict` + `pytest -m "not e2e"` green.
  - [ ] Snapshot test: generated `/openapi/v1` doc ⊇ the #389 operation set for carried-over ops.
- **Depends on:** Tasks 5, 6

## Task 8: Backend — move exposed routers under `/openapi/v1/bots` + verify gateway JWT
- **Goal:** The backend serves the client-facing paths directly and rejects unsigned/direct access.
- **Files:** `src/backend/src/agentclaw/community/adapters/http/app.py` and the exposed routers under `src/backend/src/agentclaw/community/adapters/http/**`
- **Done when:**
  - [ ] Externally-exposed routers are dual-mounted at `/openapi/v1/bots/…` alongside the existing `/api/…` (transition).
  - [ ] A dependency on the `/openapi/v1` routes verifies the gateway-signed JWT (HMAC bare) and rejects missing/invalid tokens.
  - [ ] Existing backend tests stay green; a test covers reject-without-JWT.
- **Depends on:** Task 3 (shared HMAC seam/contract)

## Task 9: Backend — `dump_openapi()` + namespace-invariant test
- **Goal:** Deterministic OpenAPI dump for publishing, and enforce `/openapi/v1` = external-only.
- **Files:** `src/backend/src/agentclaw/community/adapters/http/app.py`, `src/backend/tests/community/contracts/gateway/test_public_namespace.py` (new)
- **Done when:**
  - [ ] `dump_openapi()` writes `app.openapi()` deterministically (stable ordering) to a file.
  - [ ] The test fails if any `/openapi/v1` route lacks the gateway-JWT verification marker (no internal route leaks into the public namespace).
- **Depends on:** Task 8

## Task 10: Backward-compatibility checker (in-repo)
- **Goal:** A focused checker that classifies two OpenAPI descriptions as compatible or breaking.
- **Files:** `src/gateway/src/gateway/community/core/forwarding/_compat.py` (new) (or a shared tools module), plus unit tests
- **Done when:**
  - [ ] `check_compatible(old, new) -> list[Breaking]` flags removed op/field, optional→required, type/`default`/enum-value changes; passes purely additive changes.
  - [ ] Unit tests cover each breaking class and the additive-passes case.
- **Depends on:** —

## Task 11: Backend release CI — compat-gate then publish
- **Goal:** On release, block breaking changes and publish the description for the gateway to auto-adopt.
- **Files:** backend release CI config/scripts; the single-box committed description file the bare catalog reads
- **Done when:**
  - [ ] CI runs `dump_openapi()` → `check_compatible(published, new)`; a breaking change fails the release unless an explicit new major.
  - [ ] On pass, the description is published to the store (OSS) and the committed single-box file is updated.
- **Depends on:** Tasks 9, 10

## Task 12: Tests & Verification
- **Goal:** Ensure the feature meets the spec acceptance criteria end-to-end.
- **Files:** the test suites above
- **Done when:**
  - [ ] Every spec acceptance criterion checks off (domain-transparent forward, deny unknown domain, fail-closed prefix auth, namespace invariant, verbatim serve, auto-adopt latest + last-known-good, doc-only degradation, publish-time compat gate, pluggable source, no hand-written endpoints/whitelist).
  - [ ] `ruff`, `mypy --strict`, `pytest -m "not e2e"` all green across gateway and backend.
- **Depends on:** Tasks 7, 11

---

## Groups

- **Group A — Forwarding core:** Tasks 1, 2, 3
  - Theme: Domain resolution, the streaming forwarder, and the principal signer — the pieces the request path needs.
- **Group B — Doc generation:** Tasks 4, 5
  - Theme: Publish-fed schema catalog (with last-known-good refresh) and the OpenAPI generator.
- **Group C — Gateway wiring + cutover:** Tasks 6, 7
  - Theme: The catch-all entrypoint, full composition, and deleting the #389 stubs — the gateway serves config-driven.
- **Group D — Backend alignment:** Tasks 8, 9
  - Theme: Backend serves the public paths, verifies the JWT, dumps its OpenAPI, and enforces the namespace invariant.
- **Group E — Compatibility gate + publish:** Tasks 10, 11
  - Theme: The compat checker and the release-time gate-then-publish flow.
- **Group F — Verification:** Task 12
  - Theme: Final spec acceptance check.
