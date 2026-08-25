# Task 1 code report — OpenAPI public Bot catalog

## Delivered

- Added `GET /openapi/v1/bots/public/search` and
  `GET /openapi/v1/bots/public/discover`, assembled before the generic Bot
  router.
- Added typed, explicit public response projections.  They allowlist only the
  specified Bot, friendship, and recommendation fields; they do not expose
  internal records, extension data, bindings, credentials, environment values,
  or recommendation context.
- Added query validation and the `draft`, `verify`, and `online` public runtime
  state set.  Discovery passes exactly `{"runtime_state": [value]}` to the
  existing service boundary.
- Added focused mounted-adapter tests with injected service doubles, including
  caller isolation, user-plus-app success, pure-app refusal, unavailable
  recommendation output, validation, and public-field projection.
- Reserved the `public` Bot path literal and updated the route/admission and
  response error-contract tests.

## Identity and error behavior

- The two catalog operations are `REFUSED` and explicitly reject verified
  app-only callers with the fixed `401001` envelope.
- Missing or invalid principals retain `401000`; verified user callers with a
  mismatched `user_id` receive `403001`.
- User-plus-app callers continue to use the verified user identity.

## TDD and verification

The initial direct `pytest` preflight could not import the project dependency
`fastapi_injector`; running the repository command through `uv` supplied the
configured environment.

| Phase | Command | Outcome |
| --- | --- | --- |
| RED | `uv run pytest tests/community/adapters/http/openapi_v1/bot_public/test_router.py -q` | 13 failed as expected: catalog paths returned 404 before implementation. |
| Focused integration | `uv run pytest tests/community/adapters/http/openapi_v1/bot_public/test_router.py tests/community/adapters/http/openapi_v1/test_app_only_refusals.py tests/community/adapters/http/openapi_v1/test_explicit_user_id.py tests/community/adapters/http/openapi_v1/test_admission_inventory.py -q` | 75 passed. |
| Contract regression | `uv run pytest tests/community/adapters/http/openapi_v1/test_path_convention.py tests/community/adapters/http/openapi_v1/test_openapi_error_schema.py tests/community/adapters/http/openapi_v1/test_schema_docs.py tests/community/adapters/http/openapi_v1/test_responses.py -q` | 67 passed. |
| Final combined regression | `uv run pytest tests/community/adapters/http/openapi_v1/bot_public/test_router.py tests/community/adapters/http/openapi_v1/test_app_only_refusals.py tests/community/adapters/http/openapi_v1/test_explicit_user_id.py tests/community/adapters/http/openapi_v1/test_admission_inventory.py tests/community/adapters/http/openapi_v1/test_path_convention.py tests/community/adapters/http/openapi_v1/test_openapi_error_schema.py tests/community/adapters/http/openapi_v1/test_schema_docs.py tests/community/adapters/http/openapi_v1/test_responses.py -q --disable-warnings` | 142 passed. |
| Static checks | `uv run ruff check src/agentclaw/community/adapters/http/openapi_v1/bot_public src/agentclaw/community/adapters/http/openapi_v1/__init__.py src/agentclaw/community/adapters/http/openapi_v1/admission.py src/agentclaw/community/adapters/http/openapi_v1/dependencies.py src/agentclaw/community/adapters/http/openapi_v1/errors.py src/agentclaw/community/adapters/http/openapi_v1/principal.py src/agentclaw/community/adapters/http/openapi_v1/responses.py tests/community/adapters/http/openapi_v1/bot_public tests/community/adapters/http/openapi_v1/test_app_only_refusals.py tests/community/adapters/http/openapi_v1/test_explicit_user_id.py tests/community/adapters/http/openapi_v1/test_path_convention.py` | Passed. |
| Compilation and diff | `uv run python -m compileall -q src/agentclaw/community/adapters/http/openapi_v1/bot_public src/agentclaw/community/adapters/http/openapi_v1/{__init__,admission,dependencies,errors,principal,responses}.py`; `git diff --check` | Passed. |

The passing test runs emit 18 existing Pydantic/Starlette deprecation warnings;
they are unrelated to this catalog change.

## Fix round 1 — review corrections

- Discovery detail records now carry the repository's authoritative `bot_type`.
  The public adapter no longer invents `personal`; a malformed discovery record
  receives the fixed unavailable-recommender envelope instead.
- `401001` is now restricted to the closed pair of catalog `REFUSED` routes.
  All pre-existing `REFUSED` operations retain `MissingPrincipalError` / `401000`
  and their byte-identical no-credential behavior. This is the compatibility
  ruling for the anti-enumeration contract.
- Shared user-id mismatch documentation explicitly publishes `403001`. The
  dynamic OpenAPI assertion covers the routes using the shared response entry.
- The catalog adapter uses low-sensitivity request diagnostics. The shared
  `BotDiscoverService` initially received a log-format change, then had that
  change fully reverted on 2026-08-19 at user direction; its pre-existing log
  format is intentionally outside this task's modification scope.

| Fix phase | Command | Outcome |
| --- | --- | --- |
| RED | `uv run pytest tests/community/adapters/http/openapi_v1/bot_public/test_router.py tests/community/adapters/http/openapi_v1/test_app_only_refusals.py tests/community/adapters/http/openapi_v1/test_explicit_user_id.py tests/community/repository/bot/test_bot_tenant_raw_sql_and_threads.py -q --disable-warnings` | 26 failed as expected: bot-type fallback, global `401001`, stale `403000` example, and the then-proposed discovery log-format change. |
| Focused GREEN | Same command after the fixes | 74 passed. |
| Final bounded regression | `uv run pytest tests/community/adapters/http/openapi_v1/bot_public/test_router.py tests/community/adapters/http/openapi_v1/test_app_only_refusals.py tests/community/adapters/http/openapi_v1/test_explicit_user_id.py tests/community/adapters/http/openapi_v1/test_admission_inventory.py tests/community/adapters/http/openapi_v1/test_path_convention.py tests/community/adapters/http/openapi_v1/test_openapi_error_schema.py tests/community/adapters/http/openapi_v1/test_schema_docs.py tests/community/adapters/http/openapi_v1/test_responses.py tests/community/repository/bot/test_bot_tenant_raw_sql_and_threads.py -q --disable-warnings` | 155 passed. |
| Static and diff | `uv run ruff check` over changed Task-1 production/tests; `git diff --check` | Passed. |

## Fix round 2 — catalog 401 OpenAPI outcomes

- Added a catalog-only 401 response declaration for both public catalog
  operations. It keeps the existing `ErrorEnvelope` wire model and fixed
  `Unauthorized` message, while publishing separate examples for missing or
  invalid credentials (`401000`) and a verified app-only caller (`401001`).
- Generic error response documentation and all other paths remain unchanged.

| Fix phase | Command | Outcome |
| --- | --- | --- |
| RED | `uv run pytest tests/community/adapters/http/openapi_v1/test_openapi_error_schema.py -q --disable-warnings` | 1 failed as expected: catalog 401 media had no multiple examples. |
| Focused GREEN | Same command after the catalog response declaration | 9 passed. |
| Final catalog/schema/admission regression | `uv run pytest tests/community/adapters/http/openapi_v1/bot_public/test_router.py tests/community/adapters/http/openapi_v1/test_app_only_refusals.py tests/community/adapters/http/openapi_v1/test_explicit_user_id.py tests/community/adapters/http/openapi_v1/test_admission_inventory.py tests/community/adapters/http/openapi_v1/test_path_convention.py tests/community/adapters/http/openapi_v1/test_openapi_error_schema.py tests/community/adapters/http/openapi_v1/test_schema_docs.py tests/community/adapters/http/openapi_v1/test_responses.py -q --disable-warnings` | 148 passed. |
| Static and diff | `uv run ruff check src/agentclaw/community/adapters/http/openapi_v1/bot_public/router.py tests/community/adapters/http/openapi_v1/test_openapi_error_schema.py`; `git diff --check` | Passed. |

## Post-review adjustment — preserve discovery-service logs

At user direction, all log-format changes in the shared
`core/bot_public/services/bot_discover_service.py` were restored and the
companion no-raw-log assertion was removed. The `bot_type` repository
projection remains because it is required for the public DTO to report the
actual Bot type. Current bounded regression:

| Command | Outcome |
| --- | --- |
| `uv run pytest tests/community/adapters/http/openapi_v1/bot_public/test_router.py tests/community/adapters/http/openapi_v1/test_app_only_refusals.py tests/community/adapters/http/openapi_v1/test_explicit_user_id.py tests/community/adapters/http/openapi_v1/test_admission_inventory.py tests/community/adapters/http/openapi_v1/test_path_convention.py tests/community/adapters/http/openapi_v1/test_openapi_error_schema.py tests/community/adapters/http/openapi_v1/test_schema_docs.py tests/community/adapters/http/openapi_v1/test_responses.py tests/community/repository/bot/test_bot_tenant_raw_sql_and_threads.py -q --disable-warnings -p no:cacheprovider` | 155 passed, 18 existing deprecation warnings, 39.11s. |
