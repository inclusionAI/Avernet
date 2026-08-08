# Plan: Public API — Name the End User in the Request

## Approach

Split the question the handlers answer today in one step into two, and put a
shared seam between them:

- **acquisition** — where the request carries the id. Per-method, by one stated
  rule: a request whose body this API defines carries it as a body field;
  everything else carries it as a query parameter.
- **adjudication** — whether the caller may act for the named user. One function,
  `resolve_request_user`, used by both acquisition paths. It is the only thing
  delegation changes later.

The 49 query-parameter operations get acquisition and adjudication in a single
FastAPI dependency. The 11 body operations declare `user_id` on their request
model and call `resolve_request_user` in the handler — the same one-line shape as
today's `owner_id = caller_owner_id(principal)`, pointed at the request instead of
the credential. `bot_id` follows the same placement rule wherever it is a
parameter rather than part of the address.

## The placement rule

> A scoping parameter (`user_id`, `bot_id`) travels **in the request body when
> that body is a schema this API defines**, and **in the query string
> otherwise**. A `bot_id` that is part of an operation's address stays in the
> path; the addressing rule in `openapi_v1/__init__.py` owns that, not this one.

"A body this API defines" means the operation's `requestBody` is
`application/json` and `$ref`s a named component. That is precisely the set that
has room for another field, and it excludes the three writes that do not:

| Operation | Body | Why it takes the query parameter |
| --- | --- | --- |
| `POST …/resources/upload` | `application/octet-stream` | body is the file |
| `POST …/skills/upload` | `application/zip` | body is the package |
| `PUT …/bots/{bot_id}/engine-config` | free-form `object` | body is the caller's own document; a reserved key would collide with it |

**Counts.** 65 published operations; 5 Bot Logs operations are out of scope
(they never derived a user from the credential — see `bot_logs/router.py:44`,
`del principal`). Of the remaining 60: **11 body-field**, **49 query-parameter**.

## Affected Components

- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/principal.py` —
  the new seam: `resolve_request_user` plus the query-parameter dependency.
- `…/openapi_v1/errors.py`, `…/openapi_v1/responses.py` — the 403 and its fixed
  public message.
- `…/openapi_v1/contracts.py` — the response table that documents the 403.
- `…/openapi_v1/__init__.py` — applies the dependency and the response table per
  group; states the placement rule next to the addressing rule.
- `…/openapi_v1/{bots,mcp,resources,routines,skills,identity}/router.py` and
  `…/openapi_v1/engine_runtime/*/router.py` — 60 handlers.
- `…/openapi_v1/{bots,resources,routines,identity,mcp}/schemas.py`,
  `…/engine_runtime/{sessions,approvals}/schemas.py` — 11 request models.
- `src/backend/src/agentclaw/community/adapters/http/app.py` — the 403 needs a
  concrete-type handler for the same reason `MissingPrincipalError` has one
  (`app.py:559`): it is raised in a dependency, so the `Exception` catch-all
  would answer it *and* re-raise through `ServerErrorMiddleware`.
- `src/backend/docs/openapi-v1/README.md` — the rule, and the status board.

## Data Model Changes

None. No table, column, or migration is involved.

## API / Interface Changes

**BREAKING for every `/openapi/v1` caller except Bot Logs** — a required
parameter is added to 60 operations. Deliberate, and the reason the spec argues
for doing it now: `route_security` admits only a Google-chain `user`, so no
external tenant can reach this surface yet.

### The seam

```python
# openapi_v1/principal.py (new)
USER_ID_QUERY = "user_id"          # and the body field of the same name

def resolve_request_user(principal: Principal, claimed: str) -> str:
    """The end user this request acts for. Raises 401 / 403; never returns ''."""
    caller = caller_owner_id(principal)          # 401 when unverifiable
    if claimed != caller:
        raise UserIdMismatchError(...)           # 403 — logs both ids
    return claimed

async def require_user_id(                       # the 49 query operations
    principal: Annotated[Principal, Depends(require_principal)],
    user_id: Annotated[str, Query(min_length=1, max_length=256, description=...)],
) -> str:
    return resolve_request_user(principal, user_id)

UserIdDep = Annotated[str, Depends(require_user_id)]
```

### A query-parameter operation (49 of them)

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

```jsonc
// GET /openapi/v1/bots/b-1?user_id=u-42 → 200   (unchanged payload)
// GET /openapi/v1/bots/b-1              → 422   {"code":422000,"message":"Invalid request","data":null,…}
// GET /openapi/v1/bots/b-1?user_id=u-99 → 403   {"code":403000,"message":"Forbidden","data":null,…}
```

### A body-field operation (11 of them)

```diff
# openapi_v1/bots/schemas.py:46 — BotCreate (model_config = extra="forbid")
 class BotCreate(BaseModel):
     model_config = _STRICT
+    user_id: str = Field(min_length=1, max_length=256,
+                         description="The end user this request acts for.")
     bot_name: str = Field(...)
```

```diff
# openapi_v1/bots/router.py:219 — create_bot
     body: BotCreate,
     request: Request,
     principal: PrincipalDep,
     ...
-    owner_id = caller_owner_id(principal)
+    owner_id = resolve_request_user(principal, body.user_id)
```

The 11 models and their operations:

| Model | Operation |
| --- | --- |
| `BotCreate` | `POST /openapi/v1/bots` |
| `BotUpdate` | `PUT …/bots/{bot_id}` |
| `ApprovalModeSet` | `PUT …/bots/approvals/{bot_id}/mode` |
| `IdentityFileWrite` | `PUT …/bots/identity/{bot_id}/{file_type}` |
| `McpConfigWrite` | `PUT …/bots/mcp/servers/{server_code}/config` |
| `ResourceCreate` | `POST …/bots/resources` |
| `ResourceUpdate` | `PUT …/bots/resources/{resource_id}` |
| `RoutineCreate` | `POST …/bots/routines` |
| `RoutineUpdate` | `PATCH …/bots/routines/{routine_id}` |
| `SessionCreate` | `POST …/bots/sessions/{bot_id}` |
| `SessionUpdate` | `PATCH …/bots/sessions/{bot_id}/{session_id}` |

### `bot_id`, brought in line

`bot_id` is a query parameter on 17 in-scope operations. Only three of them have
a body this API defines, so only three move:

```diff
# openapi_v1/resources/schemas.py:31 — ResourceCreate  (…and ResourceUpdate:40)
 class ResourceCreate(BaseModel):
+    user_id: str = Field(...)
+    bot_id: str = Field(description="Bot this resource belongs to.")
     name: str = Field(...)
```

```diff
# openapi_v1/resources/router.py:197 — create_resource
     body: ResourceCreate,
-    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
```

| Operation | `bot_id` today | after |
| --- | --- | --- |
| `POST …/resources` | query | **body** |
| `PUT …/resources/{resource_id}` | query | **body** |
| `PATCH …/routines/{routine_id}` | query | **body** |
| `POST …/routines` | body | body (already correct — the precedent) |
| 14 others (GET ×9, DELETE ×2, bodyless `POST …/routines/{id}/run`, the 2 uploads) | query | query |

`{bot_id}` in a path is untouched on all 28 operations that address a bot that
way.

### Response documentation

```diff
# openapi_v1/contracts.py:84
+USER_SCOPED_ERROR_RESPONSES = {**ERROR_RESPONSES, 403: {"model": ErrorEnvelope,
+    "description": "The request names a user the caller may not act for"}}
-ENGINE_RUNTIME_ERROR_RESPONSES = {**ERROR_RESPONSES, 501: …, 504: …}
+ENGINE_RUNTIME_ERROR_RESPONSES = {**USER_SCOPED_ERROR_RESPONSES, 501: …, 504: …}
```

403 stays **out** of `ERROR_RESPONSES`: `test_openapi_error_schema.py:55` asserts
every operation documents every status in that dict, and Bot Logs cannot return a
403.

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
# openapi_v1/__init__.py:96 — build_public_router
 _PUBLIC_AUTH = [Depends(require_principal)]
+# Declared independently of require_user_id, not merely inside it: a request
+# that also fails query/body validation must still answer 401, and FastAPI
+# skips calling a dependency whose own params failed to validate.
+_USER_SCOPED = [*_PUBLIC_AUTH, Depends(require_user_id)]
```

`_USER_SCOPED` is attached to every group **except** `bot_logs`, which keeps
`_PUBLIC_AUTH` and `ERROR_RESPONSES`. It is what puts the query parameter on the
49 — and, deliberately, on the 11 body operations too, so the loop stays one
list. **Open detail for implementation:** a body operation must not end up
declaring *both*; either exclude the 11 from `_USER_SCOPED` and let their handler
argument carry it, or accept the duplicate. Task 3 resolves this against the
generated document, and the convention test (below) is what pins the answer.

```diff
# adapters/http/app.py:559 — beside _principal_error_handler
+@app.exception_handler(UserIdMismatchError)
+async def _user_id_mismatch_handler(request, exc) -> JSONResponse: ...
```

Per-router edits are mechanical and identical in shape; the counts are: bots 13,
mcp 6, resources 9, routines 7, skills 6, identity 3, sessions 7, engine 3,
approvals 3, models 2, connection 1.

Eight of the 60 do not use the value (`check_bot_name`; `list_mcp_servers`,
`list_mcp_tenants`, `get_mcp_server`; `list_resources`, `create_resource`,
`get_resource`, `update_resource`). They declare the parameter and `del` it, with
a comment — the shape `bot_logs/router.py:44` already uses.

## Dependencies

None. No new package, no version bump.

## Risks & Mitigations

- **Risk:** a request that is wrong in two ways answers 422 instead of 401,
  leaking that the credential was fine. Observed in a spike: when
  `require_user_id` is the *only* dependency naming the principal, FastAPI never
  calls it (its own parameter failed validation) and the 401 never fires.
  **Mitigation:** `require_principal` stays declared independently in
  `_USER_SCOPED`; `test_explicit_user_id.py` asserts 401 outranks a missing
  parameter.
- **Risk:** the placement rule decays into per-category habit again — the thing
  the spec cites as the reason to settle it.
  **Mitigation:** a convention test over the generated document, in the shape of
  `test_path_convention.py`, so a new operation fails the build rather than
  review.
- **Risk:** two tests assert the *opposite* of the new contract —
  `test_sessions.py:507` and `test_approvals.py:94` require a caller-supplied
  `user_id` to be a 422. **Mitigation:** they invert to "403 for another user's
  id", and keep asserting 422 for `engine` — that field stays forbidden. The
  `extra="forbid"` config is what keeps the rest of the guard intact.
- **Risk:** five of the 11 models (`IdentityFileWrite`, `ResourceCreate`,
  `ResourceUpdate`, `RoutineCreate`, `RoutineUpdate`) have no `extra="forbid"`,
  so a `user_id` sent today is silently ignored and would now be honoured.
  **Mitigation:** it is honoured only after `resolve_request_user` accepts it, so
  the widest outcome is 403. Not tightened here — that is a separate change.
- **Risk:** ~232 test call sites across 10 files.
  **Mitigation:** the 49 query operations are covered by setting
  `client.params = {"user_id": …}` once per `TestClient` (httpx merges default
  params); only the body operations need per-call edits. Verify the httpx
  behaviour in task 6 before relying on it.

## Alternatives Considered

- **A single required header, `X-Avernet-User-Id`.** One placement for all 60,
  no request-model changes, no per-method rule, and it matches the gateway's own
  delegation sketch (auth design §15 reserves an `xoneid`/`x-end-user-id`
  header). Rejected by the user in favour of the more conventionally RESTful
  reading: the user is an argument of the operation, not transport metadata.
  Recorded because it is the fallback if the three-exception table proves
  unworkable in review.
- **Query parameter everywhere.** Uniform and cheap, and there is a precedent on
  the surface (`GET …/bots/logs/traces?user_id=`). Rejected for the same reason.
- **Preferring the request's id over the credential's, with no check.** A
  privilege escalation: any verified user could read another's data. Not viable
  before delegation exists.
- **Omitting the parameter on the 8 operations that ignore it.** Rejected in the
  spec: it makes a client learn which 8 of 60 differ.

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
def test_every_user_scoped_operation_names_its_user(): ...       # 60/60
def test_placement_follows_the_body_or_query_rule(): ...         # 11 body, 49 query
def test_bot_id_sits_where_the_user_id_sits(): ...               # the 3 moved + 12 unmoved
def test_bot_logs_operations_are_unchanged(): ...                # 5, no parameter, no 403
def test_naming_another_user_is_403_on_both_paths(): ...         # query and body
def test_two_rejected_ids_give_identical_bodies(): ...
def test_missing_parameter_is_422_and_no_caller_is_401(): ...    # precedence
def test_403_is_documented_only_on_user_scoped_operations(): ...
```

Updated in place:

```python
# engine_runtime/test_sessions.py:507  — inverts
def test_caller_supplied_identity_is_rejected():   # engine → 422; user_id → 403
# engine_runtime/test_approvals.py:94   — inverts
# resources/test_resources_handlers.py  — 4 `principal=None` cases now exercise
#                                          resolve_request_user, not each handler
```

Then the module gates from `AGENTS.md`: backend SAST, `tests/community` in full
(the openapi_v1 suite is 499 tests today), changed-line coverage, and singlebox
coverage — `flow_coverage.py:75` notes every `/openapi/v1` route answers 401 in
singlebox, so the E2E surface is unaffected by the new parameter.
