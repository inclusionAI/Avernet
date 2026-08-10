# Plan: Public API — Owner-Granted Bot Authorization for Applications

## Approach

One new persisted record (`ac_bot_app_grant`) read from both ends: a bot-scoped
group (`/openapi/v1/bots/{bot_id}/authorized-apps`, three operations) and an
app-scoped read (`/openapi/v1/authorized-bots`, one operation). Nothing in the
admission path moves: the verifier still refuses every identity set that names
no end user, `UserIdDep` still requires the request's `user_id` to be the
verified caller, and no existing route changes who may call it.

The app-scoped read is a **user + app** call, not an application-only one — the
same both-parties posture as granting. That is what keeps it inside this
feature: it needs no verifier relaxation. It is also forced rather than chosen,
and the reason is worth stating because it forecloses the obvious alternative:
the gateway's auth runner iterates **only the identities a route declares**
(`gateway/core/authn/_runner.py:40`), so an app presenting its token to a
`user: required` route is never resolved and never reaches the signed principal.
There is no way to carry an app identity onto a user-only route to scope an
answer by it. Either the route declares `app` — and then the app is mandatory —
or the app is invisible. Declaring it is the option that needs no new admission.

The consent shape is enforced by two mechanisms that **already exist and are
already proven in production code**, so this feature adds no auth machinery:

- the gateway's `route_security` table, which already expresses
  `{user: required, app: required}` for `/openapi/v1/bots/logs/**`;
- the backend's `require_user_and_app_principal`
  (`adapters/http/openapi_v1/dependencies.py:194`), already used by
  `bot_logs/router.py:27` and pinned by `test_bot_logs_routes_require_user_and_app_principal`.

Grant reads its application identity off the verified `AppPrincipal`; there is
no `app_id` parameter, so a request cannot point a grant at any application but
the caller. Withdraw and list need only the owner, and therefore name `app_id`
in the path.

## Affected Components

- `src/backend/src/agentclaw/community/core/bot_app_grant/` — **new** domain
  module: ORM model, records, service. Sibling in shape to `core/bot_collaborator/`.
- `src/backend/src/agentclaw/community/core/repository/protocols/bot/app_grant.py`
  — **new** repository contract, in the `bot` domain (which per
  `core/repository/README.md` already serves `bot_collaborator`).
- `src/backend/src/agentclaw/community/core/repository/implementations/bot/app_grant.py`
  — **new** ORM body.
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorized_apps/`
  — **new** router group: 3 bot-scoped routes + the 1 app-scoped read. One
  module because both ends read one record and share its schemas; two routers
  because they mount at different prefixes.
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py:100-165`
  — register both **before** the `{bot_id}` wildcard `bots` router.
- `src/backend/src/agentclaw/community/di/modules/` — **new** DI module binding
  the protocol to the implementation, modelled on `bot_collaborator_module.py:52`.
- `src/gateway/configs/application.yaml:111` — one `route_security` entry.
- `src/gateway/configs/schemas/bots.openapi.json` — regenerated build output.

## Data Model Changes

**Two tables, and the split is the whole design.** One table cannot hold both
"exactly one live grant" and "every past grant period", because the invariant
wants a unique key and the history wants unlimited rows on those same columns.
Soft-delete looked like it bridged them and does not: putting `status` in the
unique key survives grant → withdraw → grant, then fails on the *second*
withdrawal, when two `revoked` rows collide. A filtered unique index
(`WHERE status='active'`) is the textbook answer and MySQL — therefore
OceanBase — does not have one.

So the invariant gets a table it can actually be declared on, and the history
gets a table with no unique key at all. This is the shape
`ac_bot_collaborator` / `ac_bot_collab_log` already uses in this codebase.

```python
# src/backend/src/agentclaw/community/core/bot_app_grant/models.py (new)
class BotAppGrantModel(Base):
    """Live grants only — a row exists iff the app may reach the bot right now."""
    __tablename__ = "ac_bot_app_grant"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)

    app_id   = Column(AutoIncrementBigInteger, nullable=False, comment="gateway avernet_application.id, from the AppPrincipal")
    app_name = Column(String(256), nullable=False, comment="app display name, snapshotted at consent time")
    bot_id   = Column(String(256), nullable=False, comment="the authorized bot")
    owner_id = Column(String(256), nullable=False, comment="bot owner, resolved server-side")
    tenant   = Column(String(128), nullable=False, comment="tenant, cross-checked on write")
    env      = Column(String(20), nullable=False, default=get_current_env)

    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("app_id", "bot_id", "owner_id", "env",
                         name="uk_app_bot_owner_env"),
        # the app's view: which of this owner's bots may this app reach
        Index("idx_app_owner_env", "app_id", "owner_id", "env"),
    )


class BotAppGrantLogModel(Base):
    """Append-only. One row per grant and per withdrawal; never updated."""
    __tablename__ = "ac_bot_app_grant_log"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)

    app_id   = Column(AutoIncrementBigInteger, nullable=False)
    app_name = Column(String(256), nullable=False)
    bot_id   = Column(String(256), nullable=False)
    owner_id = Column(String(256), nullable=False)
    tenant   = Column(String(128), nullable=False)
    env      = Column(String(20), nullable=False, default=get_current_env)
    action   = Column(String(32), nullable=False, comment="granted | revoked")

    gmt_create = Column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        # reconstruct a bot's authorization history in order
        Index("idx_log_bot_owner_env", "bot_id", "owner_id", "env", "gmt_create"),
    )
```

Notes on the shape:

- **No unique key on the log, deliberately.** Its job is to accept every event,
  including the fourth `revoked` for the same pair. A constraint there would
  reintroduce exactly the bug this split fixes.
- **The live table needs no `status` and no `revoked_at`.** A grant is live iff
  its row exists, so there is no second state to model and nothing nullable to
  reason about. The `revoked_at` column — and the `| None` question it raised —
  disappears with the soft-delete it belonged to.
- **`env` is in the unique key**, matching `ac_bot_collaborator`'s
  `uk_bot_pk_user_env` (`core/bot_collaborator/models.py:139`). The natural key
  is `(app_id, bot_id, owner_id)` *within an environment*; omitting `env` would
  make one row collide across environments sharing a database.
- **No `bot_pk`.** `bot_id` alone is not unique across owners
  (`core/engine_runtime/gate.py:87`), but `bot_id` + `owner_id` carries the same
  uniqueness without putting a surrogate key into a public-facing record.
- **No `granted_by` / `revoked_by`.** Under owner-only authority both equal
  `owner_id` by construction — a column that can only restate another.
- **The log duplicates `app_name` and `tenant` rather than joining.** It is an
  audit record: it must still read correctly after the live row is gone, which
  is precisely when it is consulted.
- `idx_app_owner_env` is not redundant with the unique key even though both lead
  on `app_id`: the key's second column is `bot_id`, so it cannot serve an
  `(app_id, owner_id)` lookup that names no bot — exactly the app's view. The
  owner's view (`bot_id`, `owner_id`) needs no index of its own; it is served by
  scanning few rows and can gain one if it ever matters.

Grant and its log write happen in **one transaction**, so the history cannot
disagree with the live state.

No migration file: the backend creates tables through `DataSourcePlugin`
`create_all()` the same way `ac_bot_collaborator` does.

### Deviation to accept or strike: `app_name`

**Not in the column list the user settled — proposed here, flagged for a
one-word decision.** `spec.md`'s user story *"an authorization I no longer
recognize is visible to me"* is not served by a bare numeric `app_id`. The
application's human-facing name is **already on the credential** at grant time
(`GatewayApp.app_name`, `core/gateway_principal/models.py:62`), so snapshotting
it costs one column, no lookup and no cross-module dependency. The backend
cannot resolve it any other way — the gateway's `avernet_application` registry
is not readable from here, and `RegisteredApp.to_record()` deliberately keeps
non-core columns gateway-side (`gateway/core/app/_orm.py:47`).

Snapshot, not a live join: it records what the owner consented to, which is the
right thing for an audit even if the app is later renamed.

**To strike it:** drop the column and the field from the list response. Nothing
else in this plan depends on it.

## API / Interface Changes

```python
# src/backend/.../adapters/http/openapi_v1/authorized_apps/router.py (new)
router = APIRouter(prefix="/bots/{bot_id}/authorized-apps", tags=["Authorized Apps"])

# Both identities. The APP half is checked here; the USER half is guaranteed
# upstream by verify_principal_token::_require_user_principal.
UserAndAppDep = Annotated[Principal, Depends(require_user_and_app_principal)]

@router.post("", response_model=Envelope[AuthorizedApp], responses=USER_SCOPED_403)
@envelope_errors
async def grant_authorized_app(
    bot_id: str, request: Request, owner_id: UserIdDep, principal: UserAndAppDep,
) -> Envelope[AuthorizedApp]: ...

@router.get("", response_model=Envelope[Page[AuthorizedApp]], responses=USER_SCOPED_403)
@envelope_errors
async def list_authorized_apps(
    bot_id: str, request: Request, owner_id: UserIdDep, principal: PrincipalDep,
) -> Envelope[Page[AuthorizedApp]]: ...

@router.delete("/{app_id}", response_model=Envelope[Deleted], responses=USER_SCOPED_403)
@envelope_errors
async def revoke_authorized_app(
    bot_id: str, app_id: int, request: Request, owner_id: UserIdDep,
    principal: PrincipalDep,
) -> Envelope[Deleted]: ...
```

```python
# .../authorized_apps/router.py — the app's view, mounted at a second prefix
app_view_router = APIRouter(prefix="/authorized-bots", tags=["Authorized Apps"])

@app_view_router.get("", response_model=Envelope[Page[AuthorizedBot]],
                     responses=USER_SCOPED_403)
@envelope_errors
async def list_authorized_bots(
    request: Request, owner_id: UserIdDep, principal: UserAndAppDep,
) -> Envelope[Page[AuthorizedBot]]:
    """Which of this owner's bots may the CALLING app reach.

    ``app_id`` comes off the principal, never a parameter — so this cannot be
    used to ask what some other application may reach.
    """
```

```jsonc
// POST /openapi/v1/bots/{bot_id}/authorized-apps?user_id=… → 201
{ "data": { "app_id": 42, "app_name": "partner-platform", "bot_id": "…",
            "granted_at": "2026-08-10T00:00:00Z" }, "request_id": "…" }

// GET  …/authorized-apps?user_id=…      → 200, live grants only
// DELETE …/authorized-apps/42?user_id=… → 200 {"deleted": true}
//   404 — no such live grant (distinct from a successful withdraw)
//   404 — bot absent OR caller is not the owner (byte-identical, deliberately)
//   403 — user_id names someone other than the verified caller (UserIdDep, unchanged)
//   401 — POST without an app identity, refused at the gateway before this runs

// GET /openapi/v1/authorized-bots?user_id=… → 200
//   the app's view: this owner's bots that the CALLING app may reach
{ "data": { "items": [ { "bot_id": "…", "granted_at": "2026-08-10T00:00:00Z" } ],
            "total": 1 }, "request_id": "…" }
//   401 — no app identity, refused at the gateway before this runs
//   200 with an empty page — the app holds no grants from this owner. NOT a 404:
//   "you have nothing here" is a valid answer, and the owner's existence is
//   already implied by their own credential being on the call.
```

No existing signature changes. This is additive to the published description.

## Key Files & Functions

```yaml
# src/gateway/configs/application.yaml:111 — route_security
   route_security:
     "/**":
       user: required
 
     "/openapi/v1/bots/**":
       user: required
 
+    # Granting an application access to a bot is a CONSENT moment, and consent
+    # requires both parties on the wire: the owner's identity and the
+    # application's own credential. The application is never named as a
+    # parameter — the handler reads it off the App principal — so this rule is
+    # what makes "you cannot grant to someone else" true rather than checked.
+    #
+    # METHOD-QUALIFIED, and that is load-bearing. GET (list) and DELETE
+    # (withdraw) on the same prefix deliberately do NOT require an App: an owner
+    # must be able to withdraw after the application's credential is lost or
+    # rotated, and asking "which apps can reach my bot?" must not depend on
+    # holding any one application's key. They inherit `user: required` from
+    # "/openapi/v1/bots/**" above, which is the correct requirement for them —
+    # left to inherit rather than restated, because a second copy is a second
+    # thing to keep in step.
+    "POST /openapi/v1/bots/{bot_id}/authorized-apps":
+      user: required
+      app: required
+
+    # The app's view — "which of this owner's bots may I reach?". Both parties
+    # again, and here the App is not merely required, it is what the answer is
+    # SCOPED BY: the handler reads app_id off this principal, so declaring the
+    # identity is what makes the scoping possible at all. The runner resolves
+    # only the identities a route declares, so on a user-only rule the App would
+    # be invisible here and the query would have nothing to filter on.
+    #
+    # Not method-qualified: GET is the only method this path has, and every
+    # method it could grow answers the same "what may I reach" question.
+    "/openapi/v1/authorized-bots":
+      user: required
+      app: required
```

Resolution is already correct with no grammar change, verified against
`gateway/core/paths/_pattern.py`:
- `_is_param` (`:45`) accepts `{bot_id}` as a one-segment wildcard.
- `specificity` (`:66`) is `(0 if has_glob else 1, literals, params)`, so the
  glob-free new rule `(1, 4, 1)` beats `/openapi/v1/bots/**` `(0, 3, 0)` on the
  first term.
- `_split_key` (`core/authn/_route_security.py:74`) already parses the
  `"METHOD /path"` form.

```python
# src/backend/.../core/bot_app_grant/services/grant_service.py (new) — domain policy lives here
class BotAppGrantService:
    def grant(self, *, bot_id: str, owner_id: str, app_id: int,
              app_name: str, tenant: str) -> BotAppGrantRecord: ...
    def list_for_bot(self, *, bot_id: str, owner_id: str) -> list[BotAppGrantRecord]: ...
    def list_for_app(self, *, app_id: int, owner_id: str) -> list[BotAppGrantRecord]: ...
    def revoke(self, *, bot_id: str, owner_id: str, app_id: int) -> None:
        """Raises GrantNotFoundError when no live grant matches."""
```

`list_for_app` needs **no bot-existence check**, and that is a deliberate
asymmetry with the other three. They resolve a named bot and inherit the masked
404 from that read; this one names no bot, so there is nothing to mask — it
returns the owner's own grant rows for the calling app, and an owner learns
nothing about anyone else's bots from their own empty list.

```python
# src/backend/.../core/repository/protocols/bot/app_grant.py (new)
class BotAppGrantRepositoryProtocol(Protocol):
    @abstractmethod
    def grant(self, data: Dict[str, Any]) -> BotAppGrantRecord:
        """Insert the live row and append 'granted' to the log, in one transaction.

        Idempotent: an existing live row is returned untouched, and nothing is
        logged, so a duplicate call cannot move gmt_create or invent a period.
        """
    @abstractmethod
    def revoke(self, bot_id: str, owner_id: str, app_id: int) -> bool:
        """Delete the live row and append 'revoked', in one transaction.

        Returns False when no live row matched, so the adapter can answer 404
        distinctly from a successful withdrawal.
        """
    @abstractmethod
    def list_for_bot(self, bot_id: str, owner_id: str) -> List[BotAppGrantRecord]: ...
    @abstractmethod
    def list_for_app(self, app_id: int, owner_id: str) -> List[BotAppGrantRecord]: ...
    @abstractmethod
    def find(self, bot_id: str, owner_id: str, app_id: int) -> Optional[BotAppGrantRecord]: ...
```

No `_active` suffixes any more: the live table holds nothing else, so "active"
stopped being a qualifier and became the table's meaning. `grant` and `revoke`
own their log writes rather than leaving them to the service — the two must be
one transaction, and a caller that can forget the second half is a caller that
will.

The two `list_*` members are the record's two reading ends. Both take
`owner_id`, so neither can return a row belonging to anyone but the caller —
the scoping is in the contract, not left to each caller to remember.

Every member `@abstractmethod` and the implementation declares the Protocol as a
base — the enforceable-contract rule from `core/repository/README.md`, held by
`tests/community/architecture/test_repository_contracts.py`.

```diff
# src/backend/.../adapters/http/openapi_v1/__init__.py:155 — mount order
 _SUBGROUPS = [
+    authorized_apps_router,
+    authorized_bots_router,
     identity_router,
     resources_router,
     routines_router,
     skills_router,
 ]
```

Must be in `_SUBGROUPS` (or another literal list) rather than after `bots`: the
`bots` router owns `/{bot_id}` and is mounted last precisely so literal
sub-groups are not shadowed by it. `authorized-apps` sits *under* `{bot_id}`, so
it is not shadowed by path shape — but registering it with the literals keeps it
under the same `USER_SCOPED_ERROR_RESPONSES` table the rest of the surface uses.
`authorized-bots` is a top-level literal and genuinely does need to precede
`bots`, since nothing else keeps a future `/openapi/v1/{something}` from
claiming it.

### Ownership, tenancy and the masked refusal

The owner check needs no new code. Only the owner may call, so `owner_id` **is**
the verified caller (`UserIdDep` already refuses a `user_id` naming anyone else,
`principal.py:174`). The existing owner-scoped read is the check:

```python
# resolves under (bot_id, owner_id) and raises BotNotFoundError otherwise —
# the same masked answer core/engine_runtime/gate.py:114 gives a non-operator
bot = bot_service.get_bot(bot_id, owner_id)
```

This gives owner-only authority for free and is *deliberately narrower* than the
MEMBER+ operator bar in `core/engine_runtime/gate.py:78` — a collaborator who may
operate a bot may not manage its authorizations.

**The tenant guard is already enforced, and lands on the right answer.**
`AvernetTenantMiddleware` binds the request tenant from the verified principal
before any handler runs, and a `user + app` identity set resolves to the *app's*
tenant (user principals assert none — `core/gateway_principal/models.py:104`).
Every service read is tenant-scoped by that guard
(`adapters/http/openapi_v1/bots/router.py:6`), so a bot in another tenant is not
found — a cross-tenant grant attempt is refused, and refused with exactly the
masked 404 we want. The plan still **stores** `tenant` on the row and asserts
this behavior in a test rather than trusting it: it is the §11 anchor the later
machine-caller work reads, and an untested inherited guard is a guard that can
be removed by someone who does not know it is load-bearing.

### Re-granting (resolves spec Open Question 2)

- **Re-granting a live grant is idempotent and does NOT move `gmt_create`.** The
  record answers "this application could reach this bot from T1"; refreshing T1
  on a duplicate call would make the audit lie about when access began. Nothing
  is appended to the log either — a duplicate call is not a new period.
- **Re-granting a previously withdrawn one inserts a fresh live row** and
  appends a second `granted` event. The earlier period is already closed in the
  log by its `revoked` event, so the two periods stay distinguishable without
  the live table modelling either of them.

With the two-table split this stops being a constraint puzzle and becomes the
obvious reading: the live table says what is true now, the log says what
happened.

## Dependencies

None. No new packages, no version bumps, no new internal service calls.

## Risks & Mitigations

- **Risk:** the gateway rule is method-qualified, so a typo in the method or a
  future path rename silently drops it back to `/openapi/v1/bots/**` — the
  endpoint would then accept a user *without* an app, and grant would read an
  application identity that is not there.
  **Mitigation:** two independent nets. A gateway resolution test asserts the
  POST rule wins and GET/DELETE resolve to user-only; and the backend's
  `require_user_and_app_principal` refuses independently, so the config is
  defense-in-depth rather than the only check. Belt and braces is warranted —
  this is the one rule the whole consent story rests on.
- **Risk:** `authorized-apps` sits under the `{bot_id}` wildcard, so mount order
  or a future route could shadow it.
  **Mitigation:** register with the literal sub-groups (before `bots`), and
  assert all four routes resolve in a router test.
- **Risk:** the app's view is scoped by `app_id` read off the principal. If that
  route's rule ever loses `app: required`, the runner stops resolving the App
  (`_runner.py:40`) and the handler has no `app_id` to filter by — a listing
  that silently widens is far worse than one that fails.
  **Mitigation:** the handler takes `require_user_and_app_principal`, which
  refuses rather than defaults, so the failure mode is a 401 and never an
  unscoped list. The gateway test pins the rule as the outer net.
- **Risk:** the record is written now and read by a *later* feature; a shape
  mistake is expensive to correct once rows exist.
  **Mitigation:** the row answers "whose bot, in which tenant" on its own
  (`owner_id` + `tenant` resolved at write time), which is exactly what the
  later path needs so it never trusts the wire.
- **Risk:** `app_id` is a gateway-owned identifier with no foreign key here;
  a deleted application leaves an orphan grant.
  **Mitigation:** accepted and out of scope. An orphan grant is inert — the
  later read path resolves on `(app_id, bot_id)` and an application that no
  longer authenticates produces no principal to match it. Noted so it is a
  decision rather than an oversight.

## Alternatives Considered

- **Reuse BaaS `APIKeyPolicy.allowed_bots`** — rejected during investigation.
  Different credential registry (BaaS `APIKeyRecord` vs gateway
  `avernet_application`), different bot-id namespace (`real_bot_id:entity_id` vs
  plain `bot_id`), and a different definition of owner (BaaS
  `check_bot_permission` string-parses `entity_id` out of the id; the backend has
  a real owner column plus a collaborator table). Reusing it means a BaaS lookup
  on the backend hot path and reconciling two ownership models. BaaS
  `allowed_bots` stays where it is, governing the BaaS chat surface only.
- **Symmetric revoke (require both identities)** — rejected. An owner could then
  never withdraw after the application's key was lost or rotated, which makes
  the revoke worthless exactly when it is needed.
- **`app_id` as a POST body/query parameter** — rejected. It would let a request
  point a grant at an application other than the caller, which is the single
  property that makes this a consent moment.
- **Soft delete in one table** — tried, and **rejected during implementation**.
  It cannot express more than one grant period: with `status` in the unique key
  the second withdrawal collides, and without it the "one live grant" invariant
  is unenforceable, because MySQL/OceanBase have no filtered unique index. An
  earlier revision of this plan chose it, and rejected the log table below as
  redundant on the grounds that "a log row would carry nothing the soft-deleted
  grant row does not". That was the error: the soft-deleted row carries exactly
  one period, and the requirement is unbounded.
- **A separate log table** — now the chosen design, for the reason just given.
  Hard delete on withdraw is safe *because* the log outlives the row, so
  "could reach this bot between T1 and T2" stays answerable without the live
  table having to carry history it cannot hold.
- **An application-only reverse view** (`GET /authorized-bots` with no user) —
  deferred, not dismissed. It is what an integrator's own reconciliation loop
  really wants, since it needs no owner session. But it is an application-only
  route, so it needs the global `_require_user_principal` relaxation plus the
  per-route fail-closed opt-in that keeps the other ~56 operations from widening
  with it — the whole of the next workstream, pulled forward to serve one
  listing. The user+app view shipped here answers the same question whenever the
  owner is present, which is the same posture the integrator is already in when
  granting.
  (The log table this once listed as a rejected alternative is now the design —
  see the soft-delete entry above for why that call was wrong.)

## Rollout

No migration ordering, no feature flag, no backwards-compatibility concern: the
table is new, the routes are new, and no existing behavior changes.

```bash
# regenerate the published description after the routes land
python src/backend/scripts/dump_openapi.py
# copies to the gateway's single-box artifact:
#   src/gateway/configs/schemas/bots.openapi.json
```

Do **not** regenerate `src/gateway/tests/fixtures/bots.openapi.json` — it is a
frozen test fixture, not a build output (established by
`src/gateway/specs/2026-08-03-gateway-path-specific-domain-routing/tasks.md:212`).

Ordering: the gateway config entry and the backend routes should land together.
The config alone is inert (no route to protect); the routes alone would accept a
user without an app at the gateway, and `require_user_and_app_principal` would
answer 401 — safe, but it advertises an endpoint no one can use.

## Test Strategy

```python
# src/backend/tests/community/adapters/http/openapi_v1/test_principal_seam.py
def test_authorized_apps_post_requires_user_and_app_principal(): ...
    # mirrors test_bot_logs_routes_require_user_and_app_principal:361
def test_authorized_apps_get_and_delete_require_only_principal(): ...
```

```python
# src/backend/tests/community/adapters/http/openapi_v1/authorized_apps/test_router.py (new)
def test_grant_reads_app_from_principal_not_from_request(): ...
def test_grant_is_idempotent_and_does_not_move_granted_at(): ...
def test_regrant_after_revoke_creates_a_new_period(): ...
def test_list_excludes_revoked_grants(): ...
def test_revoke_absent_grant_is_404_distinct_from_successful_revoke(): ...
def test_non_owner_answer_is_byte_identical_to_absent_bot(): ...
def test_collaborator_may_operate_but_may_not_grant(): ...
def test_cross_tenant_bot_is_not_grantable(): ...

# the app's view
def test_list_authorized_bots_is_scoped_to_the_calling_app(): ...
    # two apps granted on the same owner's bots see disjoint lists
def test_list_authorized_bots_is_scoped_to_the_calling_owner(): ...
    # the same app, granted by two owners, sees only the calling owner's bots
def test_list_authorized_bots_excludes_revoked(): ...
def test_list_authorized_bots_empty_is_200_not_404(): ...
```

```python
# src/backend/tests/community/core/bot_app_grant/test_grant_service.py (new)
def test_owner_and_tenant_are_resolved_at_write_time_not_read_from_request(): ...
def test_revoked_row_retains_its_interval(): ...
```

```python
# src/gateway/tests/unit/core/authn/test_route_security.py
def test_post_authorized_apps_requires_user_and_app(): ...
def test_get_and_delete_authorized_apps_require_user_only(): ...
def test_authorized_bots_requires_user_and_app(): ...
    # all assert against the real configs/application.yaml, not a fixture table,
    # so a typo in the shipped config fails the suite
```

Coverage/CI: `src/backend/` changes select the Backend gate — SAST, unit tests,
changed-line coverage, and singlebox coverage (`AGENTS.md` → Pre-push Module
Selection). `src/gateway/` has no standalone lint step and runs nothing in
lint-only mode, so the gateway test above must be run explicitly. The new
`core/bot_app_grant` module needs its Core/Router denominators declared in
`scripts/ci/singlebox_coverage_modules.yaml` if the backend module's manifest
enumerates paths rather than globbing — **verify before implementing**; if it
does, add meaningful acceptance coverage rather than excluding the paths.
