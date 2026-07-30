# Tasks: Public API — Engine Runtime Surface (Track C)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

All paths are relative to `src/backend/`. Source package is
`src/agentclaw/community/`, abbreviated `…/` below.

---

## Task 1: Extend the shared public contract for engine-sourced results  `[x]`
- **Goal:** Give `Envelope` the `warning` field and document the two new HTTP
  statuses this track can return, before anything depends on them.
- **Files:**
  - `…/adapters/http/openapi_v1/contracts.py`
  - `tests/community/adapters/http/openapi_v1/test_responses.py`
- **Done when:**
  - [x] `Envelope.warning: str = ""` exists with a description explaining it is
        non-empty only when the engine served the request with a documented
        limitation.
  - [x] `ErrorEnvelope` is **unchanged** — no `warning` on error responses.
  - [x] `ERROR_RESPONSES` gains `501` and `504` entries, both `ErrorEnvelope`.
  - [x] Existing envelope tests still pass, plus a new assertion that a success
        envelope serialises `warning` and that it defaults to `""`.
  - [x] Spot-check one existing category (bots) still serialises correctly with
        the added key.
- **Depends on:** —
- **Note:** This is the one change touching a contract shared with all seven
  existing categories (plan assumption 1). Keep it to exactly these edits.
- **Found while doing it:** `_error_response` was building `Envelope`, so the
  new field leaked a `warning: ""` key into every error body — including the six
  existing categories' — while `ERROR_RESPONSES` documents `ErrorEnvelope`,
  which has no such field. Fixed at the source: error paths now build
  `ErrorEnvelope`. The three pre-existing envelope-shape tests pass unmodified,
  and a regression guard asserts the emitted key set equals the documented
  model's fields.

## Task 2: Create the `core/engine_runtime` module skeleton and error types  `[x]`
- **Goal:** Stand up the new core module with its models, errors and Context
  Boundary README, with no relay logic yet.
- **Files:**
  - `…/core/engine_runtime/__init__.py` (new)
  - `…/core/engine_runtime/README.md` (new)
  - `…/core/engine_runtime/models.py` (new)
  - `…/core/engine_runtime/errors.py` (new)
- **Done when:**
  - [x] `models.py` defines `EngineResult(data, total, warning)`,
        `ConnectionResult`, `SocketInfo`.
  - [x] `errors.py` defines `EngineCapabilityUnsupportedError`,
        `EngineDeviceNotReadyError`, `EngineUpstreamError`,
        `EngineBotTypeNotSupportedError` — semantic state only, **no HTTP
        status** (Rule 7, `docs/arch/arch.rules.md:203`).
  - [x] `README.md` carries a `## Context Boundary` yaml block in the shape of
        `…/core/cron/README.md:5-25`, declaring every cross-module import the
        relay will make.
  - [x] **Also required:** a new core package must be declared in the E2E
        coverage manifest (`tests/community/framework/flow_coverage.py`) or
        `test_e2e_module_coverage.py` fails — there is no third state between
        covered and exempt. Exempt, with a specific reason: every path ends in
        an HTTP call to a device singlebox cannot provide.
  - [x] `tests/community/architecture/` passes.
- **Depends on:** —

## Task 3: Implement `EngineRuntimeRelay`  `[x]`
- **Goal:** One place that resolves the caller's bot, resolves its device,
  forwards a single engine call, and normalises the response.
- **Files:**
  - `…/core/engine_runtime/relay.py` (new)
  - `tests/community/core/engine_runtime/test_relay.py` (new)
- **Done when:**
  - [x] `_resolve_bot(bot_id, owner_id)` goes through `BotService` scoped by
        owner and raises `BotNotFoundError` for a bot that is not the caller's.
        **This runs before any device work.**
  - [x] `_resolve_device` calls `DeviceContextResolver.resolve_for_bot`
        (`…/core/devices/services/device_context_resolver.py:61`) and wraps
        `DeviceNotBoundError` / `ConnInfoBuildError` as
        `EngineDeviceNotReadyError`.
  - [x] `call(...)` forwards via `DeviceAdapterTransport.invoke`
        (`…/plugin_api/device_adapter_transport.py:59`).
  - [x] `_normalise` turns the engine's
        `ApiResponse{success, data, message, warning, total}` into
        `EngineResult`; a `200` carrying `success: false` raises
        `EngineUpstreamError` and never reaches a caller.
  - [x] `DeviceAdapterHTTPStatusError` with `status_code == 501` becomes
        `EngineCapabilityUnsupportedError`; other non-2xx become
        `EngineUpstreamError`.
  - [x] Unit tests cover each translation above, including the
        `success: false` case and the `warning`/`total` pass-through.
- **Depends on:** Task 2
- **Decided while implementing:** `UnknownProviderError` is **not** folded into
  `EngineDeviceNotReadyError`. A binding row naming a provider we don't know is
  bad data on our side; reporting it as retryable would have callers retrying
  something no retry can fix. It propagates to the existing 500 mapping.
  `DeviceAdapterTimeoutError` likewise propagates unwrapped — it is already the
  precise public answer (504), and wrapping would lose it.

## Task 4: Expose the relay as a Service API Protocol and wire DI
- **Goal:** Let routers Inject a Protocol, never the concrete relay.
- **Files:**
  - `…/api/engine_runtime_service.py` (new)
  - `…/di/modules/engine_runtime_module.py` (new)
  - `…/di/container.py`
  - `tests/community/architecture/test_service_api_conformance.py`
- **Done when:**
  - [ ] `EngineRuntimeRelayProtocol` is `@runtime_checkable` with **real**
        signatures for `call`, `capabilities`, `connection`.
  - [ ] `EngineRuntimeModule` binds concrete → singleton and aliases the
        Protocol, following `…/di/modules/cron_module.py:32-44`.
  - [ ] Module registered in `…/di/container.py` alongside `CronModule`
        (`container.py:106`).
  - [ ] `(EngineRuntimeRelayProtocol, EngineRuntimeRelay)` added to `_PAIRS` in
        the conformance test, which checks **full signature equality** — names,
        kinds, defaults and coroutine status — so the Protocol must match the
        concrete class exactly.
  - [ ] `test_api_layer_is_protocols_only.py` and
        `test_http_adapter_layer_is_http_only.py` pass.
- **Depends on:** Task 3

## Task 5: Define the public enums and shared schemas
- **Goal:** One module of enums plus the shared payload models, documented so a
  client generator produces usable types.
- **Files:**
  - `…/adapters/http/openapi_v1/engine_runtime/__init__.py` (new)
  - `…/adapters/http/openapi_v1/engine_runtime/enums.py` (new)
  - `tests/community/adapters/http/openapi_v1/engine_runtime/test_schema_docs.py` (new)
- **Done when:**
  - [ ] `SocketKind` (`chat`, `terminal`), `ApprovalMode`
        (`approve`, `on-miss`, `never`), `MessageRole`
        (`user`, `assistant`, `system`, `tool_use`, `tool_result`) all subclass
        `str, Enum`.
  - [ ] `EngineName` is **reused** from what the bots category already uses
        (`…/core/workspace/constants.py`, see
        `openapi_v1/bots/router.py:65-68`) — not redefined.
  - [ ] Every enum carries `x-enum-descriptions` covering **every** member.
  - [ ] `ApprovalMode` is referenced on request models only; no response model
        annotates it (plan investigation finding 4 — the local stub returns
        `"auto"`).
  - [ ] Schema test asserts: every enum is `str`-based, every member has an
        `x-enum-descriptions` entry, every field of every model in this package
        has a non-empty `description`, every model has a schema `description`.
- **Depends on:** —

## Task 6: Map the engine's error surface onto the public envelope
- **Goal:** Every failure this track can produce answers as an `Envelope`, in
  the right order.
- **Files:**
  - `…/adapters/http/openapi_v1/responses.py`
  - `tests/community/adapters/http/openapi_v1/test_responses.py`
- **Done when:**
  - [ ] `ENVELOPE_ERRORS` gains, all **before** the `BotServiceError` base entry
        at `responses.py:171`:
        `EngineCapabilityUnsupportedError` → `(501, …)`,
        `EngineBotTypeNotSupportedError` → `(501, "Not supported for this bot type")`,
        `EngineDeviceNotReadyError` → `(409, …)`,
        `DeviceAdapterTimeoutError` → `(504, …)`,
        `DeviceAdapterEndpointNotFoundError` → `(501, …)`,
        `EngineUpstreamError` → `(502, …)`,
        `DeviceAdapterHTTPStatusError` → `(502, …)` **last of this group**.
  - [ ] Both `501` messages name the capabilities endpoint.
  - [ ] Messages are fixed strings — never `str(exc)`.
  - [ ] A test asserts ordering: the specific transport errors resolve before
        `DeviceAdapterHTTPStatusError`, and every new entry resolves before
        `BotServiceError`. This change adds two base/leaf pairs at once, which
        is exactly the "map the base class last" trap from the Track B gotchas.
- **Depends on:** Task 2

## Task 7: Sessions group — 7 endpoints
- **Goal:** Wrap the engine's session surface.
- **Files:**
  - `…/adapters/http/openapi_v1/engine_runtime/sessions/{__init__,router,schemas}.py` (new)
  - `tests/community/adapters/http/openapi_v1/engine_runtime/test_sessions.py` (new)
- **Done when:**
  - [ ] All 7 routes served under
        `/openapi/v1/bots/{bot_id}/sessions…`, with `PATCH` on the resource
        mapping to the engine's `POST …/{id}/update`.
  - [ ] `session_id` path encoding settled and documented on every route that
        takes one; reuse whatever the list returns as `id` rather than inventing
        a second scheme. Tests cover a plain and an encoded id.
  - [ ] `Page.total` is exact, via the fetch-then-slice approach used by
        `openapi_v1/routines/router.py:126-131`, with a documented cap on the
        engine-side request.
  - [ ] `extra="forbid"`; `user_id`, `engine`, `agent_id` rejected → `422`.
  - [ ] **Gated on `bot_type == "personal"`** (plan assumption 6). All seven
        routes raise `EngineBotTypeNotSupportedError` → `501` on a `service`
        bot, checked **before** any device call. `DeviceContext.bot_type` is
        already populated (`core/devices/services/device_context.py:41`).
        Rationale: the engine drops `user_id` and returns every session on the
        device (`plugins/openclaw/_session.py:125-132`), so on a service bot the
        owner would see other callers' sessions and message history.
  - [ ] A test asserts the `501` on a service bot **and** that the transport was
        never invoked for it — the check must precede the forward, not filter
        its result.
  - [ ] Success + each mapped error covered, using the in-memory transport.
- **Depends on:** Tasks 4, 5, 6

## Task 8: Engine + models groups — 5 endpoints
- **Goal:** Wrap the two read-only groups that share one shape.
- **Files:**
  - `…/adapters/http/openapi_v1/engine_runtime/engine/{__init__,router,schemas}.py` (new)
  - `…/adapters/http/openapi_v1/engine_runtime/models/{__init__,router,schemas}.py` (new)
  - `tests/community/adapters/http/openapi_v1/engine_runtime/test_engine_models.py` (new)
- **Done when:**
  - [ ] `…/engine/{status,capabilities,available}` served; `available` maps to
        the engine's `/api/engine/list`. **`switch` and `restart` are not
        exposed** (plan Out of Scope).
  - [ ] `…/models` and `…/models/{model_id:path}` served, the `:path` converter
        preserving ids containing `/`.
  - [ ] Capability names in the capabilities payload are `list[str]`, with the
        known vocabulary in the field description — not a validating enum.
  - [ ] Engine-status `process` and `transition` stay **open dicts**, not
        modelled — they are assembled ad hoc at `manager.py:743-748`.
- **Depends on:** Tasks 4, 5, 6

## Task 9: Approvals group — 3 endpoints
- **Goal:** Wrap approvals, resolving the vocabulary mess the plan documented.
- **Files:**
  - `…/adapters/http/openapi_v1/engine_runtime/approvals/{__init__,router,schemas}.py` (new)
  - `tests/community/adapters/http/openapi_v1/engine_runtime/test_approvals.py` (new)
- **Done when:**
  - [ ] `GET …/approvals/mode` (query `session_key`) and
        `PUT …/approvals/mode` (body) replace the engine's two POSTs.
  - [ ] Request body uses the `ApprovalMode` enum; the value is forwarded
        **verbatim**, no translation.
  - [ ] Response `mode` is typed `str`, and a test proves a stub returning
        `"auto"` does not raise.
  - [ ] `GET …/approvals/modes` is gated on `APPROVAL_GET`, unlike the engine's
        ungated route, so all three approval routes agree per bot. The
        divergence is documented in the handler docstring.
  - [ ] `501` covered on an engine declaring neither approval capability
        (claude_code).
- **Depends on:** Tasks 4, 5, 6

## Task 10: Connection endpoint
- **Goal:** Replace the `get_device_connection` hand-off with a sanitised
  socket list.
- **Files:**
  - `…/adapters/http/openapi_v1/engine_runtime/connection/{__init__,router,schemas}.py` (new)
  - `…/core/engine_runtime/relay.py`
  - `tests/community/adapters/http/openapi_v1/engine_runtime/test_connection.py` (new)
- **Done when:**
  - [ ] `GET /openapi/v1/bots/{bot_id}/connection` returns
        `Envelope[Connection]` with `sockets` as a **list** of records carrying
        a `kind` enum.
  - [ ] `chat` is derived from the bot's `active_engine` — a backend fact, no
        device call. `terminal` appears only when the engine declares
        `WEB_SHELL_OPEN`.
  - [ ] The payload contains **no** `target`, `type`, or bare `token` field; a
        test asserts those keys are absent.
  - [ ] `expires_at` prefers a provider-reported expiry and falls back to
        `now + ttl` for the TTL actually requested — never a hardcoded constant
        independent of it.
  - [ ] The bot is resolved owner-scoped **first**, and the resolved owner is
        passed as operator, so the wider public-bot/collaborator check in
        `…/core/devices/services/device_service.py:974-1006` cannot widen this
        surface. A test proves a collaborator (non-owner) gets `404`.
  - [ ] A capabilities failure fails the endpoint with the same `409` as every
        other route, rather than silently omitting a socket.
- **Depends on:** Tasks 4, 5, 6

## Task 11: Mount the routers and enforce the path invariant
- **Goal:** Make the six groups reachable, in the right order, under the right
  prefix.
- **Files:**
  - `…/adapters/http/openapi_v1/__init__.py`
  - `tests/community/adapters/http/openapi_v1/engine_runtime/test_routing.py` (new)
- **Done when:**
  - [ ] All five routers added to `_SUBGROUPS` (`openapi_v1/__init__.py:33`),
        above `bots_router`, so `/openapi/v1/bots/mcp` still resolves ahead of
        `/openapi/v1/bots/{bot_id}`.
  - [ ] A test walks **every** route on the five routers and asserts the path
        starts with the literal `/openapi/v1/bots/` — the gateway-routing
        invariant; a route mounted elsewhere is unreachable in production.
  - [ ] A test asserts the total new route count is 16.
  - [ ] A test generates the OpenAPI document and asserts the enums appear as
        `type: string` with the expected `enum` lists — catching the case where
        a model annotates an enum but Pydantic emits a bare string.
- **Depends on:** Tasks 7, 8, 9, 10

## Task 12: Cross-tenant and owner isolation across all 16 routes
- **Goal:** Prove the isolation claim on every route, not a sample.
- **Files:**
  - `tests/community/adapters/http/openapi_v1/engine_runtime/test_tenant_isolation.py` (new)
- **Done when:**
  - [ ] Parametrised over **all 16 routes**: a `bot_id` owned by another caller
        returns a masked `404`, byte-identical to a non-existent bot.
  - [ ] Same for a cross-tenant `bot_id`, against the **real** Track A guard —
        mirror the shape of
        `tests/community/adapters/http/openapi_v1/test_bots_tenant_isolation.py`.
  - [ ] Asserts the transport was **never invoked** for a bot the caller does
        not own — not merely that the status was `404`. The Track A guard cannot
        see a device-side read, so a `404` alone does not prove the forward was
        skipped.
- **Depends on:** Task 11

## Task 13: Tests & Verification
- **Goal:** Ensure the feature meets the spec's acceptance criteria.
- **Files:** `docs/openapi-v1/README.md`, `docs/openapi-v1/README.zh-CN.md`
- **Done when:**
  - [ ] Every acceptance criterion in `spec.md` checks off, including the two
        that no single earlier task owns: error responses expose no internal
        identifiers or credentials, and no new stored records or schema change
        were introduced.
  - [ ] Full `tests/community` green with the internal suite **unmodified**.
  - [ ] `tests/community/architecture/` green.
  - [ ] Both READMEs' Track C board updated from ⬜ TODO to done with the PR
        number, plus a dated changelog line — the README's own standing rule.
- **Depends on:** Task 12

---

## Groups

- **Group A — Shared foundation:** Tasks 1, 2, 3, 4
  - Theme: the contract change, the core relay, and its Protocol/DI wiring —
    everything the six groups sit on. Nothing is callable yet.
- **Group B — Public contract primitives:** Tasks 5, 6
  - Theme: enums, schema-documentation rules, and the engine→envelope error
    mapping. Reviewable independently of any handler.
- **Group C — Endpoint groups:** Tasks 7, 8, 9, 10
  - Theme: the 16 handlers. Each task is one coherent slice and can be reviewed
    on its own diff.
- **Group D — Wiring and isolation:** Tasks 11, 12
  - Theme: mount the surface, pin the `/bots` path invariant, and prove
    isolation on every route.
- **Group E — Verification:** Task 13
  - Theme: final spec acceptance check and board update.

---

## Plan gaps this breakdown surfaced

1. **`session_id` encoding is still undecided.** The plan flags it as a risk;
   Task 7 has to *pick* one. Whoever starts Task 7 should settle it against a
   real engine session id before writing the route, not during review.
2. **The `Page.total` cap has no number.** Task 7 requires "a documented cap" but
   neither spec nor plan states one. Needs a value chosen with the engine's
   session-list behaviour in mind.
3. **`EngineRuntimeRelayProtocol` must match the concrete class exactly.** The
   conformance gate checks full signature equality — parameter names, kinds,
   defaults and coroutine status — which the plan mentions only as "real
   signatures". Task 4 will fail CI if the Protocol drifts by a single default.
