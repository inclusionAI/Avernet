# Per-Endpoint Dependency-Injection Test Framework

A test framework that lets you cover any backend HTTP endpoint by
**declaring** a case rather than writing the invocation. The framework
boots the real FastAPI app with production components swapped for
test doubles, issues the call to the declared endpoint, and checks
the declared expectations.

Because the framework owns invocation, a case's declared
`(method, path)` is necessarily the endpoint exercised — annotations
cannot lie about coverage.

---

## Quick start

Drop a file under `tests/endpoints/`. That's it.

```python
# tests/endpoints/test_user_get_smoke.py
from tests.factories.access import make_staff_user
from tests.framework import (
    CaseInput, ExpectError, ExpectSuccess, endpoint_test,
)


@endpoint_test(
    method="GET",
    path="/api/v1/user/{user_type}/{user_id}",
    scenario="ok",
    input=CaseInput(path_params={"user_type": "staff", "user_id": "u_smoke"}),
    seed=lambda world: make_staff_user(world, user_id="u_smoke"),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"userId": "u_smoke"}},
    ),
)
def get_user_ok():
    """Body intentionally empty — the framework owns invocation."""


@endpoint_test(
    method="GET",
    path="/api/v1/user/{user_type}/{user_id}",
    scenario="not_found",
    input=CaseInput(path_params={"user_type": "staff", "user_id": "missing"}),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def get_user_not_found():
    pass
```

Run:

```bash
cd src/backend
uv run pytest tests/endpoints/ -v
```

Pytest reports each case as its own line:

```
test_endpoint_injection[GET /api/v1/user/{user_type}/{user_id} :: ok]      PASSED
test_endpoint_injection[GET /api/v1/user/{user_type}/{user_id} :: not_found]  PASSED
```

---

## The case shape

| Field              | Type                                | Purpose                                                                                            |
| ------------------ | ----------------------------------- | -------------------------------------------------------------------------------------------------- |
| `method`           | `"GET"` \| `"POST"` \| …            | HTTP verb.                                                                                         |
| `path`             | `str`                               | Route template **with placeholders**: `/api/v1/user/{user_type}/{user_id}`. **Not** the substituted URL. |
| `scenario`         | `str`                               | Short `snake_case` label describing the outcome (`ok`, `not_found`, `forbidden`). Unique per `(method, path)`. |
| `expect`           | `ExpectSuccess` \| `ExpectError`    | What the framework asserts.                                                                        |
| `input`            | `CaseInput` (optional)              | `path_params` / `query_params` / `headers` / `json_body`.                                          |
| `seed`             | `Callable[[World], None]` (opt.)    | Pre-call hook. Runs against the same injector the endpoint will use, so writes are observable.    |
| `extra_assertions` | `tuple[Callable[[Response, World], None], ...]` (opt.) | Custom assertions over the response and post-call state.                  |

### Inputs vs. URL anatomy

```
/api/v1/user/staff/u_smoke?include=profile
─────────────┬────┬─────── ──────┬────────
        path part            query string

path_params:  {"user_type": "staff", "user_id": "u_smoke"}   → fills {…} slots
query_params: {"include": "profile"}                          → appended as ?include=…
headers:      {"X-Trace-Id": "..."}                            → HTTP request headers
json_body:    {"...": "..."}                                  → request body (POST/PUT/PATCH)
```

### Expectation shapes

```python
ExpectSuccess(
    status=200,                                          # HTTP status
    json_equals={"a": 1, "b": 2},                        # exact match (optional)
    json_contains={"a": 1},                              # subset match  (optional)
)

ExpectError(
    status=200,                                          # often the API returns 200 with
                                                         # an error envelope; pin the
                                                         # envelope shape via json_contains.
    json_contains={"success": False, "error_code": 404},
    exception_type=None,                                 # reserved; best-effort, no contract yet
)
```

**`json_contains` semantics:**
- Dicts: every key in the expectation must exist in the actual; values match recursively.
- Lists: every expected item must match **some** actual item (existence, not pairwise).
- Scalars: equality.
- Extra fields in the response are ignored — additive API changes don't break existing cases.

When you need exact-equality, use `json_equals`. When you need ordered list matching or
disjoint matching, use `extra_assertions` lambdas.

---

## The standard: happy + key error paths

Every endpoint should ship with **at least two cases**: a happy path
(`ExpectSuccess`) and at least one key error path (`ExpectError`). The
most common error scenarios:

- `404` not-found (missing resource)
- `403` forbidden (access denied)
- `422` validation failure (malformed input)
- The endpoint's domain-specific error envelopes

The coverage gate (`ENDPOINT_COVERAGE_GATE=strict`) enforces this once
backfill is underway.

### Why declare instead of writing the invocation yourself?

The framework can guarantee that the declared `(method, path)` is what
the test actually exercises. If authors wrote `client.get(...)`
themselves, they could decorate a function `path="/api/v1/user/..."`
but actually call `/api/v1/something_else` and the test would pass —
the annotation would be a lie. By owning invocation, the framework
makes the annotation self-enforcing.

This also means the decorated function's **body is never invoked**.
Leave it empty (`pass` or a docstring). Logic in the body is dead code
the framework ignores.

---

## Seeding via `World`

Seed callables receive a `World` — a typed handle on the per-test
injector. `world.get(SomeService)` resolves the same instance the
endpoint will resolve, so writes are visible to the request:

```python
def seed_a_user(world: World) -> None:
    svc = world.get(UserService)
    svc.upsert_user(user_id="u1", user_type="staff", status="active")
```

### Factories: `tests/factories/<domain>.py`

Repetition across cases is the cue to factor seeds into reusable
helpers, the Object-Mother pattern:

```python
# tests/factories/access.py
def make_staff_user(world: World, *, user_id="u_smoke", status="active") -> UserInfoRecord:
    svc = world.get(UserService)
    svc.upsert_user(user_id=user_id, user_type="staff", status=status)
    return svc.get_user(user_id=user_id, user_type="staff")
```

Factories **call real services** — they don't back-door INSERTs.
Domain invariants the services enforce continue to apply, so seeded
state can't be richer than the live system permits.

---

## No mocking in `tests/endpoints/`

The framework guarantees that a case's declared `(method, path)` was
really called. That guarantee is worth little if the code behind the
route was replaced before the request went out — so **endpoint cases may
not use `unittest.mock`, `monkeypatch`, `mocker`, or `setattr`**.
`tests/framework/test_no_mock_in_endpoint_tests.py` enforces it (AST
scan, so comments and docstrings that mention mocks are fine), and
`test_no_mock_on_world_get.py` covers the related "overwrite a method on
a `world.get(...)` handle" hack.

In order of preference:

1. **Seed the state that produces the outcome.** Most error branches are
   reachable: a missing row → 404, a caller without the role → 403, a
   malformed body → 422, a bot with no device binding → the service's own
   `ValueError`. These test more than an injected exception does, and they
   document the failure a user would actually hit.
2. **Drive a system boundary through its DI seam.** Boundary plugins under
   `plugins/local/` inherit `MockSeam`, so
   `world.get(SomeBoundaryPlugin).set_response("method", value)` /
   `.set_override("method", fn)` stands in for the *edge* — an HTTP
   upstream, a device link, AgentPass — while everything inside stays real.
   `MockSeam` also records `.calls`, so assertions read the calls the
   endpoint actually made. Use `json_response(...)` /
   `http_envelope_response(...)` from the framework to build replies.
3. **Bind a stand-in through the injector** when a step genuinely cannot
   run on a test host — a `sudo rsync` onto a NAS mount, a multi-minute
   publish. `bind_method` / `bind_overrides` / `bind_failing_method`
   (`tests/framework/di_seams.py`) build a *subclass* of whatever the graph
   wired, override the named methods, and bind it on the per-test injector.
   The production class is untouched and the substitution is discarded with
   the test — neither is true of a class-level patch, which survives a
   failed assertion and poisons whatever runs next.

If a file constructs a service directly and never issues a request, it is
a unit test: put it under `tests/services/` (or the matching tree), not
here.

---

## Isolation guarantees

Every case runs against a **freshly-built injector** backed by a
**freshly-created in-memory SQLite database**:

- `tests/framework/fixtures.py::app_with_testing_modules` (function
  scope) builds a new injector via `testing_modules_for(RuntimeConfig(LOCAL, SQLITE))`,
  bootstraps the schema with `Base.metadata.create_all`, swaps
  `app.state.injector` for the duration of the test, and restores it
  on teardown.
- `TestingDatabaseModule` calls `reset_for_tests()` so each build gets
  a brand-new SQLite engine — no state bleed between cases.
- `plugins/local/database.py` is **in-memory only** (no file-backed
  branch); a `pytest_sessionfinish` hook fails the session if a
  `backend.db` or `device.db` ever appears at the repo root.

The `world` fixture in a case automatically depends on
`app_with_testing_modules`, so by the time `world.get(...)` runs the
swap is already in place.

---

## Auto-discovery (no central list)

Both sides of "drop a file, get coverage" are zero-touch:

- **Test side:** `tests/endpoints/conftest.py` glob-imports every
  `test_*.py` in this tree at conftest load. Any new file you add is
  picked up next run — no registry to update.
- **Endpoint side:** the coverage gate reads `app.routes` from the
  **live** FastAPI app. Any new `@router.get(...)` is in the gate's
  view as soon as its router is included in `api/app.py`.

---

## Test IDs

Format: `<METHOD> <PATH> :: <SCENARIO>`. Path is the **template** with
`{placeholders}`, not the substituted URL — keeps related cases
grouped in reports.

```
test_endpoint_injection[GET /api/v1/user/{user_type}/{user_id} :: ok]
test_endpoint_injection[GET /api/v1/user/{user_type}/{user_id} :: not_found]
```

Filter with `-k`: `pytest -k "not_found"`, `pytest -k "GET and access"`.
The `::` separator is chosen to be a non-identifier delimiter so `-k
"ok"` doesn't accidentally match substrings inside paths.

The registry rejects duplicate `(method, path, scenario)` at
registration time, so IDs are unique by construction.

---

## The coverage gate

A single **always-on** test in `tests/framework/test_coverage_gate.py`
enforces the contract:

> Every live endpoint either has a happy + error case, or appears on
> the frozen baseline at `tests/framework/coverage_baseline.txt`.

The baseline records the routes that were already uncovered when the
gate was introduced. Those are tracked debt — the gate does not fail
on them.

The gate **fails** in two situations, both with actionable messages:

1. **NEW gap** — a route added or uncovered after the baseline was
   captured. New endpoints must ship with happy + error cases.
   ```
   1 NEW uncovered endpoint(s) — add happy + error cases:
     + POST /api/v1/foo: missing [happy, error]
   ```
2. **STALE baseline entry** — a route that used to be uncovered but
   no longer is. This forces the baseline to shrink as you burn down
   debt; you cannot leave dead lines behind.
   ```
   1 STALE baseline entry/entries — delete these lines from
   tests/framework/coverage_baseline.txt:
     - GET /api/v1/bar: missing [happy, error]
   ```

### Burning down the baseline

1. Pick a line from `coverage_baseline.txt`. Each line is one
   `(method, path)` that needs coverage.
2. Add happy + error cases under `tests/endpoints/`. Run the suite —
   the gate now fails with a STALE entry diagnostic.
3. Delete the line the message points at. Run again — green.
4. Commit. The baseline shrinks by one line.

Cases that only partially cover a route (e.g. happy added, error
still missing) cause the gate to fail with BOTH a STALE entry (the
old "missing [happy, error]") AND a NEW entry (today's "missing
[error]"). Update the baseline line to match the new shape; the gate
goes green for that route until the remaining shape is added.

### Regenerating the baseline (rare)

If the route table changes drastically (a large refactor, mass-add of
endpoints), rebuild the baseline from today's gaps:

```bash
cd src/backend
python -m tests.framework.coverage_gate --regen
```

This rewrites `coverage_baseline.txt` with every current gap. Use
sparingly — the point of the baseline is to be a deliberately
shrinking debt log, not a free escape hatch.

---

## Known limitations

- **`ExpectError.exception_type` is best-effort.** The field is
  declared and accepted but not asserted today. The first real case
  that needs precise domain-exception → envelope mapping will tighten
  the contract.
- **No `multipart/form-data` or file-upload support yet.** Add fields
  to `CaseInput` when the first such case lands.
- **`World` is a thin wrapper** (just `.get(cls)` + `.injector`).
  Intentional — richer affordances (`world.db`, `world.now`, etc.)
  grow on demand as real cases reveal what's needed.
