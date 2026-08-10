# Plan — Admit the App-Principal-Only Caller Against an Owner's Grant

Implements `spec.md` in this directory. Issue #950; follows #937.

## Approach

Three changes, in dependency order, plus a blocker cleared first.

**0. Clear the blocker.** `delete_bot` revokes every grant standing against the
bot, inside the deletion. Until this holds, app-only resolution would hand an
application a bot its owner deleted.

**1. Make "an application with no end user" expressible, per route.** Today
`verify_principal_token` refuses any identity set naming no end user. That guard
moves **up one layer, not away**: verification starts admitting a set that names
an end user *or* an application (and still refuses one naming neither), and
`require_principal` — the dependency every public route already depends on,
directly or through `UserIdDep` — takes over refusing the user-less set. Routes
that opt in declare a *different* dependency instead.

The move is a move, not a deletion, and that is what preserves the property the
guard was placed for. The refusal is still in one shared place rather than in 60
handlers; "a rule every handler has to remember is not a rule" still holds. What
changes is only that the shared place is now one the route selects, which is what
"per-route opt-in" requires. A route that says nothing gets `require_principal`
and refuses, so the default stays closed.

Why not keep the guard in the verifier and pass it a per-route flag: the verifier
is transport-agnostic (Rule 7) and route-blind by design, and the middleware that
drives it (`AvernetTenantMiddleware` → `resolve_caller`) runs before routing. Any
route awareness there means a second route table inside the backend, kept in step
with the gateway's by hand. The dependency graph is already the surface's way of
saying what a route requires, and it is already enumerable by a test.

**2. Resolve the acting owner from the grant.** A new dependency replaces
`UserIdDep` on admitted routes only. For a user-bearing caller it behaves exactly
as `require_user_id` does today. For an app-only caller it refuses a `user_id`
parameter outright and resolves the owner from the grant on
`(app_id, bot_id)`, in the request's tenant and env.

**3. Name the admitted operations.** One declared table, consumed by the routers
and by the fail-closed test, and reflected in the gateway's `route_security` and
in the published description.

## Affected Components

| Component | Change |
| --- | --- |
| `core/gateway_principal/verifier.py` | `_require_user_principal` → `_require_admissible_principal`: admit user-or-app, refuse neither. |
| `core/gateway_principal/verifier.py` | `VerifiedCaller.app_id` / `.has_user` accessors. |
| `adapters/http/openapi_v1/dependencies.py` | `require_principal` gains the user requirement; new `require_operating_caller`. |
| `adapters/http/openapi_v1/principal.py` | New `require_acting_owner` / `ActingOwnerDep`. |
| `adapters/http/openapi_v1/app_only_routes.py` *(new)* | The declared allow-list, single source. |
| `adapters/http/openapi_v1/errors.py`, `responses.py`, `app.py` | `GrantNotResolvableError` → masked 404. |
| `core/bot_app_grant/services/grant_service.py` | `resolve_owner`, `revoke_all_for_bot`. |
| `core/repository/protocols/bot/app_grant.py` + implementation | `find_by_app_and_bot`, `revoke_all_for_bot`. |
| `api/bot_app_grant_service.py` | Protocol additions. |
| `core/bot_management/services/bot_service.py` | `delete_bot` revokes grants. |
| `adapters/http/openapi_v1/{bots,engine_runtime/sessions,resources}/router.py` | Swap the dependency on admitted routes. |
| `src/gateway/configs/application.yaml` | `route_security` rules for the admitted paths. |
| `core/bot_app_grant/README.md`, `docs/openapi-v1/` | Carried-gap note removed; admitted set documented. |

## Data Model Changes

**None.** No column, index or table changes. This is the point of #937 having
shipped the record first: `ac_bot_app_grant` already holds `(avernet_tenant,
app_id, bot_id, owner_id, env)` under `uk_bot_app_grant_scope`, and a row exists
iff access is in force, so resolution needs no status filter and no migration.

`idx_bot_app_grant_app_owner` is `(avernet_tenant, app_id, owner_id, env)` — it
leads with tenant and app, so the new `(tenant, app_id, bot_id, env)` lookup uses
its first two columns and filters the rest. An application holds few grants, so
the residual filter is over a handful of rows. **No new index**; adding one for a
lookup whose selectivity is already this good would be speculative.

`owner_id` stays at 256 (spec Out of Scope). Comparison is byte-exact under the
deployed `utf8mb4_bin` collation, which is what resolution assumes.

## API / Interface Changes

### The admitted operations

| Method | Path | Family |
| --- | --- | --- |
| `GET` | `/openapi/v1/bots/{bot_id}` | bot lifecycle |
| `GET` | `/openapi/v1/bots/{bot_id}/status` | bot lifecycle |
| `POST` | `/openapi/v1/bots/{bot_id}/restart` | bot lifecycle |
| `GET` | `/openapi/v1/bots/sessions/{bot_id}` | sessions |
| `POST` | `/openapi/v1/bots/sessions/{bot_id}` | sessions |
| `GET` | `/openapi/v1/bots/sessions/{bot_id}/{session_id}` | sessions |
| `PATCH` | `/openapi/v1/bots/sessions/{bot_id}/{session_id}` | sessions |
| `DELETE` | `/openapi/v1/bots/sessions/{bot_id}/{session_id}` | sessions |
| `GET` | `/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages` | messages |
| `DELETE` | `/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages` | messages |
| `GET` | `/openapi/v1/bots/resources/{resource_id}/download` | file download |

Eleven operations. Everything else on the surface refuses the app-only caller.

### `user_id` on an admitted operation

`user_id` becomes **optional in the schema** on these eleven operations only, and
conditionally required in behavior:

- caller names an end user → `user_id` **required**, must equal the caller
  (`422` absent, `403` mismatched) — byte-identical to today;
- caller is app-only → `user_id` **must be absent** (`403` if supplied), and the
  owner comes from the grant.

`str | None` here is an intentional contract state, not defensive widening: an
app-only caller genuinely supplies none, and both branches define behavior for
their own case. That is exactly the test AGENTS.md sets for an optional type.

The other 45 user-scoped operations keep `UserIdDep` and a required `user_id`
verbatim. This is why the swap is per route rather than a change to
`require_user_id`.

### Refusals

| Situation | Answer |
| --- | --- |
| App-only caller on a non-admitted route | `401 Unauthorized` |
| Access-key-only or bot-only caller, anywhere | `401 Unauthorized` |
| App-only caller, no live grant for `(app, bot)` | `404 Not found` (byte-identical to bot-not-found) |
| App-only caller supplying `user_id` | `403 Forbidden` |
| User caller, `user_id` naming another user | `403 Forbidden` (unchanged) |

The `404` masks. An application that guesses a bot id it holds no grant on gets
the same answer as for a bot that does not exist, so the surface discloses no bot
it is not authorized for.

### Gateway `route_security`

The admitted paths become `{user: optional, app: optional}`:

```yaml
"/openapi/v1/bots/sessions/**":
  user: optional
  app: optional
```

Both optional, because the rule must admit *either* shape and the table cannot
express "at least one of". `_runner.py` resolves each declared identity and
returns those present; with neither present the set is empty, the gateway adds no
principal header, and the backend answers `401` from `require_principal`. So
"neither" is still refused — one hop later than before, at the component rather
than at the edge. That relocation is named in Risks.

Declaring `app` at all is what makes any of this possible: the runner resolves
only declared identities, so on a `user: required` rule an App credential never
reaches the signed principal.

The bot-lifecycle rules must be method-qualified — `GET /openapi/v1/bots/{bot_id}`
opts in while `PUT` and `DELETE` on the same path must not — following the
precedent `POST …/authorized-apps` set.

## Key Files & Functions

### `core/gateway_principal/verifier.py`

- `_require_user_principal` → `_require_admissible_principal(principals)`: refuse
  a set naming neither a `user` nor an `app`; keep the blank-subject-id check for
  the user it does name. Rewrite the docstring: it currently points at this issue
  as the place to lift the guard, so it must now say where the guard *went* and
  why that placement still holds for routes not yet written.
- `VerifiedCaller.has_user -> bool` and `VerifiedCaller.app_id -> int | None`
  (`None` = "this set names no application", a real contract state).
- `VerifiedCaller.user_id` keeps its `""` fallback and gains a docstring note:
  `""` is now *reachable* — an app-only caller — and `caller_owner_id` turning it
  into a `401` is exactly the wanted answer on a non-admitted route.
- `VerifiedCaller.tenant`: its docstring claims "asserts no tenant" always means
  "a user and nothing else". Still true (an app asserts a tenant), but the
  reasoning cites `_require_user_principal`; update the citation.

### `adapters/http/openapi_v1/dependencies.py`

```python
async def require_principal(connection) -> Principal:      # + names an end user
async def require_operating_caller(connection) -> Principal  # user OR app-only
```

`require_principal` keeps its name and its meaning for the 45 operations that
have it today; the added check is the guard moving in from the verifier. Both
funnel every refusal into the same `MissingPrincipalError` / `1008`, so a caller
still cannot tell which half failed.

`resolve_avernet_tenant` is unchanged and now resolves an app-only caller's
tenant from its `AppPrincipal` — previously such a token failed verification and
fell back to the default. Safe on a non-admitted route because the route still
`401`s before touching data, which is the same argument its docstring already
makes; extend that docstring to cover the new case explicitly.

### `adapters/http/openapi_v1/app_only_routes.py` (new)

```python
APP_ONLY_OPERATIONS: frozenset[tuple[str, str]]   # (method, FastAPI path template)
```

Literal FastAPI path templates, not patterns — the test compares them against
`route.path` on the mounted app, so a typo fails loudly rather than silently
matching nothing. One module so the routers, the test and the description
generator read the same list.

### `adapters/http/openapi_v1/principal.py`

```python
async def require_acting_owner(
    connection: HTTPConnection,
    caller: Annotated[Principal, Depends(require_operating_caller)],
    user_id: Annotated[str | None, Query(alias="user_id", min_length=1)] = None,
) -> str
ActingOwnerDep = Annotated[str, Depends(require_acting_owner)]
```

- user-bearing → delegate to the existing comparison; missing `user_id` raises
  the same `422` FastAPI produces today (raised explicitly, since the parameter
  is now schema-optional — a detail the tests must pin).
- app-only → `user_id` present ⇒ `UserIdMismatchError`; else `_request_bot_id`
  then `grants.resolve_owner(...)`.

`_request_bot_id(connection)` reads `connection.path_params["bot_id"]`, falling
back to `connection.query_params["bot_id"]` for the resources route, whose
`bot_id` is a required query parameter. Path first: a path segment addresses the
resource, so where both exist the path is what the operation is about. Absent
both ⇒ refuse, which is unreachable for a declared route and is the fail-closed
answer for one added to the list by mistake.

The `_for_log` bounding already in this module applies to the app-only refusals
too — the rejected `user_id` is still caller-chosen text.

### `core/bot_app_grant`

```python
def resolve_owner(self, *, app_id: int, bot_id: str) -> str    # raises GrantNotFoundError
def revoke_all_for_bot(self, *, bot_id: str, owner_id: str) -> int
```

`resolve_owner` raises rather than returning `str | None`: "not authorized" is an
outcome the caller must handle, and a `None` here would be one `if` away from
being scoped by nothing. The adapter maps it to the masked `404`.

Repository additions:

```python
def find_by_app_and_bot(self, app_id: int, bot_id: str) -> Optional[BotAppGrantRecord]
def revoke_all_for_bot(self, bot_id: str, owner_id: str) -> int
```

`find_by_app_and_bot` takes **no `owner_id`** — that is the whole point, it is
what the lookup produces. It takes no tenant either: `register_avernet_tenant_guard`
appends the tenant predicate to every read, so the row it can return is
necessarily in the request's tenant. `env` is `get_current_env()` as everywhere
else in this repository.

The unique key makes at most one row match `(tenant, app_id, bot_id, env)` per
owner. Two owners cannot hold the same `bot_id` (`ac_bots.bot_id` is globally
unique for bots created since the id generator landed), but the *table* does not
enforce that, so the query must be deterministic: order by `id` and take the
first, and log a warning if more than one row matched. Silently picking one would
mean silently choosing whose data the application reads.

`revoke_all_for_bot` deletes every live row for the bot and appends one `revoked`
log event per deleted row, in one `transactional_orm_session()` — the same atomicity
argument `revoke` already makes. Log rows are built from the live rows, not from
arguments, so the history records the app name as it stood at consent.

### `core/bot_management/services/bot_service.py`

In `delete_bot`, after the ownership check and **before** `soft_delete_by_owner`:

```python
self._grant_service_provider().revoke_all_for_bot(bot_id=bot_id, owner_id=user_id)
```

Before the soft delete, so a revocation failure aborts the deletion and leaves a
consistent pair; after it, a failure would leave a deleted bot with live grants —
the exact state this task exists to prevent. Failures propagate (AGENTS.md: never
swallow a failed write).

Injected as a provider callable, following `_device_service_provider`, which is
how this service already takes a late-bound collaborator. `BotAppGrantService`
consumes `BotRepository` from `core.repository`, never `bot_management`, so the
direction is acyclic.

### Description regeneration

`USER_ID_DESCRIPTION` is one shared string across all 56 operations and must stay
that way for the 45. The eleven admitted operations get their own description
constant stating that the parameter is required for a user caller and refused for
an application caller. Then regenerate the published description exactly as
#937's Task 12 did.

## Test Strategy

The fail-closed property is the one that must be a *test*, not a review habit.

1. **Route inventory (the anti-inheritance test).** Enumerate every route on the
   built public app. Assert: every route depends on `require_principal` or
   `require_operating_caller` and never both; the set depending on
   `require_operating_caller` equals `APP_ONLY_OPERATIONS` exactly, compared as a
   set of `(method, path)` so a route added to the surface *or* to the list
   without the other fails. Extends `tests/.../test_principal_seam.py`, which
   already enumerates routes this way.
2. **Verifier.** App-only set admitted; user+app admitted; access-key-only and
   bot-only refused; blank user subject id still refused; `app_id` read off the
   set; contradictory-tenant behavior unchanged.
3. **Admitted route, app-only, happy path** — owner resolved from the grant,
   handler scoped by it, response identical to the same call by the owner.
4. **Admitted route, app-only, no grant** → `404`, byte-identical to a
   nonexistent bot; **with `user_id`** → `403`; **grant for another bot** →
   `404`; **grant held by another application** → `404` (resolution never widens).
5. **Non-admitted route, app-only** → `401`, sampled across groups plus one per
   family adjacent to an admitted route (`PUT`/`DELETE /bots/{bot_id}`,
   `POST /bots/resources/upload`).
6. **User callers unchanged** — the existing suites must pass untouched; that is
   the "nothing else changes" criterion, so no expectation may be edited to
   accommodate this feature. Any edit to an existing expectation is a finding.
7. **Deletion revokes.** Grant then delete → the grant is gone, one `revoked` log
   row exists, and an app-only call against the deleted bot is refused by the
   resolution path. Delete with no grants succeeds. A repository failure aborts
   the deletion.
8. **Gateway.** `RouteSecurity.resolve` returns the expected requirement for each
   admitted path and, method-qualified, the unchanged one for its non-admitted
   siblings.

## Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| The guard's move silently opens a route that declares no principal dependency at all. | The route-inventory test asserts *every* route declares one of the two. It already exists in weaker form (`test_public_routes_require_principal`) and is strengthened rather than replaced. |
| `{user: optional, app: optional}` moves the unauthenticated refusal from the gateway edge to the backend. | Refusal still happens, at `require_principal`, before any handler. Pinned by test 5. Named here because it changes *where* an unauthenticated request dies, which matters for edge rate-limiting and for reading gateway logs. |
| `resolve_avernet_tenant` now returns an app's tenant on routes that will `401`. | No data is reachable — the route refuses first, the same argument the docstring already makes for the default fallback. Test 5 covers it. |
| Two grant rows for one `bot_id` under different owners make resolution ambiguous. | Deterministic order plus a warning log; the unique key and globally-unique bot ids make it unreachable in practice, and the log says so if it ever is not. |
| Revoking inside `delete_bot` adds a write to a long method with several failure paths. | Placed before the soft delete so a failure aborts cleanly; failures propagate. Test 7 pins both directions. |
| Legacy `default` bots have non-unique ids across tenants. | The tenant guard scopes the lookup, so a `default` grant resolves only within the request's tenant. Worth a comment at the lookup; not a new exposure. |

## Alternatives Considered

- **Relax the verifier and check per handler.** Rejected: it is the arrangement
  the verifier's docstring exists to argue against, and it fails for exactly the
  routes that forget.
- **Keep the guard in the verifier, pass a route flag from `resolve_caller`.**
  Rejected: puts a second route table in the backend, kept in step with the
  gateway's by hand, and pushes routing knowledge into a Rule 7 module.
- **Accept `user_id` and validate it against the resolved owner.** Rejected: the
  application is never told the owner id — its own view of its grants returns bot
  ids and grant times — so this would demand a value the API does not publish.
- **A per-group scope column on the grant.** Rejected as speculative (AGENTS.md);
  no caller has asked, and the allow-list is a property of the surface.
- **Filter deleted bots at resolution instead of revoking on delete.** Rejected:
  it is the carried gap re-implemented per reader, which is what #937 said not to
  do. Revoking makes the invariant hold for every reader at once.

## Rollout

No migration, no config flag. The feature is live when the gateway's
`route_security` rules ship, and inert before that: without the rules the App
identity never reaches the backend's principal, so every request on the admitted
paths is a user request and behaves exactly as today. The backend change is
therefore safe to deploy first, and **must** be — an application reaching the
backend before `require_operating_caller` exists would be refused, which is
correct but makes the order matter for the partner, not for safety.

Rollback is the gateway config: revert the `route_security` rules and the App
identity stops reaching the backend, with no code rollback and no data to undo.

## Dependencies

- `ac_bot_app_grant` and its repository (shipped, #937).
- Gateway `app` identity chain and `route_security` (shipped).
- Nothing blocks: the deletion invariant is Task 1 of this plan, not a
  prerequisite outside it.
