# Plan — Admit the App-Principal-Only Caller Against an Owner's Grant

Implements `spec.md` in this directory. Issue #950; follows #937.

## Approach

The change is smaller than its blast radius suggests, because the seam it needs
was built ahead of it. `user_id` already travels in the request on every
user-scoped operation, and `require_user_id` is already the single place that
decides what it means. Today that decision is an equality check against the
verified caller. This feature replaces the check — not the parameter, not the
schema, not a single handler signature that names a user.

Four parts.

**1. Clear the blocker.** `delete_bot` revokes every grant standing against the
bot, inside the deletion. It matters more now than the issue framed it: with
deletion admitted for an application, an app can delete a bot it was granted, and
its own authorization must go with it.

**2. Make the user-less caller verifiable, and refused by default.**
`verify_principal_token` stops requiring an end user and starts requiring an end
user *or* an application; `require_principal` — which every public route already
depends on, directly or through `UserIdDep` — takes over refusing the user-less
set. The guard **moves up one layer; it is not deleted.** It is still one shared
place rather than sixty handlers, so "a rule every handler has to remember is not
a rule" still holds. What changes is that the shared place is now one a route
selects, which is what per-route opt-in requires. A route that says nothing gets
`require_principal` and refuses.

Not done in the verifier with a per-route flag: the verifier is transport-agnostic
(Rule 7) and route-blind, and the middleware that drives it runs before routing.
Route awareness there means a second route table in the backend, hand-synced with
the gateway's.

**3. Authorize against the grant.** A caller object carries the acting owner and,
for an app-only caller, the application. Where the request names a bot, a
dependency checks the grant before the handler runs. Where it does not, the
handler either narrows its result to the granted set or the route is refused.

**4. Say which operations are in which group**, in one table, consumed by the
routers, the fail-closed test, and the published description.

## The three admission modes

Every public operation is in exactly one group. The group is a property of the
operation's shape, not a taste judgement, which is what makes the table
reviewable.

| Mode | Shape | Rule |
| --- | --- | --- |
| **A — grant-checked** | the request names a bot | admitted iff a live grant exists for `(app_id, bot_id, user_id)` |
| **B — grant-filtered** | the operation returns a set of the owner's bots | admitted; result narrowed to granted bots |
| **C — owner-gated** | no bot dimension, answer concerns the owner's account | admitted iff the app holds ≥1 live grant from that owner |
| **C-open** | no bot dimension, no `user_id`, answer identical for every caller in the tenant | admitted on authentication alone |
| **D — refused** | everything else | `401` |

### Mode A — grant-checked (≈50 operations)

| Group | Operations | Where `bot_id` is |
| --- | --- | --- |
| `bots` | `GET`/`PUT`/`DELETE /{bot_id}`, `POST /{bot_id}/restart`, `GET /{bot_id}/{auth-status,status,passport}`, `GET`/`PUT /{bot_id}/engine-config` | path |
| `sessions` | all 7 | path |
| `approvals` | all 3 | path |
| `engine` | all 3 | path |
| `models` | both | path |
| `connection` | `GET /{bot_id}` | path |
| `identity` | all 3 | path |
| `resources` | all 9 | required query |
| `routines` | the 6 taking `bot_id` | required query |
| `routines` | `POST ""` (create) | request **body** |
| `skills` | `GET ""`, `POST /upload` | required query |
| `skills` | `GET`/`DELETE /{skill_id}`, `POST /{skill_id}/{activate,deactivate}` | **resolved from `skill_id`** |

### Mode B — grant-filtered (2 operations)

- `GET /openapi/v1/bots` — the owner's bot list, narrowed to granted bots.
- `GET /openapi/v1/bots/authorized` — the application's own view. It already
  exists and already takes `user_id`; today it requires a user principal
  alongside the app. Admitting it app-only makes it the discovery endpoint an
  integration needs: *"which of this owner's bots may I reach?"* answered by the
  credential the integration actually holds.

Both are already computable: `BotAppGrantService.list_for_app(app_id, owner_id)`
returns exactly the granted set, filtered against live bots.

### Mode C / C-open (5 operations)

- **C:** `GET /openapi/v1/bots/ceiling` — the owner's bot quota. Gated on holding
  at least one live grant from that owner, so a stranger application learns
  nothing about an account that never authorized it.
- **C-open:** `GET /openapi/v1/bots/check-name` and the three MCP catalogue reads
  (`/mcp/servers`, `/mcp/tenants`, `/mcp/servers/{server_code}`). These carry no
  `user_id`, so there is no owner to gate against. They are admitted on
  authentication alone, and that is not a new exposure — every authenticated
  caller in the tenant already gets the identical answer.

### Mode D — refused (14 operations)

`POST /openapi/v1/bots` (create); the three per-bot authorization operations
(`POST`/`GET`/`DELETE …/authorized-apps`); the five `bots/logs` operations; the
three MCP configuration operations (`GET /servers/{code}/permissions`, `GET`/`PUT
/servers/{code}/config`); and the two `loadtest` endpoints, which have no user
scope and are untouched by this change.

The bot-logs refusal has a reason worth keeping: on that group `user_id` means
*whose traces to read* over a tenant-level observability surface, not *whose call
this is*. A grant does not translate into that meaning, and admitting it would
widen a surface that deliberately requires both a user and an app today.

## Data Model Changes

**None.** `uk_bot_app_grant_scope` is `(avernet_tenant, app_id, bot_id, owner_id,
env)` — exactly the tuple Mode A checks — and a row exists iff access is in
force, so the check needs no status filter and no migration.

The existing `BotAppGrantRepositoryProtocol.find(bot_id, owner_id, app_id)`
already answers Mode A. **No new repository read is needed for authorization**,
which is a direct consequence of `user_id` staying on the wire: the owner is
given, so the lookup is a unique-key probe rather than a search for an owner.

Mode B is `list_for_app`, which exists. Mode C is "does `list_for_app` return
anything", answerable through the same call.

The only new repository member is `revoke_all_for_bot`, for the deletion
invariant.

## API / Interface Changes

**No operation's request or response schema changes.** `user_id` stays required
everywhere it is required today; no parameter is added, removed or made optional.
Only the published *description* of `user_id` changes on admitted operations, to
say what it means for an application caller.

### Refusals

| Situation | Answer |
| --- | --- |
| App-only caller on a Mode D operation | `401 Unauthorized` |
| Access-key-only or bot-only caller, anywhere | `401 Unauthorized` |
| App-only caller, no live grant for `(app, bot, owner)` | `404 Not found` — byte-identical to bot-not-found |
| App-only caller on a Mode C operation with no grant from that owner | `404 Not found` |
| User caller, `user_id` naming another user | `403 Forbidden` (unchanged) |

The `404` masks: an application guessing a bot id it holds no grant on gets the
same answer as for a bot that does not exist.

`403` is deliberately **not** used for a missing grant. On this surface `403`
means "you are authenticated and this is not yours", which confirms the bot
exists. The masked `404` is the shape every other ownership refusal already uses.

### Gateway `route_security`

Enumerating ~55 admitted paths would be a table nobody can review. Enumerate the
**refusals** instead — the shorter, more interesting list:

```yaml
"/openapi/v1/bots/**":
  user: optional
  app: optional

"POST /openapi/v1/bots":            {user: required}
"/openapi/v1/bots/logs/**":         {user: required, app: required}
"/openapi/v1/bots/{bot_id}/authorized-apps/**": {user: required, app: required}
"/openapi/v1/bots/mcp/servers/*/config":        {user: required}
"/openapi/v1/bots/mcp/servers/*/permissions":   {user: required}
```

Both identities optional on the wide rule, because it must admit either shape and
the table cannot express "at least one of". `_runner.py` resolves each declared
identity and returns those present; with neither present the set is empty, the
gateway adds no principal header, and the backend answers `401` from
`require_principal`. "Neither" is still refused — one hop later, at the component
rather than the edge. That relocation is named in Risks.

Declaring `app` at all is what makes any of this possible: the runner resolves
only declared identities, so under `user: required` an App credential never
reaches the signed principal.

**The gateway table is a second expression of the same policy, and the two must
not drift.** The refusal list above is derived from Mode D; a test asserts the
gateway resolves `user: required` for exactly the Mode D paths and the optional
pair for the rest.

## Key Files & Functions

### `core/gateway_principal/verifier.py`

- `_require_user_principal` → `_require_admissible_principal`: refuse a set naming
  neither a `user` nor an `app`; keep the blank-subject-id check for a user it
  does name.
- Rewrite its docstring. The current one names *this issue* as the place to lift
  the guard, so the replacement must say where the guard went and why that
  placement still holds for routes not yet written. A reader arriving from #950
  must not be sent looking for a rule that moved.
- `VerifiedCaller.has_user -> bool`, `VerifiedCaller.app_id -> int | None`
  (`None` = "this set names no application", a real contract state).
- `VerifiedCaller.user_id`'s `""` fallback is now *reachable* (an app-only
  caller); document that `caller_owner_id` turning it into `401` is the wanted
  answer on a Mode D route, not a degraded one.

### `adapters/http/openapi_v1/dependencies.py`

```python
async def require_principal(connection) -> Principal        # + names an end user
async def require_operating_caller(connection) -> Principal # user OR app-only
```

`require_principal` keeps its name and meaning for every Mode D route; the added
check is the guard arriving from the verifier. Both funnel refusals into the same
`MissingPrincipalError` / `1008`.

`resolve_avernet_tenant` is unchanged and now resolves an app-only caller's
tenant from its `AppPrincipal`. Safe on a Mode D route because the route still
`401`s before touching data — the same argument its docstring already makes for
the default fallback; extend it to cover this case.

### `adapters/http/openapi_v1/admission.py` (new)

The policy table and the caller object, in one module so the routers, the test
and the description generator cannot disagree.

```python
class AdmissionMode(StrEnum): GRANT_CHECKED, GRANT_FILTERED, OWNER_GATED, OPEN, REFUSED
ADMISSION: dict[tuple[str, str], AdmissionMode]   # (method, FastAPI path template)

@dataclass(frozen=True)
class ActingCaller:
    owner_id: str
    app_id: int | None            # None = a human caller; no grant check applies
    def require_bot(self, bot_id: str) -> None       # raises GrantNotResolvableError
    def granted_bot_ids(self) -> frozenset[str] | None  # None = no filtering
```

`app_id: int | None` is an intentional contract state — "this caller is a human"
— and every consumer branches on it explicitly. That is the AGENTS.md test for an
optional, not defensive widening.

### `adapters/http/openapi_v1/principal.py`

`require_user_id` gains the branch its own docstring predicted, and keeps its
signature and its required `user_id`:

- caller names an end user → compare with the caller, `403` on mismatch.
  Unchanged, byte for byte.
- app-only caller → `user_id` is the acting owner; authorization is the grant,
  checked by the mode-specific dependency below.

New dependencies built on it:

- `GrantCheckedOwnerDep` — Mode A where `bot_id` is on the wire. Reads
  `connection.path_params["bot_id"]`, falling back to
  `connection.query_params["bot_id"]`; path first, because a path segment
  addresses the resource. Absent both ⇒ refuse (unreachable for a correctly
  placed route; the fail-closed answer for a misplaced one). Calls
  `caller.require_bot(...)`, returns `owner_id`.
- `ActingCallerDep` — Modes A-without-a-wire-`bot_id`, B and C. Returns the
  `ActingCaller`; the handler does the rest, explicitly.

**The grant probe runs once per request** — the dependency is evaluated once and
the result is the returned owner id. It is a unique-key point lookup on an index
that already exists.

### Handlers that do change

Almost all of Mode A is a dependency swap with **no body change**: once the owner
is resolved and the grant checked, `bot_service.get_bot(bot_id, owner_id)`, the
ownership-masked `404` and the tenant guard are the same code on the same values.
Seven operations genuinely change:

| Operation | Change |
| --- | --- |
| `GET /openapi/v1/bots` | narrow to `caller.granted_bot_ids()` when set |
| `GET /openapi/v1/bots/authorized` | scope by the principal's `app_id` when app-only |
| `POST /openapi/v1/bots/routines` | `caller.require_bot(body.bot_id)` after parsing |
| `GET`/`DELETE /skills/{skill_id}`, `POST /skills/{skill_id}/{activate,deactivate}` | resolve the skill's bot, then `caller.require_bot(...)` |

The skill routes need a bot for a `skill_id`. `LocalSkillQueryServiceProtocol`
already resolves a skill for an actor; the plan is to read the skill's `bot_id`
through it and check the grant before acting. Refusing these four instead would
leave the skills group split two-admitted / four-refused for a reason invisible
from outside; admitting them unchecked would let an application reach a skill on
a bot it was never granted, because the underlying service scopes by owner only.

### Mode B filtering

`list_bots` must filter **before** paginating, so the page counts describe the
filtered result. Filtering a page after the fact would leak the size of what was
withheld and would return short pages.

### `core/bot_app_grant` and `core/bot_management`

- Repository: `revoke_all_for_bot(bot_id, owner_id) -> int` — deletes every live
  row for the bot and appends one `revoked` log event per deleted row, in one
  `transactional_orm_session()`, log rows built from the live rows so the recorded
  `app_name` is the one at consent time.
- Service: `revoke_all_for_bot(*, bot_id, owner_id) -> int`; no
  `GrantNotFoundError` — "the bot had no authorizations" is a normal outcome of
  deletion.
- `BotService.delete_bot` calls it **before** `soft_delete_by_owner`, via a
  provider callable following `_device_service_provider`. Before, so a failure
  aborts the deletion; after, a failure would leave a deleted bot with live
  grants. Failures propagate.

## Test Strategy

1. **Route inventory (the anti-inheritance test).** Every route on the built app
   appears in `ADMISSION` exactly once, and its declared dependency matches its
   mode. A route added to the surface without a mode, or given a mode without the
   matching dependency, fails. The failure message names the route and says what
   to do — it fires for someone who has never read this spec.
2. **Verifier.** App-only admitted; user+app admitted; user-only unchanged;
   access-key-only and bot-only refused; blank user subject id still refused.
3. **Mode A.** Grant present → identical response to the owner's own call. No
   grant → `404` compared **byte-for-byte** against the nonexistent-bot response.
   Grant for another bot → `404`. Grant held by another application → `404`.
   Deleted bot → refused.
4. **Mode A without a wire `bot_id`.** The four skill routes and `create_routine`
   refuse when the resolved bot is not granted — the case that would otherwise
   silently pass.
5. **Mode B.** Two bots, one granted → exactly one returned, and the page count
   says one. The owner's own call still returns both. No grants → empty, `200`.
6. **Mode C / C-open.** `ceiling` admitted with a grant, `404` without one;
   `check-name` and the MCP catalogue admitted with no grant at all.
7. **Mode D.** `401` on every one of the fourteen, enumerated from `ADMISSION`
   rather than sampled, so the list cannot rot.
8. **User callers unchanged** — the existing suites pass **with no expectation
   edited**. An edited expectation is a finding, not a fix.
9. **Deletion revokes**, including when the deleting caller is the application,
   which removes its own access. Failure aborts the deletion.
10. **Gateway.** `RouteSecurity.resolve` returns `user: required` for exactly the
    Mode D paths and the optional pair for the rest, driven off the same table.

## Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| The surface admitted here is wide (~55 of 63 operations), so a mistake in one mode assignment is a real authorization bug. | Modes are assigned by operation *shape*, not taste; the table is one reviewable artifact; the inventory test proves the surface matches it. |
| The guard's move silently opens a route declaring no principal dependency at all. | The inventory test asserts every route declares one. Strengthens the existing `test_public_routes_require_principal` rather than replacing it. |
| `{user: optional, app: optional}` moves the unauthenticated refusal from the gateway edge to the backend. | Still refused, at `require_principal`, before any handler. Named because it changes where unauthenticated traffic dies — relevant to edge rate-limiting and to reading gateway logs. |
| The gateway table and `ADMISSION` drift apart. | Test 10 derives the expected gateway resolution from `ADMISSION`. |
| A grant probe per Mode A request adds a DB read to ~50 operations. | Unique-key point lookup on an existing index, once per request. Only for app-only callers — a human caller's path is untouched. |
| Mode B filtering applied after pagination would leak counts and return short pages. | Filter before paginate; test 5 asserts the count. |
| The four skill routes depend on resolving a bot from a `skill_id`. | If resolution cannot be done cleanly before the handler, the fallback is to refuse those four and say so — never to admit them unchecked. |
| Legacy `default` bot ids are non-unique across tenants. | The tenant guard scopes the grant lookup, so a `default` grant is only ever matched within the request's tenant. Comment at the lookup. |

## Alternatives Considered

- **Drop `user_id` on app-only calls and derive the owner from
  `(app_id, bot_id)`.** Rejected on review: `user_id` was moved into the request
  precisely so an operation would have somewhere to name a user when the identity
  set stops carrying one. Deriving it would make the parameter optional across the
  surface, change ~56 published schemas, and discard the check that makes a
  guessed `bot_id` useless.
- **Relax the verifier and check per handler.** Rejected: the arrangement the
  verifier's docstring exists to argue against; it fails for the routes that
  forget.
- **Pass a route flag into `resolve_caller`.** Rejected: a second route table in
  the backend, hand-synced with the gateway's, inside a Rule 7 module.
- **Refuse every operation that does not name a bot.** Rejected on review: it
  refuses `list_bots`, so an integration cannot discover its own scope and the
  owner must enumerate bot ids out of band.
- **Enumerate admitted paths in the gateway.** Rejected: ~55 rules nobody can
  review, against 6 for the refusals.
- **A per-group scope column on the grant.** Rejected as speculative.

## Rollout

No migration, no config flag. The backend change is inert until the gateway rules
ship: without them the App identity never reaches the backend's principal, so
every request is a user request and behaves exactly as today. Deploy the backend
first.

Rollback is the gateway config alone: revert the `route_security` rules and the
App identity stops reaching the backend. No code rollback, no data to undo.

## Dependencies

- `ac_bot_app_grant`, its repository and `list_for_app` (shipped, #937).
- Gateway `app` identity chain and `route_security` (shipped).
- Nothing external blocks; the deletion invariant is Task 1 here.
