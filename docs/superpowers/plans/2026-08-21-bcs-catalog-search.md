# BCS Catalog Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `GET /openapi/v1/bots/catalog/search` query BCS `/v2/bots/search` for the requested page, then return only the current page's exact `(bot_id, entity_id)` matches from Backend.

**Architecture:** The BCS adapter uses the existing BCS-qualified `HttpClient` and a fixed relative path. It maps the OpenAPI query `search/page/page_size` to BCS `q/offset/limit` and fixes `tc_bot=true`, parses each BCS `bot_uuid` as `<bot_id>:<entity_id>`, and exposes only typed addresses to the core service. The core service uses the existing tenant-scoped ORM repository pair lookup, preserves BCS page order, and reports the count of joins in the current page as `total`.

**Tech Stack:** FastAPI, Python, Injector, typed `HttpClient`, SQLAlchemy ORM, pytest.

**Spec:** `spec/pipeline/openapi-bot-public-bcs-join/001-spec-output.md`

## Global Constraints

- Do not add or expose `binding_id`, device data, BCS raw fields, tokens, environments, or BCS caller headers.
- Use only the configured BCS-qualified `HttpClient` and the constant relative path `/v2/bots/search`; do not construct URLs from request data.
- Map `page` to `offset=(page-1)*page_size`, `page_size` to `limit`, non-empty `search` to `q`, and always send `tc_bot=true`; do not pass visibility, status, friendship, or identity filters to BCS.
- Parse BCS `bot_uuid` only as the stable `<bot_id>:<entity_id>` pair. Reject malformed or non-Bot BCS records without returning a partial page.
- Backend remains authoritative for public Bot fields and uses the existing tenant-scoped ORM pair lookup. `total` is the current page's joined count, not BCS's cross-page `total`.
- Keep legacy `/api/v1/bot-public/search` Backend-only, keep Discover and `bot_discover_service.py` unchanged, and do not modify, stage, or remove `.superpowers/`.
- Logs contain only request ID, counts, and failure categories.

---

### Task 1: Define and test the BCS page adapter

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_public/catalog_metadata.py`
- Modify: `src/backend/src/agentclaw/community/core/bot_public/services/bot_catalog_metadata_service.py`
- Test: `src/backend/tests/community/core/bot_public/test_bot_catalog_metadata_service.py`

**Interfaces:**
- Consumes: `HttpClient[QUALIFIER_BCN]`, `BotCatalogCaller`, request trace ID.
- Produces: `BotCatalogMetadataServiceProtocol.search_public_bot_metadata(search, page, page_size, caller, request_id) -> Sequence[BotCatalogMetadata]`.

- [ ] **Step 1: Write failing adapter tests**

```python
def test_bcs_catalog_search_maps_current_page_and_parses_exact_address():
    result = service.search_public_bot_metadata(
        search="agent", page=2, page_size=20, caller=caller, request_id="trace"
    )
    assert result == [BotCatalogMetadata(BotCatalogAddress("bot-1", "owner-1"), "bot")]
    assert client.calls_to("get")[0].kwargs["params"] == {
        "q": "agent", "offset": 20, "limit": 20, "tc_bot": True
    }
```

Add independent tests that a blank search omits `q`, and malformed `bot_uuid`, duplicate addresses, non-Bot records, malformed JSON, or upstream HTTP failure raises `BotCatalogMetadataUnavailableError` without exposing upstream detail.

- [ ] **Step 2: Run the adapter tests and verify RED**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_bot_catalog_metadata_service.py -q`

Expected: FAIL because the protocol and real BCS adapter do not yet support page search.

- [ ] **Step 3: Implement the minimum typed adapter**

```python
response = self._http.get(
    "/v2/bots/search", params=params, timeout=self._timeout
)
response.raise_for_status()
```

Use a fixed client/relative path, validate root `items`, require `actor_kind == "bot"`, split `bot_uuid.rsplit(":", 1)`, and emit a low-sensitivity result/failure log. Convert all transport and shape failures to the typed unavailable error.

- [ ] **Step 4: Run the adapter tests and verify GREEN**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_bot_catalog_metadata_service.py -q`

Expected: PASS.

### Task 2: Join the current BCS page in Backend

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py`
- Test: `src/backend/tests/community/core/bot_public/test_bot_public_service.py`

**Interfaces:**
- Consumes: ordered `Sequence[BotCatalogMetadata]` from Task 1 and `BotRepository.list_public_bots_by_owner_bot_pairs`.
- Produces: `{"total": len(joined_current_page), "items": joined_current_page}` in BCS result order.

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_catalog_search_joins_only_the_current_bcs_page_and_reports_its_count():
    metadata.search_public_bot_metadata.return_value = [
        BotCatalogMetadata(BotCatalogAddress("bot-1", "owner-1"), "bot"),
        BotCatalogMetadata(BotCatalogAddress("missing", "owner-2"), "bot"),
    ]
    repository.list_public_bots_by_owner_bot_pairs.return_value = [backend_bot("bot-1", "owner-1")]

    result = service.search_catalog_public_bots_by_keyword(search="agent", page=3, page_size=20, caller=caller, request_id="trace")

    assert result["total"] == 1
    assert [item["bot_id"] for item in result["items"]] == ["bot-1"]
```

Add tests for BCS order restoration after unordered ORM rows, same `bot_id` under a different `entity_id` not joining, BCS unavailable translating to the fixed public 502 error, and legacy search never calling BCS.

- [ ] **Step 2: Run the service tests and verify RED**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_bot_public_service.py -q`

Expected: FAIL because current code asks Backend for all candidates first and computes global joined pagination.

- [ ] **Step 3: Implement the minimum current-page join**

Call the metadata port before the repository. Request only the ordered unique address pairs from `BotRepository.list_public_bots_by_owner_bot_pairs`, map Backend results by exact address, restore BCS order, retain current sensitive-value clearing as defense in depth, and do not re-slice the page.

- [ ] **Step 4: Run the service tests and verify GREEN**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_bot_public_service.py -q`

Expected: PASS.

### Task 3: Wire DI and update the published contract

**Files:**
- Modify: `src/backend/src/agentclaw/community/di/modules/bot_public_module.py`
- Modify: `src/backend/tests/community/endpoints/test_openapi_bot_public_catalog.py`
- Modify: `src/backend/docs/openapi-v1/bot-public-catalog-api.zh-CN.md`
- Modify: `src/backend/docs/openapi-v1/README.md`
- Modify: `spec/pipeline/openapi-bot-public-bcs-join/001-spec-output.md`
- Modify: `spec/pipeline/openapi-bot-public-bcs-join/002-code-report.md`
- Modify: `/Users/helloworld/Desktop/codes/teamclaw/log.md`

**Interfaces:**
- Consumes: BCS adapter from Task 1.
- Produces: all profiles resolve a real BCS catalog adapter; `/search` returns 200 for an available BCS page even when the join is empty, and 502 only for BCS failure.

- [ ] **Step 1: Write failing DI/endpoint tests**

```python
def test_test_profile_binds_catalog_port_to_bcs_adapter():
    port = build_injector(profile=DeployProfile.TEST).get(BotCatalogMetadataServiceProtocol)
    assert isinstance(port, BcnBotCatalogMetadataService)
```

Keep the endpoint test at the HTTP boundary and add an available empty-page assertion so an empty BCS page is not confused with the former unconfigured 502 mode.

- [ ] **Step 2: Run DI/endpoint tests and verify RED**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_bot_catalog_metadata_service.py tests/community/endpoints -q -k 'catalog'`

Expected: FAIL because DI still uses `UnavailableBotCatalogMetadataService`.

- [ ] **Step 3: Make the narrow DI and documentation changes**

Bind the port to the BCS adapter using the existing BCS-qualified client. Update documentation from “fixed 502” to “current BCS page is queried with `q/offset/limit`; Backend returns the exact current-page join and its joined count.” Regenerate OpenAPI only if the generated schema changes.

- [ ] **Step 4: Run DI/endpoint tests and verify GREEN**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public/test_bot_catalog_metadata_service.py tests/community/endpoints -q -k 'catalog'`

Expected: PASS.

### Task 4: Verify the smallest safe diff

**Files:**
- Modify only files from Tasks 1–3 plus generated schema if generation changes it.

- [ ] **Step 1: Run targeted and architecture tests**

Run: `cd src/backend && uv run pytest tests/community/core/bot_public tests/community/adapters/http/openapi_v1/bot_public tests/community/endpoints -q -k 'catalog or bot_public'`

- [ ] **Step 2: Run static and contract checks**

Run: `cd src/backend && uv run ruff check <changed-python-files>`

Run: `git diff --check`

Run the existing OpenAPI dump/compare command if documentation or descriptions changed.

- [ ] **Step 3: Check scope and record the outcome**

Confirm the diff has no BCSFuse/Discover changes, no raw SQL, no URL construction from inputs, and no `.superpowers/` changes. Append the verified implementation and test result to the project log.
