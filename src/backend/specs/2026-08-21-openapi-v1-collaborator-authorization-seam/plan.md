# Plan: Public API — One Collaborator Authorization Seam

## Approach

One FastAPI dependency factory carries the whole answer — level, lock, audit —
and one table says which answer each operation wants. Neither is declared on a
handler: a custom `APIRoute` subclass reads the table at route construction and
appends the dependency itself, so an operation cannot forget the check and
cannot opt out of it. The same construction refuses to build a route the table
does not name, which moves the fail-closed default from a CI assertion to a
startup failure.

The table's initial rows record **what each group enforces today**, so
introducing the seam changes no answer anywhere. Only harness — whose check is
currently keyed on something other than what it acts on — is moved onto the
seam, which is what demonstrates the seam works end to end.

## Affected Components

- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py`
  — **new.** The mode vocabulary, the per-operation table, the route class that
  applies it.
- `…/openapi_v1/editors_gate.py` — **new.** The seam itself: resolve, adjudicate,
  lock, audit.
- `…/openapi_v1/errors.py` — two new dependency-raised error types.
- `…/openapi_v1/__init__.py:387` `build_public_router` — the totality check and
  the "every route came through the seam's route class" assertion.
- `…/openapi_v1/harness/router.py` — loses its local access dependency, its
  `bot_id == "default"` bypass and its body-borne owner; gains `OwnerIdDep`.
- `…/adapters/http/app.py:659` — exception handlers for the two new types
  (a dependency-raised error never reaches `@envelope_errors`).
- 36 router modules under `openapi_v1/` — one-line `route_class=` on each
  `APIRouter(...)`.
- `src/backend/docs/openapi-v1/README.md` — the rule, where the next author
  looks.

Read, not modified: `core/bot_collaborator/protocols.py`
(`resolve_operable_permission_level`),
`core/bot_collaborator/services/collaborator_lock_service.py:266`
(`get_lock_info`), `core/repository/protocols/bot/collaborator.py:218`
(`BotCollabLogRepositoryProtocol.insert`),
`core/repository/protocols/bot/bot.py:39` (`get_by_id_and_owner`).

## Data Model Changes

None. `ac_bot_collaborator` and `ac_bot_collab_log` already carry everything the
seam reads and writes.

## API / Interface Changes

### The mode vocabulary

```python
# openapi_v1/authorization.py (new)
@dataclass(frozen=True)
class Editors:
    """Adjudicated here: the seam attaches and enforces."""
    level: PermissionLevel          # the minimum; OWNER always passes
    mutates: bool = False           # True → edit lock required, action audited

@dataclass(frozen=True)
class ServiceChecked:
    """A service adjudicates this today. Recorded, not attached."""
    level: PermissionLevel          # what that service actually enforces
    where: str                      # module path, so the row is checkable

OWNER_SCOPED  = _Sentinel("owner-scoped")   # resolves by (bot_id, caller); no collaborator dimension yet
SELF_CHECKED  = _Sentinel("self-checked")   # owner arrives on a record, not the wire
NO_BOT        = _Sentinel("no-bot")         # operation has no bot to adjudicate
```

`Editors` / `ServiceChecked` are dataclasses rather than enum members because a
bare mode cannot carry the level, and a level that lives anywhere but the row is
a second place to look.

### The table

```python
# openapi_v1/authorization.py — one row per public operation, no exceptions
AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    ("POST", "/openapi/v1/bots/{bot_id}/harness/apply"):    Editors(ADMIN, mutates=True),
    ("GET",  "/openapi/v1/bots/{bot_id}/harness/dim-report"): Editors(MEMBER),
    ("GET",  "/openapi/v1/bots/{bot_id}/channels"):         ServiceChecked(MEMBER, "engine_runtime.gating"),
    ("POST", "/openapi/v1/bots/{bot_id}/channels"):         ServiceChecked(ADMIN,  "channels.router._require_admin"),
    ("GET",  "/openapi/v1/bots/{bot_id}"):                  OWNER_SCOPED,
    ("GET",  "/openapi/v1/bots/check-name"):                NO_BOT,
    # … every operation on the surface, exactly once
}
```

Follow-on work for #906/#907 is then literally `OWNER_SCOPED` → `Editors(...)`.

### The seam

```python
# openapi_v1/editors_gate.py (new)
def require_editors(rule: Editors) -> Callable:
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
        bot = _resolve(request, bot_id, owner_id)              # get_by_id_and_owner; None → refuse
        level = _level(request, bot, caller, owner_id)         # resolve_operable_permission_level; any failure → NONE
        if level < rule.level:
            raise EditorPermissionError(...)                   # → 404, masked
        if rule.mutates:
            _require_edit_lock(request, bot_id, owner_id, caller)   # → 423 naming the holder
        yield
        if rule.mutates and level < PermissionLevel.OWNER:
            _audit(request, bot_id, owner_id, caller, request.scope["route"].path)
    return _gate
```

Three settings, three independent effects. The internal interceptor couples lock
to audit (`persist_audit_log=False` silently skips the lock,
`core/bot_collaborator/interceptor/collaborator.py:104`); this does not.

### The attach

```python
# openapi_v1/authorization.py
class PublicAPIRoute(APIRoute):
    def __init__(self, path: str, endpoint: Callable, **kw):
        rule = AUTHORIZATION.get((_method(kw), path))
        if rule is None:
            raise PublicRouteNotAuthorized(f"{_method(kw)} {path} is absent from AUTHORIZATION")
        if isinstance(rule, Editors):
            kw["dependencies"] = [*(kw.get("dependencies") or []), Depends(require_editors(rule))]
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

### The build-time totality check

```diff
# openapi_v1/__init__.py:502 — end of build_public_router
      public.include_router(bots_router, responses=ERROR_RESPONSES, dependencies=_PUBLIC_AUTH)
+     _assert_every_route_authorized(public)   # route class present on all; table has no orphan rows
      return public
```

`PublicAPIRoute.__init__` catches "operation missing from the table"; this
catches "router built without the route class" and "table row with no
operation" — the two failures the route class cannot see itself.

### New error types

```python
# openapi_v1/errors.py
class EditorPermissionError(Exception):
    """Caller below the operation's level (→ 404, byte-identical to absent bot)."""

class EditLockRequiredError(Exception):
    """Bot has collaborators and the caller does not hold its lock (→ 423)."""
```

```diff
# adapters/http/app.py:659 — beside _grant_not_resolvable_handler
+ @app.exception_handler(EditorPermissionError)   # → 404 via _public_mapped_error
+ @app.exception_handler(EditLockRequiredError)   # → 423, body names the holder
```

Registered as concrete types for the reason `errors.py:36` gives: a
dependency-raised error never reaches `@envelope_errors`.

### Harness, corrected

```diff
# openapi_v1/harness/router.py:76 — delete require_harness_bot_access entirely
- async def require_harness_bot_access(user_id, bot_id, bot_repo, collaborator_service) -> str:
-     if bot_id == "default":
-         return user_id                      # authorization bypass
-     bot = bot_repo.get_by_id(bot_id)        # "no owner check" — not a permission method
```

```diff
# openapi_v1/harness/router.py:131 — every handler, same shape
  async def harness_diagnose(
      bot_id: Annotated[str, Path(...)],
      body: HarnessDiagnoseRequest,
      user_id: UserIdDep,
-     _: HarnessBotAccessDep,
+     owner_id: OwnerIdDep,
  ):
-     ...  entity_id=body.entity_id
+     entity = _agreed_entity(body.entity_id, owner_id)   # must equal, else refuse
+     ...  entity_id=entity
```

```jsonc
// POST /openapi/v1/bots/{bot_id}/harness/apply?user_id=u-1&owner_id=u-2
// body {"entity_id": "u-2", …} → 200   (agrees with the authorized owner)
// body {"entity_id": "u-9", …} → 404   (disagrees; refused, not honoured)
// body {}                      → 200   (omitted; the authorized owner is used)
```

Non-breaking for a correct caller, and it removes the second locator — the
`entity_id` the check never saw. The retained-but-must-agree rule is the one
`engine_runtime/params.py:139` already applies to an application's `owner_id`.

## Key Files & Functions

```python
# openapi_v1/editors_gate.py (new) — the four helpers the gate composes
def _resolve(request, bot_id: str, owner_id: str) -> Mapping[str, Any]: ...
def _level(request, bot, caller: str, owner_id: str) -> PermissionLevel: ...   # failure → NONE
def _require_edit_lock(request, bot_id: str, owner_id: str, caller: str) -> None: ...
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
- **Risk:** A router module is added later without `route_class=`, so its routes
  silently skip the table.
  **Mitigation:** that is exactly what `_assert_every_route_authorized` fails
  on, at startup.
- **Risk:** The seam's `owner_id` parameter appears on operations that did not
  publish one, changing the API document.
  **Mitigation:** in this phase only harness gains it, and gaining it is the
  point. Every other adjudicated row is `ServiceChecked`, which attaches
  nothing.
- **Risk:** Double adjudication where a `ServiceChecked` group is later moved to
  the seam with a different bar.
  **Mitigation:** the row records the level the service enforces, so the
  migration is a comparison, not a guess.
- **Risk:** A `yield` dependency changes exception ordering for handlers.
  **Mitigation:** the post-`yield` half only audits, is wrapped so it cannot
  raise, and is skipped unless the handler returned.

## Alternatives Considered

- **Declare the dependency on each route, verify against a table** (the current
  grant design, `test_admission_inventory.py`). Rejected: two sources for one
  fact, 43 hand-declarations to keep in step, and omission is caught only in CI.
  This deliberately reverses that design's stated "the decision is visible on
  the route that carries it" — traded for single-source and a startup failure.
- **Port `with_interceptors` as a decorator.** Rejected: it rewrites
  `__signature__` and smuggles the `Request` through a reserved kwarg
  (`interceptor/base.py:150`), and — decisively — a decorator is invisible to
  any test that reads the built router's effective dependencies.
- **One merged table with `ADMISSION`.** Rejected for now: `admission.py` is
  already 973 lines and answers a different question (which *machine* callers
  are admitted). Kept adjacent with a cross-check test instead.
- **Migrate all `ServiceChecked` groups in this phase.** Rejected: eleven groups
  of behaviour change would hide the seam's introduction.

## Rollout

No migration, no flag. The change is inert for every group except harness, and
inertness is asserted rather than assumed (Task 6). If the route-class attach
proves unworkable the fallback above is a one-file swap that leaves the table
and the seam untouched.

## Test Strategy

```python
# tests/…/openapi_v1/test_authorization_inventory.py (new)
def test_every_public_route_has_a_row(): ...
def test_no_row_without_a_route(): ...
def test_every_route_is_a_public_api_route(): ...
def test_unlisted_route_fails_to_build(): ...          # the fail-closed default
def test_authorization_and_admission_cover_the_same_operations(): ...
```

```python
# tests/…/openapi_v1/test_editors_gate.py (new)
def test_owner_passes_every_level(): ...
def test_below_level_is_404_not_403(): ...
def test_unresolvable_bot_refuses(): ...               # fail-closed, vs the interceptor's skip
def test_collaborator_lookup_failure_refuses(): ...
def test_mutation_without_lock_is_423_naming_holder(): ...
def test_read_never_takes_the_lock(): ...
def test_non_owner_mutation_writes_one_audit_record(): ...
def test_owner_mutation_writes_none(): ...
def test_audit_failure_does_not_fail_the_request(): ...
```

```python
# tests/…/openapi_v1/harness/test_harness_authorization.py (new)
def test_default_bot_id_no_longer_bypasses(): ...
def test_body_entity_id_must_agree_with_authorized_owner(): ...
def test_collaborator_below_admin_cannot_apply(): ...
def test_bot_of_another_owner_is_404(): ...
```

```python
# tests/…/openapi_v1/test_authorization_is_inert.py (new)
def test_every_non_harness_route_answers_as_before(): ...   # sweep, status-for-status
```

Existing suites that must stay green untouched:
`test_admission_inventory.py`, `test_principal_seam.py`,
`engine_runtime/test_operator_access.py`.
