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
- `adapters/http/openapi_v1/responses.py` — envelope builders + error mapping +
  the `cluster_for_engine` / `validate_engine_cluster` helpers (or a sibling
  `clusters.py`).
- `core/bot_management/create_flow.py` — the create + auth-status orchestration
  extracted from the internal router (decision 3), called by both surfaces.
- `tests/community/adapters/http/openapi_v1/test_bots_endpoints.py` — per-endpoint
  success-shape tests + the cross-tenant non-reachability test.

Changed:
- `adapters/http/openapi_v1/bots/router.py` — replace all 13 stub bodies.
- `adapters/http/openapi_v1/bots/schemas.py` — `cluster_name` becomes
  `Literal["ACRA", "ANDC"]` on `Bot`/`BotCreate` with the combination rule in the
  field descriptions.
- `adapters/http/bot_management/router.py` — the internal create / auth-status
  handlers delegate to `create_flow` instead of inlining it (behavior-preserving).
- `core/bot_management/services/bot_service.py` — additive `engine`/`status`
  filter params on the list-by-conditions query (decision 2).

Not touched: Track A mechanism, guards, middleware, `dependencies.py` seams. All
internal router/service behavior stays green under the unmodified internal suite;
the only internal edits (create-flow extraction, additive list filters) are
covered by that suite as regression guards.

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
`engine ← active_engine`, `bot_type`, `status`, `owner_entity_id ← owner_id`,
`cluster_name ← cluster_for_engine(active_engine)` (see below).
The adapter lives as one function `_to_bot(d: dict) -> Bot` in the router.

### cluster_name — validated, engine-derived

`cluster_name` is a public enum with two values, in strict bijection with the
engine:

- `ANDC` ⟺ engine `teclaw` (the `teclaw` device provider / `TECLAW_PROVIDER_TYPE`
  in `deploy/provider_resolver.py`).
- `ACRA` ⟺ every other engine (the ARCA/baas default provider).

A shared helper in `responses.py` (or a small `clusters.py`) owns both directions
so the rule lives in one place:
- `cluster_for_engine(engine) -> "ANDC" | "ACRA"` — used by `_to_bot` on read.
- `validate_engine_cluster(engine, cluster) -> None` — raises a
  `ClusterMismatchError` (→ 400) when the pair violates the bijection; used on
  create.

`Bot.cluster_name` and `BotCreate.cluster_name` become a constrained
`Literal["ACRA", "ANDC"]`. On create, the handler validates the pair, then passes
only `engine`/`engine_type` down to `create_bot` (the provider is still resolved
internally by baas from the container — `cluster_name` is a validated public
view, not a new provisioning input, so the create internals are untouched). The
OpenAPI doc advertises the enum + the combination rule in the field
descriptions so callers learn valid values and valid pairings from the contract.

## Resolved decisions

1. **`cluster_name` — validated, engine-derived (`ACRA`/`ANDC`).** Kept as a
   public enum in strict bijection with the engine (`ANDC` ⟺ `teclaw`, `ACRA` ⟺
   everything else). Derived from `active_engine` on read; validated on create
   (mismatch → 400). Not threaded into provisioning — the provider is still
   resolved internally. See the "cluster_name" subsection above.

2. **List filters `keyword`/`engine`/`status` — extend the service query.** Add
   `engine`/`status` (and confirm `keyword`) filter params to
   `bot_service.list_bots_by_conditions` (additive; existing callers pass
   nothing new and see identical behavior) so pagination `total` stays exact.

3. **Passport-entangled endpoints — extract to a shared helper.** Move the
   create + auth-status orchestration out of the internal router into a
   `core/bot_management` create-flow module both routers call. The internal
   router delegates to it, preserving its behavior (guarded by the unmodified
   internal suite); the public handler calls the same entry point.

4. **`check-name` semantics — keep `{name, exists}`.** Matches the internal
   `check_bot_name_exists` return directly; no inversion to `available`.

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
