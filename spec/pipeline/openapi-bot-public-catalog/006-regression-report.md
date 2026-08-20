# Local regression report — Bot Public OpenAPI catalog

## Scope

Fresh local, read-only regression of the current uncommitted Task 1/Task 2
catalog changes, refreshed after the documented 401 examples and served-schema
synchronization. This report follows the backend context in `002-code-report.md`
and gateway context in `004-gateway-report.md`.
No deployment, remote/ARCA operation, commit, push, or production/test/config
edit was performed. The only file written for this run is this report.

## Environment

- Refresh time: 2026-08-19 00:47:08 CST
- Avernet worktree HEAD: `87185a97b`
- OCB worktree HEAD: `eba0ccfed`
- Both worktrees retained their pre-existing uncommitted catalog changes.

## Results

| Area | Verification | Result |
| --- | --- | --- |
| Avernet backend | Combined new catalog, admission, identity, path, OpenAPI schema/error/response, and BotDiscover repository regression suite | PASS — 156 passed, 18 pre-existing Pydantic/Starlette deprecation warnings, 37.72s |
| Avernet backend (current) | Same bounded suite after the user-directed restoration of `BotDiscoverService` log formatting | PASS — 155 passed, 18 pre-existing Pydantic/Starlette deprecation warnings, 39.11s |
| Existing runtime binding path | Connection stage addressing, owner/collaborator authorization, and service-Bot stage resolution regression suite | PASS — 104 passed, 18 pre-existing Pydantic/Starlette deprecation warnings, 2.11s |
| Avernet gateway | Route-security resolver tests | PASS — 43 passed, 0.46s |
| OCB gateway | Route-security resolver tests | PASS — 32 passed, 0.32s |
| Served schemas | `bots.openapi.json` parses as JSON in both worktrees; each catalog path has exactly a `GET` operation and publishes the required named error examples | PASS |
| Patch hygiene | `git diff --check` in both worktrees | PASS |

### Avernet backend command

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --locked pytest \
  tests/community/adapters/http/openapi_v1/bot_public/test_router.py \
  tests/community/adapters/http/openapi_v1/test_app_only_refusals.py \
  tests/community/adapters/http/openapi_v1/test_explicit_user_id.py \
  tests/community/adapters/http/openapi_v1/test_admission_inventory.py \
  tests/community/adapters/http/openapi_v1/test_path_convention.py \
  tests/community/adapters/http/openapi_v1/test_openapi_error_schema.py \
  tests/community/adapters/http/openapi_v1/test_schema_docs.py \
  tests/community/adapters/http/openapi_v1/test_responses.py \
  tests/community/repository/bot/test_bot_tenant_raw_sql_and_threads.py \
  -q --disable-warnings -p no:cacheprovider
```

The refreshed suite completed with `156 passed, 18 warnings in 37.72s`.

### Existing runtime binding command

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --locked pytest \
  tests/community/adapters/http/openapi_v1/engine_runtime/test_stage_addressing.py \
  tests/community/adapters/http/openapi_v1/engine_runtime/test_operator_access.py \
  tests/community/adapters/http/openapi_v1/engine_runtime/test_connection.py \
  tests/community/core/engine_runtime/test_stage.py -q
```

The existing connection contract remains green with `104 passed, 18 warnings in
2.11s`; it covers the internal binding resolution semantics retained by this
catalog change, including service-Bot `draft` / `verify` / `online` stages and
caller authorization.

### Gateway and schema commands

```bash
# Run separately in each worktree's src/gateway directory.
PYTHONDONTWRITEBYTECODE=1 uv run --locked pytest \
  tests/unit/core/authn/test_route_security.py -q -p no:cacheprovider
```

The schema check loaded both generated `bots.openapi.json` artifacts with
`json.loads` and asserted for both
`/openapi/v1/bots/public/search` and
`/openapi/v1/bots/public/discover` that:

- the path exposes only `GET`;
- the `401` response has named `missing_or_invalid_credentials` (`401000`)
  and `verified_app_only_caller` (`401001`) examples; and
- the `403` example publishes `403001`.

## Conclusion

**PASS** — the requested local regression evidence is green. This is local
validation only; no remote ACI or ARCA result was requested or produced.
