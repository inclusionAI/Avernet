# Tasks: Config-Driven Gateway Forwarding

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

> **Out of this feature's scope:** principal signing/verification (the
> gateway↔backend JWT) is owned by the auth workstream. Forwarding integrates with
> that seam but does not implement it; the live cutover depends on it (see Rollout
> in `plan.md`).

## Task 1: Domain map config + resolver `[x]`
- **Goal:** Load `upstreams.yaml` and resolve a request's leading path segment to a target server (unknown domain → no match).
- **Files:** `src/gateway/src/gateway/community/core/forwarding/_domains.py` (new), `src/gateway/configs/upstreams.yaml` (new)
- **Done when:**
  - [x] `DomainMap.from_yaml` parses `domains` (server + schema source) and `servers` (base_url, env-expanded).
  - [x] `resolve(path) -> Server | None` returns the server for a configured leading segment (after the `/openapi/v1` base) and `None` otherwise.
  - [x] Unit tests cover match, no-match, and env-var base_url expansion.
- **Depends on:** —

## Task 2: Forwarder SPI + bare httpx plugin `[x]`
- **Goal:** A streaming HTTP forwarder behind an SPI so flavors can swap.
- **Files:** `src/gateway/src/gateway/community/spi/forwarder/{_protocols,_models,__init__}.py` (new), `src/gateway/src/gateway/community/plugins/forwarder/bare/_plugin.py` (new), `pyproject.toml`
- **Done when:**
  - [x] `Forwarder.forward(request) -> AsyncContextManager[ForwardResponse]` protocol defined (neutral `ForwardRequest`/`ForwardResponse` models).
  - [x] Bare plugin issues the upstream call with `httpx`, streaming the response body (raw bytes) and dropping hop-by-hop headers both directions. (Downstream principal conveyance/signing is added by the auth workstream at this seam; not built here.)
  - [x] `httpx` promoted to a runtime dependency; new code is mypy-consistent with the codebase (only the ubiquitous bare-plugin subclass-Any artifact).
  - [x] Conformance test against a real ASGI app (status, headers, multi-chunk streamed body, hop-by-hop stripping).
- **Depends on:** —

## Task 3: SchemaCatalog SPI + file loader + background refresh `[x]`
- **Goal:** Provide the current published description per domain, refreshed in the background with last-known-good.
- **Files:** `src/gateway/src/gateway/community/spi/schema_catalog/{_protocols,__init__}.py` (new), `src/gateway/src/gateway/community/plugins/schema_catalog/bare/_plugin.py` (new)
- **Done when:**
  - [x] `SchemaCatalog.current(domain) -> dict` protocol defined.
  - [x] Bare plugin reads a local file (single-box); `refresh_loop` re-reads every `refresh_seconds` and swaps the in-memory copy.
  - [x] On read failure or malformed/unparseable content, the previous copy is retained (last-known-good); no exception escapes.
  - [x] Unit tests: adopts a changed source; keeps last-known-good on failure (malformed, missing, non-mapping); background loop adopts then stops.
- **Depends on:** —

## Task 4: OpenAPI generator `[x]`
- **Goal:** Produce the served doc from a published description — filter to the domain namespace and attach auth metadata.
- **Files:** `src/gateway/src/gateway/community/core/forwarding/_openapi.py` (new)
- **Done when:**
  - [x] `generate_openapi(description, rules) -> dict` keeps only `/openapi/v1` paths and their transitively-referenced `components`.
  - [x] Each operation carries `x-avernet-security` resolved from the prefix auth rules.
  - [x] Unit tests: namespace filter, transitive component collection, security attach (default + specific), input not mutated.
- **Depends on:** Task 3

## Task 5: Catch-all forwarding entrypoint `[x]`
- **Goal:** One route that authenticates and forwards verbatim — the runtime request path.
- **Files:** `src/gateway/src/gateway/community/adapters/web/_forward.py` (new)
- **Done when:**
  - [x] Resolves the domain (unknown → `404` envelope, never open-proxy, before auth); authenticates (fail-closed) before any forward.
  - [x] Forwards the path **verbatim** to the resolved server via `Forwarder`, streaming the response with duplicate headers preserved; upstream failure → `502` envelope. (The auth workstream attaches the signed principal at the forwarder seam.)
  - [x] Integration tests (real streaming forwarder + stub upstream): auth reject-before-forward, unknown-domain 404, success + duplicate Set-Cookie, SSE streaming, upload verbatim.
- **Depends on:** Tasks 1, 2

## Task 6: Wire gateway + retire #389 stub routers `[x]`
- **Goal:** Compose the pieces and cut over the app to config-driven serving.
- **Files:** `src/gateway/src/gateway/community/bootstrap/_forwarding.py` (new), `src/gateway/src/gateway/community/adapters/web/app.py`, `src/gateway/src/gateway/community/adapters/web/routers/**` (deleted), `src/gateway/configs/schemas/bots.openapi.json` (seed artifact)
- **Done when:**
  - [x] `build_forwarding` composes forwarder, domain map, and schema catalog (+ background refresh lifecycle); existing `Authenticator` wiring preserved.
  - [x] `app.py` mounts the catch-all, overrides `app.openapi` to serve the generated doc, starts/stops the refresher on lifespan, and drops `include_all`.
  - [x] The seven `routers/<group>/` stub packages are deleted (+ their obsolete tests); ruff + `pytest -m "not e2e"` green (288); mypy consistent with baseline.
  - [x] Snapshot tests: served `/openapi/v1` doc ⊇ the published (#389) operation set, every op carries `x-avernet-security`, only the public namespace is exposed. (Seed artifact captured from the #389 contract; replaced by the backend's own published artifact in Group D/E.)
  - Note: fixed a pre-existing test-isolation leak (`test_runner_plugin` left `GATEWAY_CONFIG_PATH` in the env) that the now-config-driven app factory exposed.
- **Depends on:** Tasks 4, 5

## Task 7: Backend — expose ALL groups under `/openapi/v1/bots` in a dedicated subdirectory `[x]`
- **Goal:** Every externally-exposed backend group (the seven #389 groups — bots, channels, identity, mcp, resources, routines, skills) serves under the `/openapi/v1/bots` prefix, in a **new dedicated subdirectory** that keeps the public surface distinct from the legacy `/api/…` routers.
- **Files:** new `src/backend/src/agentclaw/community/adapters/http/openapi_v1/**` (dedicated public-API package); `src/backend/src/agentclaw/community/adapters/http/app.py` (mount it). Legacy `/api/…` routers untouched.
- **Done when:**
  - [x] `openapi_v1/_rehome.py` re-mounts the existing group routers under `/openapi/v1/bots/…` (path move only — same handlers/deps reused, no logic written). `bot_management` (`/api/bots`) collapses onto the domain root (no `bots/bots`); other groups become sub-paths. 119 public paths under the community profile; `app.openapi()` generates with no operationId collisions.
  - [x] Public surface lives in its own subdirectory, distinct from legacy `/api/…` which is untouched.
  - [x] Existing backend gateway contract suite stays green (102 passed).

## Task 8: Backend — `dump_openapi()` + public-namespace test `[x]`
- **Goal:** Deterministic OpenAPI dump for publishing, and enforce that the public namespace holds only the intended `bots` surface.
- **Files:** `src/backend/src/agentclaw/community/adapters/http/openapi_v1/dump.py` (new), `src/backend/tests/community/contracts/gateway/test_public_namespace.py` (new)
- **Done when:**
  - [x] `dump_openapi()` writes the public `/openapi/v1` description deterministically (sorted keys). Regenerated the gateway's `bots.openapi.json` artifact (119 paths) from it.
  - [x] The namespace test fails if any route under `/openapi/v1` falls outside `/openapi/v1/bots`; plus a populated-surface sanity check on the re-home router.
- **Depends on:** —

## Task 9: Backward-compatibility checker (in-repo)
- **Goal:** A focused checker that classifies two OpenAPI descriptions as compatible or breaking.
- **Files:** `src/gateway/src/gateway/community/core/forwarding/_compat.py` (new) (or a shared tools module), plus unit tests
- **Done when:**
  - [ ] `check_compatible(old, new) -> list[Breaking]` flags removed op/field, optional→required, type/`default`/enum-value changes; passes purely additive changes.
  - [ ] Unit tests cover each breaking class and the additive-passes case.
- **Depends on:** —

## Task 10: Backend release CI — compat-gate then publish
- **Goal:** On release, block breaking changes and publish the description for the gateway to auto-adopt.
- **Files:** backend release CI config/scripts; the single-box committed description file the bare catalog reads
- **Done when:**
  - [ ] CI runs `dump_openapi()` → `check_compatible(published, new)`; a breaking change fails the release unless an explicit new major.
  - [ ] On pass, the description is published to the store (OSS) and the committed single-box file is updated.
  - [ ] (If the release pipeline lives outside this repo, deliver the dump+gate+publish script and document the wiring hand-off.)
- **Depends on:** Tasks 8, 9

## Task 11: Tests & Verification
- **Goal:** Ensure the feature meets the spec acceptance criteria end-to-end.
- **Files:** the test suites above
- **Done when:**
  - [ ] Every spec acceptance criterion checks off (domain-transparent forward, deny unknown domain, fail-closed prefix auth, namespace invariant, verbatim serve, auto-adopt latest + last-known-good, doc-only degradation, publish-time compat gate, pluggable source, no hand-written endpoints/whitelist).
  - [ ] `ruff`, `mypy --strict`, `pytest -m "not e2e"` all green across gateway and backend.
- **Depends on:** Tasks 6, 10

---

## Groups

- **Group A — Forwarding core:** Tasks 1, 2
  - Theme: Domain resolution and the streaming forwarder — the pieces the request path needs.
- **Group B — Doc generation:** Tasks 3, 4
  - Theme: Publish-fed schema catalog (with last-known-good refresh) and the OpenAPI generator.
- **Group C — Gateway wiring + cutover:** Tasks 5, 6
  - Theme: The catch-all entrypoint, full composition, and deleting the #389 stubs — the gateway serves config-driven.
- **Group D — Backend alignment:** Tasks 7, 8
  - Theme: The backend serves the whole `bots` surface under `/openapi/v1/bots`, dumps its OpenAPI, and enforces the namespace invariant.
- **Group E — Compatibility gate + publish:** Tasks 9, 10
  - Theme: The compat checker and the release-time gate-then-publish flow.
- **Group F — Verification:** Task 11
  - Theme: Final spec acceptance check.
