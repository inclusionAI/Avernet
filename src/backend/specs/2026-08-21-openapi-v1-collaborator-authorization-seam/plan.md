# Plan: Public API — One Collaborator Authorization Seam

## Approach

One FastAPI dependency factory carries the answer — level, and the audit record
that follows a non-owner mutation — and one table says which answer each
operation wants. Neither is declared on a handler: a custom `APIRoute` subclass
reads the table at route construction and appends the dependency itself, so an
operation cannot forget the check and cannot opt out of it. The same
construction refuses to build a route the table does not name, which moves the
fail-closed default from a CI assertion to an assembly failure.

The table's initial rows record **what each group enforces today**, so
introducing the seam changes no answer anywhere. No group adopts the seam in
this change; adoption is #906/#907, and each is then a row edit.

## Affected Components

- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py`
  — **new.** The mode vocabulary, the per-operation table, the route class that
  applies it.
- `…/openapi_v1/bot_access.py` — **new.** The seam: resolve, adjudicate,
  audit.
- `…/openapi_v1/errors.py` — one new dependency-raised error type.
- `…/openapi_v1/__init__.py:387` `build_public_router` — the totality check and
  the "every route came through the seam's route class" assertion.
- `…/adapters/http/app.py:659` — an exception handler for the new type
  (a dependency-raised error never reaches `@envelope_errors`).
- 36 router modules under `openapi_v1/` — one-line `route_class=` on each
  `APIRouter(...)`.
- `src/backend/docs/openapi-v1/README.md` — the rule, where the next author
  looks.

Read, not modified: `core/bot_collaborator/protocols.py`
(`resolve_operable_permission_level`),
`core/repository/protocols/bot/collaborator.py:218`
(`BotCollabLogRepositoryProtocol.insert`),
`core/repository/protocols/bot/bot.py:39` (`get_by_id_and_owner`).

Deliberately untouched: `core/bot_collaborator/services/collaborator_lock_service.py`
and every current caller of it. The seam carries no lock; the locks that
`channels/router.py:171` and `service_publication_facade.py:544` enforce stay
exactly as they are.

## Data Model Changes

None. `ac_bot_collaborator` and `ac_bot_collab_log` already carry everything the
seam reads and writes.

## API / Interface Changes

### The mode vocabulary

Every row answers one question — **who enforces this operation's
authorization, and at what level?** Two modes are the permanent answer
(`spec.md` *The Final Shape*); three more are scaffolding that exists only
while operations are still on their way to one of the two.

```python
# openapi_v1/authorization.py (new) — PERMANENT
@dataclass(frozen=True)
class Check:
    """**The seam enforces this.** Its dependency is attached to the route.

    The level is a parameter and not a further mode: the surface's bars really
    do differ per operation — MEMBER to drive a bot's sessions, ADMIN to write
    a channel, OWNER to restart a container.

    No row is `Check` when this lands. It is what a follow-on session switches
    a row *to* when it moves that group's enforcement here.
    """
    level: PermissionLevel          # the minimum; OWNER always passes

@dataclass(frozen=True)
class NoCheck:
    """**Nothing to verify.** The seam attaches nothing, deliberately.

    Covers two situations, which is why the reason is required and not a
    comment: the operation has no bot dimension at all (`"tenant-identical
    catalogue"`), or it is bot-scoped and intentionally unguarded
    (`"share viewers render panels without an editor relation"` —
    `render_screens/gating.py:31`). A reviewer must be able to tell a decision
    from an oversight.
    """
    reason: str
```

```python
# openapi_v1/authorization.py (new) — SCAFFOLDING, deleted when its last row leaves
@dataclass(frozen=True)
class ServiceChecked:
    """A service enforces this, elsewhere. → becomes `Check(level)`.

    The row exists because every operation must have one (the totality rule
    below) — this is how an operation says "I am covered, just not here" — and
    it records the bar to preserve, so the migration is a comparison rather
    than a guess.
    """
    level: PermissionLevel          # what that module actually enforces
    where: str                      # importable path — asserted to resolve

#: No collaborator dimension decided yet: the operation resolves the bot as
#: ``(bot_id, caller)``, so only the owner reaches it. → becomes `Check(...)`,
#: at a level #906 / #907 decide. `Check(OWNER)` would be today's behaviour
#: exactly — with the owner defaulting to the caller, requiring OWNER means the
#: caller must be the owner, so the seam and the handler resolve the same bot.
OWNER_SCOPED = _Sentinel("owner-scoped")

#: No bot on the wire to adjudicate — the owner arrives on a record. Only the
#: retiring addresses in ``deprecated/``, whose own comment says the set "is
#: empty the day this package is deleted". → disappears with that package.
SELF_CHECKED = _Sentinel("self-checked")
```

Worked example, `POST /openapi/v1/bots/{bot_id}/channels`:

```python
# today — checked by channels/router.py:152 `_require_admin` at ADMIN
("POST", "/openapi/v1/bots/{bot_id}/channels"): ServiceChecked(ADMIN, "…openapi_v1.channels.router")

# after the channels follow-on: `_require_admin` is deleted, the row flips
("POST", "/openapi/v1/bots/{bot_id}/channels"): Check(ADMIN)
```

Same answer to the caller, from one place instead of two. Contrast
`GET /openapi/v1/bots/{bot_id}`, which is `OWNER_SCOPED` today: #906 flipping it
to `Check(MEMBER)` **does** change behaviour — collaborators start getting
through — which is why that is a policy decision and not a mechanical
migration.

`ServiceChecked`'s weakness, stated so it is not discovered later: it is a claim
about code that lives somewhere else, and the `level` in particular can drift
from what that code really does. The citation test proves the module exists and
contains a permission call; it cannot prove the number is right. That is checked
by hand when the row migrates — which is also why it is scaffolding rather than
a mode anyone should be comfortable leaving in place.

There is no `mutates` flag: with no lock, the only thing it distinguished was
whether to audit, and the table key already carries the method (`spec.md`
*Decisions* 3). Nor is there a mode for bot-*type* gating — `SUPPORTED_BOT_TYPES`
answers a capability question (501), not an authorization one, and stays in the
handlers.

### The table

```python
# openapi_v1/authorization.py — one row per public operation, no exceptions
AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    ("GET",  "/openapi/v1/bots/{bot_id}/channels"):  ServiceChecked(MEMBER, "…openapi_v1.engine_runtime.gating"),
    ("POST", "/openapi/v1/bots/{bot_id}/channels"):  ServiceChecked(ADMIN,  "…openapi_v1.channels.router"),
    ("GET",  "/openapi/v1/bots/{bot_id}"):           OWNER_SCOPED,
    ("POST", "/openapi/v1/bots/{bot_id}/harness/apply"): ServiceChecked(ADMIN, "…openapi_v1.harness.router"),
    ("GET",  "/openapi/v1/bots/check-name"):         NoCheck("tenant-identical name availability"),
    # … every operation on the surface, exactly once
}
```

Every row in this change is `ServiceChecked` / `OWNER_SCOPED` / `SELF_CHECKED` /
`NoCheck`. `Check` is the mode a migration session switches a row to.

The harness rows record today's behaviour like any other group's — its defect
(`spec.md` *Motivation*) is that group owner's change, not this one's.

### The seam

```python
# openapi_v1/bot_access.py (new)
def require_check(rule: Check) -> Callable:
    """Build the dependency for one operation's rule.

    Enforcement only — the return value is not reachable from a handler, which
    is why handlers that need the resolved owner keep declaring `OwnerIdDep`.
    FastAPI caches per request, so that is not a second lookup.
    """
    async def _gate(
        bot_id: BotIdPath,
        caller: UserIdDep,
        owner_id: OwnerIdDep,
        request: Request,
    ) -> AsyncIterator[None]:
        bot = _resolve(request, bot_id, owner_id)          # get_by_id_and_owner; None → refuse
        level = _level(request, bot, caller, owner_id)     # any failure → NONE
        if level < rule.level:
            raise BotAccessRefusedError(...)               # → 404, masked
        yield
        if _audited(request) and level < PermissionLevel.OWNER:
            _audit(request, bot_id, owner_id, caller, request.scope["route"].path)
    return _gate

def _audited(request: Request) -> bool:
    """Reads are not audited. The table key already carries the method."""
    return request.method.upper() not in _READ_METHODS   # {"GET", "HEAD", "OPTIONS"}
```

Permission and audit are independent: `_audited` consults only the method, and
`_audit` cannot influence the level check. Nothing here reads or writes the edit
lock. Contrast the internal interceptor, where `persist_audit_log=False`
silently also skips the lock (`interceptor/collaborator.py:104`).

### The attach

```python
# openapi_v1/authorization.py
class PublicAPIRoute(APIRoute):
    def __init__(self, path: str, endpoint: Callable, **kw):
        rule = AUTHORIZATION.get((_method(kw), path))
        if rule is None:
            raise PublicRouteNotAuthorized(f"{_method(kw)} {path} is absent from AUTHORIZATION")
        if isinstance(rule, Check):
            kw["dependencies"] = [*(kw.get("dependencies") or []), Depends(require_check(rule))]
        super().__init__(path, endpoint, **kw)
```

Viable because paths are final at construction: every `openapi_v1` router
declares its own full `/openapi/v1/...` prefix, no `include_router` call in
`build_public_router` passes `prefix=`, and `app.py:852` mounts with none.

```diff
# openapi_v1/channels/router.py — the whole per-module change, ×36
- router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/channels", tags=["channels"])
+ router = APIRouter(
+     prefix="/openapi/v1/bots/{bot_id}/channels",
+     tags=["channels"],
+     route_class=PublicAPIRoute,
+ )
```

### The assembly-time totality check

```diff
# openapi_v1/__init__.py:502 — end of build_public_router
      public.include_router(bots_router, responses=ERROR_RESPONSES, dependencies=_PUBLIC_AUTH)
+     _assert_every_route_authorized(public)   # route class present on all; table has no orphan rows
      return public
```

`PublicAPIRoute.__init__` catches "operation missing from the table"; this
catches "router built without the route class" and "table row with no
operation". Both raise, so the app does not start — not merely a failing
assertion (`spec.md` acceptance criteria).

### New error type

```python
# openapi_v1/errors.py
class BotAccessRefusedError(Exception):
    """Caller below the operation's level (→ 404, byte-identical to absent bot)."""
```

```diff
# adapters/http/app.py:659 — beside _grant_not_resolvable_handler
+ @app.exception_handler(BotAccessRefusedError)   # → 404 via _public_mapped_error
```

Registered as a concrete type for the reason `errors.py:36` gives: a
dependency-raised error never reaches `@envelope_errors`. No lock error type is
added — the seam raises none.

## Key Files & Functions

```python
# openapi_v1/bot_access.py (new) — the three helpers the gate composes
def _resolve(request, bot_id: str, owner_id: str) -> Mapping[str, Any]: ...
def _level(request, bot, caller: str, owner_id: str) -> PermissionLevel: ...   # failure → NONE
def _audit(request, bot_id: str, owner_id: str, actor: str, route: str) -> None: ...  # never raises
```

Services come off `request.app.state.injector`, matching `principal.py:322`
`_grant_reader` — the seam runs at request time and cannot take constructor
injection.

```diff
# core/bot_collaborator/interceptor/collaborator.py:186 — NOT ported
-             ctx.metadata["permission_skipped"] = True
-             return ctx        # unresolvable owner ⇒ request proceeds
```

`_level` returns `NONE` on any failure instead, matching
`core/engine_runtime/gate.py:96`.

```python
# _audit — swallows, but loudly. spec.md Decisions 2.
except Exception:
    logger.exception("[editors] audit write failed bot=%s owner=%s actor=%s route=%s", ...)
```

## Dependencies

None. No new packages; `fastapi>=0.115` (`pyproject.toml:34`) already exposes
`APIRouter(route_class=…)`.

## Risks & Mitigations

- **Risk:** `include_router` may not preserve a custom route class, making the
  attach invisible after assembly.
  **Mitigation:** Task 1 pins it with a test asserting every route in the
  assembled router is a `PublicAPIRoute` and carries the expected dependency.
  If it does not hold, fall back to a post-build pass over `public.routes` using
  `fastapi.dependencies.utils.get_parameterless_sub_dependant` — same table,
  same seam, one file instead of 36.
- **Risk:** The `Check` code path has no production caller until #906/#907,
  so it is an unused branch that can rot.
  **Mitigation:** its behaviour is pinned by direct tests over a fixture router
  that declares `Check` rows, so the path is exercised on every run even
  though no shipped operation takes it. Flagged as `spec.md` Open Question 1.
- **Risk:** A router module is added later without `route_class=`, so its routes
  silently skip the table.
  **Mitigation:** that is exactly what `_assert_every_route_authorized` fails
  on, at assembly.
- **Risk:** A `ServiceChecked` row's claimed level drifts from what the cited
  module really enforces.
  **Mitigation:** partial only — a test asserts the cited path imports and
  contains a permission call. It cannot verify the level; that is compared by
  hand when the row is migrated.
- **Risk:** A `yield` dependency changes exception ordering for handlers.
  **Mitigation:** the post-`yield` half only audits, is wrapped so it cannot
  raise, and is skipped unless the handler returned.

## Alternatives Considered

- **Declare the dependency on each route, verify against a table** (the current
  grant design, `test_admission_inventory.py`). Rejected: two sources for one
  fact, 43 hand-declarations to keep in step, and omission is caught only in CI.
  This deliberately reverses that design's stated "the decision is visible on
  the route that carries it" — traded for single-source and an assembly failure.
- **Port `with_interceptors` as a decorator.** Rejected: it rewrites
  `__signature__` and smuggles the `Request` through a reserved kwarg
  (`interceptor/base.py:150`), and — decisively — a decorator is invisible to
  any test that reads the built router's effective dependencies.
- **Carry the edit lock in the seam.** Deferred, not rejected — `spec.md`
  *Decisions* 1. It would change answers for every group that later adopts the
  seam, and it deserves its own decision.
- **Keep a `mutates` flag for the audit rule.** Rejected: with no lock it
  distinguished only "audit or not", which the method already says.
- **One merged table with `ADMISSION`.** Rejected for now: `admission.py` is
  already 973 lines and answers a different question (which *machine* callers
  are admitted). Kept adjacent with a cross-check test instead.
- **Fix harness here as the seam's first adopter.** Removed from scope at the
  owner's direction; handed to that group's owner as its own change.

## Rollout

No migration, no flag. The change is inert for every group, and inertness is
asserted rather than assumed (Task 5). If the route-class attach proves
unworkable the fallback above is a one-file swap that leaves the table and the
seam untouched.

## Test Strategy

```python
# tests/…/openapi_v1/test_authorization_inventory.py (new)
def test_every_public_route_has_a_row(): ...
def test_no_row_without_a_route(): ...
def test_every_route_is_a_public_api_route(): ...
def test_unlisted_route_fails_assembly(): ...              # the fail-closed default
def test_service_checked_rows_cite_a_real_enforcer(): ...
def test_authorization_and_admission_cover_the_same_operations(): ...
```

```python
# tests/…/openapi_v1/test_bot_access.py (new) — over a fixture router
def test_owner_passes_every_level(): ...
def test_below_level_is_404_not_403(): ...
def test_unresolvable_bot_refuses(): ...                   # fail-closed, vs the interceptor's skip
def test_collaborator_lookup_failure_refuses(): ...
def test_non_owner_write_writes_one_audit_record(): ...
def test_read_writes_no_audit_record(): ...
def test_owner_write_writes_none(): ...
def test_audit_failure_logs_and_does_not_fail_the_request(): ...
def test_gate_never_touches_the_lock_service(): ...
```

```python
# tests/…/openapi_v1/test_authorization_is_inert.py (new)
def test_every_route_answers_as_before(): ...              # sweep, status-for-status
def test_service_level_edit_locks_still_enforced(): ...    # channels, publications
```

Existing suites that must stay green untouched:
`test_admission_inventory.py`, `test_principal_seam.py`,
`engine_runtime/test_operator_access.py`.
