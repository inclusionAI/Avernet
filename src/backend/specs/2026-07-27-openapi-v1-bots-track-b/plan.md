# Plan — Public API Bots Category (Track B)

## Approach

The internal `/api/bots` router (`adapters/http/bot_management/router.py`) already
implements every behavior the public surface needs, delegating to
`BotServiceProtocol`, `PolicyServiceProtocol`, `EngineConfigService`, and
`PassportPlugin`. Track B is **faithful re-wiring**: the public handlers call the
same services with the caller's principal as the owner identity, then adapt the
returned dicts into the public `Envelope`/`Page` schemas.

Two structural pieces are shared across all 13 handlers and are built once:

1. **A response helper** (`openapi_v1/responses.py`) that builds `Envelope[T]`
   from a payload + the request's trace id, plus small constructors for the
   created/accepted/deleted cases.
2. **A domain-error → envelope mapping** so every route returns an `Envelope`
   (data null) on the known failure cases rather than an unstructured 500.

Services are obtained exactly as the internal router does — `Injected(Protocol)`
from `agentclaw.community.di` — which is plain FastAPI dependency resolution and
independent of the `require_principal` seam. Identity comes from
`resolve_avernet_tenant(request)` (tenant) and `require_principal` (caller); the
caller's staff id is passed as the `user_id` / `entity_id` / `owner_id` argument
the services already expect. The public handlers **do not** use the internal
`owner_id` query param or the `CollaboratorPermissionInterceptor` — an external
caller acts only as itself.

Because Track A Stage 1 already scopes every bot read/write to the request
tenant (the `do_orm_execute` guard + `get_by_id_and_owner`), a cross-tenant
`{bot_id}` resolves to "not found" through the same `BotNotFoundError` path as a
genuinely missing bot — satisfying the spec's non-distinguishability criterion
with no extra handler logic.

## Files

New:
- `adapters/http/openapi_v1/responses.py` — envelope builders + error mapping.
- `tests/community/adapters/http/openapi_v1/test_bots_endpoints.py` — per-endpoint
  success-shape tests + the cross-tenant non-reachability test.

Changed:
- `adapters/http/openapi_v1/bots/router.py` — replace all 13 stub bodies.
- `adapters/http/openapi_v1/bots/schemas.py` — small adjustments if the
  `cluster_name` decision (below) changes the `Bot` model.

Possibly changed (pending the Passport decision below):
- `core/bot_management/services/bot_service.py` (or a new
  `core/bot_management/create_flow.py`) — extract the create + auth-status
  orchestration currently living in the internal router, so both surfaces call
  one implementation instead of duplicating ~250 lines.

Not touched: Track A mechanism, guards, middleware, `dependencies.py` seams, and
every internal router/service behavior (internal tests stay green, unmodified).

## Response + error contract

`responses.py`:
- `envelope(data, request, *, code=CODE_OK, message="OK") -> Envelope[T]` — reads
  `request.state.trace_id` for `request_id` (falls back to `""` if unset).
- `page(total, items, request) -> Envelope[Page[T]]`.
- Thin wrappers `created(...)` (201000), `accepted(...)` (202000),
  `deleted(request)` → `Envelope[Deleted]`.
- `ENVELOPE_ERRORS: dict[type[Exception], tuple[int, int]]` mapping domain
  errors to `(http_status, business_code)`:
  - `BotNotFoundError` → 404 (also the cross-tenant case)
  - `BotNameExistsError` → 409
  - `BotNameInvalidError` → 400
  - `BotLimitExceededError` / `DeviceLimitError` → 409
  - `BotPermissionError` → 404 (never reveal existence across owners/tenants)
  - `BotInvalidLifecycleStateError` → 409
  - `PassportError` → 502
- A single decorator `@envelope_errors` wraps each handler, catches the mapped
  types, and returns an `Envelope` with `data=None` and the mapped code; unknown
  exceptions propagate to the app's existing 500 handler.

## Per-endpoint wiring

| # | Route | Service call | Adapt |
|---|---|---|---|
| 1 | POST `/bots` | create-flow (see Passport note) | dict→`Bot` (201) or pending→`BotAuthPending` (202) |
| 2 | GET `/bots` | `bot_service.list_bots_by_conditions(...)` (filters) | items→`Bot`, wrap `Page` |
| 3 | GET `/bots/check-name` | `bot_service.check_bot_name_exists(name)` | `exists` (no invert — see decision) |
| 4 | GET `/bots/ceiling` | `PolicyService.get_bots_ceiling(entity_id=principal)` | int→`Ceiling` |
| 5 | GET `/bots/{id}` | `bot_service.get_bot(id, user_id=principal)` | dict→`Bot` |
| 6 | PUT `/bots/{id}` | `bot_service.update_bot(id, user_id=principal, ...)` | dict→`Bot` |
| 7 | DELETE `/bots/{id}` | `bot_service.delete_bot(id, user_id=principal)` | bool→`Deleted` |
| 8 | POST `/bots/{id}/restart` | `bot_service.restart_bot(id, user_id=principal)` | dict→`Bot` |
| 9 | GET `/bots/{id}/auth-status` | create-flow `query_auth_status` (Passport note) | →`BotAuthStatus` |
| 10 | GET `/bots/{id}/status` | `bot_service.get_bot(...)` + assemble | →`BotStatus` |
| 11 | GET `/bots/{id}/passport` | `bot_service.get_bot(...)` guard + `PassportPlugin.query_agent_passport(...)` | →`Passport` |
| 12 | GET `/bots/{id}/engine-config` | `get_bot` prelude + `EngineConfigService.read_bot_config(...)` (async) | dict pass-through |
| 13 | PUT `/bots/{id}/engine-config` | `get_bot` prelude + `EngineConfigService.write_bot_config(...)` (async) | dict pass-through |

`BotModel.to_dict()` → `Bot` field map: `bot_id`, `bot_name`, `bot_desc`,
`engine ← active_engine`, `bot_type`, `status`, `owner_entity_id ← owner_id`.
The adapter lives as one function `_to_bot(d: dict) -> Bot` in the router.

## Decisions this plan forces (need your call)

1. **`cluster_name` has no backing field.** The public `Bot` schema requires
   `cluster_name`, but no column, `create_bot` param, or `update_bot` param
   carries it anywhere in the internal path. Options:
   (a) **drop `cluster_name` from `Bot`/`BotCreate`** (recommended — nothing
   populates it); (b) source it from the device-binding's cluster on read and
   ignore on write; (c) stash it in `ext`. Recommend (a) unless the gateway
   contract needs it.

2. **List filters `keyword`/`engine`/`status`.** `bot_service.list_bots` doesn't
   filter by these. `list_bots_by_conditions` covers `bot_name` (≈keyword) and
   more, but not `engine`/`status` directly. Plan: use `list_bots_by_conditions`
   for keyword + pagination and apply `engine`/`status` as an in-handler filter
   on the returned page, OR (cleaner) add the two filter params to the existing
   service query. Recommend extending the service query so pagination totals stay
   correct; flag if you'd rather not touch the service.

3. **Passport-entangled endpoints (create / auth-status).** The create and
   auth-status orchestration (~250 lines: preflight → passport apply → 202
   branch → create_bot → relationship) lives in the **internal router**, not a
   service. Options: (a) **extract it into a shared `create_flow` helper** both
   routers call (recommended — no duplication, internal behavior preserved by
   delegating); (b) duplicate the orchestration in the public handler. (a) is
   more code-movement but avoids two copies drifting. Recommend (a).

4. **`check-name` semantics.** The stub `NameCheck` is `{name, exists}` (not
   `available`). Keeping `exists` matches the internal `check_bot_name_exists`
   return directly; no inversion. Confirm the field name stays `exists`.

## Test strategy

- One happy-path test per endpoint asserting: status code, envelope `code`, and
  the `data` shape (spot-checking mapped fields).
- Cross-tenant test: create a bot under tenant A (via the guard's tenant scope),
  then with the request bound to tenant B assert GET/PUT/DELETE/`{id}`-routes
  all return 404 — never the bot.
- List filter test: seed bots differing by `engine`/`status`/name; assert each
  filter narrows and `total` is accurate.
- Internal suite runs unmodified and green (regression guard on the extracted
  create-flow, if we take decision 3a).
- Test client wires `resolve_avernet_tenant` to a controllable tenant so both
  tenants can be exercised while the real authenticator is still a stub.

## Out of scope (unchanged from spec)

Real authenticator, other six categories, Track A changes, F2 indexes,
background-job tenant review.
