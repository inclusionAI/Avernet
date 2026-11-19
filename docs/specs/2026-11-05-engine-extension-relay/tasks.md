# Engine Extension Relay — Tasks

- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- Paths are relative to `apps/backend/src/agentclaw/community/` unless stated.
- Gate after each group: `DEPLOY_PROFILE=test uv run pytest tests/community -k engine_extension -v`.
- Final gate: `./scripts/ci_test.sh`. Changed-line coverage must be ≥ 80%.

## Group A — Contracts and configuration

1. [ ] Define the `plugin_api/engine_extension.py` Protocols:
   - `ExtensionAssertionSigner` (`sign`, `public_jwks`);
   - `ExtensionTransport` (async `forward` returning a buffered or streamed
     outcome; one-shot closeable body);
   - the shared DTOs (`ExtensionRequest`, `ExtensionOutcome`) and the error
     types (`Unreachable`, `Timeout`, `ResponseTooLarge`, `BadRedirect`).
   - *Test:* the Protocol contract inventory test passes
     (`tests/community/architecture/test_protocol_contracts.py`).
2. [ ] Write `core/engine_extension/config.py`, the registry config model and its
   load-time validation:
   - canonical engine keys and unique ids;
   - the https rule and its single-box exception;
   - no userinfo, query, or fragment in `base_url`;
   - secret names only.
   - *Test:* table-driven valid and invalid configs, where every invalid config
     fails at boot.
3. [ ] Write `core/engine_extension/registry.py`, implementing
   `EngineExtensionRegistry.for_engine`. A disabled or missing entry returns
   `None`.
   - *Test:* unit.

## Group B — Manifest

4. [ ] Write `core/engine_extension/manifest.py`:
   - parse OpenAPI 3.1 plus `x-avernet`;
   - check that `contract` and `engine` match the registry;
   - enforce the size and operation limits;
   - compile the most-specific-wins matcher, ignoring operations without
     `x-avernet`.
   - *Test:* matching precedence, ignored operations, and rejecting a
     malformed manifest or one for the wrong engine.
5. [ ] Add background refresh with a last-known-good copy. If the manifest
   cannot be fetched at boot, the relay answers 503
   `engine_extension_manifest_unavailable`.
   - *Test:* fake clock plus a fake fetcher, covering stale, recovered, and
     malformed refreshes.

## Group C — Request shaping

6. [ ] Write `core/engine_extension/paths.py`:
   - normalize the path (decode once, reject traversal and encoded separators);
   - build the upstream URL.
   - *Test:* a traversal corpus, and a property test that the built URL always
     starts with `base_url`.
7. [ ] Write `core/engine_extension/headers.py`, the request and response
   allow-lists and the Avernet headers to add.
   - *Test:* `cookie`, `authorization`, `x-avernet-*`, and `set-cookie` never
     pass through.
8. [ ] Write `core/engine_extension/claims.py`, which builds the assertion
   claims from the caller, `BotFacts`, the operation, and the request:
   - `sub` and `act` for App-on-behalf-of-user;
   - `htm`/`htu`;
   - `jti` equals the request id;
   - no PII and no device fields.
   - *Test:* the claim sets for each caller type are pinned (golden).

## Group D — Plugins

9. [ ] Add `plugins/local/engine_extension.py`:
   - an ephemeral ES256 signer;
   - an in-memory `ExtensionTransport` built on `MockSeam`, where an unstubbed
     call raises.
10. [ ] Add `plugins/community/engine_extension/signer.py`:
    - ES256 from a PEM resolved through `SecretResolver`, for both the
      `current` and `next` keys;
    - a JWKS that holds both keys;
    - in strict profiles, boot fails without a key; elsewhere the relay answers
      503.
    - Log a key fingerprint, never the key.
11. [ ] Add `plugins/community/engine_extension/transport.py`:
    - `httpx.AsyncClient` with `follow_redirects=False`;
    - a resolver pinned to the checked IP, with the private-range deny-list
      and the operator override;
    - optional mTLS;
    - streaming request and response;
    - a cumulative size cap, idle and total timeouts, and upstream cancellation
      on client disconnect.
12. [ ] Write the contract suites, which run against both the local and the
    community implementations:
    - `tests/community/contracts/test_engine_extension_signer.py`: the token
      verifies against the JWKS, `kid` is set, and rotation keeps old tokens
      valid during the overlap window;
    - `tests/community/contracts/test_engine_extension_transport.py`:
      streaming, the cap, the timeout mapping, redirects that are not followed,
      and a refused private IP.
    - Wire both into the DI profiles.

## Group E — Core relay

13. [ ] Write `core/engine_extension/limits.py`:
    - per-replica token buckets per `(server, tenant, bot)`;
    - a semaphore per server;
    - a breaker with closed, open, and half-open states.
    - *Test:* fake clock.
14. [ ] Add `active_engine` to `BotFacts`, filled in by
    `EngineRuntimeRelay.resolve_bot`.
    - *Test:* the existing `engine_runtime` tests still pass, and `BotFacts`
      still exposes no device fields.
15. [ ] Write `core/engine_extension/relay.py`, the orchestration in the order of
    plan §1, together with `errors.py` (code → HTTP plus envelope) and
    `audit.py`, which writes one audit line per call with no bodies or secrets.
    - *Test:* each failure branch returns its specified status, and the
      transport is not called whenever the relay rejects before forwarding.
    - *Test:* a masked 404 is returned for unauthorized callers.
    - *Test:* an App caller without a grant is refused.
    - *Test:* a caller type outside the operation's audience gets 403.
16. [ ] Write the discovery service, which filters the manifest operations by the
    caller type.
    - *Test:* unit.

## Group F — HTTP adapters

17. [ ] Add `adapters/http/openapi_v1/engine_extension/router.py`:
    - the discovery, relay, and JWKS routes (`PublicAPIRoute`);
    - entries in `admission_routes.py`: `GRANT_CHECKED_OWN_BOT` for the bot
      routes and `OPEN` for JWKS;
    - `StreamingResponse` for streamed outcomes.
    - *Test:* adapter tests through `TestClient`, plus the route-surface and
      admission-table consistency test.
18. [ ] Add `adapters/http/engine_extension/router.py`, the internal `/api`
    surface on `get_request_context`.
    - *Test:* adapter tests, showing that both surfaces produce the same
      assertion claims for the same user.
19. [ ] Regenerate the backend OpenAPI artifact (`bots.openapi.json`) for the
    three new public routes. The relay route's description links to the
    discovery endpoint.

## Group G — Gateway, reference server, docs

20. [ ] Gateway `apps/gateway/configs/application.yaml`: add the two
    `route_security` entries from plan §2.1. Gateway route-security tests cover
    unauthenticated access to JWKS and admission of a `bot` principal on
    `/engine/ext/**`.
    - Run `just test-ci` in `apps/gateway`.
21. [ ] Add `tests/community/support/reference_extension_server.py`. It verifies
    assertions through JWKS and serves one plain endpoint and one SSE endpoint.
    - Add an E2E test: backend, then the community transport, then the reference
      server, for both a JSON call and an SSE call.
22. [ ] Singlebox: an opt-in `ENGINE_EXT_REFERENCE=1` starts the reference
    server and registers it for `openclaw`. Document this in the singlebox
    README.
23. [ ] Write `docs/engine-extensions/extension-server-contract.md`, the public
    engine-owner contract described in plan §8, and add a `CONTEXT.md` glossary
    entry for Engine Extension API, Extension Server, and Extension Assertion.
24. [ ] Final checks:
    - every new file is under 1,000 lines;
    - `./scripts/ci_test.sh` passes;
    - update this file's checkboxes.
