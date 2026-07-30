# Plan: Public API — MCP Category (Track B)

## Approach

Replace the six stub handlers in `openapi_v1/mcp/router.py` with real ones that
obtain the existing MCP services via `Injected` and wrap results in the shared
`Envelope`/`Page` builders — the same shape the bots slice established.

The internal `/api/mcp` router carries real logic inside its handler bodies
(validation, the write→push→rollback sequence, API-key masking, `extInfo`
stripping, the network-type allowlist). Copying it into the public handlers
would give us two versions of a credential-write flow. Instead that logic moves
into `core/mcp/`, request-agnostic and raising typed domain errors, and **both**
routers call it — the pattern `core/bot_management/create_flow.py` established
in #494. The internal router keeps its `HTTPException` shapes by catching the
new errors at its boundary, which is how the extraction is proven
behavior-preserving: `test_mcp_config_internal_unchanged.py` pins its exact JSON
bodies and passes unmodified.

Tenant scoping needs no work here — `AvernetTenantMiddleware` binds the request
tenant and the Stage 5 guard (merged, `c8d1fb1`) filters every ORM read and
write beneath the services. Owner scoping is `caller_owner_id(principal)`,
which replaces the internal surface's `user.staffId`.

## Affected Components

- `adapters/http/openapi_v1/mcp/` — the six public handlers and their
  request/response models. The bulk of the change.
- `adapters/http/openapi_v1/responses.py` — this category's domain errors added
  to `ENVELOPE_ERRORS`.
- `core/mcp/` (new modules) — the extracted, request-agnostic config-write
  flow, marketplace presentation rules, masking, and the error types.
- `adapters/http/mcp/router.py` — internal router rewired to call the extracted
  code; its HTTP contract unchanged.
- `docs/openapi-v1/README.md` + `README.zh-CN.md` — status board, changelog.

Not touched: `core/mcp/services/*` service bodies, the repositories, the
models, `di/modules/mcp_module.py`, and `AvernetTenantMiddleware`.

## Data Model Changes

**None.** Stage 5 (#564) added `avernet_tenant` to `ac_user_mcp_config` and
`ac_bot_mcp_call_config`, registered the guards, and replaced the unique key
with `(avernet_tenant, user_id, server_code, env)`. No column, no guard, no DDL
in this change.

## API / Interface Changes

### Endpoints (paths unchanged from the stubs — decision 1 in `spec.md`)

| Method | Path | Success |
|---|---|---|
| GET | `/openapi/v1/bots/mcp/servers` | `Envelope[Page[McpServer]]` |
| GET | `/openapi/v1/bots/mcp/tenants` | `Envelope[list[McpTenant]]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}` | `Envelope[McpServerDetail]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/permissions` | `Envelope[McpPermission]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/config` | `Envelope[McpConfig]` |
| PUT | `/openapi/v1/bots/mcp/servers/{server_code}/config` | `Envelope[McpConfig]` |

`GET /servers` takes `page`/`page_size` via `PageParamsDep`
(`contracts.py:100-118`) and an optional `keyword`, forwarded as MCP Center's
`search_key`.

### Schema changes (`openapi_v1/mcp/schemas.py`)

- **All models gain `model_config = ConfigDict(extra="forbid")`.** Per the bots
  slice's rule (`docs/openapi-v1/README.md`, Track B recipe step 5), an unknown
  or unsupported field must be a 422, not a silent drop.
- **`McpConfigWrite.sync_mode` is removed** (`schemas.py:60`). Decision 3 in
  `spec.md`: no single-device push path exists.
- **`McpConfigWrite.endpoint_env`** becomes `Literal["PROD", "PRE"] | None` and
  **`transport_protocol`** `Literal["SSE", "STREAMABLE_HTTP"] | None`, matching
  the values the internal route validates at `adapters/http/mcp/router.py:251-258`.
  This deliberately moves enum validation into the request model so the caller
  gets a field-level 422 (already enveloped by the app-level handler, and
  already declared in `ERROR_RESPONSES`) instead of a fixed 400 string. It is
  the one place the public surface answers *better* than the internal one; the
  core flow keeps its own check as the internal path's backstop.
  - Note: the internal route upper-cases `transport_protocol` before validating
    (`router.py:256`). The public model is strict — `sse` is a 422, not a silent
    normalization — because `extra="forbid"` reasoning applies to values too.
- **`McpConfig.api_key`** documented as always masked.

### New domain errors (`core/mcp/errors.py`)

Raised by the extracted flow, mapped by each surface:

| Error | Public (`ENVELOPE_ERRORS`) | Internal (preserved) |
|---|---|---|
| `McpServerNotFoundError` | 404 `"Not found"` | 404 `f"MCP server {code} not found"` |
| `McpHeadersInvalidError` | 400 `"Invalid MCP headers"` | 400 `<validator's message>` |
| `McpConfigValueError` | 400 `"Invalid MCP configuration"` | 400 `<field message>` |
| `McpSyncFailedError` | 502 `"Device sync failed"` | 500 `<sync error>` |
| `McpMarketUnavailableError` | 502 `"MCP service error"` | 500 `<upstream message>` |

Public messages are fixed and carry no identifier — `validate_headers_for_mcp`
returns Chinese internal-language text (`config_service.py:80`), which is
exactly what the fixed-message rule exists to stop.

The public/internal status divergence on the last two rows is intentional: 502
is the correct class for a downstream failure and matches how the bots slice
mapped `PassportError` and `ConnInfoBuildError` (`responses.py:118,131`). The
internal surface keeps 500 because its tests pin it.

## Key Files & Functions

### New — `core/mcp/`

- **`core/mcp/errors.py`** (new) — the five error types above. Dependency-free,
  mirroring `openapi_v1/errors.py`.
- **`core/mcp/presentation.py`** (new) — marketplace rules both surfaces apply:
  - `ALLOWED_NETWORK_TYPES = ("INTERNET", "OFFICE")` — today duplicated at
    `adapters/http/mcp/router.py:108` and `:162`.
  - `strip_ext_info(mcp: dict) -> dict` and `strip_ext_info_from_list(...)` —
    moved verbatim from `adapters/http/mcp/router.py:58-85`.
  - `is_network_type_visible(detail: dict) -> bool` — the `:163-166` rule.
  - `mask_api_key(key: str | None) -> str | None` — the masking expression
    duplicated at `:308-309` and `:372-373`. One definition, so the two
    surfaces cannot drift on how much of a credential they reveal.
- **`core/mcp/config_flow.py`** (new) — the write orchestration lifted from
  `adapters/http/mcp/router.py:250-337`, FastAPI-free:
  ```python
  def read_unified_config(*, user_id, server_code, config_service) -> UnifiedConfig
  async def write_unified_config(
      *, user_id, server_code, entity_id, entity_type,
      api_key, headers, endpoint_env, transport_protocol,
      config_service, market_service, sync_service,
  ) -> UnifiedConfig
  ```
  `write_unified_config` keeps the internal ordering exactly: validate values →
  validate headers → confirm the server exists (external check *before* the
  write, so a bad code never touches the DB) → `update_user_unified_config`
  keeping the old row → `sync_mcp_detail_to_all_bots` → on failure
  `rollback_unified_config` and raise `McpSyncFailedError`. `UnifiedConfig` is a
  frozen dataclass with the key already masked, so no caller can forget.

### Modified — public surface

- **`openapi_v1/mcp/router.py`** (:36-91, all six stubs) — real handlers. Each
  takes `request: Request` (required by `@envelope_errors`), `PrincipalDep`,
  and its services via `Injected`; each opens with
  `owner_id = caller_owner_id(principal)`.
  - `list_mcp_servers` — `market_service.get_mcp_list(page_num=…, page_size=…,
    search_key=keyword, network_types=ALLOWED_NETWORK_TYPES)`, then
    `strip_ext_info_from_list`, then `page(total, items, request)`.
  - `get_mcp_server` — `get_mcp_detail`; `None` **or** network-type-invisible →
    `McpServerNotFoundError` (one raise site, so the two paths are
    indistinguishable, per `spec.md`).
  - `check_mcp_permission` — `auth_service.check_mcp_permission_detail(owner_id,
    server_code)`. `owner_id` comes from the principal; the internal route's
    `user_id` query parameter (`adapters/http/mcp/router.py:173`) is **not**
    exposed.
  - `get_mcp_config` / `update_mcp_config` — delegate to `config_flow`.
    `entity_id = owner_id`, `entity_type = "staff"`, matching
    `_get_path_params`'s defaults (`:52-53`) and how the public bots create path
    passes `entity_id=owner_id` (`bots/router.py:253`).
  - No `IAM_TOKEN` cookie handling. The internal detail route exchanges a
    browser cookie for a live `tools/list` (`:152-158`); a registered-tenant
    caller has no such cookie, and forwarding one below the adapter boundary is
    the same thing the bots slice refused at `bots/router.py:159-175`. Public
    detail returns MCP Center data.
- **`openapi_v1/mcp/schemas.py`** — as described above, plus `_to_server` /
  `_to_server_detail` / `_to_tenant` adapters (module-private in `router.py`,
  mirroring `bots/router.py:93-105`) mapping MCP Center's camelCase
  (`serverCode`, `networkTypes`, `transportProtocol`) onto the snake_case public
  models. Tenant keys `code`/`name`/`categories` are confirmed by
  `tests/community/contracts/gateway/schema_snapshots/response_data/rule16_GET_api_mcp_tenants.json`.
- **`openapi_v1/responses.py:106-143`** — add the five errors. Placement rule
  from the bots gotchas: these are a flat set with no shared base, so ordering
  among them is free, but they all go **above** `BotServiceError`.

### Modified — internal surface (behavior-preserving)

- **`adapters/http/mcp/router.py`**
  - `:58-85` — `_remove_ext_info_*` deleted; imports from `core/mcp/presentation.py`.
  - `:108-115`, `:162-166` — allowlist and visibility rule from the same module.
  - `:237-344` — `update_mcp_unified_config` becomes a thin adapter: call
    `write_unified_config`, catch the typed errors, re-raise the identical
    `HTTPException`s. `sync_results` are still needed for its response
    (`MCPUnifiedConfigData.sync_results`), so `write_unified_config` returns
    them alongside the config; the public handler ignores them.
  - `:347-386` — `get_mcp_unified_config` calls `read_unified_config`.
  - `:307-309`, `:370-373` — masking via `mask_api_key`.

### Architecture gate

New cross-module imports must be declared before `tests/community/architecture/`
will pass — the Stage 1 gotchas record this failing CI twice:
- `core/mcp/README.md` `## Context Boundary` — add `MCPMarketService` to
  `provides` (the flow now consumes it) and any new `internal_dependencies`.
- `adapters/http/openapi_v1/` gains an import of `core.mcp.*`; check that
  module's README the same way.

## Dependencies

None new. No package, no version bump, no new internal service. `MCPMarketService`,
`MCPAuthService`, `MCPConfigService` and `MCPSyncService` are already bound in
`di/modules/mcp_module.py` and already injected by the internal router.

## Risks & Mitigations

- **Risk:** The extraction silently changes internal behavior — the highest-cost
  failure here, since the internal MCP config API is live.
  **Mitigation:** `test_mcp_config_internal_unchanged.py` (283 lines, added by
  #564) pins the exact JSON of GET and POST, the masking of a short key, the
  400 detail string, and tenant stamping, through the **real** service and
  repository. It must pass unmodified — that is the acceptance test for the
  extraction, and it exists precisely because `test_mcp.py` mocks the services
  and cannot see this layer.

- **Risk:** A credential leaks in full through a response, a log, or an error.
  **Mitigation:** `mask_api_key` is the only formatter and `UnifiedConfig`
  carries the key already masked, so a handler cannot reach the raw value to
  return it. A test asserts the raw key appears nowhere in the response text of
  either surface, for both a long and a short key.

- **Risk:** The rollback path is the least-tested branch in the internal flow and
  now becomes a public guarantee.
  **Mitigation:** A dedicated test drives a sync failure and asserts both that
  `rollback_unified_config` restored the prior row and that the response is 502
  — plus the create case, where rollback means *deleting* the row
  (`config_service.py:172-177`) rather than restoring one.

- **Risk:** Permission checks fail open on a marketplace error
  (`auth_service.py:67-73` returns `has_permission: True`), so an upstream
  outage answers "yes" to every external caller.
  **Mitigation:** Preserved deliberately (`spec.md` Open Question 1,
  recommendation accepted): this endpoint is advisory and the MCP server itself
  enforces. Pinned by a test so the behavior is a recorded decision rather than
  an accident, and noted in the handler docstring.

- **Risk:** Public `PUT config` pushes to devices — a write with real side
  effects on a surface whose caller identity is still a stub.
  **Mitigation:** No mitigation needed today: `require_principal` returns `None`,
  so every real request is a 401 before reaching the handler. Recorded because
  it becomes live the moment the auth workstream lands, and it is the strongest
  reason not to widen this category's write surface.

- **Risk:** Route shadowing — `/servers/{server_code}` swallowing a literal like
  `/tenants`.
  **Mitigation:** `/tenants` sits on a different path segment than
  `/servers/...`, so there is no overlap. The group-level ordering that *does*
  matter (`mcp` before the bots `{bot_id}` wildcard) is already correct in
  `openapi_v1/__init__.py:34-41` and this change does not touch it.

## Alternatives Considered

- **Copy the internal handler bodies into the public handlers.** Fastest, and
  wrong for exactly the reason #494 recorded: two copies of the create flow
  would have drifted within a release. This flow writes credentials and rolls
  back — the worst candidate for a second copy.
- **Extract into a new service class rather than module-level functions.**
  Would need DI registration, a Protocol in `api/`, and an entry in the Service
  API conformance gate, for orchestration with no state. `create_flow.py` set
  the precedent of plain functions taking already-injected services; following
  it keeps the two extractions shaped alike.
- **Validate `endpoint_env` in the core flow only, answering 400 like internal.**
  Consistent across surfaces, but throws away the structured field error the
  public envelope already documents for 422, and means a typo comes back as a
  fixed string that doesn't say which of the two fields was wrong. Both checks
  exist; the model's runs first on the public path.
- **Expose `sync_results` in the public response.** Rejected with decision 2 —
  it presumes the partial-success model that #560 has not ruled on, and once
  published it cannot be withdrawn without a breaking change.
- **Move the paths to top-level `/openapi/v1/mcp`.** Considered and rejected by
  the owner (decision 1); the router stays authoritative.

## Rollout

No flag, no migration, no ordering constraint. The public surface answers 401 to
every real request until the auth workstream replaces `require_principal`, so
these handlers are unreachable in production on merge — the same state bots has
been in since #494. The internal surface changes behavior not at all.

Ship as one PR (#610), with the handoff board and changelog moved in the same
change per the README's standing rule.

## Test Strategy

New files under `tests/community/adapters/http/openapi_v1/`, following the bots
harness (a minimal FastAPI app, services bound to mocks through the injector,
`require_principal` overridden per test):

1. **`test_mcp_endpoints.py`** — all six handlers. Per endpoint: success shape,
   the envelope's `code`/`request_id`, and each mapped failure. Specifically:
   masking for long and short keys; a never-configured server reporting
   `has_config: false` rather than 404; a network-type-invisible server and an
   unknown code returning byte-identical 404s; `extInfo` stripped from detail
   and list; permission derived from the principal with no way to pass another
   identity; `sync_mode` and any unknown field rejected as 422; missing
   principal → 401.
2. **`test_mcp_config_flow.py`** — the extracted flow directly, no HTTP: merge
   semantics (an omitted field is unchanged), the ordering guarantee (an unknown
   server code never reaches the repository), rollback-to-previous on sync
   failure, and rollback-as-delete when the row was newly created.
3. **`test_mcp_tenant_isolation.py`** — against the **real** Stage 5 guard with
   a real `UserMCPConfigRepository` over SQLite, in the shape of
   `test_bots_tenant_isolation.py`: a config written under tenant A is invisible
   from tenant B; a tenant-B write creates its own row instead of overwriting;
   both tenants hold a row for the same `user_id` + `server_code` (the case the
   Stage 5 unique-key swap made possible) and neither displaces the other.
4. **`test_mcp_presentation.py`** — masking, `extInfo` stripping, and the
   network-type rule as pure units.

Regression gates that must pass **unmodified**:
- `tests/community/api/mcp/routers/test_mcp_config_internal_unchanged.py`
- `tests/community/api/mcp/routers/test_mcp.py`
- `tests/community/architecture/` (run after the README boundary edits)
- the full `tests/community` suite

Not covered: a live MCP Center or a real device push. Both are external
dependencies the existing suites already mock, and this change adds no new
outbound call.
