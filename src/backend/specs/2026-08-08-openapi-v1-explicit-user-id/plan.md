# Plan: Public API — Name the End User in the Request

## Approach

One required query parameter, `user_id`, on the 56 operations that scope to a
user; one dependency that reads it and decides whether the caller may act for
that user. Handlers stop asking the credential who they are for and take the
answer as an argument.

Nothing else moves. No request model changes, no path changes, `bot_id` stays
exactly where it is. The whole change is a dependency, an error, a response
entry, 56 handler signatures, and the tests that pin them.

## The placement rule

> **`user_id` is a required query parameter on every `/openapi/v1` operation
> that scopes to a user.** It is never a body field and never a path segment.
> `bot_id` is unaffected: it stays in the path where it addresses a bot and in
> the query string where it is a parameter.

Written this way because the user id is not an attribute of any resource on this
surface — it is who the call is for, the same value on every operation and the
same meaning on a read as on a write. A body describes the resource; a path
names it; neither is what this is.

**Counts.** 65 published operations.

| Set | Count | Treatment |
| --- | --- | --- |
| Scope to a user | 52 | gain `?user_id=` |
| User-scoped in principle, not yet enforced (`list_resources`, `create_resource`, `get_resource`, `update_resource`) | 4 | gain `?user_id=` |
| No user dimension (`check_bot_name`, `list_mcp_servers`, `list_mcp_tenants`, `get_mcp_server`) | 4 | unchanged |
| Bot Logs | 5 | unchanged |

## Affected Components

- `…/openapi_v1/principal.py` — the seam: `require_user_id` / `UserIdDep`.
- `…/openapi_v1/errors.py`, `…/openapi_v1/responses.py` — the 403 and its fixed
  public message.
- `…/openapi_v1/contracts.py` — the 403's documented response entry.
- `…/openapi_v1/__init__.py` — attaches that entry per group; states the
  placement rule next to the addressing rule.
- `…/openapi_v1/{bots,mcp,resources,routines,skills,identity}/router.py` and
  `…/openapi_v1/engine_runtime/*/router.py` — 56 handler signatures, plus a
  `responses=` entry on 15 route decorators (see below).
- `src/backend/src/agentclaw/community/adapters/http/app.py` — the 403 needs a
  concrete-type handler for the same reason `MissingPrincipalError` has one
  (`app.py:559`): it is raised in a dependency, so the `Exception` catch-all
  would answer it *and* re-raise through `ServerErrorMiddleware`.
- `src/backend/docs/openapi-v1/README.md` — the rule, and the status board.

**No schema module is touched.** The 11 request models keep their current
fields.

## Data Model Changes

None. No table, column, or migration is involved.

## API / Interface Changes

**BREAKING for 56 `/openapi/v1` operations** — a required query parameter is
added. Deliberate, and the reason the spec argues for doing it now:
`route_security` admits only a Google-chain `user`, so no external tenant can
reach this surface yet.

### The seam

```python
# openapi_v1/principal.py (new)
USER_ID_QUERY = "user_id"

async def require_user_id(
    principal: Annotated[Principal, Depends(require_principal)],
    user_id: Annotated[str, Query(min_length=1, max_length=256, description=...)],
) -> str:
    """The end user this request acts for. 401 unverifiable, 403 not the caller."""
    caller = caller_owner_id(principal)           # 401 when unverifiable
    if user_id != caller:
        raise UserIdMismatchError(...)            # 403 — logs both ids
    return user_id

UserIdDep = Annotated[str, Depends(require_user_id)]
```

The equality check is the whole of the "nothing else changes" promise, and it is
the single line delegation replaces later. Nothing else in the surface knows how
the question is answered.

### Every affected operation, in one shape

```diff
# openapi_v1/bots/router.py:351 — get_bot
 async def get_bot(
     bot_id: str,
     request: Request,
-    principal: PrincipalDep,
+    user_id: UserIdDep,
     bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
 ) -> Envelope[Bot]:
-    owner_id = caller_owner_id(principal)
-    bot = bot_service.get_bot(bot_id, owner_id)
+    bot = bot_service.get_bot(bot_id, user_id)
```

Writes take exactly the same shape — the body is untouched:

```diff
# openapi_v1/bots/router.py:367 — update_bot
     body: BotUpdate,
     request: Request,
-    principal: PrincipalDep,
+    user_id: UserIdDep,
```

```jsonc
// GET    /openapi/v1/bots/b-1?user_id=u-42          → 200  (payload unchanged)
// PUT    /openapi/v1/bots/b-1?user_id=u-42          → 200  body {"bot_name": "Ada"}
// DELETE /openapi/v1/bots/b-1?user_id=u-42          → 200
// GET    /openapi/v1/bots/b-1                       → 422  {"code":422000,"message":"Invalid request","data":null,…}
// GET    /openapi/v1/bots/b-1?user_id=u-99          → 403  {"code":403000,"message":"Forbidden","data":null,…}
```

The four operations with no user dimension keep their current signature and
answer exactly as they do today.

### Where the dependency is declared

**Per handler, not per group.** `_PUBLIC_AUTH` can stay at group level because
it applies to every route without exception; `require_user_id` has four
exceptions, two of which sit inside groups whose other routes do take it (1 of
13 in `bots`, 3 of 6 in `mcp`). A group-level dependency would put the parameter
on them.

The cost is that assembly no longer makes it impossible to forget on a new
route. The convention test is what recovers that — the same trade
`test_path_convention.py` already makes for the addressing rule.

### Documenting the 403

```diff
# openapi_v1/contracts.py:84
+USER_SCOPED_ERROR_RESPONSES = {**ERROR_RESPONSES, 403: {"model": ErrorEnvelope,
+    "description": "The request names a user the caller may not act for"}}
+USER_SCOPED_403 = {403: USER_SCOPED_ERROR_RESPONSES[403]}   # for per-route merge
-ENGINE_RUNTIME_ERROR_RESPONSES = {**ERROR_RESPONSES, 501: …, 504: …}
+ENGINE_RUNTIME_ERROR_RESPONSES = {**USER_SCOPED_ERROR_RESPONSES, 501: …, 504: …}
```

403 stays **out** of `ERROR_RESPONSES`: `test_openapi_error_schema.py:55` asserts
every operation documents every status in that dict, and Bot Logs cannot return
one. Applied in two ways, for the same reason the dependency is:

- **Group level** for the nine groups with no exemption — `resources`,
  `routines`, `skills`, `identity`, and the five engine-runtime groups.
- **Route level** for `bots` (12 of 13) and `mcp` (3 of 6), which keep
  `ERROR_RESPONSES` at group level and add `responses=USER_SCOPED_403` on the
  routes that can return it. FastAPI merges router-level and route-level
  `responses`, so the four exempt operations document exactly what they can
  return. 15 decorator edits.

## Key Files & Functions

```python
# openapi_v1/errors.py (new class)
class UserIdMismatchError(Exception):
    """The request's user id is not the verified caller's (→ 403)."""
```

```diff
# openapi_v1/responses.py:163 — ENVELOPE_ERRORS
     PrincipalVerificationError: (401, "Unauthorized"),
+    UserIdMismatchError: (403, "Forbidden"),
```

```diff
# adapters/http/app.py:559 — beside _principal_error_handler
+@app.exception_handler(UserIdMismatchError)
+async def _user_id_mismatch_handler(request, exc) -> JSONResponse: ...
```

Per-router handler counts (signatures changed / operations in group): bots 12/13,
mcp 3/6, resources 9/9, routines 7/7, skills 6/6, identity 3/3, sessions 7/7,
engine 3/3, approvals 3/3, models 2/2, connection 1/1.

The four `resources` handlers that take the parameter without using it declare
it and `del` it with a one-line reason — they are the not-yet-enforced case, not
the no-user-dimension case, and the comment should say which.

## Dependencies

None. No new package, no version bump.

## Risks & Mitigations

- **Risk:** a request that is wrong in two ways answers 422 instead of 401,
  leaking that the credential was fine. Observed in a spike: when
  `require_user_id` is the *only* dependency naming the principal, FastAPI never
  calls it (its own parameter failed validation) and the 401 never fires.
  **Mitigation:** `_PUBLIC_AUTH` keeps `require_principal` declared
  independently at group level on every group; a test asserts 401 outranks a
  missing parameter.
- **Risk:** the exemption list decays — a fifth operation quietly drops the
  parameter, or one of the four `resources` operations is "cleaned up" into the
  exempt set.
  **Mitigation:** the convention test asserts the exempt set is *exactly* those
  four addresses, and the docs give a reason per entry.
- **Risk:** ~232 test call sites across 10 files.
  **Mitigation:** every affected operation takes it in the same place, so
  `client.params = {"user_id": …}` once per `TestClient` covers all of them
  (httpx merges default params). Verify that behaviour in task 5 before relying
  on it; if it does not hold, one helper does the same job in one place.
- **Risk:** the four `resources` operations advertise a parameter they ignore,
  and a reader concludes the surface is inconsistent.
  **Mitigation:** the `del` comment and the docs both say *why* — enforcement is
  a known gap, not an absent dimension.

## Alternatives Considered

- **`user_id` in the request body on the 11 JSON-body operations** (the
  conventionally RESTful reading; the plan's first revision). Rejected in review:
  a body describes the resource, and beside `bot_name` in a `PUT` payload the
  user id reads as a property being set on the bot. It also needed a
  three-row exception table for the writes whose body this API does not define
  (two raw-byte uploads, the free-form engine-config document), 11 schema
  changes, three `bot_id` moves, and split placement across verbs on the same
  resource — while buying no log-hygiene benefit, since 49 operations carry the
  value in the query string regardless.
- **`user_id` as a path segment**, either `/bots/{bot_id}/users/{user_id}` or
  `/users/{user_id}/bots/…`. Rejected: the first inverts the ownership and
  claims to address a user the endpoint does not return; the second is coherent
  but closed, because the first segment after `/openapi/v1` is the gateway's
  domain selector and `test_public_namespace.py` pins every path under
  `/openapi/v1/bots`.
- **A required `X-Avernet-User-Id` header.** Uniform, no request changes, and
  aligned with the gateway's delegation sketch (auth design §15 reserves an
  `xoneid` / `x-end-user-id` header). Rejected in favour of the parameter being
  a visible argument of the operation rather than transport metadata.
- **Requiring the parameter on all 60**, including the four with no user
  dimension. Rejected in review: it asks for a value those operations cannot
  use, and a marketplace catalogue read has no user-shaped answer to give.
- **Preferring the request's id over the credential's, with no check.** A
  privilege escalation: any verified user could read another's data.

## Rollout

No migration, no flag, no ordering constraint against a deploy. One commit; the
generated API description changes with it. External tenants cannot reach the
surface yet (`route_security` admits only a Google-chain `user`), so the breaking
change lands before it can break anyone.

## Test Strategy

New file, in the shape of `test_path_convention.py` — asserted against the
generated document so a later operation is covered without editing it:

```python
# tests/community/adapters/http/openapi_v1/test_explicit_user_id.py (new)
def test_every_user_scoped_operation_requires_user_id(): ...      # 56/56, in query
def test_the_exempt_operations_are_exactly_these_four(): ...      # pinned by address
def test_user_id_is_never_a_body_field_or_a_path_segment(): ...
def test_bot_id_placement_is_unchanged_from_head(): ...
def test_bot_logs_operations_are_unchanged(): ...                 # 5, no parameter, no 403
def test_naming_another_user_is_403(): ...
def test_two_rejected_ids_give_identical_bodies(): ...
def test_missing_parameter_is_422_and_no_caller_is_401(): ...     # precedence
def test_403_is_documented_on_exactly_the_56(): ...
```

Updated in place — and note that the two tests which asserted the *opposite*
contract no longer need to change: `test_sessions.py:507` and
`test_approvals.py:94` require a caller-supplied `user_id` **in the body** to be
a 422, and under this revision the body still forbids it. They stay green as
written, which is one more argument for the query parameter.

```python
# resources/test_resources_handlers.py — the 4 `principal=None` cases now
# exercise require_user_id, not each handler
```

Then the module gates from `AGENTS.md`: backend SAST, `tests/community` in full
(the openapi_v1 suite is 499 tests today), changed-line coverage, and singlebox
coverage — `flow_coverage.py:75` notes every `/openapi/v1` route answers 401 in
singlebox, so the E2E surface is unaffected by the new parameter.
