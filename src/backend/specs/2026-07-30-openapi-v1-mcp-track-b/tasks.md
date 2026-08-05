# Tasks: Public API — MCP Category (Track B)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

The order is deliberate: build the shared `core/mcp/` primitives, prove them
behavior-preserving by rewiring the **internal** router (its pinned tests are the
proof), then wire the public surface on top, then isolation + docs. Nothing
public is written until the extraction is green.

---

## Task 1: [x] Extract shared presentation helpers + error types
- **Goal:** Give both surfaces one definition of masking, `extInfo` stripping,
  the network-type allowlist, and the domain errors the flow raises.
- **Files:**
  - `src/agentclaw/community/core/mcp/errors.py` (new)
  - `src/agentclaw/community/core/mcp/presentation.py` (new)
  - `tests/community/core/mcp/test_presentation.py` (new)
- **Done when:**
  - [x] `errors.py` defines `McpServerNotFoundError`, `McpHeadersInvalidError`,
        `McpConfigValueError`, `McpSyncFailedError`, `McpMarketUnavailableError`
        — dependency-free, mirroring `openapi_v1/errors.py`.
  - [x] `presentation.py` defines `ALLOWED_NETWORK_TYPES = ("INTERNET",
        "OFFICE")`, `strip_ext_info` / `strip_ext_info_from_list` (moved verbatim
        from `adapters/http/mcp/router.py:58-85`), `is_network_type_visible`, and
        `mask_api_key(key) -> str | None`.
  - [x] `mask_api_key` reproduces the existing expression exactly for both
        branches — `len > 8` → `key[:4] + "****" + key[-4:]`, else `"****"`,
        `None` → `None` — matching `router.py:308-309` and `:372-373`.
  - [x] Unit tests cover: masking for a long key, a short key (`≤8`), and `None`;
        `extInfo` removed from a tool's `inputSchema.properties` and left intact
        when absent; the network-type rule for INTERNET/OFFICE/neither/empty.
        **15 passed.**
- **Depends on:** —

## Task 2: [x] Extract the config read/write flow
- **Goal:** Lift the internal write orchestration (validate → server-exists →
  write → push → rollback) into a FastAPI-free function both surfaces call.
- **Files:**
  - `src/agentclaw/community/core/mcp/config_flow.py` (new)
  - `tests/community/core/mcp/test_mcp_config_flow.py` (new)
- **Done when:**
  - [x] `read_unified_config(*, user_id, server_code, config_service) ->
        UnifiedConfig` and async `write_unified_config(...)` exist, taking
        already-injected services, raising the Task 1 errors, and returning a
        frozen `UnifiedConfig` whose `api_key` is **already masked** (so no
        caller can reach the raw key).
  - [x] `write_unified_config` preserves the internal ordering from
        `router.py:250-337` exactly: validate values → validate headers →
        `market_service.get_mcp_detail` (missing → `McpServerNotFoundError`,
        *before* any DB write) → `update_user_unified_config` keeping the old row
        → `sync_mcp_detail_to_all_bots` → on failure `rollback_unified_config`
        then raise `McpSyncFailedError`.
  - [x] `write_unified_config` returns the sync results alongside the config, so
        the internal adapter can keep populating `MCPUnifiedConfigData.sync_results`
        (the public handler ignores them).
  - [x] Header validation surfaces the validator's failure as
        `McpHeadersInvalidError`; the `endpoint_env`/`transport_protocol` value
        check remains as the internal path's backstop, raising
        `McpConfigValueError`.
  - [x] Tests (no HTTP): an omitted field is left unchanged (merge, not replace);
        an unknown server code never reaches `update_user_unified_config`;
        rollback restores the prior row on sync failure; rollback **deletes** the
        row when it was newly created (`config_service.py:172-177`). **9 passed.**
  - [x] Also folded in `list_marketplace_servers` / `list_marketplace_tenants`
        (raise `McpMarketUnavailableError` on upstream `success: False`) so the
        internal list/tenant routes and the public handlers share the same
        upstream-failure rule. *(Small addition beyond the plan's named
        functions — same module, same pattern; flagged here.)*
- **Depends on:** Task 1

## Task 3: [x] Rewire the internal router onto the extracted code
- **Goal:** Make `/api/mcp` call the shared flow and helpers, with its HTTP
  contract byte-identical — this is the proof the extraction preserved behavior.
- **Files:**
  - `src/agentclaw/community/adapters/http/mcp/router.py`
  - `src/agentclaw/community/core/mcp/README.md` (context boundary)
- **Done when:**
  - [x] `_remove_ext_info_*` deleted from the router; `strip_ext_info*`,
        `ALLOWED_NETWORK_TYPES`, `is_network_type_visible`, `mask_api_key`
        imported from `core/mcp/presentation.py`.
  - [x] `update_mcp_unified_config` becomes a thin adapter: call
        `write_unified_config`, catch each typed error and re-raise the identical
        `HTTPException` (same status, same `detail` string) it raised before;
        `get_mcp_unified_config` calls `read_unified_config` (message keyed on
        `exists`, not `has_config`, to preserve the empty-row case).
  - [x] `core/mcp/README.md` `## Context Boundary` updated — `MCPMarketService`
        added to `provides`. **No new `internal_dependencies`:** the flow
        *receives* the market/config/sync services as parameters rather than
        importing them, so the arch gate stayed green with no dependency edit
        (the plan's predicted new import did not materialize — receiving beats
        importing).
  - [x] **`test_mcp_config_internal_unchanged.py` and `test_mcp.py` pass
        UNMODIFIED** — 16 passed, neither file touched.
  - [x] `tests/community/architecture/` passes (108 passed); full MCP api+core
        suite 168 passed.
- **Depends on:** Task 2

## Task 4: [x] Public MCP request/response schemas
- **Goal:** Turn the stub models into the strict public contract.
- **Files:** `src/agentclaw/community/adapters/http/openapi_v1/mcp/schemas.py`
- **Done when:**
  - [x] A module-level `_STRICT = ConfigDict(extra="forbid")` is applied to the
        **request** body `McpConfigWrite` (mirroring `bots/schemas.py:16`, which
        guards request bodies — `BotCreate`/`BotUpdate` — and leaves
        server-constructed response models plain). An unknown field is a 422.
  - [x] `McpConfigWrite.sync_mode` is removed.
  - [x] `McpConfigWrite.endpoint_env` is `Literal["PROD", "PRE"] | None` and
        `transport_protocol` is `Literal["SSE", "STREAMABLE_HTTP"] | None` — no
        `DEV`; `None` means "leave unchanged".
  - [x] `McpConfig.api_key` documented as always masked; `McpTenant` documented
        as a marketplace concept distinct from the Avernet isolation tenant.
  - [x] Response models still carry the fields the adapters populate. Package
        imports clean under pytest (113 openapi_v1 tests collect); strict
        behavior (`sync_mode`/`DEV` → 422) verified and covered by Task 5.
- **Depends on:** —

## Task 5: [x] Wire the six public handlers
- **Goal:** Replace the `NotImplementedError` stubs with real handlers on the
  shared flow, owner-scoped via the principal.
- **Files:** `src/agentclaw/community/adapters/http/openapi_v1/mcp/router.py`
- **Done when:**
  - [x] Every handler takes `request: Request`, `PrincipalDep`, its services via
        `Injected`, carries `@envelope_errors`, and opens with
        `owner_id = caller_owner_id(principal)`.
  - [x] `list_mcp_servers` → `list_marketplace_servers(..., keyword,
        network_types=ALLOWED_NETWORK_TYPES)` (upstream failure → 502),
        `page(total, items, request)`. **No per-item `extInfo` strip:** the list
        projects to `McpServer`, which carries no `tools`, so there is nothing to
        strip — kept the list lightweight and dropped the dead call.
  - [x] `get_mcp_server` → `get_mcp_detail`; `None` **or** network-type-invisible
        raise `McpServerNotFoundError` from **one** site, so the two 404 paths are
        indistinguishable (test asserts byte-identical bodies).
  - [x] `list_mcp_tenants` → `list_marketplace_tenants`, mapped to `McpTenant`;
        an upstream `success: False` raises `McpMarketUnavailableError` (→ 502).
  - [x] `check_mcp_permission` → `check_mcp_permission_detail(owner_id,
        server_code)`; **no** `user_id` query parameter (test proves a spoofed
        `?user_id=` is ignored — the principal's owner is used).
  - [x] `get_mcp_config` → `read_unified_config`; `update_mcp_config` →
        `write_unified_config` then re-read for a response equal to a subsequent
        GET; `entity_id = owner_id`, `entity_type = "staff"`; no `IAM_TOKEN`
        cookie handling.
  - [x] Module-private `_to_server` / `_to_server_detail` / `_to_tenant` /
        `_to_config` adapters map MCP Center camelCase → the snake_case models.
  - [x] Endpoint tests (`test_mcp_endpoints.py`, new) cover every handler:
        success shape + envelope `code`/`request_id`; masking long+short keys +
        raw key never in response text; never-configured → `has_config: false`
        (not 404); invisible vs unknown → byte-identical 404; `extInfo` stripped
        on detail; permission from principal only; sync-failure → 502 + rollback;
        `sync_mode`/`DEV` → 422; missing principal → 401. **21 passed; full
        openapi_v1 suite 136 passed.**
- **Depends on:** Tasks 2, 4

## Task 6: [x] Map the MCP domain errors to envelopes
- **Goal:** Every error these handlers raise answers in the envelope; nothing
  reaches the generic 500 fallback.
- **Files:** `src/agentclaw/community/adapters/http/openapi_v1/responses.py`
- **Done when:**
  - [x] `ENVELOPE_ERRORS` gains the five errors with fixed public messages:
        `McpServerNotFoundError`→404 "Not found",
        `McpHeadersInvalidError`→400 "Invalid MCP headers",
        `McpConfigValueError`→400 "Invalid MCP configuration",
        `McpSyncFailedError`→502 "Device sync failed",
        `McpMarketUnavailableError`→502 "MCP service error".
  - [x] They are placed above `BotServiceError` (they share no base with it, so
        order among themselves is free); no message is `str(exc)`, and none
        carries the validator's Chinese text. Trailing comment corrected — the
        MCP entries are a separate hierarchy from `BotServiceError`.
  - [x] A test asserts every mapped error round-trips to its status + fixed
        message, and that the 404 message is byte-identical to the not-found
        the bots surface returns. **18 passed** (test_responses + error_schema).
- **Depends on:** Task 1
- **Note:** Landed before Task 5 (its only dependency is Task 1) so Task 5's
  endpoint tests exercise the mappings.

## Task 7: [x] Cross-tenant isolation against the real Stage 5 guard
- **Goal:** Prove a config in another tenant is invisible and un-overwritable
  through the path the handlers use — end to end, not mocked.
- **Files:** `tests/community/adapters/http/openapi_v1/test_mcp_tenant_isolation.py`
  (new)
- **Done when:**
  - [x] Drives the **flow the handlers call** (`read_unified_config` /
        `write_unified_config`) against a real `MCPConfigService` + real
        `UserMCPConfigRepository` + the real Stage 5 guard over SQLite (only the
        marketplace + device-sync collaborators are mocked). This is a strictly
        stronger proof than testing the repo directly — it covers the exact
        layer the public GET/PUT use.
  - [x] A config written under tenant A is invisible from tenant B (read →
        `has_config: false`, `api_key: None`); a tenant-B write creates B's own
        row rather than overwriting A's (A's stored bytes verified untouched).
  - [x] Two tenants each hold a row for the same `user_id` + `server_code` and
        neither sees or displaces the other (the case the Stage 5 unique-key swap
        made possible — would `IntegrityError` on the old key). **4 passed.**
- **Depends on:** Task 5

## Task 8: [x] Move the handoff board + changelog
- **Goal:** Reflect that mcp Track B has landed, in the same PR that lands it.
- **Files:** `src/backend/docs/openapi-v1/README.md`,
  `src/backend/docs/openapi-v1/README.zh-CN.md`
- **Done when:**
  - [x] The Track B board flips `mcp` to `✅ DONE — PR #610` (both editions);
        Track B counters → 2 of 7, and the (pre-existing-stale) Track A DoD
        counter corrected to 2 of 6 (Stage 5 mcp merged in #564).
  - [x] The `/openapi/v1/bots/mcp/...` vs top-level path note is resolved to the
        nested shape for mcp (decision 1), remaining groups noted as inheriting
        the precedent.
  - [x] A dated changelog line (both editions) records the category, the
        shared-flow extraction, the three decisions, and the preserved fail-open
        permission behavior.
  - [x] The reference-slice recipe (step 8) gains mcp as the second worked
        reference, specifically for the extract-shared-logic pattern.
- **Depends on:** Task 5

## Task 9: [x] Tests & Verification
- **Goal:** Ensure the feature meets every `spec.md` acceptance criterion and
  the internal surface is untouched.
- **Files:** — (runs the suites)
- **Done when:**
  - [x] Every `spec.md` acceptance criterion maps to a passing test — see the
        matrix below. Marketplace / permission / config read+write / isolation /
        error contract / internal surface all covered.
  - [x] The fail-open permission behavior is pinned by a test
        (`test_permission_is_fail_open_by_decision` at the public layer +
        `test_auth_service.test_check_mcp_permission_detail_mcp_center_failure`
        at the service) and noted in the `check_mcp_permission` docstring.
  - [x] The rollback-on-sync-failure path answers 502 and leaves the stored row
        as it was / absent on create —
        `test_put_config_sync_failure_is_502_and_rolls_back`,
        `test_sync_failure_rolls_back_update_and_raises`,
        `test_sync_failure_after_create_rolls_back_as_delete`.
  - [x] Raw API keys (long + short) appear nowhere in a response —
        `test_raw_key_never_appears_in_response_text`, masking tests both surfaces.
  - [x] `test_mcp_config_internal_unchanged.py`, `test_mcp.py` pass UNMODIFIED
        (confirmed via `git diff` — not in the changed-file set);
        `tests/community/architecture/` 108 passed; MCP contract snapshots
        (`test_rule16/17_mcp`, `test_mcp_center/auth`) 10 passed. Broad sweeps:
        `tests/community/adapters/http/` 384, MCP+openapi+isolation suites 338.
        `test_responses.py` is additions-only.
- **Depends on:** Tasks 3, 5, 6, 7, 8

### Acceptance criteria → test matrix
| spec.md criterion | Test |
|---|---|
| List: page+total, keyword filters name/code | `test_list_servers_maps_fields_and_omits_tools`, `test_list_servers_forwards_keyword_and_paging` |
| Only allowed network types visible; hidden==unknown 404 | `test_invisible_server_is_identical_404`, `test_unknown_server_is_404_not_found` |
| Detail includes tools; internal `extInfo` stripped | `test_server_detail_strips_ext_info`, `test_presentation` |
| List tenants (code/name/categories) | `test_list_tenants` |
| Upstream marketplace failure → upstream error (502) | `test_list_servers_upstream_failure_is_502`, `test_tenants_upstream_failure_is_502` |
| Permission: access + level + per-tool | `test_permission_shape` |
| Permission is caller's own only (no id param) | `test_permission_uses_caller_identity_only` |
| Local server permitted without external auth | inherited/unchanged: `test_auth_service` (service layer) |
| Config read: env/transport/headers/has_config | `test_get_config_absent_reports_no_config`, `test_get_config_masks_long_key` |
| API key masked, any length | `test_get_config_masks_long_key`, `test_get_config_masks_short_key`, `test_presentation` |
| Never-configured → has_config:false not 404 | `test_get_config_absent_reports_no_config` |
| Write stores + pushes + returns masked | `test_put_config_success_returns_reread_state` |
| Omitted field unchanged (merge) | `test_write_forwards_params_for_merge_and_push` |
| env/transport validated (bad → caller error) | `test_put_config_rejects_dev_endpoint_env_as_422`, `test_bad_endpoint_env_raises_before_any_write` |
| Headers validated before storing | `test_invalid_headers_raise_before_any_write` |
| Unknown server → 404, nothing stored | `test_put_config_unknown_server_is_404`, `test_unknown_server_raises_before_any_write` |
| Push fail → rollback + fail | `test_put_config_sync_failure_is_502_and_rolls_back` (+ 2 flow rollback tests) |
| Unknown/immutable field rejected (422) | `test_put_config_rejects_sync_mode_as_422` |
| Reads/writes scoped to caller identity | `test_put_config_scopes_write_to_caller`, `test_permission_uses_caller_identity_only` |
| Foreign tenant invisible + un-overwritable (real guard) | `test_mcp_tenant_isolation` (4 tests) |
| Two tenants same user+server coexist | `test_two_tenants_same_user_and_server_coexist` |
| Every failure enveloped, fixed message | `test_mcp_errors_map_to_status_and_fixed_message` |
| Every domain error mapped (no generic 500 escape) | mapping test + Group B review verdict |
| Internal API shapes unchanged, tests unmodified | `test_mcp_config_internal_unchanged.py` + `test_mcp.py` UNMODIFIED (16), contract snapshots (10) |

---

## Groups

- **Group A — Shared extraction (behavior-preserving):** Tasks 1, 2, 3
  - Theme: Lift masking / presentation / errors and the write-flow into
    `core/mcp/`, and prove it by rewiring the internal router with its pinned
    tests unmodified. Lands as a self-contained refactor even before any public
    handler exists.
- **Group B — Public MCP surface:** Tasks 4, 5, 6
  - Theme: The strict public schemas, the six wired handlers, and the error
    mapping — the category becomes callable (modulo the 401 auth stub).
- **Group C — Isolation & docs:** Tasks 7, 8
  - Theme: Prove tenant safety against the real guard and move the handoff board
    in the same PR.
- **Group D — Verification:** Task 9
  - Theme: Final spec acceptance check + internal-surface regression gate.
