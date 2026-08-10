# Plan — Admit the App-Principal-Only Caller Against a User's Grant

Implements `spec.md` in this directory. Issue #950; follows #937.

## Approach

Five parts, in dependency order.

**1. Reshape the record.** `ac_bot_app_grant` gains `user_id`, the delegating
user, distinct from `owner_id`, the bot's owner. The unique key rekeys onto it.
The tables are deployed but empty, so this is an `ALTER` with nothing to
backfill — a window that closes the moment the first grant is written.

**2. Let a collaborator delegate.** `grant_authorized_app` stops resolving the
bot through an owner-scoped read and starts adjudicating "may this user operate
this bot", which is the bar `core/engine_runtime/gate.py` already applies. The
owner gains visibility of, and override on, every grant against their bot.

**3. Clear the deletion blocker.** `delete_bot` revokes every grant standing
against the bot, whoever delegated it, inside the deletion.

**4. Make the user-less caller verifiable, and refused by default.**
`verify_principal_token` stops requiring an end user and starts requiring an end
user *or* an application; `require_principal` — which every public route already
depends on — takes over refusing the user-less set. The guard **moves up one
layer; it is not deleted.** It is still one shared place rather than sixty
handlers. What changes is that the shared place is now one a route selects,
which is what per-route opt-in requires; a route that says nothing gets
`require_principal` and refuses.

Not done in the verifier with a per-route flag: the verifier is transport-agnostic
(Rule 7) and route-blind, and the middleware that drives it runs before routing.
Route awareness there means a second route table in the backend, hand-synced
with the gateway's.

**5. Authorize against the grant, and re-adjudicate the user.** The app-only
request names its delegating user; the grant supplies the bot's owner; the
existing gate then decides whether that user may operate that bot — live, on
every request.

## The invariant, and where it is enforced

> An application's reach is exactly its granting user's reach, and never more.

It holds because of *where* the checks sit, not because anything compares two
capability sets:

| Question | Answered by | Consulted |
| --- | --- | --- |
| May this application act as this user at all, on this bot? | the grant record | per request |
| May this user operate this bot, and at what level? | the existing collaborator gate | per request, unchanged |

The second is the one that makes the invariant *live* rather than a snapshot.
Nothing is copied into the grant at consent time — no level, no capability list
— so nothing can go stale. Removing U as a collaborator on B ends the
application's access to B on the next request, with no revocation and no
cleanup, because the gate that refuses U now refuses the application acting as
U. Conversely, promoting U from member to admin widens the application in step,
which is the same rule read forwards.

This is also why the grant needs no scope column: the bound is a person's live
authority, which the platform already models.

## Data Model Changes

### `ac_bot_app_grant`

```
+ user_id VARCHAR(256) NOT NULL COLLATE utf8mb4_bin   -- the delegating user
  owner_id VARCHAR(256) NOT NULL                       -- the bot's owner (kept)

- UNIQUE KEY uk_bot_app_grant_scope (avernet_tenant, app_id, bot_id, owner_id, env)
+ UNIQUE KEY uk_bot_app_grant_scope (avernet_tenant, app_id, bot_id, user_id, env)

- KEY idx_bot_app_grant_app_owner (avernet_tenant, app_id, owner_id, env)
+ KEY idx_bot_app_grant_app_user  (avernet_tenant, app_id, user_id,  env)

  KEY idx_bot_app_grant_bot_owner (avernet_tenant, bot_id, owner_id, env)   -- unchanged
```

**`user_id` replaces `owner_id` in the key rather than joining it**, and the
reason is a hard limit rather than a preference. The key is 2392 bytes today
(`avernet_tenant` 64×4 + `app_id` 8 + `bot_id` 256×4 + `owner_id` 256×4 + `env`
20×4). Adding a 256-character column gives 3416, past InnoDB's 3072-byte cap —
the same wall that holds `owner_id` at 256 in the first place. Swapping keeps it
at 2392.

It is also the correct semantics. Two collaborators may each authorize the same
application for the same bot; those are two delegations with two different
scopes of authority, independently withdrawable. Keyed on `owner_id` they would
collide and the second would be silently swallowed by the idempotent grant path.

`owner_id` stays as a non-key column: it is what the app-only path reads to
address the bot, and denormalizing it keeps that read from needing a bot lookup.

`idx_bot_app_grant_bot_owner` is untouched. Its `(avernet_tenant, bot_id)`
prefix already serves both new bot-scoped reads — the owner's "every grant on my
bot, whoever delegated it" and the deletion sweep — neither of which supplies an
`app_id` to reach past the unique key's second column.

### `ac_bot_app_grant_log`

Gains `user_id` on the same terms. Free: the table has no unique key by design,
so nothing is constrained by the addition. Its bot-history index is unchanged.

### Migration

The tables ship in `core/bot_app_grant/sql/2026_08_10_bot_app_grant.sql` and are
**deployed but unpopulated**, so the `ALTER` has nothing to backfill and no
`NOT NULL` default to invent. That window closes as soon as one grant is
written; if it has closed by the time this lands, the migration needs a backfill
(`user_id := owner_id`, correct for every row #937 could have produced, since
only owners could grant) and this plan must be revised to say so rather than
assumed still open.

Two files must move together, and the README already names their drift as the
failure the DDL exists to prevent: a new dated migration carrying the `ALTER`s,
and the original `CREATE` updated so a fresh install lands in the identical
shape. A task checkbox compares them column for column.

`user_id` declares `COLLATE utf8mb4_bin` explicitly. The deployed table pins that
collation where the checked-in `.sql` does not — the drift #950 names as a
separate follow-up — so an `ALTER` without an explicit collation would inherit
`utf8mb4_bin` on the deployed table and the default elsewhere. This is the
column every app-only request resolves on; it must compare byte-exact in both
places, whatever happens to the follow-up.

## The three admission modes

### First: the surface has two id models, not one

Easy to miss, because both spell their parameter `user_id`.

**User-scoped groups** (`bots`, `resources`, `routines`, `skills`, `identity`,
`mcp`): `user_id` is the caller, and the bot is resolved by
`get_by_id_and_owner(bot_id, user_id)`. Caller and owner are necessarily the
same person; a non-owner gets a masked `404`. **A shared bot is unreachable here
for a human too**, so an application acting as that human inherits the same
limit automatically — no special handling, and the invariant holds by
construction.

**Engine-runtime groups** (`sessions`, `engine`, `models`, `approvals`,
`connection` — 16 operations): `user_id` is the **caller**, and `owner_id` names
the **addressed bot's owner**, defaulting to the caller. `resolve_operable_bot`
adjudicates, admitting the owner *or a member-level collaborator*. This is where
a shared bot is reachable, and therefore where the delegation actually pays off.

For an app-only caller on these, `owner_id` comes **from the grant record**, not
from the request. An explicitly supplied value must equal it; anything else is
refused before the resolve. Leaving it to fail downstream would work today by
coincidence — two independent refusals lining up — and coincidence is not a
boundary.

### The modes

| Mode | Shape | Rule |
| --- | --- | --- |
| **A1 — grant-checked, user-scoped** | names a bot; caller *is* the owner | live grant for `(app_id, bot_id, user_id)` |
| **A2 — grant-checked, owner-addressed** | names a bot *and* an owner | A1, plus `owner_id` taken from the record; a supplied value must match |
| **B — grant-filtered** | returns a set of bots | admitted; narrowed to granted bots |
| **C — user-gated** | no bot dimension, concerns the user's account | ≥1 live grant from that user |
| **C-open** | no bot dimension, no `user_id`, tenant-identical answer | authentication alone |
| **D — refused** | everything else | `401` |

### Mode A1 — user-scoped (≈34 operations)

`bots` (`GET`/`PUT`/`DELETE /{bot_id}`, `POST /{bot_id}/restart`,
`GET /{bot_id}/{auth-status,status,passport}`, `GET`/`PUT /{bot_id}/engine-config`);
`identity` (3, path); `resources` (9, required query); `routines` (6, required
query) and `POST ""` (body); `skills` `GET ""` / `POST /upload` (query) and the
four `{skill_id}` routes (**resolved from `skill_id`**).

### Mode A2 — owner-addressed (16 operations)

`sessions` (7), `engine` (3), `approvals` (3), `models` (2), `connection` (1).
All take `bot_id` in the path and `OwnerIdDep` alongside `UserIdDep`.

`connection` runs its gate inside `EngineConnectionService` rather than through
`gating.py`, so the owner-substitution must be applied at its own seam. A
comment must say so: it is the one group where reading the router does not show
the adjudication.

### Mode B — grant-filtered (2 operations)

- `GET /openapi/v1/bots` — the user's own bots, narrowed to granted ones.
- `GET /openapi/v1/bots/authorized` — the application's own view, and now the
  **complete** one: a granted bot the delegating user does not own never appears
  in the list above, so without this it would be undiscoverable. It already
  takes `user_id`; admitting it app-only makes it reachable by the credential an
  integration actually holds.

### Mode C / C-open (5 operations)

- **C:** `GET /openapi/v1/bots/ceiling` — gated on ≥1 live grant from the named
  user, so a stranger application learns nothing about an account that never
  authorized it.
- **C-open:** `GET /openapi/v1/bots/check-name` and the three MCP catalogue
  reads. No `user_id`, so nothing to gate on; every authenticated caller in the
  tenant already gets the identical answer.

### Mode D — refused (14 operations)

`POST /openapi/v1/bots`; the three per-bot authorization operations; the five
`bots/logs` operations; the three MCP configuration operations; the two
`loadtest` endpoints (no user scope, untouched).

The bot-logs refusal has a reason worth keeping: there `user_id` means *whose
traces to read* over a tenant-level observability surface, not *whose call this
is*. A grant does not translate into that meaning.

## API / Interface Changes

**No request schema changes.** `user_id` stays required everywhere it is
required today; no parameter is added, removed or made optional. One **additive**
response change: the owner's authorization listing gains the delegating user,
without which the owner cannot tell who let an application in.

### Granting, reshaped

`grant_authorized_app` today resolves the bot through
`bot_service.get_bot(bot_id, owner_id=caller)` — an owner-scoped read, which is
exactly what confines grants to owned bots. It becomes: resolve the bot and
adjudicate that the caller may **operate** it (owner, or collaborator at member
level or above), then record `user_id = caller` and `owner_id = the resolved
bot's owner`.

The refusal shape does not change: a caller who may not operate the bot gets the
masked `404` a caller naming a nonexistent bot gets.

`authorized_apps`' module docstring currently argues the opposite position —
that the grant bar is *deliberately narrower* than the operate bar, because
"handing a machine credential durable, human-free access to a bot is not the
same power as driving it." That argument is now overruled, and the docstring
must be rewritten rather than left contradicting the code. The counter-argument
to record: a delegation is bounded by the delegator's own live access and
re-adjudicated per request, so it confers no power the delegator does not
already hold and cannot outlive it.

### The owner's override

- `GET /bots/{bot_id}/authorized-apps` — the owner sees **every** live grant on
  the bot with its delegating user; a non-owner collaborator sees only their own.
- `DELETE /bots/{bot_id}/authorized-apps/{app_id}` — the owner may withdraw any;
  a collaborator only their own. Since the key is now
  `(app_id, bot_id, user_id)`, a withdrawal must name *which* delegation: the
  owner's call targets a grant by its delegating user, and the collaborator's
  implicitly targets their own.

That last point is a genuine API question the spec leaves open by implication:
`DELETE …/{app_id}` no longer identifies one row when two users delegated the
same app. **Resolution: the owner's withdrawal removes every delegation of that
application on that bot.** It matches what an owner means by "revoke this app's
access to my bot", and it needs no new path segment. A collaborator's withdrawal
removes only their own.

### Refusals

| Situation | Answer |
| --- | --- |
| App-only caller on a Mode D operation | `401` |
| Access-key-only or bot-only caller, anywhere | `401` |
| App-only caller, no live grant for `(app, bot, user)` | `404` — byte-identical to bot-not-found |
| App-only caller whose delegating user may no longer operate the bot | `404` — from the existing gate, unchanged |
| App-only caller naming an owner other than the grant's | `404` |
| App-only caller on a Mode C operation with no grant from that user | `404` |
| User caller, `user_id` naming another user | `403` (unchanged) |

`403` is deliberately **not** used for a missing grant: on this surface it means
"you are authenticated and this is not yours", which confirms the bot exists.

### Gateway `route_security`

Enumerating ~55 admitted paths would be a table nobody can review. Enumerate the
**refusals** instead:

```yaml
"/openapi/v1/bots/**":            {user: optional, app: optional}
"POST /openapi/v1/bots":          {user: required}
"/openapi/v1/bots/logs/**":       {user: required, app: required}
"/openapi/v1/bots/{bot_id}/authorized-apps/**": {user: required, app: required}
"/openapi/v1/bots/mcp/servers/*/config":      {user: required}
"/openapi/v1/bots/mcp/servers/*/permissions": {user: required}
```

Both identities optional on the wide rule, because it must admit either shape
and the table cannot express "at least one of". `_runner.py` resolves each
declared identity and returns those present; with neither present the set is
empty, the gateway adds no principal header, and the backend answers `401` from
`require_principal`. "Neither" is still refused — one hop later, at the component
rather than the edge. Named in Risks.

Declaring `app` at all is what makes any of this possible: the runner resolves
only declared identities, so under `user: required` an App credential never
reaches the signed principal.

## Key Files & Functions

### `core/gateway_principal/verifier.py`

- `_require_user_principal` → `_require_admissible_principal`: refuse a set
  naming neither a `user` nor an `app`; keep the blank-subject-id check.
- Rewrite its docstring — it currently names *this issue* as the place to lift
  the guard, so the replacement must say where the guard went and why that
  placement still holds for routes not yet written.
- `VerifiedCaller.has_user`, `VerifiedCaller.app_id -> int | None` (`None` = "no
  application in this set", a real contract state).
- `VerifiedCaller.user_id`'s `""` fallback is now reachable; document that
  `caller_owner_id` turning it into `401` is the wanted answer on a Mode D route.

### `adapters/http/openapi_v1/dependencies.py`

`require_principal` gains the end-user requirement (the guard arriving from the
verifier); `require_operating_caller` admits user-bearing **or** app-only. Both
funnel refusals into the same `MissingPrincipalError` / `1008`.

`resolve_avernet_tenant` now resolves an app-only caller's tenant from its
`AppPrincipal`; safe on a Mode D route because the route still `401`s before
touching data — the argument its docstring already makes, extended.

### `adapters/http/openapi_v1/admission.py` (new)

```python
class AdmissionMode(StrEnum): A1, A2, B, C, OPEN, REFUSED
ADMISSION: dict[tuple[str, str], AdmissionMode]   # every public operation

@dataclass(frozen=True)
class ActingCaller:
    user_id: str                     # the delegating user, or the human caller
    app_id: int | None               # None = a human caller; no grant applies
    def require_bot(self, bot_id: str) -> str          # → the bot's owner_id
    def granted_bot_ids(self) -> frozenset[str] | None # None = no filtering
```

`require_bot` returns the owner because A2 needs it and A1 discards it — one
lookup, one place. `app_id: int | None` is an intentional contract state ("this
caller is a human") and every consumer branches on it explicitly, which is the
AGENTS.md test for an optional.

### `adapters/http/openapi_v1/principal.py` and `engine_runtime/params.py`

`require_user_id` keeps its signature and its required `user_id`, and gains the
branch its own docstring predicted: compare with the caller when there is one,
consult the grant when there is not.

`resolve_owner_id` (A2's `OwnerIdDep`) gains the app-only branch: default to the
**grant's** `owner_id` rather than to `user_id`, and refuse a supplied value that
disagrees. This is the single point where the app-only path differs from the
human path on those 16 operations.

`GrantNotResolvableError` → `(404, "Not found")` byte-identical to
`BotNotFoundError`, with an `app.py` handler alongside `UserIdMismatchError`,
because a dependency-raised error never reaches `@envelope_errors`.

### Handlers that change

Almost all of A1/A2 is a dependency swap with **no body change**: once the user
is established and the grant checked, the downstream calls are the same code on
the same values. Seven operations genuinely change: the two listings (filter),
`POST /routines` (`require_bot(body.bot_id)` after parsing), and the four
`skills/{skill_id}` routes (resolve the skill's bot, then check).

`list_bots` must filter **before** paginating, so page counts describe the
filtered result; filtering after would leak the size of what was withheld and
return short pages.

### `core/bot_app_grant`

- `BotAppGrantRecord` gains `user_id`; `grant()` takes it.
- `find(bot_id, user_id, app_id)` — the same member, rekeyed from `owner_id` to
  `user_id`. **No new read is needed for authorization**, which follows from
  `user_id` staying on the wire: the delegating user is given, so the lookup is a
  unique-key probe.
- `list_for_app(app_id, user_id)` — rekeyed. Its liveness filter currently runs
  `list_live_bot_ids_by_owner(owner_id)`, which is **wrong under the new model**:
  granted bots need not belong to the delegating user, so that filter would drop
  every shared one. It becomes a liveness check by `bot_id`.
- `list_for_bot(bot_id)` — no longer takes `owner_id` as a scope; the caller
  decides whether to narrow to one delegating user.
- `revoke(bot_id, user_id, app_id)` — rekeyed; plus an owner-override form that
  removes every delegation of one application on one bot.
- `revoke_all_for_bot(bot_id) -> int` — the deletion sweep, whoever delegated.
  Deletes every live row and appends one `revoked` event per row in one
  `transactional_orm_session()`, log rows built from the live rows.

### `core/bot_management/services/bot_service.py`

`delete_bot` calls `revoke_all_for_bot` **before** `soft_delete_by_owner`, via a
provider callable following `_device_service_provider`. Before, so a failure
aborts the deletion; after, a failure would leave a deleted bot with live
grants. Failures propagate.

## Test Strategy

1. **Route inventory (the anti-inheritance test).** Every route on the built app
   appears in `ADMISSION` exactly once and declares the dependency its mode
   requires. A route added without a mode, or a mode without the dependency,
   fails with a message naming the route and what to do.
2. **Verifier.** App-only admitted; user+app admitted; user-only unchanged;
   access-key-only and bot-only refused; blank subject id still refused.
3. **The invariant, directly.** U collaborates on P's bot B at member level;
   U grants app A. A reaches B. **Remove U as a collaborator → A is refused on
   the next request, with the grant row still present.** Re-add → A works again.
   This is the test that proves the bound is live rather than a snapshot; if
   only one test survives review, it is this one.
4. **Level, not just presence.** A acting as a member-level U is refused what a
   member-level U is refused, and the refusal is the gate's, not a new one.
5. **A1/A2.** Grant present → response identical to the delegating user's own
   call. No grant → `404` compared **byte-for-byte** against nonexistent-bot.
   Grant for another bot, another app, or another delegating user → `404`.
   A2 with `owner_id` naming anyone but the grant's owner → `404`.
   A2 with `owner_id` omitted → resolves from the record.
6. **A shared bot is unreachable on A1 groups** even with a grant — because it
   is unreachable for the human too. Pins that the invariant needs no special
   handling there.
7. **Two collaborators, one app, one bot** → two rows, independently
   withdrawable; withdrawing one leaves the other working.
8. **Owner override.** Owner sees a collaborator's grant with its delegating
   user, and can withdraw it; the collaborator sees only their own.
9. **Mode B.** Two bots, one granted → one returned, count says one; the user's
   own call returns both; no grants → empty `200`; a granted *shared* bot appears
   in the application's own view and not in the bot list.
10. **Mode C / C-open / D.** C admitted with a grant and `404` without; C-open
    admitted with none; D `401` on **all fourteen**, enumerated from `ADMISSION`
    rather than sampled.
11. **Deletion revokes** every delegation, including when an application
    performs the deletion; failure aborts.
12. **Migration.** The `ALTER` and the `CREATE` produce the same shape column for
    column and index for index.
13. **User callers unchanged** — existing suites pass **with no expectation
    edited**. An edited expectation is a finding, not a fix.
14. **Gateway.** `RouteSecurity.resolve` yields `user: required` for exactly the
    Mode D paths and the optional pair for the rest, derived from `ADMISSION` so
    the two expressions of the policy cannot drift.

## Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| The empty-table window closes before this lands, making the `ALTER` a data migration. | Verify emptiness at implementation time; if populated, backfill `user_id := owner_id` (correct for every row #937 could produce) and revise this plan rather than assume. |
| Widening who may grant is an authorization change, not a data change. | It is bounded by an existing gate rather than a new rule, and re-adjudicated per request. Tests 3 and 4 pin the bound; the contradicted docstring is rewritten rather than left to mislead. |
| A bot owner discovers machine access they did not authorize. | The owner's listing shows every grant with its delegator, and the owner can withdraw any. That is the whole of the override, and it is why "see only" was not taken. |
| The surface admitted here is wide (~55 of 63 operations). | Modes are assigned by operation *shape*, not taste; the table is one reviewable artifact; the inventory test proves the surface matches it. |
| `{user: optional, app: optional}` moves the unauthenticated refusal from the edge to the backend. | Still refused at `require_principal` before any handler. Named because it changes where unauthenticated traffic dies — relevant to edge rate-limiting and gateway logs. |
| `list_for_app`'s owner-based liveness filter silently drops every shared bot. | Called out above as a required change, with test 9 pinning a shared granted bot in the result. |
| `DELETE …/{app_id}` no longer identifies one row. | Resolved above: the owner's withdrawal removes every delegation of that app on that bot; a collaborator's removes their own. |
| A grant probe per request on ~50 operations. | Unique-key point lookup on an index that exists, once per request, and only for app-only callers. |
| Legacy `default` bot ids are non-unique across tenants. | The tenant guard scopes the lookup. Comment at the lookup. |

## Alternatives Considered

- **Keep the grant owner-only** (the first draft). Rejected by review: a person
  who collaborates on their team's bots rather than owning them could authorize
  nothing, and the failure would look identical to a missing grant.
- **Add `user_id` to the unique key alongside `owner_id`.** Rejected: 3416 bytes,
  past InnoDB's cap.
- **Snapshot the delegator's permission level into the grant.** Rejected: it
  makes the bound a copy that goes stale, and re-adjudicating costs nothing
  because the gate already runs.
- **Drop `user_id` and derive the user from `(app_id, bot_id)`.** Rejected: the
  parameter exists precisely so an operation has somewhere to name a user when
  the identity set stops carrying one; deriving it would make it optional across
  the surface and discard the check that makes a guessed `bot_id` useless.
- **Relax the verifier and check per handler.** Rejected: the arrangement the
  verifier's docstring exists to argue against.
- **Refuse every operation that does not name a bot.** Rejected: it refuses the
  listings, so an integration cannot discover its own scope.
- **Enumerate admitted paths in the gateway.** Rejected: ~55 rules nobody can
  review, against 6 for the refusals.

## Rollout

The schema change ships first and alone — it is additive and inert. The backend
change is then inert until the gateway rules ship: without them the App identity
never reaches the backend's principal, so every request is a user request and
behaves as today.

Rollback is the gateway config alone: revert the `route_security` rules and the
App identity stops reaching the backend. No code rollback. The column stays;
dropping it is neither necessary nor safe once grants exist.

## Dependencies

- `ac_bot_app_grant` and its repository (shipped, #937), reshaped here.
- `core/engine_runtime/gate.py`'s operator adjudication (shipped).
- Gateway `app` identity chain and `route_security` (shipped).
