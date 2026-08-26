# Task 2 gateway report

## Delivered

- Added exact `GET` route-security rules for `/openapi/v1/bots/public/search`
  and `/openapi/v1/bots/public/discover` in both Avernet and OCB.  Both require
  a user and accept an optional app identity, so they override the broad
  optional `bots/**` rule.
- Added resolver behavior tests for both paths in both repositories.  The tests
  assert `user: required` and `app: optional` from the shipped YAML.
- Generated Avernet's `bots.openapi.json` from the active backend surface with
  `src/backend/scripts/dump_openapi.py` (not by hand).
- Synchronized the two generated catalog paths into OCB with their eight
  missing transitive schema components.  The ninth reachable component,
  `ErrorEnvelope`, was already byte-equivalent.  OCB's non-catalog schema
  content was preserved.

## TDD evidence

Tests were added before either YAML configuration changed.

```bash
uv run --locked pytest tests/unit/core/authn/test_route_security.py -q
```

- Avernet RED: `2 failed, 41 passed`; both catalog paths incorrectly resolved
  to `user: optional` through `/openapi/v1/bots/**`.
- OCB RED: `2 failed, 30 passed`; the same expected failure.

After adding the exact rules, the focused checks were green:

```bash
# Avernet src/gateway
uv run --locked ruff check tests/unit/core/authn/test_route_security.py && \
  uv run --locked pytest tests/unit/core/authn/test_route_security.py -q

# OCB src/gateway
uv run --locked ruff check tests/unit/core/authn/test_route_security.py && \
  uv run --locked pytest tests/unit/core/authn/test_route_security.py -q
```

- Avernet: `All checks passed!`, `43 passed in 0.42s`.
- OCB: `All checks passed!`, `32 passed in 0.28s`.

## Schema generation and validation

The backend dump requires its normal deployment profile:

```bash
DEPLOY_PROFILE=community src/backend/.venv/bin/python \
  src/backend/scripts/dump_openapi.py \
  src/gateway/configs/schemas/bots.openapi.json
```

Outcome: successful generation of the Avernet served schema.  Startup emitted
the expected local warning that no gateway principal signing key is configured;
it did not prevent OpenAPI generation.

An in-memory JSON validator parsed both schema files and checked:

- both paths expose only `GET`, require non-empty `user_id`, and return the
  generated envelope schemas;
- all nine transitive component references resolve and match between Avernet
  and OCB;
- `PublicBot`, `DiscoveredPublicBot`, `Friendship`, and `Recommendation` have
  exactly the public allowlisted properties, with no binding, database/device,
  extension, credential, environment, instance, or raw-response fields;
- removing the two new paths and their eight new components from OCB yields its
  original `HEAD` schema exactly.

Outcome:

```text
validated JSON + closure: bots.openapi.json, bots.openapi.json; paths=2, refs=9; OCB unrelated content preserved
```

## Fix round 1: catalog 401 examples

The backend contract now distinguishes unauthenticated callers from verified
app-only callers.  Avernet was regenerated with the project dump script, then
only the two catalog path items and their existing reachable component closure
were synchronized to OCB:

```bash
DEPLOY_PROFILE=community src/backend/.venv/bin/python \
  src/backend/scripts/dump_openapi.py \
  src/gateway/configs/schemas/bots.openapi.json
```

The refresh completed successfully.  The local profile emitted the known
missing gateway-principal-signing-key warning, which does not prevent OpenAPI
generation.

An in-memory JSON validation then parsed both schema files and asserted, for
both `GET /openapi/v1/bots/public/search` and
`GET /openapi/v1/bots/public/discover`:

- named `missing_or_invalid_credentials` (`401000`) and
  `verified_app_only_caller` (`401001`) examples on the `401` response;
- `403001` on the `403` response;
- complete nine-component reference closure and identical catalog path/component
  content in Avernet and OCB;
- allowlisted public projection fields only, with no forbidden binding,
  database/device, extension, credential, environment, instance, or raw
  recommendation fields;
- no `401001` example outside these catalog paths in OCB, and exact preservation
  of all other OCB schema content after the catalog additions are removed.

Outcome:

```text
validated JSON: 401000+401001 and 403001 on 2 paths; closure=9 refs; no leaks; no unexpected 401001 examples; OCB unrelated content preserved
```

Formatting validation also passed in both repositories:

```bash
git diff --check -- src/gateway/configs/schemas/bots.openapi.json
```
