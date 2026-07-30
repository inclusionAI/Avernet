# Tasks: Public API — Engine Runtime Surface (Track C)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

All paths are relative to `src/backend/`. Source package is
`src/agentclaw/community/`, abbreviated `…/` below.

---

## Task 1: Document the two new HTTP statuses this track can return  `[x]`
- **Goal:** Declare `501`/`504` for the engine-runtime groups. **Originally also
  added `Envelope.warning`; that was reversed — see the note below.**
- **Files:**
  - `…/adapters/http/openapi_v1/contracts.py`
  - `tests/community/adapters/http/openapi_v1/test_responses.py`
- **Done when:**
  - [x] `Envelope` is **unchanged** — four fields, as before this track.
  - [x] `501`/`504` live in `ENGINE_RUNTIME_ERROR_RESPONSES`, a per-group
        superset of `ERROR_RESPONSES`, **not** in the surface-wide dict.
  - [x] A regression guard pins the envelope to its four documented fields.
  - [x] Existing envelope tests pass unmodified.
- **Depends on:** —
- **Reversed 2026-07-30 (plan assumption 1):** `Envelope.warning` was added and
  then removed at the owner's call. Only `POST …/sessions` on `claude_code`
  could ever set it — rule C2 keeps the other limited capabilities (MCP, skills)
  off this surface — and that engine's caveat describes how the session key is
  established, not a degraded result. A field permanently empty on 15 of 16
  endpoints and on all six other categories does not justify changing a shared
  contract. Engine caveats are logged in `relay._normalise`;
  `…/engine/capabilities` is where a caller discovers limitations.
  **Net effect: Track C changes nothing outside its own prefix.**
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
  - [x] `models.py` defines `EngineResult(data, total, limited)`, `BotFacts`,
        `ConnectionResult`, `SocketInfo`. (`warning` became the `limited` flag
        in review — the engine's warning text is internal prose and must not
        reach a caller; `BotFacts` was added so the raw bot record, which
        carries device binding internals, never reaches a public handler.)
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
        covered and exempt. Exempt **temporarily** — the first reason written
        here was wrong (singlebox does bind the in-memory transport, and cron
        crosses the same seam with a real flow); the real blocker is that the
        endpoints do not exist until Task 11. Task 12b removes the exemption.
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
        `success: false` case and the `limited`/`total` handling.
- **Depends on:** Task 2
- **Decided while implementing:** `UnknownProviderError` is **not** folded into
  `EngineDeviceNotReadyError`. A binding row naming a provider we don't know is
  bad data on our side; reporting it as retryable would have callers retrying
  something no retry can fix. It propagates to the existing 500 mapping.
  `DeviceAdapterTimeoutError` likewise propagates unwrapped — it is already the
  precise public answer (504), and wrapping would lose it.

## Task 4: Expose the relay as a Service API Protocol and wire DI  `[x]`
- **Goal:** Let routers Inject a Protocol, never the concrete relay.
- **Files:**
  - `…/api/engine_runtime_service.py` (new)
  - `…/di/modules/engine_runtime_module.py` (new)
  - `…/di/container.py`
  - `tests/community/architecture/test_service_api_conformance.py`
- **Done when:**
  - [x] `EngineRuntimeRelayProtocol` is `@runtime_checkable` with **real**
        signatures for `resolve_bot` and `call` (see the adjustment note below).
  - [x] `EngineRuntimeModule` binds concrete → singleton and aliases the
        Protocol, following `…/di/modules/cron_module.py:32-44`.
  - [x] Module registered in `…/di/container.py` alongside `CronModule`
        (`container.py:106`).
  - [x] `(EngineRuntimeRelayProtocol, EngineRuntimeRelay)` added to `_PAIRS` in
        the conformance test, which checks **full signature equality** — names,
        kinds, defaults and coroutine status — so the Protocol must match the
        concrete class exactly.
  - [x] `test_api_layer_is_protocols_only.py` and
        `test_http_adapter_layer_is_http_only.py` pass.
- **Depends on:** Task 3
- **Adjusted while implementing:** the Protocol declares `resolve_bot` + `call`,
  not the planned `capabilities` + `connection`. Those two were going to be
  relay methods, but `capabilities` is just `call()` against one path, and
  `connection` composes device-service output rather than forwarding — it
  belongs in its own service (Task 10), not on the forwarding relay. `resolve_bot`
  is on the Protocol because handlers must branch on bot facts (`bot_type`,
  `active_engine`) *before* deciding whether to forward at all.
- **Also required:** typing the Protocol with `EngineResult` is a new
  `api/ → core/` import, so `api/README.md`'s Context Boundary needed the
  declaration or `test_module_boundaries` fails.

## Task 5: Define the public enums and shared schemas  `[x]`
- **Goal:** One module of enums plus the shared payload models, documented so a
  client generator produces usable types.
- **Files:**
  - `…/adapters/http/openapi_v1/engine_runtime/__init__.py` (new)
  - `…/adapters/http/openapi_v1/engine_runtime/enums.py` (new)
  - `tests/community/adapters/http/openapi_v1/engine_runtime/test_schema_docs.py` (new)
- **Done when:**
  - [x] `SocketKind` (`chat`, `terminal`), `ApprovalMode`
        (`approve`, `on-miss`, `never`), `MessageRole`
        (`user`, `assistant`, `system`, `tool_use`, `tool_result`) all subclass
        `str, Enum`.
  - [x] `EngineName` is **not an enum at all** — see the correction below. The
        bots category's runtime validation against `_get_engine_types()` is
        reused instead.
  - [x] Every enum carries `x-enum-descriptions` covering **every** member.
  - [x] `ApprovalMode` is referenced on request models only; no response model
        annotates it (plan investigation finding 4 — the local stub returns
        `"auto"`).
  - [x] Schema test asserts: every enum is `str`-based, every member has an
        `x-enum-descriptions` entry, every field of every model in this package
        has a non-empty `description`, every model has a schema `description`.
- **Depends on:** —
- **Corrected while implementing:** `EngineName` was planned as the fourth enum,
  "reused from what the bots category already uses". Reading the source, what
  bots uses is `_get_engine_types()`, which reads the **`ENGINE_TYPES`
  environment variable** (`core/workspace/constants.py:17-30`) — deployment
  configuration, not a closed set. A static enum would contradict any deployment
  that configures its own list, and on a response field it would fail closed.
  Engine names stay `str`, validated at runtime exactly as bots does. That makes
  three enums, not four; a test asserts no `EngineName` is exported.

## Task 6: Map the engine's error surface onto the public envelope  `[x]`
- **Goal:** Every failure this track can produce answers as an `Envelope`, in
  the right order.
- **Files:**
  - `…/adapters/http/openapi_v1/responses.py`
  - `tests/community/adapters/http/openapi_v1/test_responses.py`
- **Done when:**
  - [x] `ENVELOPE_ERRORS` gains, all **before** the `BotServiceError` base entry
        at `responses.py:171`:
        `EngineCapabilityUnsupportedError` → `(501, …)`,
        `EngineBotTypeNotSupportedError` → `(501, "Not supported for this bot type")`,
        `EngineDeviceNotReadyError` → `(409, …)`,
        `DeviceAdapterTimeoutError` → `(504, …)`,
        `DeviceAdapterEndpointNotFoundError` → `(501, …)`,
        `EngineUpstreamError` → `(502, …)`,
        `DeviceAdapterHTTPStatusError` → `(502, …)` **last of this group**.
  - [x] Both `501` messages name the capabilities endpoint.
  - [x] Messages are fixed strings — never `str(exc)`.
  - [x] A test asserts ordering: the specific transport errors resolve before
        `DeviceAdapterHTTPStatusError`, and every new entry resolves before
        `BotServiceError`. This change adds two base/leaf pairs at once, which
        is exactly the "map the base class last" trap from the Track B gotchas.
- **Depends on:** Task 2
- **Corrected while implementing:** the plan treated
  `DeviceAdapterEndpointNotFoundError` as a subclass of
  `DeviceAdapterHTTPStatusError` and ordered them accordingly. They are
  **siblings** — `TimeoutError` plus two independent `ValueError` subclasses
  (`plugin_api/device_adapter_transport.py:28-38`). So there is one base/leaf
  pair here (`EngineRuntimeError`), not two. A test now pins the sibling
  relationship, so if the transport ever introduces a hierarchy the missing
  ordering rule fails loudly instead of silently swallowing a leaf.
- **Also:** `EngineResourceNotFoundError` maps to a message byte-identical to
  `BotNotFoundError`'s, asserted by test — otherwise a caller could distinguish
  "this session is gone" from "this bot is not yours".

## Task 7: Sessions group — 7 endpoints  `[x]`
- **Goal:** Wrap the engine's session surface.
- **Files:**
  - `…/adapters/http/openapi_v1/engine_runtime/sessions/{__init__,router,schemas}.py` (new)
  - `tests/community/adapters/http/openapi_v1/engine_runtime/test_sessions.py` (new)
- **Done when:**
  - [x] All 7 routes served under
        `/openapi/v1/bots/{bot_id}/sessions…`, with `PATCH` on the resource
        mapping to the engine's `POST …/{id}/update`.
  - [x] `session_id` encoding **settled by measurement, not choice**: ids are
        used **verbatim**. A colon is legal in a URL path segment (RFC 3986) and
        routes correctly, including with a `/messages` suffix — verified against
        Starlette. Percent-encoded `/` does *not* survive routing, so an id
        containing a slash would be unaddressable; no engine id format produces
        one. No encoding scheme was invented.
  - [x] `Page.total` is exact, via fetch-then-slice as
        `openapi_v1/routines/router.py` does, capped at 500 engine-side items.
        When the cap bites, `total` is a floor and the truncation is **logged**
        rather than silently presented as complete.
  - [x] `extra="forbid"`; `user_id`, `engine`, `agent_id` rejected → `422`.
  - [x] **Gated on `bot_type == "personal"`** (plan assumption 6). All seven
        routes raise `EngineBotTypeNotSupportedError` → `501` on a `service`
        bot, checked **before** any device call. `DeviceContext.bot_type` is
        already populated (`core/devices/services/device_context.py:41`).
        Rationale: the engine drops `user_id` and returns every session on the
        device (`plugins/openclaw/_session.py:125-132`), so on a service bot the
        owner would see other callers' sessions and message history.
  - [x] A test asserts the `501` on a service bot **and** that the transport was
        never invoked for it — the check must precede the forward, not filter
        its result.
  - [x] Success + each mapped error covered, using the in-memory transport.
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

## Task 12b: Add the singlebox E2E flow for engine_runtime
- **Goal:** Remove the module's exemption from the E2E coverage gate.
- **Files:**
  - `tests/community/_flows/engine_runtime/api_lifecycle.py` (new)
  - `tests/community/framework/flow_coverage.py`
- **Done when:**
  - [ ] A flow drives the public engine-runtime endpoints over
        `InMemoryDeviceAdapterTransport`, the same seam
        `tests/community/_flows/cron/api_lifecycle.py` already uses.
  - [ ] `engine_runtime` is **removed** from `SINGLEBOX_E2E_EXEMPT`.
- **Depends on:** Task 11
- **Why this is a task and not an exemption:** the exemption written in Task 2
  claimed singlebox has no transport to offer. That was wrong —
  `di/modules/infrastructure/singlebox/devices.py` binds the in-memory
  transport, and `cron` crosses the identical seam with a real flow. The only
  genuine blocker is that the endpoints do not exist until Task 11. Exempting
  permanently would leave the one module whose entire job is crossing into a
  device outside the gate.

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
- **Group D — Wiring and isolation:** Tasks 11, 12, 12b
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
